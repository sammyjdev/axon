"""AXON familiar — visual prototype v3 (real data).

v3 wires the demo to actual AXON state instead of fake events:

  - dendrites: one per active user shell, each tied to a specific TTY.
    Activity = TTY atime changes => that dendrite fires.
  - counters: ADRs from data/axon.db, tokens saved from stats.jsonl
    (filtered to real compression engines, T-104 pollution excluded).
  - timeline: merged most-recent ADRs + nonzero compression events.
  - ghost filter: 30 min (was 12h) — closer to "actually visible".
  - state: WORKING if any dendrite fired in last 5s; AWAKE otherwise.

Hard-coded paths point at /Users/samdev/dev/axon. For v0 inside
src/axon/pet/ this would come from RuntimeConfig.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

# ---------- Paths to real AXON data ----------
#
# Resolution order:
#   1. $AXON_ROOT env var
#   2. walk up from cwd looking for data/axon.db
#   3. fallback: ~/dev/axon
#
# v0 should pull from axon.config.runtime.load_runtime_config() instead.

def _find_axon_root() -> Path:
    env = os.environ.get("AXON_ROOT")
    if env:
        return Path(env).expanduser()
    cur = Path.cwd()
    for _ in range(10):
        if (cur / "data" / "axon.db").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return Path.home() / "dev" / "axon"

AXON_ROOT = _find_axon_root()
AXON_DB = AXON_ROOT / "data" / "axon.db"
STATS_JSONL = AXON_ROOT / "data" / "compression" / "stats.jsonl"
REAL_ENGINES = {
    "caveman/phi3+rtkx",
    "caveman/phi3+rtk",  # historical telemetry (pre-rtkx rebrand)
    "caveman/phi3",
    "rtkx",
    "rtk",  # historical
    "fallback",
    "disabled",
}

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

# ---------- Shell detection ----------

SHELL_RE = re.compile(r"\b(zsh|bash|fish)\b")
GHOST_IDLE_THRESHOLD_S = 30 * 60  # was 12h; 30min matches "visible terminals"

@dataclass
class ShellInfo:
    tty: str
    idle_s: float
    is_ghost: bool

def detect_shells() -> list[ShellInfo]:
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid,tty,user,command"],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except Exception:
        return []
    user = os.environ.get("USER", "")
    now = time.time()
    seen: set[str] = set()
    shells: list[ShellInfo] = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        _pid, tty, who, cmd = parts
        if who != user or tty == "??":
            continue
        if not SHELL_RE.search(cmd.split()[0].rsplit("/", 1)[-1]):
            continue
        if tty in seen:
            continue
        seen.add(tty)
        try:
            idle = now - os.stat(f"/dev/{tty}").st_atime
        except Exception:
            idle = 0
        shells.append(ShellInfo(tty=tty, idle_s=idle, is_ghost=idle > GHOST_IDLE_THRESHOLD_S))
    return shells

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
    tty: str
    last_atime: float = 0.0
    fire_start: float = 0.0

    def poll_activity(self, now: float) -> None:
        try:
            atime = os.stat(f"/dev/{self.tty}").st_atime
        except Exception:
            return
        if self.last_atime == 0:
            self.last_atime = atime
            return
        if atime > self.last_atime + 0.3:
            self.fire_start = now
            self.last_atime = atime

def _pick_directions(n: int) -> list[int]:
    if n <= 0:
        return []
    if n >= 8:
        return list(range(8))
    return sorted({round(i * 8 / n) % 8 for i in range(n)})[:n]

# ---------- Familiar renderer ----------

def render_familiar(state: State, dendrites: list[Dendrite], tick: int, now: float) -> list[str]:
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
        recently_used = (now - d.last_atime) < 30 if d.last_atime else False
        color = base if recently_used else dim

        if 0 <= my < GRID_H and 0 <= mx < GRID_W:
            grid[my][mx] = mg
            grid_color[my][mx] = color
        if 0 <= ty < GRID_H and 0 <= tx < GRID_W:
            grid[ty][tx] = tg
            grid_color[ty][tx] = color

        if firing:
            phase = (now - d.fire_start) / 1.0
            if phase < 0.5:
                if 0 <= my < GRID_H and 0 <= mx < GRID_W:
                    grid[my][mx] = "●"
                    grid_color[my][mx] = (255, 245, 200)
            else:
                if 0 <= ty < GRID_H and 0 <= tx < GRID_W:
                    grid[ty][tx] = "●"
                    grid_color[ty][tx] = (255, 245, 200)

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

# ---------- Real data sources ----------

@dataclass
class Moment:
    ts: str       # ISO timestamp
    kind: str     # "adr" | "saved" | "handoff"
    text: str

def fetch_adr_data() -> tuple[int, list[Moment]]:
    """Return (total_adr_count, last_4_adr_moments)."""
    if not AXON_DB.exists():
        return (0, [])
    try:
        con = sqlite3.connect(f"file:{AXON_DB}?mode=ro", uri=True)
        cur = con.cursor()
        total = cur.execute("SELECT count(*) FROM adr").fetchone()[0]
        rows = cur.execute(
            "SELECT created_at, project, title FROM adr "
            "ORDER BY created_at DESC LIMIT 4"
        ).fetchall()
        con.close()
    except Exception:
        return (0, [])
    moments = []
    for created_at, project, title in rows:
        text = title if len(title) <= 26 else title[:24] + "…"
        moments.append(Moment(ts=created_at, kind="adr", text=text))
    return (total, moments)

def fetch_compression_data() -> tuple[int, list[Moment]]:
    """Return (lifetime_tokens_saved, last_4_saved_moments)."""
    if not STATS_JSONL.exists():
        return (0, [])
    total = 0
    hits = []
    try:
        with STATS_JSONL.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("engine") not in REAL_ENGINES:
                    continue
                saved = r.get("reduction_tokens", 0)
                if saved > 0:
                    total += saved
                    hits.append(r)
    except Exception:
        return (0, [])
    hits.sort(key=lambda r: r.get("ts", ""), reverse=True)
    moments = []
    for r in hits[:4]:
        moments.append(Moment(
            ts=r.get("ts", ""),
            kind="saved",
            text=f"compressed {r['reduction_tokens']} tokens",
        ))
    return (total, moments)

def fetch_handoffs() -> tuple[int, list[Moment]]:
    """Return (handoff_count, recent_handoff_moments).

    Approximation: count distinct (session, agent) pairs as handoff candidates.
    sessions table is empty in current production, so this returns (0, []).
    """
    if not AXON_DB.exists():
        return (0, [])
    try:
        con = sqlite3.connect(f"file:{AXON_DB}?mode=ro", uri=True)
        cur = con.cursor()
        # Naive: count session rows as a stand-in until real handoff events exist
        n = cur.execute("SELECT count(*) FROM sessions").fetchone()[0]
        con.close()
        return (n, [])
    except Exception:
        return (0, [])

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

# ---------- Card pieces ----------

WIDTH = 38

KIND_ICON = {"adr": "✦", "handoff": "↪", "saved": "◇"}
KIND_COLOR = {
    "adr":     (255, 215, 100),
    "handoff": (180, 200, 255),
    "saved":   (160, 220, 180),
}

def render_counters(adrs: int, handoffs: int, saved_tokens: int) -> list[str]:
    dim_c = rgb(140, 150, 160)
    accent = rgb(220, 220, 230)
    def bar(n: int, cap: int = 10) -> str:
        filled = min(n, cap)
        return f"{accent}{'●' * filled}{dim_c}{'·' * (cap - filled)}{RESET}"
    return [
        f"{dim_c}▎ lifetime{RESET}",
        f"{dim_c}▎  {bar(min(adrs, 10))}  {accent}{adrs} ADRs{RESET}",
        f"{dim_c}▎  {bar(handoffs)}  {accent}{handoffs} handoffs{RESET}",
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

def compose_card(familiar: list[str], counters: list[str], timeline: list[str], status: str) -> str:
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

async def main() -> None:
    sys.stdout.write(HIDE)
    sys.stdout.write(CLEAR)
    sys.stdout.flush()

    # Initial real-data fetch (cheap; we refresh occasionally)
    adr_total, adr_moments = fetch_adr_data()
    tokens_saved, save_moments = fetch_compression_data()
    handoffs_total, handoff_moments = fetch_handoffs()

    def merge_moments() -> list[Moment]:
        all_m = adr_moments + save_moments + handoff_moments
        all_m.sort(key=lambda m: m.ts, reverse=True)
        return all_m

    # Initial dendrite layout from shells
    shells = detect_shells()
    active_shells = [s for s in shells if not s.is_ghost]
    ghost_count = sum(1 for s in shells if s.is_ghost)
    dirs = _pick_directions(len(active_shells))
    dendrites = [
        Dendrite(direction=dirs[i], tty=active_shells[i].tty)
        for i in range(min(len(dirs), len(active_shells)))
    ]

    last_shell_check = 0.0
    last_data_refresh = time.time()
    happy_until = 0.0
    last_adr_total = adr_total

    start = time.time()
    tick = 0
    try:
        while True:
            now = time.time()

            # poll TTY activity for each dendrite — drives fire animations
            for d in dendrites:
                d.poll_activity(now)

            # rebuild dendrite list when shell set changes (every 3s)
            if now - last_shell_check > 3.0:
                last_shell_check = now
                shells = detect_shells()
                active_shells = [s for s in shells if not s.is_ghost]
                ghost_count = sum(1 for s in shells if s.is_ghost)
                if {d.tty for d in dendrites} != {s.tty for s in active_shells}:
                    dirs = _pick_directions(len(active_shells))
                    old_by_tty = {d.tty: d for d in dendrites}
                    dendrites = []
                    for i, s in enumerate(active_shells[: len(dirs)]):
                        existing = old_by_tty.get(s.tty)
                        if existing:
                            existing.direction = dirs[i]
                            dendrites.append(existing)
                        else:
                            dendrites.append(Dendrite(direction=dirs[i], tty=s.tty))

            # refresh real data every 10s
            if now - last_data_refresh > 10.0:
                last_data_refresh = now
                adr_total, adr_moments = fetch_adr_data()
                tokens_saved, save_moments = fetch_compression_data()
                handoffs_total, handoff_moments = fetch_handoffs()
                if adr_total > last_adr_total:
                    happy_until = now + 5.0  # celebrate new ADR
                last_adr_total = adr_total

            # state: HAPPY > WORKING (any recent fire) > AWAKE
            if now < happy_until:
                current = State.HAPPY
            elif any(d.fire_start > 0 and (now - d.fire_start) < 5.0 for d in dendrites):
                current = State.WORKING
            else:
                current = State.AWAKE

            familiar = render_familiar(current, dendrites, tick, now)
            counters = render_counters(adr_total, handoffs_total, tokens_saved)
            timeline = render_timeline(merge_moments())

            ghost_note = f" (+{ghost_count} ghost)" if ghost_count else ""
            status = (
                f"axon · {len(dendrites)} terminal"
                f"{'s' if len(dendrites) != 1 else ''}"
                f"{ghost_note} · {current.value}"
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
