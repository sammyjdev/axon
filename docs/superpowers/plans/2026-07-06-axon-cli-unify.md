# Unify axon CLI (retire pb) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `axon` (the packaged entry point at `src/axon/__main__.py`) the single, complete CLI by re-registering every still-relevant command currently only reachable through the orphaned `pb` binary, then delete that stray binary and update the docs that tell users to run `pb ...`.

**Architecture:** `axon.cli.pb` keeps being the module that *implements* the migrated commands and sub-apps (same pattern T6.3 already established for `adr`/`graph`/`profile`/`session`/`scan`/`search`/`rtk*`/`run`/`git`) — no reimplementation, just import the existing Typer objects/functions into `axon.__main__` and register them. Two commands collide by name with different behavior: `doctor` (pb.py's dec-111/112/113/114 diagnostic wins; axon's RTK/caveman presence checks get folded into it as an extra report section) and `init` (axon's repo-bootstrap `init` stays; pb.py's env-scaffold `init` is re-registered as `axon bootstrap`). The permanently-cut T6.3 commands (`ask`, `index`, `watch`, `til`, `deep`, `expand`, `career`, `cost`) stay cut — this plan deletes their source from `pb.py` instead of leaving them as dead code, since ponytail/deletion-over-addition applies once a cut is final. Two repo scripts depend on cut commands and get their dependency documented as removed, not silently left broken.

**Tech Stack:** Python 3.11+, Typer ≥0.12, pytest + `typer.testing.CliRunner`.

## Global Constraints

- No behavior change to any already-migrated command (`adr`, `graph`, `profile`, `session`, `scan`, `search`, `rtk*`, `run`, `git`, `install-hooks`, `init`, `familiar`, `serve`, `serve-http`, `health`, `gain`, `status`, `export`, `ingest-vault`).
- `axon.cli.pb` is not deleted as a module — it keeps `_get_db_path`, shared helpers, and every surviving command's implementation. Only its 8 permanently-cut command functions (and code that exists solely to support them) are deleted from it.
- Every new top-level command name added to `axon` must not collide with an existing one. Verify with `grep -n '@app.command\|add_typer' src/axon/__main__.py` before each task.
- `pb` stops existing as a runnable command by the end of this plan — no script, no alias, no mention in living docs as something to run.
- Portuguese docstrings/help text already in `pb.py` are preserved verbatim when re-registered (no translation drive-by).

---

## File Structure

- **Modify** `src/axon/__main__.py` — add `bootstrap`, `setup`, `configure`, `note`, `session-save`, `index-dev` top-level commands and `hooks`, `pending`, `portability` sub-apps; replace the bespoke `doctor` with pb.py's, folding in the RTK/caveman section.
- **Modify** `src/axon/cli/pb.py` — delete `ask`, `index`, `watch`, `til_*`, `deep_*`, `expand_*`, `career_*`, `cost_*` command functions and their now-unused `*_app` Typer objects (`career_app`, `cost_app`, `til_app`, `deep_app`, `expand_app`); add the RTK/caveman presence section to `doctor`.
- **Modify** `tests/cli/test_axon_cli.py` — replace `test_doctor_runs_and_reports_presence` with an equivalent that exercises the merged doctor via `axon`; add registration tests for the 9 newly-surfaced commands/sub-apps.
- **Modify** `pyproject.toml` — no change expected (already `axon = "axon.__main__:app"`), but Task 12 verifies this explicitly as a regression guard.
- **Modify** `scripts/collect_metrics_mac.sh`, `scripts/install_vault_hook.sh` — remove the `pb ask` / `pb cost compression` / `pb til --promote-today` calls (dead: those commands no longer exist anywhere).
- **Modify** (repo docs) `README.md`, `docs/PROJECT_OVERVIEW.md`, `docs/USAGE_GUIDE.md`, `docs/ADR.md`, `docs/MIGRATION.md` — replace `pb ...` examples with the `axon` equivalents.
- **Create** `docs/decisions/dec-125-retire-pb-entry-point.md` — records this change (historical dec-100/111-114 files that quote `pb doctor` are left as-is; they are point-in-time records, not living docs).
- **Untouched** `~/.claude/AXON.md` — that file lives outside this repo (global dotfile); flagged as a manual follow-up in the final task, not part of this plan's diff.

### Command inventory being migrated (verified by reading `src/axon/cli/pb.py` line numbers)

| pb.py source | New `axon` surface | Notes |
| --- | --- | --- |
| `doctor` (`pb.py:729`) | `axon doctor` (replaces `__main__.py:275` version) | dec-111–114 checks win; RTK/caveman section folded in |
| `init` (`pb.py:844`, engine/vault scaffold) | `axon bootstrap` | renamed — collides with axon's own `init` (repo bootstrap) |
| `configure` (`pb.py:1040`) | `axon configure` | direct re-registration |
| `note` (`pb.py:1313`) | `axon note` | direct re-registration |
| `session_save` (`pb.py:1322`, already dual-decorated `@session_app.command("save")`) | `axon session-save` (top-level alias) | `axon session save` already works today via the shared `session_app`; only the top-level alias is missing |
| `index_dev` (`pb.py:2491`) | `axon index-dev` | direct re-registration |
| `hooks_app` (`pb.py:36`, commands at `1552`/`1652`) | `axon hooks install` / `axon hooks status` | distinct from existing `axon install-hooks`; both survive |
| `pending_app` (`pb.py:35`, commands at `1876`/`1896`) | `axon pending drain` / `axon pending recover` | direct re-registration |
| `portability_app` (`pb.py:34`, commands at `2654`/`2666`) | `axon portability export` / `axon portability import` | direct re-registration |
| `setup` (`pb.py:2813`) | `axon setup` | direct re-registration (distinct from the renamed `bootstrap`) |
| `ask` (`pb.py:413`) | — deleted | cut, stays cut |
| `index` (`pb.py:2410`) | — deleted | cut, stays cut |
| `watch` (`pb.py:2585`) | — deleted | cut, stays cut |
| `til_*` (`pb.py:2026-2113`, `2250`) | — deleted | cut, stays cut |
| `deep_*` (`pb.py:2276-2302`) | — deleted | cut, stays cut |
| `expand_*` (`pb.py:2324-2389`) | — deleted | cut, stays cut |
| `career_*` (`pb.py:1931-1965`) | — deleted | cut, stays cut |
| `cost_*` (`pb.py:1966-2025`) | — deleted | cut, stays cut |

---

## Task 1: Merge `doctor` — pb.py wins, fold in axon's RTK/caveman section

**Files:**
- Modify: `src/axon/cli/pb.py:729-841` (the `doctor` function body)
- Modify: `src/axon/__main__.py:275-406` (delete the bespoke `doctor`, import pb's instead)
- Test: `tests/cli/test_axon_cli.py`

**Interfaces:**
- Consumes: `axon.context.rtk.rtk_binary_path`, `axon.router.compressor.caveman_compress`, `axon.observability.compression_telemetry.CompressionTelemetryStore`, `axon.cli.pb._get_db_path`, `axon.store.session_store.SessionStore` (all already imported somewhere in one of the two files today).
- Produces: a single `doctor(stale_days: int = 7, apply: bool = False, ci: bool = False)` function in `pb.py`, registered as `axon`'s only `doctor` command.

- [ ] **Step 1: Write the failing test**

Replace `test_doctor_runs_and_reports_presence` in `tests/cli/test_axon_cli.py` with:

```python
def test_doctor_runs_and_reports_presence(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.cli.pb._get_db_path", lambda: tmp_path / "axon.db")
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (0, 1, 2)
    assert "AXON doctor" in result.stdout
    assert "capture & adr checks" in result.stdout
    assert "## Presence" in result.stdout
    assert "## Liveness" in result.stdout
    assert "axon: ok" in result.stdout
    assert "caveman engine: ok" in result.stdout


def test_doctor_supports_ci_mode(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.cli.pb._get_db_path", lambda: tmp_path / "axon.db")
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    result = runner.invoke(app, ["doctor", "--ci"])
    assert result.exit_code == 0
    import json
    payload = json.loads(result.stdout)
    assert payload["version"] == "1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k test_doctor"`
Expected: FAIL — current `axon.__main__.doctor` has no `## Presence`/`capture & adr checks` combined output and no `--ci`/`--apply` options, so at least one assertion mismatches.

- [ ] **Step 3: Write minimal implementation**

In `src/axon/cli/pb.py`, extend the existing `doctor` function (`pb.py:729`) to append the RTK/caveman presence+liveness section that currently lives in `axon.__main__`. Insert this block right after the existing `typer.echo(human_format(results))` line (`pb.py:834`), before the `severity = max_severity(results)` line:

```python
    # RTK/caveman presence + liveness (merged from the former axon.__main__ doctor)
    import subprocess
    import sys
    from datetime import UTC, datetime, timedelta
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(days=stale_days)

    def fmt_age(ts: datetime) -> str:
        delta = now - ts
        if delta.days >= 1:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        return f"{hours}h ago" if hours else "just now"

    presence_lines: list[str] = ["", "## Presence"]
    try:
        v = _pkg_version("axon-mcp")
    except PackageNotFoundError:
        v = "unknown"
    presence_lines.append(f"- axon: ok ({v})")

    from axon.context.rtk import rtk_binary_path

    rtk_path = rtk_binary_path()
    if rtk_path:
        try:
            rtk_v = subprocess.check_output([rtk_path, "--version"], text=True, timeout=3).strip()
        except Exception:
            rtk_v = "unknown"
        presence_lines.append(f"- rtkx: ok ({rtk_v}) [{rtk_path}]")
    else:
        presence_lines.append("- rtkx: not installed (run `axon rtk-install`)")

    try:
        from axon.router.compressor import caveman_compress  # noqa: F401

        presence_lines.append("- caveman engine: ok (axon.router.compressor)")
    except Exception as exc:
        presence_lines.append(f"- caveman engine: error ({exc})")

    presence_lines += ["", "## Liveness"]

    async def _latest_decision_ts():
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            ts = await store.latest_decision_ts()
            return datetime.fromisoformat(ts) if ts is not None else None
        finally:
            await store.close()

    try:
        latest_dec_ts = asyncio.run(_latest_decision_ts())
        if latest_dec_ts is None:
            presence_lines.append("- axon captures: none yet (commit something in an axon-init'd repo)")
        else:
            if latest_dec_ts.tzinfo is None:
                latest_dec_ts = latest_dec_ts.replace(tzinfo=UTC)
            tag = "stale" if latest_dec_ts < stale_cutoff else "ok"
            presence_lines.append(f"- axon captures: {tag} (last {fmt_age(latest_dec_ts)})")
    except Exception as exc:
        presence_lines.append(f"- axon captures: error ({exc})")

    try:
        from axon.observability.compression_telemetry import CompressionTelemetryStore

        tstore = CompressionTelemetryStore()
        records = tstore.load_all()
        if not records:
            presence_lines.append("- compression telemetry: none yet")
        else:
            latest = records[-1]
            latest_ts = datetime.fromisoformat(latest.ts)
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.replace(tzinfo=UTC)
            tag = "stale" if latest_ts < stale_cutoff else "ok"
            presence_lines.append(
                f"- compression telemetry: {tag} ({len(records)} records, last {fmt_age(latest_ts)})"
            )
            caveman_recent = [r for r in records[-50:] if r.engine.startswith("caveman/")]
            if caveman_recent:
                presence_lines.append(
                    f"- caveman engine activity: ok ({len(caveman_recent)} of last 50 records)"
                )
            else:
                presence_lines.append(
                    "- caveman engine activity: not seen in last 50 records (compression may be falling back)"
                )
    except Exception as exc:
        presence_lines.append(f"- compression telemetry: error ({exc})")

    if sys.platform == "darwin":
        share_root = Path.home() / "Library" / "Application Support"
    else:
        share_root = Path.home() / ".local" / "share"
    rtk_db = share_root / "rtkx" / "history.db"
    for name in ("rtkx", "rtk"):
        candidate = share_root / name / "history.db"
        if candidate.exists():
            rtk_db = candidate
            break
    if rtk_db.exists():
        rtk_ts = datetime.fromtimestamp(rtk_db.stat().st_mtime, tz=UTC)
        tag = "stale" if rtk_ts < stale_cutoff else "ok"
        presence_lines.append(f"- rtkx activity: {tag} (history.db touched {fmt_age(rtk_ts)})")
    else:
        presence_lines.append(f"- rtkx activity: not found ({rtk_db})")

    typer.echo("\n".join(presence_lines))
```

Also add a `stale_days` option to the `doctor` signature at `pb.py:729` (it currently only has `apply`/`ci`):

```python
def doctor(
    stale_days: Annotated[
        int, typer.Option("--stale-days", help="Threshold (days) after which an activity is reported as stale.")
    ] = 7,
    apply: Annotated[
        bool, typer.Option("--apply", help="Interactive: prompt to apply suggested fixes. Requires TTY.")
    ] = False,
    ci: Annotated[
        bool, typer.Option("--ci", help="JSON output to stdout, exit 0 always (for CI pipelines).")
    ] = False,
) -> None:
```

In `src/axon/__main__.py`, delete the entire bespoke `doctor` function (lines `275-406`), and add `doctor` to the existing `from axon.cli.pb import (...)` block near the bottom of the file (`__main__.py:569-583`):

```python
from axon.cli.pb import (  # noqa: E402
    adr_app,
    doctor,
    git_proxy,
    graph_app,
    profile_app,
    rtk,
    rtk_init,
    rtk_install_cmd,
    rtk_proxy,
    rtk_status,
    run_proxy,
    scan,
    search,
    session_app,
)
```

and register it alongside the other standalone commands:

```python
app.command("doctor")(doctor)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py tests/doctor/test_cli_doctor.py -q"`
Expected: PASS (all doctor tests green — `tests/doctor/test_cli_doctor.py` imports `axon.cli.pb.app` directly and is unaffected by the `__main__.py` change).

- [ ] **Step 5: Commit**

```bash
git add src/axon/cli/pb.py src/axon/__main__.py tests/cli/test_axon_cli.py
git commit -m "feat: merge axon doctor into pb.py's dec-111-114 diagnostic"
```

---

## Task 2: `axon bootstrap` (renamed from pb.py's `init`)

**Files:**
- Modify: `src/axon/__main__.py`
- Test: `tests/cli/test_axon_cli.py`

**Interfaces:**
- Consumes: `axon.cli.pb.init` (`pb.py:844`, unchanged signature: `engine: str, vault: str, mode: str = "full-local", force: bool = False`).
- Produces: `axon bootstrap --engine ... --vault ...`.

- [ ] **Step 1: Write the failing test**

```python
def test_bootstrap_scaffolds_env_and_config(tmp_path):
    engine_dir = tmp_path / "engine"
    vault_dir = tmp_path / "vault"
    result = runner.invoke(
        app, ["bootstrap", "--engine", str(engine_dir), "--vault", str(vault_dir)]
    )
    assert result.exit_code == 0
    assert (engine_dir / ".env.local").exists()
    assert (engine_dir / "axon.toml").exists()
    assert "Scaffold criado" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k test_bootstrap"`
Expected: FAIL — `No such command 'bootstrap'`.

- [ ] **Step 3: Write minimal implementation**

Add `init` (aliased on import to avoid shadowing `__main__`'s own `init`) to the `from axon.cli.pb import (...)` block:

```python
from axon.cli.pb import (  # noqa: E402
    adr_app,
    doctor,
    git_proxy,
    graph_app,
    profile_app,
    rtk,
    rtk_init,
    rtk_install_cmd,
    rtk_proxy,
    rtk_status,
    run_proxy,
    scan,
    search,
    session_app,
)
from axon.cli.pb import init as pb_bootstrap  # noqa: E402
```

Register it:

```python
app.command("bootstrap")(pb_bootstrap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k test_bootstrap"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/axon/__main__.py tests/cli/test_axon_cli.py
git commit -m "feat: register pb.py's env-scaffold init as axon bootstrap"
```

---

## Task 3: `axon setup`, `axon configure`, `axon index-dev`

**Files:**
- Modify: `src/axon/__main__.py`
- Test: `tests/cli/test_axon_cli.py`

**Interfaces:**
- Consumes: `axon.cli.pb.setup` (`pb.py:2813`, no args), `axon.cli.pb.configure` (`pb.py:1040`), `axon.cli.pb.index_dev` (`pb.py:2491`, registered under CLI name `"index-dev"` in pb.py already).

- [ ] **Step 1: Write the failing test**

```python
def test_setup_configure_index_dev_registered():
    names = _registered_command_names()
    for name in ("setup", "configure", "index-dev"):
        assert name in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k test_setup_configure_index_dev"`
Expected: FAIL — none of the three names registered yet.

- [ ] **Step 3: Write minimal implementation**

Add to the `from axon.cli.pb import (...)` block:

```python
    configure,
    index_dev,
    setup,
```

(alphabetical, matching the existing style) and register:

```python
app.command("setup")(setup)
app.command("configure")(configure)
app.command("index-dev")(index_dev)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k test_setup_configure_index_dev"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/axon/__main__.py tests/cli/test_axon_cli.py
git commit -m "feat: register axon setup/configure/index-dev"
```

---

## Task 4: `axon note` + `axon session-save`

**Files:**
- Modify: `src/axon/__main__.py`
- Test: `tests/cli/test_axon_cli.py`

**Interfaces:**
- Consumes: `axon.cli.pb.note` (`pb.py:1313`), `axon.cli.pb.session_save` (`pb.py:1322`).

- [ ] **Step 1: Write the failing test**

```python
def test_note_and_session_save_registered():
    names = _registered_command_names()
    for name in ("note", "session-save"):
        assert name in names


def test_session_save_subcommand_already_shared():
    result = runner.invoke(app, ["session", "save", "--help"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k 'note_and_session_save'"`
Expected: FAIL — `note` and `session-save` absent from `--help` (the `session save` sub-command test is expected to already PASS, since `session_app` is shared — confirms no regression).

- [ ] **Step 3: Write minimal implementation**

Add to the `from axon.cli.pb import (...)` block:

```python
    note,
    session_save,
```

Register:

```python
app.command("note")(note)
app.command("session-save")(session_save)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k 'note_and_session_save or session_save_subcommand'"`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/axon/__main__.py tests/cli/test_axon_cli.py
git commit -m "feat: register axon note and session-save"
```

---

## Task 5: `axon hooks`, `axon pending`, `axon portability` sub-apps

**Files:**
- Modify: `src/axon/__main__.py`
- Test: `tests/cli/test_axon_cli.py`

**Interfaces:**
- Consumes: `axon.cli.pb.hooks_app`, `axon.cli.pb.pending_app`, `axon.cli.pb.portability_app` (all pre-existing Typer objects, `pb.py:34-36`).

- [ ] **Step 1: Write the failing test**

```python
def test_hooks_pending_portability_subapps_registered():
    names = _registered_command_names()
    for name in ("hooks", "pending", "portability"):
        assert name in names


def test_hooks_subapp_is_invocable():
    result = runner.invoke(app, ["hooks", "--help"])
    assert result.exit_code == 0
    assert "install" in result.stdout


def test_pending_subapp_is_invocable():
    result = runner.invoke(app, ["pending", "--help"])
    assert result.exit_code == 0
    assert "drain" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k 'hooks_pending_portability or hooks_subapp or pending_subapp'"`
Expected: FAIL — the three sub-apps are absent.

- [ ] **Step 3: Write minimal implementation**

Add to the `from axon.cli.pb import (...)` block:

```python
    hooks_app,
    pending_app,
    portability_app,
```

Register alongside the existing `app.add_typer(...)` calls:

```python
app.add_typer(hooks_app, name="hooks")
app.add_typer(pending_app, name="pending")
app.add_typer(portability_app, name="portability")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k 'hooks_pending_portability or hooks_subapp or pending_subapp'"`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/axon/__main__.py tests/cli/test_axon_cli.py
git commit -m "feat: register axon hooks/pending/portability sub-apps"
```

---

## Task 6: Delete the permanently-cut commands from `pb.py`

**Files:**
- Modify: `src/axon/cli/pb.py`
- Test: `tests/cli/test_axon_cli.py` (no change — `test_cut_commands_absent` already covers this; re-verify it still passes)

Delete, in `src/axon/cli/pb.py`:
- `ask` function and its `@app.command()` decorator (`pb.py:413-575`, everything between `def ask(` and the next `@app.command()` for `rtk`).
- `index` function (`pb.py:2410-2490`, up to `index_dev`'s decorator).
- `watch` function (`pb.py:2585-2653`, up to `portability_export`'s decorator).
- `career_app` Typer object (`pb.py:27`) and its three commands `career_metrics`/`career_brief`/`career_interview` (`pb.py:1931-1965`), plus `app.add_typer(career_app, name="career")` (`pb.py:40`).
- `cost_app` Typer object (`pb.py:28`) and its commands `cost_root`/`cost_today`/`cost_week`/`_show_cost`/`cost_compression` (`pb.py:1966-2025`), plus `app.add_typer(cost_app, name="cost")` (`pb.py:41`).
- `til_app` Typer object (`pb.py:29`) and `til_capture`/`_capture_til`/`_list_til_pending`/`_do_promote_today`/`_reindex_howtos`/`til_to_howto` (`pb.py:2026-2113`, `2250-2275`), plus `app.add_typer(til_app, name="til")` (`pb.py:42`).
- `deep_app` Typer object (`pb.py:30`) and `deep_suggest`/`deep_list` (`pb.py:2276-2323`), plus `app.add_typer(deep_app, name="deep")` (`pb.py:45`... verify exact line via `grep -n add_typer`).
- `expand_app` Typer object (`pb.py:31`) and `expand_run`/`expand_review`/`expand_approve`/`expand_reject`/`_handle_expand_expected_error` (`pb.py:195-202`, `2324-2409`), plus `app.add_typer(expand_app, name="expand")`.

Before deleting each block, run `rtk proxy "grep -n 'def ask\|def index\b\|def watch\b\|career_app\|cost_app\|til_app\|deep_app\|expand_app' src/axon/cli/pb.py"` to get current line numbers — line numbers drift after each deletion, so delete **one command group at a time, bottom of file first**, re-grepping between deletions.

- [ ] **Step 1: Run the existing cut-list test to confirm current baseline**

Run: `rtk proxy "python3 -m pytest tests/cli/test_axon_cli.py -q -k test_cut_commands_absent"`
Expected: PASS (already passing before this task — this task deletes *source*, not registration, so this test's status doesn't change)

- [ ] **Step 2: Delete the eight command groups from `src/axon/cli/pb.py`** (bottom-to-top order: `expand_*`, `watch`, `index`, `deep_*`, `til_*`, `cost_*`, `career_*`, `ask`)

- [ ] **Step 3: Run pb.py's own test suite (if any) plus the full CLI test files**

Run: `rtk proxy "python3 -m pytest tests/cli/ tests/doctor/ -q"`
Expected: PASS — no test references the deleted functions (`test_cut_commands_absent` only checks they're absent from `axon`'s registered names, which was already true).

Run: `rtk proxy "python3 -c 'import axon.cli.pb'"`
Expected: no `ImportError` / `NameError` (catches any leftover reference to a deleted `*_app` object or helper).

- [ ] **Step 4: Run ruff to catch unused imports left behind by the deletion**

Run: `rtk ruff check src/axon/cli/pb.py`
Expected: no errors (fix any `F401 unused import` surfaced by the deletion).

- [ ] **Step 5: Commit**

```bash
git add src/axon/cli/pb.py
git commit -m "chore: delete permanently-cut CLI commands (ask/index/watch/til/deep/expand/career/cost)"
```

---

## Task 7: Retire the stray `pb` binary

**Files:** none in the repo (this is machine-state cleanup, not a code change) — verify `pyproject.toml` as a regression guard.

- [ ] **Step 1: Confirm `pyproject.toml` never declared `pb`**

Run: `rtk proxy "grep -n '^pb ' pyproject.toml"`
Expected: no output (there is no `pb` entry to remove — it was never in `[project.scripts]`; the binary was a stray file in the pipx venv's `bin/`, already deleted from this machine during the earlier `ModuleNotFoundError: prometheus` fix session).

- [ ] **Step 2: Reinstall the editable `axon` entry point so `bin/` matches `entry_points.txt` exactly**

Run: `pipx reinstall axon-mcp`
Expected: completes without error; regenerates `~/.local/pipx/venvs/axon-mcp/bin/` from the current `entry_points.txt` (only `axon`).

- [ ] **Step 3: Smoke-test**

Run: `type -a pb`
Expected: `pb not found` (zsh) — confirms no `pb` binary remains anywhere on `PATH`.

Run: `axon --help`
Expected: shows `bootstrap`, `setup`, `configure`, `note`, `session-save`, `index-dev`, `hooks`, `pending`, `portability` alongside the pre-existing commands.

- [ ] **Step 4: No commit** (nothing in the repo changed in this task)

---

## Task 8: Update living docs and dependent scripts

**Files:**
- Modify: `README.md`, `docs/PROJECT_OVERVIEW.md`, `docs/USAGE_GUIDE.md`, `docs/ADR.md`, `docs/MIGRATION.md`
- Modify: `scripts/collect_metrics_mac.sh`, `scripts/install_vault_hook.sh`
- Create: `docs/decisions/dec-125-retire-pb-entry-point.md`

Historical decision docs (`dec-100`, `dec-110` through `dec-114`, `docs/CAPTURE_ROBUSTNESS.md`, `docs/ROADMAP.md`, and anything under `docs/superpowers/plans/`) are **not** rewritten — they are point-in-time records of decisions made when `pb` was the live command name. `docs/ADR.md` is the index; add a pointer there to the new dec, per its existing format (don't rewrite prior entries).

- [ ] **Step 1: Replace every `pb <cmd>` example with `axon <cmd>` in the five living docs**

Run: `rtk proxy "grep -rn 'pb doctor\|pb adr\|pb init\|pb session\|pb hooks\|pb pending\|pb configure\|pb setup\|pb note' README.md docs/PROJECT_OVERVIEW.md docs/USAGE_GUIDE.md docs/ADR.md docs/MIGRATION.md"` to enumerate every line, then edit each: `pb doctor` → `axon doctor`, `pb init --engine ... --vault ...` → `axon bootstrap --engine ... --vault ...` (the renamed command), everything else keeps its subcommand name unchanged (`pb adr review` → `axon adr review`, `pb hooks install` → `axon hooks install`, etc).

- [ ] **Step 2: Fix the two scripts that call permanently-cut commands**

In `scripts/collect_metrics_mac.sh`: delete the `pb ask latency` measurement block (lines referencing `QUERY`, the `pb ask` subprocess call, and the `== pb ask latency ==` echo) and the `pb cost compression` block (`command -v pb` check + `pb cost compression || true`) — both commands are gone for good. Leave a one-line comment noting why: `# pb ask / pb cost compression removed — those commands are permanently cut (see dec-125).`

In `scripts/install_vault_hook.sh`: delete the `pb til --promote-today` block (`command -v pb` check + the `pb til` call) — same reason, same comment.

- [ ] **Step 3: Write `docs/decisions/dec-125-retire-pb-entry-point.md`**

```markdown
# dec-125 — Retire the `pb` entry point, finish the T6.3 CLI unification

- Status: accepted
- Date: 2026-07-06

## Context

dec-100 planned `CLI entry point: pb → axon` but only renamed the package/env
vars/DB file. T6.3 (2026-05-22) built the new `axon` entry point and
re-registered a subset of `pb.py`'s commands, deliberately cutting
`ask`/`index`/`watch`/`til`/`deep`/`expand`/`career`/`cost`. Everything else in
`pb.py` kept working only because a stray `pb` script (never declared in
`pyproject.toml`'s `[project.scripts]`, a leftover from the pre-dec-100
`prometheus-engine` install) survived on disk. `pb.py` kept gaining real
features after T6.3 — `hooks` (dec-113), `pending` (dec-112), the dec-111/114
doctor checks — that were never ported to `axon`, so the officially packaged
CLI silently fell behind the one people actually ran.

## Decision

- `axon` becomes the single, complete CLI. Every still-relevant `pb.py`
  command is re-registered onto `axon.__main__:app`: `hooks`, `pending`,
  `portability`, `configure`, `note`, `session-save`, `index-dev`, `setup`.
- `doctor`: pb.py's dec-111–114 diagnostic (`--apply`/`--ci`, capture/adr/
  toolchain checks) wins over axon's simpler RTK/caveman presence check;
  the RTK/caveman section is folded into the winning `doctor` as an
  additional report section.
- `init`: axon's own `init` (install hooks + index a repo) is unchanged;
  pb.py's `init` (env/config scaffold for a fresh install) is renamed to
  `axon bootstrap` to avoid the name collision.
- The permanently-cut T6.3 commands stay cut. Their source is deleted from
  `pb.py` (not left as dead code).
- The stray `pb` binary is removed from the pipx venv; nothing on this or any
  future machine should register a `pb` script again, since `pyproject.toml`
  never declares one.

## Consequences

- `scripts/collect_metrics_mac.sh` and `scripts/install_vault_hook.sh` lose
  the metrics/automation they ran through the now-permanently-cut `ask`/
  `cost`/`til` commands.
- Historical decision docs (dec-100, dec-110–114) still say `pb doctor` /
  `pb init` — left as-is; they describe decisions made when those were the
  live command names.
- `~/.claude/AXON.md` (a global dotfile outside this repo) still documents
  `pb ...` commands and needs a manual follow-up edit — out of scope for this
  repo's diff.
```

- [ ] **Step 4: Add a pointer entry in `docs/ADR.md`**

Follow the file's existing per-entry format (check the last few entries for the exact bullet style before adding), pointing at `docs/decisions/dec-125-retire-pb-entry-point.md`.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/PROJECT_OVERVIEW.md docs/USAGE_GUIDE.md docs/ADR.md docs/MIGRATION.md docs/decisions/dec-125-retire-pb-entry-point.md scripts/collect_metrics_mac.sh scripts/install_vault_hook.sh
git commit -m "docs: replace pb examples with axon, record dec-125"
```

---

## Task 9: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `rtk proxy "python3 -m pytest tests/ -q"`
Expected: all tests pass.

- [ ] **Step 2: Lint**

Run: `rtk ruff check src/axon/__main__.py src/axon/cli/pb.py tests/cli/test_axon_cli.py`
Expected: no errors.

- [ ] **Step 3: Real-binary smoke test**

Run: `axon --help`
Expected: full command list including every name from the inventory table above; no `ask`/`index`/`watch`/`til`/`deep`/`expand`/`career`/`cost`.

Run: `axon doctor --ci | python3 -c "import json,sys; json.load(sys.stdin)"`
Expected: valid JSON, no error.

Run: `type -a pb`
Expected: `pb not found`.

- [ ] **Step 4: Commit** (only if Steps 1-3 required fixes; otherwise nothing to commit)

---

## Self-Review

**Spec coverage:**
- Fold pb.py's full current surface into `axon`, keeping cuts cut → Tasks 1-6.
- Resolve `doctor`/`init` collisions per the agreed exceptions → Tasks 1-2.
- Delete stray `pb` binary → Task 7.
- Update docs/scripts that reference `pb` → Task 8.
- Full-suite + lint + real-binary verification → Task 9.

**Placeholder scan:** every step has literal code or an exact command; the only prose-only steps are Task 6's deletion instructions (necessarily descriptive since they remove code rather than add it) and Task 8's doc-editing steps (mechanical find/replace, not novel logic).

**Type consistency:** `doctor(stale_days: int = 7, apply: bool = False, ci: bool = False)` is the same signature used in Task 1's test and Task 9's smoke test. `_registered_command_names()` (already defined in `tests/cli/test_axon_cli.py:197`) is reused unchanged across Tasks 2-5.

**Known follow-up (out of scope for this plan):** `~/.claude/AXON.md` is a global dotfile outside this repository and still documents `pb` commands — needs a separate manual edit, flagged in dec-125's Consequences section.
