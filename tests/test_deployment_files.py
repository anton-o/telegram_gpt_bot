import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shell_scripts_have_valid_bash_syntax():
    scripts = ["deploy.sh", "deploy-remote.sh"]

    subprocess.run(
        ["bash", "-n", *(str(PROJECT_ROOT / script) for script in scripts)],
        check=True,
    )


def test_deployment_scripts_are_executable():
    for script in ("deploy.sh", "deploy-remote.sh"):
        assert os.access(PROJECT_ROOT / script, os.X_OK)


def test_deployment_scripts_expose_help_without_side_effects():
    for script in ("deploy.sh", "deploy-remote.sh"):
        result = subprocess.run(
            [str(PROJECT_ROOT / script), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "Usage:" in result.stdout


@pytest.mark.parametrize(
    ("contents", "expected_error"),
    [
        ("192.0.2.1\n/root/key\n", "invalid configuration line"),
        ("UNKNOWN=value\n", "unknown configuration key"),
        ("REMOTE_HOST=one\nREMOTE_HOST=two\n", "duplicate configuration key"),
    ],
)
def test_deployment_commands_reject_unsafe_config_before_network_access(
    tmp_path, contents, expected_error
):
    config = tmp_path / "deploy.cfg"
    config.write_text(contents)

    result = subprocess.run(
        [
            str(PROJECT_ROOT / "deploy.sh"),
            "--config",
            str(config),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert expected_error in result.stderr


def test_deployment_example_defines_expected_keys():
    config_path = PROJECT_ROOT / "deploy_config.cfg.EXAMPLE"
    keys = {
        line.split("=", 1)[0]
        for line in config_path.read_text().splitlines()
        if line and not line.startswith("#")
    }

    assert keys == {
        "REMOTE_HOST",
        "REMOTE_USER",
        "SSH_KEY_PATH",
        "REMOTE_APP_DIR",
        "SERVICE_NAME",
    }


def test_systemd_unit_uses_uv_environment_without_runtime_sync():
    unit = (PROJECT_ROOT / "deploy" / "tlggptbot.service").read_text()

    assert "WorkingDirectory=/root/python" in unit
    assert "ExecStart=/root/python/.venv/bin/python /root/python/main.py" in unit
    assert "ExecStartPre=" not in unit
    assert "uv sync" not in unit


def test_routine_deploy_is_staged_and_main_only():
    local_deploy = (PROJECT_ROOT / "deploy.sh").read_text()
    remote_deploy = (PROJECT_ROOT / "deploy-remote.sh").read_text()

    assert "origin/main" in local_deploy
    assert "working tree must be clean" in local_deploy
    assert "uv pin drift requires an explicit runtime upgrade" in remote_deploy
    assert 'NEW_APP="${APP_DIR}.new.${DEPLOYMENT_ID}"' in remote_deploy
    assert "rolling back" in remote_deploy
    assert "health_start=\"$(date '+%Y-%m-%d %H:%M:%S')\"" in remote_deploy


def test_runtime_version_pins_stay_aligned():
    versions = dict(
        line.split("=", 1)
        for line in (PROJECT_ROOT / "deploy" / "runtime-versions.conf")
        .read_text()
        .splitlines()
        if line and not line.startswith("#")
    )
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "unit-tests.yml").read_text()
    makefile = (PROJECT_ROOT / "Makefile").read_text()

    assert (PROJECT_ROOT / ".python-version").read_text().strip() == versions[
        "PYTHON_VERSION"
    ]
    assert f'version: "{versions["UV_VERSION"]}"' in workflow
    assert "include deploy/runtime-versions.conf" in makefile
