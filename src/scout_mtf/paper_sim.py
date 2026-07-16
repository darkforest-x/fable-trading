"""Paper simulation for scout_mtf picks — no exchange orders.

Uses latest radar grades (A/B by default), walks recent 15m history with the
same expanded dense + above-EMA55 entry idea, TP5/SL2 barriers, 0.3% cost.
Side-branch only: writes data/scout_mtf/paper_latest.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.judgment.candidates import add_indicators, strict_mask
from src.scout_mtf.tf_scan import fetch_candles

PROJECT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT / "data" / "scout_mtf"
LATEST = OUT_DIR / "latest.json"
PAPER_LATEST = OUT_DIR / "paper_latest.json"

TP_ATR = 5.0
SL_ATR = 2.0
HORIZON = 48  # 15m bars (~12h) — short pulse horizon for radar paper
COST = 0.003
WARMUP = 200


def _simulate_trade(frame: pd.DataFrame, entry_i: int) -> dict[str, Any] | None:
    """Enter at open of entry_i; barriers from atr at entry_i-1 (signal bar)."""
    if entry_i <= 0 or entry_i >= len(frame):
        return None
    sig = frame.iloc[entry_i - 1]
    atr = float(sig.get("atr14") or 0)
    entry = float(frame.iloc[entry_i]["open"])
    if not np.isfinite(atr) or atr <= 0 or entry <= 0:
        return None
    tp = entry + TP_ATR * atr
    sl = entry - SL_ATR * atr
    last_i = min(entry_i + HORIZON - 1, len(frame) - 1)
    outcome = "timeout"
    exit_px = float(frame.iloc[last_i]["close"])
    exit_i = last_i
    for j in range(entry_i, last_i + 1):
        hi = float(frame.iloc[j]["high"])
        lo = float(frame.iloc[j]["low"])
        hit_tp = hi >= tp
        hit_sl = lo <= sl
        if hit_tp and hit_sl:
            outcome, exit_px, exit_i = "sl_ambiguous", sl, j
            break
        if hit_sl:
            outcome, exit_px, exit_i = "sl", sl, j
            break
        if hit_tp:
            outcome, exit_px, exit_i = "tp", tp, j
            break
    gross = exit_px / entry - 1.0
    net = gross - COST
    entry_ts = frame.iloc[entry_i].get("open_time")
    exit_ts = frame.iloc[exit_i].get("open_time")
    return {
        "entry_i": entry_i,
        "exit_i": exit_i,
        "entry_px": round(entry, 8),
        "exit_px": round(exit_px, 8),
        "tp_px": round(tp, 8),
        "sl_px": round(sl, 8),
        "atr": round(atr, 8),
        "entry_time": str(entry_ts) if entry_ts is not None else "",
        "exit_time": str(exit_ts) if exit_ts is not None else "",
        "outcome": outcome,
        "gross_ret": round(gross, 6),
        "net_ret": round(net, 6),
        "bars_held": int(exit_i - entry_i + 1),
    }


def paper_test_symbol(inst_id: str, symbol: str, *, grade: str, composite: float) -> dict[str, Any]:
    """Replay dense entries on recent 15m for one symbol."""
    try:
        raw = fetch_candles(inst_id, "15m", limit=300)
    except Exception as exc:  # noqa: BLE001
        return {
            "symbol": symbol,
            "inst_id": inst_id,
            "grade": grade,
            "composite": composite,
            "ok": False,
            "error": str(exc),
            "trades": [],
        }
    if len(raw) < WARMUP + 10:
        return {
            "symbol": symbol,
            "inst_id": inst_id,
            "grade": grade,
            "composite": composite,
            "ok": False,
            "error": "bars_too_few",
            "trades": [],
        }

    en = add_indicators(raw)
    try:
        mask = strict_mask(en, mode="expanded")
    except Exception:
        mask = pd.Series(False, index=en.index)

    trades: list[dict] = []
    cooldown_until = -1
    # only last 96 signal bars to keep paper focused on recent regime
    start = max(WARMUP, len(en) - 96)
    for i in range(start, len(en) - 1):
        if i < cooldown_until:
            continue
        row = en.iloc[i]
        dense = bool(mask.iloc[i]) if i < len(mask) else False
        above = pd.notna(row.get("ema55")) and float(row["close"]) >= float(row["ema55"])
        if not (dense and above):
            continue
        t = _simulate_trade(en, i + 1)
        if t is None:
            continue
        t["signal_time"] = str(en.iloc[i].get("open_time", ""))
        trades.append(t)
        cooldown_until = t["exit_i"] + 4  # small gap between paper trades

    nets = [t["net_ret"] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = sum(losses) if losses else 0.0
    pf = (gross_win / abs(gross_loss)) if gross_loss < 0 else (float("inf") if gross_win > 0 else 0.0)
    return {
        "symbol": symbol,
        "inst_id": inst_id,
        "grade": grade,
        "composite": composite,
        "ok": True,
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4) if trades else None,
        "mean_net": round(float(np.mean(nets)), 5) if nets else None,
        "total_net": round(float(sum(nets)), 5) if nets else 0.0,
        "profit_factor": round(float(pf), 3) if pf != float("inf") else None,
        "pf_inf": pf == float("inf"),
        "trades": trades,  # full list so UI can drill into each fill
    }


def run_paper_test(
    *,
    grades: tuple[str, ...] = ("A", "B"),
    only_gain: bool = True,
    max_symbols: int = 12,
    latest_path: Path | None = None,
) -> dict[str, Any]:
    """Paper-test radar picks from latest.json."""
    path = Path(latest_path) if latest_path else LATEST
    if not path.exists():
        return {
            "ok": False,
            "error": "没有扫描结果，请先点「开始扫描」",
            "symbols": [],
        }
    scan = json.loads(path.read_text(encoding="utf-8"))
    picks = []
    for r in scan.get("results") or []:
        if r.get("grade") not in grades:
            continue
        if only_gain and r.get("rank_side") != "gain":
            continue
        picks.append(r)
        if len(picks) >= max_symbols:
            break

    if not picks:
        return {
            "ok": False,
            "error": "当前没有 A/B 档（涨幅侧）可模拟，请先扫描或放宽筛选",
            "symbols": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    per: list[dict[str, Any]] = []
    for r in picks:
        per.append(
            paper_test_symbol(
                str(r.get("inst_id") or r["symbol"].replace("_", "-")),
                str(r["symbol"]),
                grade=str(r.get("grade")),
                composite=float(r.get("composite") or 0),
            )
        )
        # gentle pacing for public candles API
        import time

        time.sleep(0.08)

    ok_rows = [p for p in per if p.get("ok") and p.get("n_trades", 0) > 0]
    all_nets = []
    for p in ok_rows:
        all_nets.extend([t["net_ret"] for t in p.get("trades") or []])
        # also count total_net from full sim stored as total_net
    # recompute from totals for accuracy (trades list may be truncated)
    total_net = sum(float(p.get("total_net") or 0) for p in ok_rows)
    n_trades = sum(int(p.get("n_trades") or 0) for p in ok_rows)
    # rebuild nets by re-summing isn't available for truncated; use mean*n approx from total
    wins = sum(
        int(round((p.get("win_rate") or 0) * (p.get("n_trades") or 0)))
        for p in ok_rows
        if p.get("win_rate") is not None
    )

    report = {
        "ok": True,
        "mode": "paper",
        "disclaimer": (
            "纯本地纸面模拟：最近约 96 根 15m 上，expanded 密集且站上 EMA55 才进场；"
            "TP5/SL2、超时 48 根、扣 0.3% 成本。不是交易所下单，也不是主线前向。"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "grades": list(grades),
            "only_gain": only_gain,
            "tp_atr": TP_ATR,
            "sl_atr": SL_ATR,
            "horizon_bars": HORIZON,
            "cost": COST,
            "entry": "expanded dense + close>=ema55, next bar open",
        },
        "n_symbols": len(picks),
        "n_symbols_with_trades": len(ok_rows),
        "n_trades": n_trades,
        "approx_wins": wins,
        "win_rate": round(wins / n_trades, 4) if n_trades else None,
        "total_net_units": round(total_net, 5),
        "mean_net_per_trade": round(total_net / n_trades, 5) if n_trades else None,
        "symbols": per,
        "scan_generated_at": scan.get("generated_at"),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_LATEST.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["output_path"] = str(PAPER_LATEST)
    return report


def load_paper_latest() -> dict[str, Any]:
    if not PAPER_LATEST.exists():
        return {
            "ok": False,
            "available": False,
            "message": "还没有模拟结果，先扫描再点「模拟测试」。",
            "symbols": [],
        }
    data = json.loads(PAPER_LATEST.read_text(encoding="utf-8"))
    data["available"] = True
    return data
