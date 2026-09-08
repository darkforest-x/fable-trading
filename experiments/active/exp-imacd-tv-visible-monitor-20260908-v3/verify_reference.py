"""Replay one owner-verified ETH 4H TradingView main-chart label in RAM.

The native UI reference is '蓄势释放 ↑ · 44根 / 收盘 1922.23', with confirmed
open 2026-08-19 08:00 UTC and close 12:00 UTC. Its captured Pine profile is
showFocus=true, showMarks=false, focusMinBars=12, focusAtrBand=.10. This
script fetches a fixed 720-bar window using public OKX history only, imports
the actual monitor engine, and prints derived event/indicator evidence.
It never persists raw OHLCV, reads credentials, starts a service, sends a
notification, places an order, or changes a chart. No whole-history Pine
parity or profitability claim follows from this one matched reference.

Run from the repository after committing this builder:
  .venv/bin/python experiments/active/exp-imacd-tv-visible-monitor-20260908-v3/verify_reference.py
Redirect stdout to a new JSON artifact if a durable acceptance is required.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.monitor.okx import OKX, merge_rows, parse_rows
from yoyo.monitor.signals import PROTOCOL_VERSION, TV_PROFILE, analyze

SYMBOL = "ETH-USDT-SWAP"
PERIOD_MS = 14_400_000
END_MS = 1_788_811_200_000  # 2026-09-07 20:00 UTC, inclusive last close.
COUNT = 720
EXPECTED_OPEN = 1_787_126_400_000
EXPECTED_CLOSE = 1_787_140_800_000
EXPECTED_PRICE = 1922.23
EXPECTED_RUN = 44
EXPECTED_PINE_SHA = "9c3f60cf2a66d55f21e964d7a9b97971d07fdc90195ade8af1a7e9d78a89ffcb"


def main():
    pine = ROOT / "yoyo/evaluation/pine/imacd_dense_mtf_v2_2.pine"
    pine_hash = hashlib.sha256(pine.read_bytes()).hexdigest()
    if pine_hash != EXPECTED_PINE_SHA:
        raise RuntimeError("reference_pine_source_changed")
    client = OKX()
    client.synchronize()
    after, collected = END_MS, []
    for _ in range(12):
        raw = client.get("/api/v5/market/history-candles",
                         {"instId": SYMBOL, "bar": "4H", "limit": "100", "after": str(after)})
        if not raw:
            break
        batch = parse_rows(raw, "4H", min(client.clock(), END_MS))
        collected.extend(row for row in batch if row["t"] + PERIOD_MS <= END_MS)
        oldest = min(int(row[0]) for row in raw)
        if oldest >= after:
            raise RuntimeError("history_pagination_did_not_advance")
        after = oldest
        if len({row["t"] for row in collected}) >= COUNT:
            break
    bars, gaps = merge_rows([], collected, "4H")
    bars = bars[-COUNT:]
    if (len(bars) != COUNT or gaps or bars[-1]["t"] + PERIOD_MS != END_MS
            or bars[0]["t"] != END_MS - COUNT * PERIOD_MS):
        raise RuntimeError("fixed_reference_window_incomplete")
    result = analyze(bars, [], "4H")
    starts = [event for event in result["events"] if event["kind"] == "tv_start"
              and event["bar_open_ms"] == EXPECTED_OPEN]
    match = starts[0] if len(starts) == 1 else None
    verified = bool(match and match["source_kind"] == "release" and match["side"] == "long"
                    and match["tv_profile"] == TV_PROFILE and match["tv_marker"] == "focus_release"
                    and match["bar_close_ms"] == EXPECTED_CLOSE
                    and abs(match["price"] - EXPECTED_PRICE) < 0.005
                    and match["near_zero_bars"] == EXPECTED_RUN
                    and match["previous_md"] > 0 and match["zero_bars"] == 0)
    relevant = [event for event in result["events"]
                if EXPECTED_OPEN - 2 * PERIOD_MS <= event["bar_open_ms"] <= EXPECTED_OPEN]
    events = [{key: event.get(key) for key in (
        "kind", "side", "bar_open_ms", "bar_close_ms", "price", "md", "sb", "previous_md",
        "previous_sb", "near_zero_bars", "zero_bars", "focus_band", "focus_start_ms",
        "focus_qualified_ms", "zone_end_ms", "source_kind", "tv_marker", "tv_profile",
        "tv_marker_visible", "tv_show_focus", "tv_show_marks", "focus_qualified_before",
        "confirmed", "ready", "is_monitor_signal")}
        for event in relevant]
    indicator_rows = [{key: row.get(key) for key in (
        "t", "bar_close_ms", "md", "sb", "focus_band", "focus", "near_zero_bars",
        "zero_bars", "zero_breakout_side", "entry_side", "release_side", "tv_start_side")}
        for row in result["chart"] if EXPECTED_OPEN - 3 * PERIOD_MS <= row["t"] <= EXPECTED_OPEN + PERIOD_MS]
    payload = {
        "label": "owner-verified-eth-4h-visible-arrow-1922.23-44",
        "verified": verified,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "pine_sha256": pine_hash,
        "signal_source_sha256": hashlib.sha256((ROOT / "yoyo/monitor/signals.py").read_bytes()).hexdigest(),
        "protocol": PROTOCOL_VERSION,
        "tv_profile": TV_PROFILE,
        "data": {"symbol": SYMBOL, "timeframe": "4H", "bars": len(bars), "gaps": gaps,
                 "first_open_ms": bars[0]["t"], "last_close_ms": bars[-1]["t"] + PERIOD_MS,
                 "input_sha256": hashlib.sha256(json.dumps(bars, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                 "requests": client.requests, "storage": "RAM_only", "higher_data": "omitted_not_a_visible_arrow_gate"},
        "expected": {"bar_open_ms": EXPECTED_OPEN, "bar_close_ms": EXPECTED_CLOSE,
                     "price": EXPECTED_PRICE, "near_zero_bars": EXPECTED_RUN, "side": "long"},
        "events": events,
        "indicator_context": indicator_rows,
        "scope": "single owner-visible Pine label with captured settings; no global parity or economic inference",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
