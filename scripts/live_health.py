#!/usr/bin/env python3
"""Live-stack health check for overnight ops.

Checks VPS-local paths (run from /opt/fable-trading):
  - forward_log mtime / pulse lag
  - executor kill switch
  - OKX live equity ping
  - ACTIVE pointer exists
Optionally alerts Telegram on hard failures.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/live_health.py
  PYTHONPATH=. .venv/bin/python scripts/live_health.py --alert
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def _age_min(path: Path) -> float | None:
    if not path.exists():
        return None
    m = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - m).total_seconds() / 60.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", action="store_true", help="TG on hard fail")
    ap.add_argument("--forward-stale-min", type=float, default=45.0)
    args = ap.parse_args()

    issues: list[str] = []
    notes: list[str] = []

    active = PROJECT / "models" / "ACTIVE"
    if not active.exists():
        issues.append("models/ACTIVE missing")
    else:
        notes.append(f"ACTIVE={active.read_text(encoding='utf-8').strip()}")

    fl = PROJECT / "data" / "forward_log.csv"
    age = _age_min(fl)
    if age is None:
        issues.append("forward_log.csv missing")
    else:
        notes.append(f"forward_log age={age:.1f}m")
        if age > args.forward_stale_min:
            issues.append(f"forward_log stale ({age:.0f}m > {args.forward_stale_min:.0f}m)")

    kill = PROJECT / "data" / "executor_KILL"
    if kill.exists():
        issues.append("executor_KILL is ON — new entries blocked")

    keys = PROJECT / "data" / "okx_demo_keys.json"
    if not keys.exists():
        issues.append("okx keys missing")
    else:
        try:
            sys.path.insert(0, str(PROJECT))
            from src.execution.okx_client import OkxDemoClient

            c = OkxDemoClient(keys_path=keys)
            eq = c.usdt_equity()
            notes.append(f"okx env={c.environment} equity={eq:.2f}")
            if c.is_demo:
                issues.append("keys still demo — not live")
            if eq < 5:
                issues.append(f"equity too low ({eq:.2f}U)")
        except Exception as exc:  # noqa: BLE001
            issues.append(f"okx ping failed: {exc}")

    pulse_log = PROJECT / "logs" / "forward_pulse.log"
    pa = _age_min(pulse_log)
    if pa is not None:
        notes.append(f"pulse_log age={pa:.1f}m")
        if pa > args.forward_stale_min:
            issues.append(f"forward_pulse.log stale ({pa:.0f}m)")

    status = "FAIL" if issues else "OK"
    line = (
        f"live_health {status} | "
        + " · ".join(notes)
        + ((" | ISSUES: " + "; ".join(issues)) if issues else "")
    )
    print(line, flush=True)

    if args.alert and issues:
        try:
            from src.notify import send

            send(
                "🚨 <b>live_health FAIL</b>\n"
                + "\n".join(f"• {x}" for x in issues)
                + "\n<code>"
                + " · ".join(notes)[:500]
                + "</code>"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"alert failed: {exc}", flush=True)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
