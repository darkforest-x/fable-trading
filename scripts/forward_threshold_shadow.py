# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/forward_threshold_shadow.py
"""Run the owner-approved q80 shadow and same-window q90/q80 funnel."""
from __future__ import annotations

import json

from src.judgment.forward_threshold_shadow import run_q80_shadow, scan_threshold_funnel
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, latest_artifact


def main() -> int:
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is None:
        raise FileNotFoundError("missing frozen MA206 artifact")
    funnel = scan_threshold_funnel(artifact)
    shadow = run_q80_shadow()
    print(
        json.dumps(
            {"funnel": funnel.to_json(), "q80_shadow": shadow.to_json()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
