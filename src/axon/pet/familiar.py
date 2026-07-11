"""AXON familiar — visual companion (dec-119 canonical sources).

Reacts to the AXON activity stream, not TTY access-time:

  - dendrites: N branches (up to 8), one per detected activity type.
    A dendrite fires when new TraceStore records arrive.
  - counters: ADRs from data/axon.db, tokens saved via load_gain().
  - timeline: recent ADR moments + compression moments via TraceStore +
    CompressionTelemetryStore (using is_compression_record).
  - activity poller: tails records.jsonl by byte offset; reads only new
    bytes on each tick (incremental, not full reload).
  - state: WORKING if any new trace record arrived in the last 5 s;
    HAPPY if a new ADR appeared; AWAKE otherwise.
  - fire colour: keyed to the record's payload.risk field when present
    (read -> cyan, write -> amber, destructive -> red).

All paths are resolved from load_runtime_config().data_root.
No hard-coded filesystem paths; no macOS TTY / ps scanning.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from axon.config.runtime import RuntimeConfig

# ---------- ANSI ----------

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
RESET = "\x1b[0m"
CLEAR = "\x1b[2J"
HOME = "\x1b[H"
HIDE = "\x1b[?25l"
SHOW = "\x1b[?25h"


def rgb(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


def vlen(s: str) -> int:
    return len(ANSI_RE.sub("", s))


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - vlen(s))


# ---------- State ----------

class State(Enum):
    AWAKE = "awake"
    WORKING = "working"
    HAPPY = "happy"


STATE_COLOR = {
    State.AWAKE:   (110, 210, 230),
    State.WORKING: (240, 200, 80),
    State.HAPPY:   (255, 215, 100),
}

# Fire colours keyed to trace-record risk class
RISK_FIRE_COLOR: dict[str, tuple[int, int, int]] = {
    "read":        (130, 220, 255),   # cyan-ish
    "write":       (255, 200, 80),    # amber
    "destructive": (255, 90, 60),     # red-orange
}
_DEFAULT_FIRE_COLOR: tuple[int, int, int] = (255, 245, 200)


# ---------- Dendrite geometry ----------

NUCLEUS = (7, 3)
GRID_W = 15
GRID_H = 7
DIRECTIONS = [
    ((0, -1), "│", (0, -2), "·"),    # N
    ((1, -1), "╱", (2, -2), "·"),    # NE
    ((1,  0), "─", (3,  0), "·"),    # E
    ((1,  1), "╲", (2,  2), "·"),    # SE
    ((0,  1), "│", (0,  2), "·"),    # S
    ((-1, 1), "╱", (-2, 2), "·"),    # SW
    ((-1, 0), "─", (-3, 0), "·"),    # W
    ((-1,-1), "╲", (-2,-2), "·"),    # NW
]


@dataclass
class Dendrite:
    direction: int
    label: str = ""
    last_fired: float = 0.0
    fire_start: float = 0.0
    fire_color: tuple[int, int, int] = field(default_factory=lambda: _DEFAULT_FIRE_COLOR)

    def trigger(self, now: float, color: tuple[int, int, int] = _DEFAULT_FIRE_COLOR) -> None:
        """Fire this dendrite now, optionally with a risk-derived colour."""
        self.fire_start = now
        self.last_fired = now
        self.fire_color = color


def _pick_directions(n: int) -> list[int]:
    if n <= 0:
        return []
    if n >= 8:
        return list(range(8))
    return sorted({round(i * 8 / n) % 8 for i in range(n)})[:n]


# ---------- Activity poller (TraceStore tail) ----------

@dataclass
class ActivityPoller:
    """Incrementally tails records.jsonl and reports new records since last poll.

    Uses a byte-offset cursor so only newly appended bytes are read on each
    tick; load_all() is never called after initialisation.
    """

    records_file: Path
    _offset: int = field(default=0, init=False)

    def poll(self) -> list[dict]:
        """Return list of newly-appended raw-dict records since the last poll."""
        if not self.records_file.exists():
            return []
        try:
            file_size = self.records_file.stat().st_size
        except OSError:
            return []
        if file_size <= self._offset:
            return []
        new_records: list[dict] = []
        try:
            with self.records_file.open("rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read(file_size - self._offset)
                self._offset = self._offset + len(chunk)
        except OSError:
            return []
        for raw_line in chunk.split(b"\n"):
            line = raw_line.strip()
            if not line:
                continue
            try:
                new_records.append(json.loads(line))
            except Exception:  # noqa: S110
                pass
        return new_records


# ---------- Real data sources (canonical stores) ----------

@dataclass
class Moment:
    ts: str       # ISO timestamp
    kind: str     # "adr" | "saved" | "trace"
    text: str


def _get_runtime() -> RuntimeConfig:
    from axon.config.runtime import load_runtime_config
    return load_runtime_config()


async def fetch_adr_data(runtime: RuntimeConfig | None = None) -> tuple[int, list[Moment]]:
    """Return (total_adr_count, last_4_adr_moments) via the repository abstraction."""
    from axon.store.session_store import SessionStore

    rt = runtime or _get_runtime()
    try:
        store = SessionStore(rt.db_path)
        await store.init()
        try:
            adrs = []
            for project in await store.all_projects():
                adrs.extend(await store.get_adrs(project, limit=1000))
        finally:
            await store.close()
    except Exception:
        return (0, [])
    adrs.sort(key=lambda a: a.created_at, reverse=True)
    moments = []
    for adr in adrs[:4]:
        text = adr.title if len(adr.title) <= 26 else adr.title[:24] + "…"
        moments.append(Moment(ts=adr.created_at.isoformat(), kind="adr", text=text))
    return (len(adrs), moments)


def fetch_compression_data(
    runtime: RuntimeConfig | None = None,
) -> tuple[int, list[Moment]]:
    """Return (lifetime_tokens_saved, last_4_saved_moments) via load_gain()."""
    from axon.observability.compression_telemetry import CompressionTelemetryStore
    from axon.observability.gain import is_compression_record, load_gain

    rt = runtime or _get_runtime()
    summary = load_gain(rt)
    tokens_saved = summary.saved_tokens

    # Build timeline moments from the most-recent real compression records
    store = CompressionTelemetryStore(rt)
    all_records = store.load_all()
    real = [r for r in all_records if is_compression_record(r)]
    real.sort(key=lambda r: r.ts, reverse=True)
    moments: list[Moment] = []
    for r in real[:4]:
        if r.reduction_tokens > 0:
            moments.append(Moment(
                ts=r.ts,
                kind="saved",
                text=f"compressed {r.reduction_tokens} tokens",
            ))
    return (tokens_saved, moments)


def fmt_when(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        local = dt.astimezone()
        same_day = local.date() == datetime.now().astimezone().date()
        if same_day:
            return local.strftime("%H:%M")
        return local.strftime("%m-%d")
    except Exception:
        return iso_ts[:10]


# ---------- Familiar renderer ----------

def render_familiar(
    state: State,
    dendrites: list[Dendrite],
    tick: int,
    now: float,
) -> list[str]:
    grid = [[" "] * GRID_W for _ in range(GRID_H)]
    grid_color: list[list[tuple[int, int, int] | None]] = [[None] * GRID_W for _ in range(GRID_H)]

    nx, ny = NUCLEUS
    r, g, b = STATE_COLOR[state]
    pulse = (math.sin(tick * 0.4) + 1) / 2
    bright = 0.55 + 0.45 * pulse
    base = (int(r * bright), int(g * bright), int(b * bright))
    dim = (int(r * 0.45), int(g * 0.45), int(b * 0.45))

    for d in dendrites:
        (mdx, mdy), mg, (tdx, tdy), tg = DIRECTIONS[d.direction]
        mx, my = nx + mdx, ny + mdy
        tx, ty = nx + tdx, ny + tdy
        firing = d.fire_start > 0 and (now - d.fire_start) < 1.0
        recently_used = (now - d.last_fired) < 30 if d.last_fired else False
        color = base if recently_used else dim

        if 0 <= my < GRID_H and 0 <= mx < GRID_W:
            grid[my][mx] = mg
            grid_color[my][mx] = color
        if 0 <= ty < GRID_H and 0 <= tx < GRID_W:
            grid[ty][tx] = tg
            grid_color[ty][tx] = color

        if firing:
            phase = (now - d.fire_start) / 1.0
            fire_col = d.fire_color
            if phase < 0.5:
                if 0 <= my < GRID_H and 0 <= mx < GRID_W:
                    grid[my][mx] = "●"
                    grid_color[my][mx] = fire_col
            else:
                if 0 <= ty < GRID_H and 0 <= tx < GRID_W:
                    grid[ty][tx] = "●"
                    grid_color[ty][tx] = fire_col

    grid[ny][nx] = "◉" if state == State.WORKING else "●"
    grid_color[ny][nx] = (r, g, b)

    if state == State.HAPPY and (tick % 4) < 2:
        spark = (255, 230, 130)
        for sx, sy in [(1, 0), (GRID_W - 2, 0), (GRID_W - 2, GRID_H - 1), (1, GRID_H - 1)]:
            grid[sy][sx] = "✦"
            grid_color[sy][sx] = spark

    out: list[str] = []
    for row, colrow in zip(grid, grid_color):
        chars: list[str] = []
        for ch, col in zip(row, colrow):
            if ch == " ":
                chars.append(" ")
            elif col is None:
                chars.append(ch)
            else:
                cr, cg, cb = col
                chars.append(f"{rgb(cr, cg, cb)}{ch}{RESET}")
        out.append("".join(chars))
    return out


# ---------- Card pieces ----------

WIDTH = 38

KIND_ICON = {"adr": "✦", "trace": "↪", "saved": "◇"}
KIND_COLOR = {
    "adr":   (255, 215, 100),
    "trace": (180, 200, 255),
    "saved": (160, 220, 180),
}


def render_counters(adrs: int, traces: int, saved_tokens: int) -> list[str]:
    dim_c = rgb(140, 150, 160)
    accent = rgb(220, 220, 230)

    def bar(n: int, cap: int = 10) -> str:
        filled = min(n, cap)
        return f"{accent}{'●' * filled}{dim_c}{'·' * (cap - filled)}{RESET}"

    return [
        f"{dim_c}▎ lifetime{RESET}",
        f"{dim_c}▎  {bar(min(adrs, 10))}  {accent}{adrs} ADRs{RESET}",
        f"{dim_c}▎  {bar(traces)}  {accent}{traces} traces{RESET}",
        f"{dim_c}▎  ~{accent}{saved_tokens:,}{dim_c} tokens saved{RESET}",
    ]


def render_timeline(moments: list[Moment]) -> list[str]:
    dim_c = rgb(140, 150, 160)
    fg = rgb(220, 220, 230)
    lines = [f"{dim_c}▎ recent moments{RESET}"]
    if not moments:
        lines.append(f"{dim_c}▎  · nothing yet{RESET}")
        return lines
    for m in moments[:4]:
        cr, cg, cb = KIND_COLOR.get(m.kind, (200, 200, 200))
        icon_color = rgb(cr, cg, cb)
        icon = KIND_ICON.get(m.kind, "·")
        when = fmt_when(m.ts)
        lines.append(
            f"{dim_c}▎  {when:<5} {icon_color}{icon} {fg}{m.text}{RESET}"
        )
    return lines


def compose_card(
    familiar: list[str],
    counters: list[str],
    timeline: list[str],
    status: str,
) -> str:
    border = rgb(90, 110, 130)
    rows: list[str] = []
    rows.append(f"{border}╭{'─' * (WIDTH - 2)}╮{RESET}")
    for line in familiar:
        rows.append(f"{border}│{RESET}  {pad(line, WIDTH - 5)} {border}│{RESET}")
    rows.append(f"{border}│{' ' * (WIDTH - 2)}│{RESET}")
    for line in counters:
        rows.append(f"{border}│{RESET}  {pad(line, WIDTH - 4)}{border}│{RESET}")
    rows.append(f"{border}│{' ' * (WIDTH - 2)}│{RESET}")
    for line in timeline:
        rows.append(f"{border}│{RESET}  {pad(line, WIDTH - 4)}{border}│{RESET}")
    target_h = 20
    while len(rows) < target_h - 1:
        rows.append(f"{border}│{' ' * (WIDTH - 2)}│{RESET}")
    rows.append(f"{border}╰{'─' * (WIDTH - 2)}╯{RESET}")
    rows.append(f"  {rgb(140, 150, 160)}{status}{RESET}")
    return "\n".join(rows)


# ---------- Main loop ----------

_NUM_DENDRITES = 6  # fixed layout; not TTY-count-driven


async def main(
    *,
    runtime: RuntimeConfig | None = None,
    frames: int | None = None,
) -> None:
    """Run the familiar live loop.

    Args:
        runtime: RuntimeConfig to use (default: load_runtime_config()).
        frames: If set, exit after this many render ticks (for CI / tests).
                None means run until Ctrl+C.
    """
    rt = runtime or _get_runtime()

    sys.stdout.write(HIDE)
    sys.stdout.write(CLEAR)
    sys.stdout.flush()

    # Initial data fetch
    adr_total, adr_moments = await fetch_adr_data(rt)
    tokens_saved, save_moments = fetch_compression_data(rt)

    from axon.observability.trace_store import TraceStore
    trace_store = TraceStore(rt)
    poller = ActivityPoller(records_file=trace_store.records_file)
    # Seed offset to end-of-file so we only react to *new* records
    if trace_store.records_file.exists():
        try:
            poller._offset = trace_store.records_file.stat().st_size
        except OSError:
            pass

    # Build fixed dendrite layout (direction-spread over 8 compass points)
    dirs = _pick_directions(_NUM_DENDRITES)
    dendrites = [Dendrite(direction=d) for d in dirs]

    last_data_refresh = time.time()
    happy_until = 0.0
    last_adr_total = adr_total
    last_activity = 0.0  # timestamp of most-recent new trace record

    # Round-robin index: which dendrite fires next on activity
    _next_dendrite = 0

    def _fire_from_record(record: dict, now: float) -> None:
        nonlocal _next_dendrite
        risk = record.get("payload", {}) if isinstance(record.get("payload"), dict) else {}
        risk_val = risk.get("risk", "") if isinstance(risk, dict) else ""
        color = RISK_FIRE_COLOR.get(str(risk_val), _DEFAULT_FIRE_COLOR)
        d = dendrites[_next_dendrite % len(dendrites)]
        d.trigger(now, color)
        _next_dendrite += 1

    start = time.time()
    tick = 0
    try:
        while frames is None or tick < frames:
            now = time.time()

            # Poll trace records for new activity
            new_records = poller.poll()
            for rec in new_records:
                _fire_from_record(rec, now)
                last_activity = now

            # Refresh ADR + compression data every 10 s
            if now - last_data_refresh > 10.0:
                last_data_refresh = now
                adr_total, adr_moments = await fetch_adr_data(rt)
                tokens_saved, save_moments = fetch_compression_data(rt)
                if adr_total > last_adr_total:
                    happy_until = now + 5.0
                last_adr_total = adr_total

            # State: HAPPY > WORKING (activity in last 5 s) > AWAKE
            if now < happy_until:
                current = State.HAPPY
            elif last_activity > 0 and (now - last_activity) < 5.0:
                current = State.WORKING
            else:
                current = State.AWAKE

            def _merge_moments() -> list[Moment]:
                all_m = list(adr_moments) + list(save_moments)
                all_m.sort(key=lambda m: m.ts, reverse=True)
                return all_m

            familiar = render_familiar(current, dendrites, tick, now)
            # trace count: use number of trace records seen this session
            trace_count = _next_dendrite
            counters = render_counters(adr_total, trace_count, tokens_saved)
            timeline = render_timeline(_merge_moments())

            status = (
                f"axon familiar · {current.value} · "
                f"{int(now - start)}s"
            )

            sys.stdout.write(HOME)
            sys.stdout.write(compose_card(familiar, counters, timeline, status))
            sys.stdout.write("\n")
            sys.stdout.flush()

            tick += 1
            await asyncio.sleep(0.25)
    finally:
        sys.stdout.write(SHOW)
        sys.stdout.write("\n")
        sys.stdout.flush()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.stdout.write(SHOW)
        sys.stdout.write("\n")
