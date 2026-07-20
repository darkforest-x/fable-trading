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


def pass_rate_digest(hours: float = 24.0) -> str:
    """Live funnel summary for the daily TG digest.

    Answers the standing question -- is the live tip pass-rate consistent with
    the pool's ~8-10%? -- without anyone having to ssh in: pulses, candidate
    sightings, new threshold passes (and how many were fresh enough to trade),
    detection lag, executor events, all over the last `hours`.
    """
    import csv
    import re
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    lines: list[str] = []

    log = PROJECT / "logs" / "forward_pulse.log"
    if log.exists():
        pulses = cands = thr = 0
        for block in log.read_text(errors="ignore").split("=== forward_pulse ")[1:]:
            m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", block)
            if not m:
                continue
            try:
                ts = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if ts < since:
                continue
            pulses += 1
            mc = re.search(r'"candidates_seen": (\d+)', block)
            mt = re.search(r'"threshold_signals_seen": (\d+)', block)
            cands += int(mc.group(1)) if mc else 0
            thr += int(mt.group(1)) if mt else 0
        lines.append(f"脉冲 {pulses} 轮 · 候选目击 {cands} 次")

    def _ts(raw: str) -> datetime | None:
        try:
            return datetime.fromisoformat(str(raw).replace("+00:00", "")).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    fl = PROJECT / "data" / "forward_log.csv"
    if fl.exists():
        lags = []
        for r in csv.DictReader(fl.open()):
            d, s = _ts(r.get("detected_at", "")), _ts(r.get("signal_time", ""))
            if d and s and d >= since:
                lags.append((d - s).total_seconds() / 60)
        # Align with executor / forward verdict (owner 2026-07-19 tip target).
        fresh = sum(1 for x in lags if x <= 20)
        hindsight = sum(1 for x in lags if x > 20)
        lines.append(
            f"新过线 {len(lags)} · tip新鲜≤20m: {fresh} · 事后: {hindsight}"
        )
        if lags:
            lines.append(f"检出延迟中位 {sorted(lags)[len(lags)//2]:.0f} 分")
        if lags and fresh == 0 and hindsight > 0:
            lines.append("⚠️ 窗口内 0 笔 tip 新鲜 — 检测层仍偏事后")

    led = PROJECT / "data" / "executor_ledger.jsonl"
    if led.exists():
        events: list[str] = []
        for ln in led.read_text().splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            t = _ts(r.get("ts", ""))
            if t and t >= since:
                events.append(str(r.get("event")))
        summary = ", ".join(f"{k}×{v}" for k, v in Counter(events).items()) or "无事件"
        lines.append(f"执行器: {summary}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", action="store_true", help="TG on hard fail")
    ap.add_argument(
        "--forward-stale-min",
        type=float,
        default=40.0,
        help="pulse/log older than this → FAIL (default 40 ≈ missed 2×15m ticks)",
    )
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

    # Daily funnel digest: the health timer fires every 30 min; the run that
    # lands in the 09:00-09:29 Beijing window (01:00-01:29 UTC) also reports
    # the last 24h live funnel, so "is the pass-rate normal?" answers itself.
    now_utc = datetime.now(timezone.utc)
    if args.alert and now_utc.hour == 1 and now_utc.minute < 30:
        try:
            from src.notify import send as _send

            _send("📈 <b>实盘日报(过去24h)</b>\n<code>" + pass_rate_digest(24.0) + "</code>")
        except Exception as exc:  # noqa: BLE001
            print(f"digest failed: {exc}", flush=True)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
