from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/train_eth3m_short_pilot_on_3060.sh"


def test_v1_launcher_requires_an_explicit_current_3060_host() -> None:
    env = os.environ.copy()
    env.pop("FABLE_3060_HOST", None)
    result = subprocess.run(
        ["bash", str(SCRIPT), "--check"],
        cwd=PROJECT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FABLE_3060_HOST is required" in result.stderr
    assert "SSH" not in result.stdout


def test_v1_launcher_help_remains_available_without_remote_configuration() -> None:
    env = os.environ.copy()
    env.pop("FABLE_3060_HOST", None)
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        cwd=PROJECT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "FABLE_3060_HOST" in result.stdout
