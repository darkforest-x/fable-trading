"""CLI worker for one signal-console model replay."""
from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import argparse
import json

from yoyo.console.replay import run_replay


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one read-only pre-holdout model replay")
    parser.add_argument("--symbol", required=True)
    args = parser.parse_args()
    try:
        payload = {"ok": True, "result": run_replay(args.symbol)}
        code = 0
    except Exception as exc:  # noqa: BLE001 -- JSON is the subprocess boundary
        payload = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        code = 1
    print(json.dumps(payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

