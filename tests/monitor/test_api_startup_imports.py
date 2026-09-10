"""Optional replay rendering must not delay the loopback API listener."""
from __future__ import annotations

import subprocess
import sys


def test_api_import_defers_frozen_replay_dependencies():
    command = (
        "import sys; import yoyo.monitor.server; "
        "assert 'yoyo.monitor.replay_chart' not in sys.modules; "
        "assert 'pandas' not in sys.modules; assert 'numpy' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
