from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_setup_defaults_to_recommended_hybrid_local_mode_on_mac(tmp_path: Path) -> None:
    result, workspace, log_path = _run_setup(
        tmp_path,
        extra_env={"OSTYPE": "darwin23"},
        sysctl_bytes=34 * 1024 * 1024 * 1024,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    env_payload = (workspace / ".env.local").read_text(encoding="utf-8")
    log_output = log_path.read_text(encoding="utf-8")

    assert "PROMETHEUS_RUNTIME_MODE=hybrid-local" in env_payload
    assert "docker compose --profile cpu up -d" in log_output
    assert "ollama pull phi3:mini" in log_output
    assert "ollama pull gemma4:e4b" in log_output
    assert "ollama pull gemma4:26b" not in log_output


def test_setup_honors_minimal_mode_and_skips_local_bootstrap(tmp_path: Path) -> None:
    result, workspace, log_path = _run_setup(
        tmp_path,
        extra_env={
            "OSTYPE": "darwin23",
            "PROMETHEUS_RUNTIME_MODE": "minimal",
        },
        sysctl_bytes=34 * 1024 * 1024 * 1024,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    env_payload = (workspace / ".env.local").read_text(encoding="utf-8")
    log_output = log_path.read_text(encoding="utf-8")

    assert "PROMETHEUS_RUNTIME_MODE=minimal" in env_payload
    assert "docker compose" not in log_output
    assert "ollama pull" not in log_output
    assert "curl " not in log_output


def test_setup_remote_infra_mode_requires_remote_host(tmp_path: Path) -> None:
    result, workspace, _ = _run_setup(
        tmp_path,
        extra_env={
            "OSTYPE": "darwin23",
            "PROMETHEUS_RUNTIME_MODE": "remote-infra",
        },
        sysctl_bytes=34 * 1024 * 1024 * 1024,
    )

    assert result.returncode != 0
    assert "modo remote-infra exige PROMETHEUS_INFRA_HOST" in result.stdout
    env_payload = (workspace / ".env.local").read_text(encoding="utf-8")
    assert "PROMETHEUS_RUNTIME_MODE=remote-infra" in env_payload


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
    env.pop("PROMETHEUS_INFRA_HOST", None)
    env.pop("PROMETHEUS_DESKTOP_HOST", None)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TEST_LOG": str(log_path),
            "PROMETHEUS_ENGINE": str(workspace),
            "PROMETHEUS_VAULT": str(tmp_path / "vault"),
        }
    )
    env.update(extra_env)
    Path(env["HOME"]).mkdir()
    Path(env["PROMETHEUS_VAULT"]).mkdir()

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
