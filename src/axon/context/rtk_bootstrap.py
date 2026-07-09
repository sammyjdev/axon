"""Bootstrap the rtkx binary from the fork's GitHub releases.

`axon rtk-install` downloads the platform artifact and places it at
``~/.axon/bin/rtkx`` (see :func:`axon.context.rtk.rtk_binary_path`). The
download step is injectable so the extraction logic is testable offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RTKX_REPO = "sammyjdev/rtkx"
_USER_AGENT = "axon-rtk-bootstrap"


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class RtkxTarget:
    triple: str
    archive_ext: str  # "zip" | "tar.gz"
    binary_name: str  # "rtkx" | "rtkx.exe"


def _bin_dir() -> Path:
    return Path.home() / ".axon" / "bin"


def detect_target(system: str | None = None, machine: str | None = None) -> RtkxTarget:
    """Resolve the release target triple for the host (or explicit os/arch)."""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()

    is_x86 = machine in {"x86_64", "amd64", "x64"}
    is_arm = machine in {"aarch64", "arm64"}

    if system == "windows":
        if is_x86:
            return RtkxTarget("x86_64-pc-windows-msvc", "zip", "rtkx.exe")
    elif system == "linux":
        if is_x86:
            return RtkxTarget("x86_64-unknown-linux-musl", "tar.gz", "rtkx")
        if is_arm:
            return RtkxTarget("aarch64-unknown-linux-gnu", "tar.gz", "rtkx")
    elif system == "darwin":
        if is_x86:
            return RtkxTarget("x86_64-apple-darwin", "tar.gz", "rtkx")
        if is_arm:
            return RtkxTarget("aarch64-apple-darwin", "tar.gz", "rtkx")

    raise BootstrapError(f"Unsupported platform: {system}/{machine}")


def artifact_name(target: RtkxTarget) -> str:
    return f"rtkx-{target.triple}.{target.archive_ext}"


def download_url(tag: str, target: RtkxTarget, repo: str = RTKX_REPO) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{artifact_name(target)}"


def checksums_url(tag: str, repo: str = RTKX_REPO) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/checksums.txt"


def _http_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req) as resp, dest.open("wb") as fh:  # noqa: S310
        fh.write(resp.read())


def resolve_latest_tag(repo: str = RTKX_REPO, *, include_prerelease: bool = False) -> str:
    """Return the most recent release tag from the GitHub API."""
    if include_prerelease:
        url = f"https://api.github.com/repos/{repo}/releases"
    else:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            payload = json.loads(resp.read())
    except OSError as exc:
        raise BootstrapError(f"Failed to query releases for {repo}: {exc}") from exc

    if include_prerelease:
        if not payload:
            raise BootstrapError(f"No releases found for {repo}")
        return str(payload[0]["tag_name"])
    return str(payload["tag_name"])


def _extract_binary(archive: Path, target: RtkxTarget, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / target.binary_name

    if target.archive_ext == "zip":
        with zipfile.ZipFile(archive) as zf:
            member = _match_member(zf.namelist(), target.binary_name)
            data = zf.read(member)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            member = _match_member(tf.getnames(), target.binary_name)
            extracted = tf.extractfile(member)
            if extracted is None:
                raise BootstrapError(f"{member} is not a file in the archive")
            data = extracted.read()

    out.write_bytes(data)
    if os.name != "nt":
        out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return out


def _match_member(names: list[str], binary_name: str) -> str:
    for name in names:
        if Path(name).name == binary_name:
            return name
    raise BootstrapError(f"{binary_name} not found in release archive")


def _parse_checksums(text: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, name = parts
        checksums[name.strip().lstrip("*")] = digest.strip().lower()
    return checksums


def _verify_checksum(archive: Path, checksums_path: Path, artifact: str) -> None:
    checksums = _parse_checksums(checksums_path.read_text())
    expected = checksums.get(artifact)
    if not expected:
        raise BootstrapError(f"No checksum entry for {artifact} in checksums.txt")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise BootstrapError(
            f"Checksum mismatch for {artifact}: expected {expected}, got {actual}"
        )


def bootstrap_rtkx(
    tag: str,
    *,
    dest_dir: Path | None = None,
    target: RtkxTarget | None = None,
    repo: str = RTKX_REPO,
    download: Callable[[str, Path], None] = _http_download,
) -> Path:
    """Download and install the rtkx binary, returning its path.

    The archive's SHA-256 is verified against the release's `checksums.txt`
    before extraction (GHSA-r7wg-f7r2-8wf7); a mismatch raises BootstrapError
    without extracting or chmod'ing anything.
    """
    target = target or detect_target()
    dest_dir = dest_dir or _bin_dir()
    artifact = artifact_name(target)
    url = download_url(tag, target, repo=repo)

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / artifact
        checksums_path = Path(tmp) / "checksums.txt"
        try:
            download(url, archive)
            download(checksums_url(tag, repo=repo), checksums_path)
        except OSError as exc:
            raise BootstrapError(f"Failed to download release assets for {tag}: {exc}") from exc
        _verify_checksum(archive, checksums_path, artifact)
        return _extract_binary(archive, target, dest_dir)
