"""Integration with the ``pre-commit`` framework (dec-113).

Generates the YAML entry block users need to add to
``.pre-commit-config.yaml`` so AXON's hooks run alongside the rest of
their pipeline. Idempotent: re-running produces the same block.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from axon.hooks.git_installer import HOOKS

_INSTALL_TIMEOUT_S = 120

_COMMIT_ID = "axon-post-commit"
_MERGE_ID = "axon-post-merge"


def _entry_scalar(event: str) -> str:
    """The hook's ``entry`` as a YAML double-quoted scalar.

    pre-commit runs ``shlex.split(entry)``, so the interpreter is shell-quoted;
    the backslash and quote escapes only matter to YAML (a Windows path).
    """
    command = f"{shlex.quote(sys.executable)} -m axon.hooks.git_event {event}"
    return '"' + command.replace("\\", "\\\\").replace('"', '\\"') + '"'


def axon_repo_entry() -> str:
    """The ``- repo: local`` block users paste into pre-commit config."""
    return (
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: axon-post-commit\n"
        "        name: AXON post-commit capture\n"
        "        entry: " + _entry_scalar("commit") + "\n"
        "        language: system\n"
        "        stages: [post-commit]\n"
        "        always_run: true\n"
        "        pass_filenames: false\n"
        "      - id: axon-post-merge\n"
        "        name: AXON post-merge capture pulled signal commits\n"
        "        entry: " + _entry_scalar("post-merge") + "\n"
        "        language: system\n"
        "        stages: [post-merge]\n"
        "        always_run: true\n"
        "        pass_filenames: false\n"
    )


def dry_run_message(config_path: Path) -> str:
    """Human-readable preview shown when ``--apply`` is not passed."""
    return (
        f"pre-commit framework detected at {config_path}\n"
        "AXON would add the following entry to its hooks list "
        "(use --apply to write):\n\n" + axon_repo_entry()
    )


def load_precommit_config(config_path: Path) -> dict:
    """The parsed config, which must be a mapping whose ``repos`` is a list."""
    try:
        data = yaml.safe_load(config_path.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(_refusal(config_path, "it is not valid YAML")) from exc
    if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
        raise ValueError(_refusal(config_path, "it has no `repos` list"))
    return data


def _refusal(config_path: Path, reason: str) -> str:
    return f"cannot add AXON entries to {config_path.name}: {reason}"


def local_hooks(data: object) -> list[dict]:
    """Every hook mapping under a ``repo: local`` item of a parsed config."""
    repos = data.get("repos") if isinstance(data, dict) else None
    return [
        hook
        for repo in (repos if isinstance(repos, list) else [])
        if isinstance(repo, dict) and repo.get("repo") == "local"
        for hook in (repo.get("hooks") if isinstance(repo.get("hooks"), list) else [])
        if isinstance(hook, dict)
    ]


def has_axon_entries(data: object) -> bool:
    return any(hook.get("id") == _COMMIT_ID for hook in local_hooks(data))


def _insert_entry(text: str, config_path: Path) -> str:
    """``text`` (LF endings) with AXON's item at the end of the ``repos`` list.

    Edits lines rather than re-serialising, so the user's comments and layout stay.
    """
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    for start, line in enumerate(lines):
        match = re.match(r"repos[ \t]*:(?:[ \t]+(.*))?$", line)
        if match:
            break
    else:
        raise ValueError(_refusal(config_path, "no top-level `repos:` line"))

    value, _, comment = (match.group(1) or "").partition("#")
    if value.strip() == "[]":
        lines[start] = "repos:" + (f"  #{comment}" if comment else "")
        end, indent = start + 1, 2
    elif not value.strip():
        # The list ends at the next top-level key or document marker.
        end = next(
            (
                i
                for i in range(start + 1, len(lines))
                if re.match(r"(---|\.\.\.)(\s|$)|[^\s#-]", lines[i])
            ),
            len(lines),
        )
        item = next(
            (m for m in (re.match(r"( *)-(\s|$)", ln) for ln in lines[start + 1 : end]) if m),
            None,
        )
        indent = len(item.group(1)) if item else 2
        # Blank lines and comments just above the next key belong to that key.
        while end > start + 1:
            previous = lines[end - 1].lstrip(" ")
            outdented = len(lines[end - 1]) - len(previous) <= indent
            if previous and not (previous.startswith("#") and outdented):
                break
            end -= 1
    else:
        raise ValueError(_refusal(config_path, "`repos` is not a block list or `[]`"))

    entry = [
        " " * indent + ln[2:] for ln in axon_repo_entry().rstrip("\n").split("\n")
    ]
    lines[end:end] = entry
    return "\n".join(lines) + "\n"


def merge_into(config_path: Path) -> bool:
    """Add AXON's entry to the config's ``repos`` list if it is not there.

    Returns True if the file was modified, False if it is missing or already holds
    the entry. Raises ``ValueError``, writing nothing, when the entry cannot be
    added without changing anything else in the config.

    An `axon-pre-push` entry an older AXON wrote is left in place on purpose:
    it sends the `push` event, which now does nothing, while editing the
    operator's YAML by text risks folding its fields into the hook above.
    """
    if not config_path.exists():
        return False
    old = load_precommit_config(config_path)
    if has_axon_entries(old):
        return False

    raw = config_path.read_bytes().decode("utf-8")
    bom = "\ufeff" if raw.startswith("\ufeff") else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    merged = _insert_entry(raw.removeprefix(bom).replace("\r\n", "\n"), config_path)

    try:
        new = yaml.safe_load(merged)
    except yaml.YAMLError as exc:
        raise ValueError(_refusal(config_path, "the result is not valid YAML")) from exc
    added = new["repos"][-1] if isinstance(new, dict) and new.get("repos") else None
    ids = {hook.get("id") for hook in local_hooks({"repos": [added]})}
    if not {_COMMIT_ID, _MERGE_ID} <= ids or new != {**old, "repos": [*old["repos"], added]}:
        raise ValueError(_refusal(config_path, "the result would change other settings"))

    config_path.write_bytes((bom + merged.replace("\n", newline)).encode("utf-8"))
    return True


def install_command(cfg_name: str) -> list[str]:
    """The `pre-commit` call, run with ``cwd=<repo>`` so the driver stays relative."""
    command = ["pre-commit", "install", "--config", cfg_name]
    for hook_type in HOOKS:
        command += ["--hook-type", hook_type]
    return command


def install_precommit_drivers(repo: Path, cfg_name: str) -> None:
    command = install_command(cfg_name)
    command[0] = shutil.which(command[0]) or command[0]
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=_INSTALL_TIMEOUT_S,
        check=False,
    )
    if done.returncode != 0:
        output = (done.stderr + done.stdout).strip()
        raise RuntimeError(f"pre-commit install failed (exit {done.returncode}): {output}")
