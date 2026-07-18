"""Poll forward_log → place market + TP/SL bracket.

Hard rules:
- OkxDemoClient reads environment from keys file (demo|live).
- Kill switch file blocks new entries.
- Circuit breaker: consecutive closed losses pause new entries.
- Invalid TP/SL refuses entry (never leave a naked position).
"""
from __future__ import annotations

import math
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.execution.config import (
    SL_ATR_MULT,
    TP_ATR_MULT,
    ExecutorConfig,
    kill_switch_active,
)
from src.execution import ledger as led
from src.execution.okx_client import OkxDemoClient, OkxDemoError
from src.execution.symbols import round_price, size_for_notional, to_okx_inst_id


def signal_key(row: pd.Series) -> str:
    return f"{row.get('source','okx')}|{row.get('symbol')}|{row.get('signal_time')}|{row.get('score')}"


_NOTIFY_EVENTS = {
    "order_placed": "🟢 <b>实盘开仓</b>",
    "order_partial": "🟡 <b>开仓成功·括号失败</b>(需人工补止损!)",
    "order_failed": "🔴 <b>下单失败</b>",
    "skipped_invalid_barriers": "⚠️ <b>拒单</b>(止盈止损价不可用)",
}


def _notify_event(ev: dict) -> None:
    """Push trade events to Telegram. Fire-and-forget: the trading loop must
    never stall or die because a notification did."""
    label = _NOTIFY_EVENTS.get(str(ev.get("event")))
    if label is None:
        return
    try:
        from src.notify import send

        parts = [label, f"品种: <b>{ev.get('inst_id') or ev.get('symbol')}</b>"]
        if ev.get("mark_px"):
            parts.append(f"价格: {ev['mark_px']}")
        if ev.get("tp_px") and ev.get("sl_px"):
            parts.append(f"止盈 {ev['tp_px']} / 止损 {ev['sl_px']}")
        if ev.get("sz"):
            parts.append(f"数量: {ev['sz']}  名义: {ev.get('notional_usdt', '?')}U")
        if ev.get("error"):
            parts.append(f"错误: {str(ev['error'])[:160]}")
        if ev.get("note"):
            parts.append(str(ev["note"])[:160])
        send("\n".join(parts))
    except Exception as exc:  # noqa: BLE001
        print(f"executor notify failed: {exc}")


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return Path(__file__).resolve().parents[2] / p


def load_actionable_signals(cfg: ExecutorConfig) -> pd.DataFrame:
    path = _resolve(cfg.forward_log)
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    if "status" in df.columns:
        df = df[df["status"].astype(str).isin(cfg.open_statuses)]
    if cfg.require_score_ge_threshold and "score" in df.columns and "threshold" in df.columns:
        df = df[pd.to_numeric(df["score"], errors="coerce") >= pd.to_numeric(df["threshold"], errors="coerce")]
    # Freshness gate: a signal stays status=open until its barrier resolves (up
    # to 18h), but the EDGE is the launch moment -- entering hours late is a
    # different, untested trade. Only rows younger than max_signal_age_min may
    # open positions (the backtest enters at the very next bar).
    if "signal_time" in df.columns:
        age_cap = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=cfg.max_signal_age_min)
        ts = pd.to_datetime(df["signal_time"], errors="coerce", utc=True)
        df = df[ts >= age_cap]
        df = df.sort_values("signal_time")
    return df.reset_index(drop=True)


def barriers(entry: float, atr_pct: float) -> tuple[float, float]:
    try:
        atr = abs(entry * float(atr_pct))
    except (TypeError, ValueError):
        atr = float("nan")
    # `atr <= 0` misses NaN (all NaN comparisons are False): a forward row with
    # atr_pct=None sailed through here on 2026-07-16, produced tp=sl=NaN -> 0.0
    # after tick rounding, OKX rejected the bracket (51250) and a REAL DOGE long
    # sat naked. `not (atr > 0)` is True for NaN, zero, and negatives alike.
    if not (atr > 0) or not math.isfinite(atr):
        atr = entry * 0.01  # 1% proxy so the position is never unprotected
    tp = entry + TP_ATR_MULT * atr
    sl = entry - SL_ATR_MULT * atr
    return tp, sl


def compute_entry_notional(
    client: OkxDemoClient | None,
    cfg: ExecutorConfig,
    *,
    open_n: int,
    open_notional: float = 0.0,
) -> dict[str, Any]:
    """How much USDT notional to open for the next slot.

    equity_times_leverage: remaining_budget / slots_left
      remaining = equity * leverage - open_notional
    fixed: cfg.notional_usdt
    """
    mode = (cfg.sizing_mode or "fixed").strip().lower()
    out: dict[str, Any] = {
        "sizing_mode": mode,
        "leverage": cfg.leverage,
        "open_n": open_n,
        "open_notional": open_notional,
    }
    if mode in {"equity_times_leverage", "equity_x_leverage", "equity_leverage"}:
        if client is None:
            out["notional_usdt"] = float(cfg.notional_usdt)
            out["note"] = "no client — fell back to fixed notional_usdt"
            return out
        equity = client.usdt_equity()
        target = max(0.0, float(equity) * float(cfg.leverage))
        remaining = max(0.0, target - max(0.0, float(open_notional)))
        slots_left = max(1, int(cfg.max_concurrent) - int(open_n))
        notional = remaining / slots_left
        out.update({
            "equity_usdt": equity,
            "target_gross_usdt": target,
            "remaining_budget_usdt": remaining,
            "slots_left": slots_left,
            "notional_usdt": notional,
        })
        return out
    out["notional_usdt"] = float(cfg.notional_usdt)
    return out


def open_one(
    client: OkxDemoClient | None,
    cfg: ExecutorConfig,
    row: pd.Series,
    *,
    dry_run: bool,
    notional_usdt: float | None = None,
    sizing_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Place one long paper trade (+ OCO). Returns ledger event dict."""
    sk = signal_key(row)
    symbol = str(row["symbol"])
    inst_id = to_okx_inst_id(symbol)
    atr_pct = float(row["atr_pct"]) if pd.notna(row.get("atr_pct")) else 0.01
    notional = float(notional_usdt if notional_usdt is not None else cfg.notional_usdt)
    event: dict[str, Any] = {
        "event": "dry_run" if dry_run else "order_placed",
        "signal_key": sk,
        "symbol": symbol,
        "inst_id": inst_id,
        "score": row.get("score"),
        "threshold": row.get("threshold"),
        "side": "buy",
        "tp_atr_mult": TP_ATR_MULT,
        "sl_atr_mult": SL_ATR_MULT,
        "td_mode": cfg.td_mode,
    }
    if sizing_meta:
        event["sizing"] = sizing_meta

    if dry_run or client is None:
        # Estimate without keys when dry-run and no client
        mark = float(row["entry_price"]) if pd.notna(row.get("entry_price")) else None
        event.update({
            "mark_px": mark,
            "notional_usdt": notional,
            "note": "dry-run: no order sent",
        })
        if mark:
            tp, sl = barriers(mark, atr_pct)
            event["tp_px"], event["sl_px"] = tp, sl
        return event

    if notional < float(cfg.min_notional_usdt):
        event["event"] = "skipped"
        event["note"] = (
            f"notional {notional:.4f} < min_notional_usdt {cfg.min_notional_usdt}"
        )
        return event

    inst = client.instrument(inst_id)
    mark = client.mark_px(inst_id)
    sz = size_for_notional(notional, mark, inst)
    tick = inst.get("tickSz") or "0.01"
    tp_raw, sl_raw = barriers(mark, atr_pct)
    tp_px = round_price(tp_raw, tick)
    sl_px = round_price(sl_raw, tick)
    event.update({
        "mark_px": mark,
        "sz": sz,
        "tp_px": tp_px,
        "sl_px": sl_px,
        "notional_usdt": notional,
        "leverage": cfg.leverage,
    })

    # The bracket IS the risk control: if these numbers are unusable, there is
    # nothing safe to place afterwards, so refuse the ENTRY -- do not discover
    # the problem with a live position already open (2026-07-16 DOGE incident).
    if not (math.isfinite(tp_px) and math.isfinite(sl_px) and 0 < sl_px < mark < tp_px):
        event["event"] = "skipped_invalid_barriers"
        event["note"] = f"tp/sl unusable: tp={tp_px} sl={sl_px} mark={mark}"
        return event

    try:
        client.set_leverage(inst_id, str(cfg.leverage), mgn_mode=cfg.td_mode)
    except OkxDemoError as exc:
        # leverage may already be set; log and continue
        event["leverage_warn"] = str(exc)

    # Account may be net_mode or long_short_mode (hedge).
    mode = client.pos_mode()
    pos_side = "long" if mode == "long_short_mode" else "net"
    event["pos_mode"] = mode
    event["pos_side"] = pos_side

    cl_id = f"f{abs(hash(sk)) % 10**10}"
    order = client.place_market(
        inst_id, "buy", sz, td_mode=cfg.td_mode, cl_ord_id=cl_id, pos_side=pos_side
    )
    event["order_resp"] = order.get("data")
    # closing side for long = sell; same posSide in hedge mode
    # Retry bracket: a transient OKX 5xx after fill must not leave us naked.
    retries = max(0, int(getattr(cfg, "bracket_retries", 2)))
    sleep_s = float(getattr(cfg, "bracket_retry_sleep_sec", 1.5))
    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            algo = client.place_bracket(
                inst_id, "sell", sz, tp_px, sl_px, td_mode=cfg.td_mode, pos_side=pos_side
            )
            event["algo_resp"] = algo.get("data")
            event["bracket_attempts"] = attempt + 1
            last_err = None
            break
        except OkxDemoError as exc:
            last_err = str(exc)
            event["algo_error"] = last_err
            if attempt < retries:
                time.sleep(max(0.2, sleep_s))
    if last_err is not None:
        event["event"] = "order_partial"  # entry ok, bracket failed — owner must watch
        event["bracket_attempts"] = retries + 1
    return event


def run_once(cfg: ExecutorConfig, *, dry_run: bool = False) -> dict[str, Any]:
    """Single poll cycle. Returns summary counters."""
    ledger_path = _resolve(cfg.ledger)
    summary: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "opened": 0,
        "skipped": 0,
        "errors": 0,
        "paused": None,
    }

    if kill_switch_active(cfg):
        summary["paused"] = f"kill switch: {cfg.kill_switch_file}"
        # Do not append paused every 30–60s — it bloated the ledger to 300+ noise rows.
        return summary

    losses = led.consecutive_losses(ledger_path)
    if losses >= cfg.max_consecutive_losses:
        summary["paused"] = f"circuit breaker: {losses} consecutive losses"
        return summary

    taken = led.signal_keys_already_taken(ledger_path)
    signals = load_actionable_signals(cfg)
    if signals.empty:
        summary["note"] = "no actionable rows in forward_log"
        return summary

    client: OkxDemoClient | None = None
    open_n = 0
    open_notional = 0.0
    if not dry_run:
        client = OkxDemoClient()
        try:
            positions = client.positions("SWAP")
            open_n = sum(1 for p in positions if abs(float(p.get("pos") or 0)) > 0)
            open_notional = client.open_swap_notional_usd()
        except OkxDemoError as exc:
            summary["errors"] += 1
            summary["error"] = str(exc)
            led.append(ledger_path, {"event": "error", "where": "positions", "error": str(exc)})
            return summary
    else:
        # dry-run: count opens from ledger order_placed without closed
        placed = {r["signal_key"] for r in led.load_all(ledger_path) if r.get("event") == "order_placed"}
        closed = {r["signal_key"] for r in led.load_all(ledger_path) if r.get("event") == "closed"}
        open_n = len(placed - closed)
        open_notional = float(cfg.notional_usdt) * open_n

    slots = max(0, cfg.max_concurrent - open_n)
    summary["open_n"] = open_n
    summary["open_notional_usd"] = open_notional
    summary["max_concurrent"] = cfg.max_concurrent
    if slots <= 0:
        summary["note"] = f"at max_concurrent={cfg.max_concurrent} (open={open_n})"
        return summary

    for _, row in signals.iterrows():
        if slots <= 0:
            break
        sk = signal_key(row)
        if sk in taken:
            continue
        try:
            sizing = compute_entry_notional(
                client, cfg, open_n=open_n, open_notional=open_notional
            )
            notional = float(sizing.get("notional_usdt") or cfg.notional_usdt)
            summary["last_sizing"] = sizing
            ev = open_one(
                client, cfg, row, dry_run=dry_run,
                notional_usdt=notional, sizing_meta=sizing,
            )
            led.append(ledger_path, ev)
            _notify_event(ev)
            if ev.get("event") in {"order_placed", "order_partial", "dry_run"}:
                summary["opened"] += 1
                slots -= 1
                open_n += 1
                open_notional += notional
                taken.add(sk)
            else:
                summary["skipped"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not kill the loop
            summary["errors"] += 1
            fail_ev = {
                "event": "order_failed",
                "signal_key": sk,
                "symbol": row.get("symbol"),
                "error": str(exc),
                "trace": traceback.format_exc(limit=4),
            }
            led.append(ledger_path, fail_ev)
            _notify_event(fail_ev)
    return summary


def run_loop(cfg: ExecutorConfig, *, dry_run: bool = False, once: bool = False) -> None:
    while True:
        summary = run_once(cfg, dry_run=dry_run)
        print(json_dumps(summary), flush=True)
        if once:
            return
        time.sleep(max(5, int(cfg.poll_seconds)))


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)
