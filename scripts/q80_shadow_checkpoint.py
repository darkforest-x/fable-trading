# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
"""Seal the first q80 same-window checkpoint after 24 market hours."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.judgment.q80_checkpoint import build_q80_checkpoint


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--status-out", type=Path, required=True)
    parser.add_argument("--ready-out", type=Path, required=True)
    args = parser.parse_args()

    latest = json.loads(args.latest.read_text(encoding="utf-8"))
    ledger = pd.read_csv(args.ledger, float_precision="round_trip")
    payload = build_q80_checkpoint(latest, ledger)
    _write_json_atomic(args.status_out, payload)
    sealed_now = False
    if payload["status"] == "ready" and not args.ready_out.exists():
        _write_json_atomic(args.ready_out, payload)
        sealed_now = True
    print(
        json.dumps(
            {
                "status": payload["status"],
                "elapsed_hours": payload["elapsed_hours"],
                "sealed_now": sealed_now,
                "ready_out": str(args.ready_out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
