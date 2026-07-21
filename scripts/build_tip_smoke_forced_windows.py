#!/usr/bin/env python3
"""Build tip-smoke forced-window symbol checklist from a forward_log snapshot (CPU).

No YOLO. No MPS. Used by eval_v13_vs_v12_tip.sh preflight so morning eval knows
exactly which symbols the smoke pack will hit.

  PYTHONPATH=. .venv/bin/python scripts/build_tip_smoke_forced_windows.py
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = PROJECT / "analysis" / "output" / "forward_log_vps_20260721.csv"
FALLBACK_LOG = PROJECT / "data" / "forward_log.csv"
DEFAULT_OUT = PROJECT / "analysis" / "output" / "tip_smoke_forced_windows.json"


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    log = args.log
    if log is None:
        log = DEFAULT_LOG if DEFAULT_LOG.exists() else FALLBACK_LOG
    if not log.exists():
        print(f"log missing: {log}")
        return 2

    rows = load_rows(log)
    by_sym: dict[str, list[dict]] = {}
    for r in rows:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        by_sym.setdefault(sym, []).append(
            {
                "signal_time": r.get("signal_time", ""),
                "detected_at": r.get("detected_at", ""),
                "status": r.get("status", ""),
                "signal_i": r.get("signal_i", ""),
            }
        )

    symbols = sorted(by_sym.keys())
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_log": str(log.relative_to(PROJECT)) if str(log).startswith(str(PROJECT)) else str(log),
        "n_log_rows": len(rows),
        "n_symbols": len(symbols),
        "symbols": symbols,
        "per_symbol_rows": {s: by_sym[s] for s in symbols},
        "eval_note": (
            "tip-smoke forces a tip/live scan at each symbol's *current* series tip "
            "(not historical signal_i). Missing local kline => that symbol errors in smoke."
        ),
        "how_to_run_after_v13": (
            "bash scripts/eval_v13_vs_v12_tip.sh   # after models/owner_v13_pad200.pt exists"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"symbols={len(symbols)} rows={len(rows)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
