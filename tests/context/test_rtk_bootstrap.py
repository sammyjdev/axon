from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest

from axon.context import rtk_bootstrap as boot


def test_detect_target_windows() -> None:
    t = boot.detect_target("Windows", "AMD64")
    assert t.triple == "x86_64-pc-windows-msvc"
    assert t.archive_ext == "zip"
    assert t.binary_name == "rtkx.exe"


def test_detect_target_linux_x86() -> None:
    t = boot.detect_target("Linux", "x86_64")
    assert t.triple == "x86_64-unknown-linux-musl"
    assert t.archive_ext == "tar.gz"
    assert t.binary_name == "rtkx"


def test_detect_target_linux_arm() -> None:
    t = boot.detect_target("Linux", "aarch64")
    assert t.triple == "aarch64-unknown-linux-gnu"


def test_detect_target_macos_arm() -> None:
    t = boot.detect_target("Darwin", "arm64")
    assert t.triple == "aarch64-apple-darwin"
    assert t.archive_ext == "tar.gz"


def test_detect_target_macos_intel() -> None:
    t = boot.detect_target("Darwin", "x86_64")
    assert t.triple == "x86_64-apple-darwin"


def test_detect_target_unsupported_raises() -> None:
    with pytest.raises(boot.BootstrapError):
        boot.detect_target("Plan9", "pdp11")


def test_download_url_construction() -> None:
    t = boot.detect_target("Linux", "x86_64")
    url = boot.download_url("v0.42.2-rtkx.1", t, repo="sammyjdev/rtkx")
    assert url == (
        "https://github.com/sammyjdev/rtkx/releases/download/"
        "v0.42.2-rtkx.1/rtkx-x86_64-unknown-linux-musl.tar.gz"
    )


def _zip_bytes(member: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, content)
    return buf.getvalue()


def _targz_bytes(member: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _fake_download_with_checksum(archive: bytes, artifact: str):
    """Serve the archive plus a matching checksums.txt, keyed by URL suffix."""
    digest = hashlib.sha256(archive).hexdigest()

    def fake_download(url: str, dest: Path) -> None:
        if url.endswith("checksums.txt"):
            dest.write_text(f"{digest}  {artifact}\n")
        else:
            dest.write_bytes(archive)

    return fake_download


def test_checksums_url_construction() -> None:
    url = boot.checksums_url("v0.42.2-rtkx.1", repo="sammyjdev/rtkx")
    assert url == (
        "https://github.com/sammyjdev/rtkx/releases/download/"
        "v0.42.2-rtkx.1/checksums.txt"
    )


def test_bootstrap_extracts_zip(tmp_path) -> None:
    target = boot.detect_target("Windows", "AMD64")
    archive = _zip_bytes("rtkx.exe", b"BINARY")
    fake_download = _fake_download_with_checksum(archive, boot.artifact_name(target))

    out = boot.bootstrap_rtkx(
        "v1", dest_dir=tmp_path, target=target, download=fake_download
    )

    assert out == tmp_path / "rtkx.exe"
    assert out.read_bytes() == b"BINARY"


def test_bootstrap_extracts_targz_and_sets_exec(tmp_path) -> None:
    target = boot.detect_target("Linux", "x86_64")
    archive = _targz_bytes("rtkx", b"ELFISH")
    fake_download = _fake_download_with_checksum(archive, boot.artifact_name(target))

    out = boot.bootstrap_rtkx(
        "v1", dest_dir=tmp_path, target=target, download=fake_download
    )

    assert out == tmp_path / "rtkx"
    assert out.read_bytes() == b"ELFISH"
    if os.name != "nt":
        assert out.stat().st_mode & stat.S_IXUSR


def test_bootstrap_raises_when_binary_missing_in_archive(tmp_path) -> None:
    target = boot.detect_target("Linux", "x86_64")
    archive = _targz_bytes("something-else", b"x")
    fake_download = _fake_download_with_checksum(archive, boot.artifact_name(target))

    with pytest.raises(boot.BootstrapError):
        boot.bootstrap_rtkx("v1", dest_dir=tmp_path, target=target, download=fake_download)


def test_bootstrap_raises_on_checksum_mismatch(tmp_path) -> None:
    target = boot.detect_target("Linux", "x86_64")
    archive = _targz_bytes("rtkx", b"ELFISH")

    def fake_download(url: str, dest: Path) -> None:
        if url.endswith("checksums.txt"):
            dest.write_text(f"{'0' * 64}  {boot.artifact_name(target)}\n")
        else:
            dest.write_bytes(archive)

    with pytest.raises(boot.BootstrapError, match="[Cc]hecksum"):
        boot.bootstrap_rtkx("v1", dest_dir=tmp_path, target=target, download=fake_download)


def test_bootstrap_raises_when_checksum_entry_missing(tmp_path) -> None:
    target = boot.detect_target("Linux", "x86_64")
    archive = _targz_bytes("rtkx", b"ELFISH")

    def fake_download(url: str, dest: Path) -> None:
        if url.endswith("checksums.txt"):
            dest.write_text("deadbeef  some-other-artifact.tar.gz\n")
        else:
            dest.write_bytes(archive)

    with pytest.raises(boot.BootstrapError, match="[Cc]hecksum"):
        boot.bootstrap_rtkx("v1", dest_dir=tmp_path, target=target, download=fake_download)


def test_attestations_url_construction() -> None:
    url = boot.attestations_url("deadbeef" * 8, repo="sammyjdev/rtkx")
    assert url == (
        "https://api.github.com/repos/sammyjdev/rtkx/attestations/"
        f"sha256:{'deadbeef' * 8}"
    )


def test_bootstrap_succeeds_silently_when_attestation_found(tmp_path) -> None:
    target = boot.detect_target("Linux", "x86_64")
    archive = _targz_bytes("rtkx", b"ELFISH")
    fake_download = _fake_download_with_checksum(archive, boot.artifact_name(target))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = boot.bootstrap_rtkx(
            "v1",
            dest_dir=tmp_path,
            target=target,
            download=fake_download,
            fetch_attestations=lambda url: [{"bundle": "..."}],
        )

    assert out.read_bytes() == b"ELFISH"


def test_bootstrap_raises_when_no_attestation_found(tmp_path) -> None:
    target = boot.detect_target("Linux", "x86_64")
    archive = _targz_bytes("rtkx", b"ELFISH")
    fake_download = _fake_download_with_checksum(archive, boot.artifact_name(target))

    with pytest.raises(boot.BootstrapError, match="[Aa]ttestation"):
        boot.bootstrap_rtkx(
            "v1",
            dest_dir=tmp_path,
            target=target,
            download=fake_download,
            fetch_attestations=lambda url: [],
        )

    assert not (tmp_path / "rtkx").exists()


def test_bootstrap_warns_but_still_installs_when_attestation_fetch_fails(tmp_path) -> None:
    target = boot.detect_target("Linux", "x86_64")
    archive = _targz_bytes("rtkx", b"ELFISH")
    fake_download = _fake_download_with_checksum(archive, boot.artifact_name(target))

    def failing_fetch(url: str) -> list:
        raise OSError("network down")

    with pytest.warns(boot.AttestationWarning, match="[Aa]ttestation"):
        out = boot.bootstrap_rtkx(
            "v1",
            dest_dir=tmp_path,
            target=target,
            download=fake_download,
            fetch_attestations=failing_fetch,
        )

    assert out.read_bytes() == b"ELFISH"
