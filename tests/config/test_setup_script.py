from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _python3_runs() -> bool:
    """setup.sh shells out to `python3`; on Windows this is often a non-functional
    Microsoft Store stub. Only run these shell-integration tests when a real
    interpreter answers to `python3`."""
    python3 = shutil.which("python3")
    if python3 is None:
        return False
    try:
        result = subprocess.run(
            [python3, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except OSError:
        return False
    return result.returncode == 0 and "Python" in (result.stdout + result.stderr)


def _symlinks_supported() -> bool:
    """Creating a directory symlink needs elevated privileges on Windows
    (WinError 1314); setup.sh needs a real `src` next to it via symlink."""
    probe = Path(REPO_ROOT) / ".pytest-symlink-probe"
    target = Path(REPO_ROOT) / "src"
    try:
        if probe.exists() or probe.is_symlink():
            probe.unlink()
        os.symlink(target, probe)
    except OSError:
        return False
    finally:
        try:
            if probe.is_symlink():
                probe.unlink()
        except OSError:
            pass
    return True


pytestmark = [
    pytest.mark.skipif(
        shutil.which("bash") is None,
        reason="setup.sh is a bash script; bash is not available on PATH",
    ),
    pytest.mark.skipif(
        not _python3_runs(),
        reason="setup.sh invokes `python3`; no working python3 interpreter on PATH",
    ),
    pytest.mark.skipif(
        not _symlinks_supported(),
        reason="setup.sh needs a `src` symlink; symlinks require elevated privileges here",
    ),
]


def test_setup_defaults_to_recommended_hybrid_local_mode_on_mac(tmp_path: Path) -> None:
    result, workspace, log_path = _run_setup(
        tmp_path,
        extra_env={"OSTYPE": "darwin23"},
        sysctl_bytes=34 * 1024 * 1024 * 1024,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    env_payload = (workspace / ".env.local").read_text(encoding="utf-8")
    log_output = log_path.read_text(encoding="utf-8")

    assert "AXON_RUNTIME_MODE=hybrid-local" in env_payload
    assert "docker compose --profile cpu up -d" in log_output
    assert "ollama pull phi3:mini" in log_output
    assert "ollama pull gemma4:e4b" in log_output
    assert "ollama pull gemma4:26b" not in log_output


def test_setup_honors_minimal_mode_and_skips_local_bootstrap(tmp_path: Path) -> None:
    result, workspace, log_path = _run_setup(
        tmp_path,
        extra_env={
            "OSTYPE": "darwin23",
            "AXON_RUNTIME_MODE": "minimal",
        },
        sysctl_bytes=34 * 1024 * 1024 * 1024,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    env_payload = (workspace / ".env.local").read_text(encoding="utf-8")
    log_output = log_path.read_text(encoding="utf-8")

    assert "AXON_RUNTIME_MODE=minimal" in env_payload
    assert "docker compose" not in log_output
    assert "ollama pull" not in log_output
    assert "curl " not in log_output


def test_setup_remote_infra_mode_requires_remote_host(tmp_path: Path) -> None:
    result, workspace, _ = _run_setup(
        tmp_path,
        extra_env={
            "OSTYPE": "darwin23",
            "AXON_RUNTIME_MODE": "remote-infra",
        },
        sysctl_bytes=34 * 1024 * 1024 * 1024,
    )

    assert result.returncode != 0
    assert "modo remote-infra exige AXON_INFRA_HOST" in result.stdout
    env_payload = (workspace / ".env.local").read_text(encoding="utf-8")
    assert "AXON_RUNTIME_MODE=remote-infra" in env_payload


def test_setup_uses_remote_infra_when_host_is_configured(tmp_path: Path) -> None:
    result, workspace, log_path = _run_setup(
        tmp_path,
        extra_env={
            "OSTYPE": "darwin23",
            "AXON_INFRA_HOST": "desktop.local",
        },
        sysctl_bytes=34 * 1024 * 1024 * 1024,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    env_payload = (workspace / ".env.local").read_text(encoding="utf-8")
    log_output = log_path.read_text(encoding="utf-8")

    assert "AXON_RUNTIME_MODE=remote-infra" in env_payload
    assert "docker compose" not in log_output
    assert "ollama pull" not in log_output
    assert "curl -sf http://desktop.local:6333/collections" in log_output
    assert "curl -sf http://desktop.local:11434/api/tags" in log_output


def test_setup_full_local_without_nvidia_keeps_small_models_only(tmp_path: Path) -> None:
    result, workspace, log_path = _run_setup(
        tmp_path,
        extra_env={
            "OSTYPE": "linux-gnu",
            "AXON_RUNTIME_MODE": "full-local",
        },
        sysctl_bytes=34 * 1024 * 1024 * 1024,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    env_payload = (workspace / ".env.local").read_text(encoding="utf-8")
    log_output = log_path.read_text(encoding="utf-8")

    assert "AXON_RUNTIME_MODE=full-local" in env_payload
    assert "docker compose --profile cpu up -d" in log_output
    assert "ollama pull phi3:mini" in log_output
    assert "ollama pull gemma4:e4b" in log_output
    assert "ollama pull gemma4:26b" not in log_output


def _run_setup(
    tmp_path: Path,
    *,
    extra_env: dict[str, str],
    sysctl_bytes: int,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copy2(REPO_ROOT / "setup.sh", workspace / "setup.sh")
    shutil.copy2(REPO_ROOT / ".env.example", workspace / ".env.example")
    os.symlink(REPO_ROOT / "src", workspace / "src")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "commands.log"
    log_path.write_text("", encoding="utf-8")

    _write_executable(
        fake_bin / "docker",
        "#!/usr/bin/env bash\nprintf '%s\\n' \"docker $*\" >> \"$TEST_LOG\"\n",
    )
    _write_executable(
        fake_bin / "ollama",
        "#!/usr/bin/env bash\nprintf '%s\\n' \"ollama $*\" >> \"$TEST_LOG\"\n",
    )
    _write_executable(
        fake_bin / "curl",
        "#!/usr/bin/env bash\nprintf '%s\\n' \"curl $*\" >> \"$TEST_LOG\"\n",
    )
    _write_executable(fake_bin / "nvidia-smi", "#!/usr/bin/env bash\nexit 1\n")
    _write_executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "sysctl",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [[ "$1" == "-n" && "$2" == "hw.memsize" ]]; then
                printf '%s\\n' "{sysctl_bytes}"
                exit 0
            fi
            if [[ "$1" == "hw.memsize" ]]; then
                printf 'hw.memsize: {sysctl_bytes}\\n'
                exit 0
            fi
            exit 1
            """
        ),
    )

    env = os.environ.copy()
    env.pop("AXON_INFRA_HOST", None)
    env.pop("AXON_DESKTOP_HOST", None)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TEST_LOG": str(log_path),
            "AXON_ENGINE": str(workspace),
            "AXON_VAULT": str(tmp_path / "vault"),
        }
    )
    env.update(extra_env)
    Path(env["HOME"]).mkdir()
    Path(env["AXON_VAULT"]).mkdir()

    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, workspace, log_path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
