"""Live ETH micro-bar monitor: rules candidates → frozen LGB → TG + log."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import urllib.request

from src.data.bars import bar_to_timedelta
from src.eth_micro.config import (
    BARS,
    MODELS_DIR,
    SIGNAL_LOG,
    SOURCE,
    STATUS_JSON,
    SYMBOL,
    bar_configs,
    ensure_dirs,
)
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.forward_types import SL_MULT, TP_MULT
from src.notify_signal import notify_signal

OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}
# Need enough bars for EMA200 + expanded scan context
LIVE_LOOKBACK = 400
SIGNAL_COLUMNS = [
    "source",
    "symbol",
    "bar",
    "signal_time",
    "entry_time",
    "entry_price",
    "score",
    "threshold",
    "tp_price",
    "sl_price",
    "atr14",
    "status",
    "notified_at",
]


def fetch_live_ohlc(bar: str, limit: int = LIVE_LOOKBACK) -> pd.DataFrame:
    inst = SYMBOL.replace("_", "-")
    url = f"{OKX_CANDLES}?instId={inst}&bar={bar}&limit={min(limit, 300)}"
    # OKX max 300 per request; page if needed
    rows: list[list] = []
    after = None
    remaining = limit
    while remaining > 0:
        page_lim = min(300, remaining)
        u = f"{OKX_CANDLES}?instId={inst}&bar={bar}&limit={page_lim}"
        if after is not None:
            u += f"&after={after}"
        req = urllib.request.Request(u, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX {bar}: {payload.get('msg')}")
        page = payload.get("data") or []
        if not page:
            break
        rows.extend(page)
        after = int(page[-1][0])
        remaining -= len(page)
        if len(page) < page_lim:
            break
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame()
    # API returns newest first
    uniq = {int(r[0]): r for r in rows}
    ordered = [uniq[k] for k in sorted(uniq)]
    frame = pd.DataFrame(
        {
            "open_time": [
                datetime.fromtimestamp(int(r[0]) / 1e3, tz=timezone.utc) for r in ordered
            ],
            "open": [float(r[1]) for r in ordered],
            "high": [float(r[2]) for r in ordered],
            "low": [float(r[3]) for r in ordered],
            "close": [float(r[4]) for r in ordered],
            "volume": [float(r[5]) for r in ordered],
        }
    )
    return frame


def load_bar_model(bar: str) -> tuple[lgb.Booster, dict] | None:
    meta_path = MODELS_DIR / f"eth_{bar}_meta.json"
    model_path = MODELS_DIR / f"eth_{bar}_reg.txt"
    if not meta_path.exists() or not model_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    booster = lgb.Booster(model_file=str(model_path))
    return booster, meta


def _read_log() -> pd.DataFrame:
    if not SIGNAL_LOG.exists():
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    return pd.read_csv(SIGNAL_LOG)


def _append_log(row: dict[str, Any]) -> None:
    ensure_dirs()
    df = _read_log()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(SIGNAL_LOG, index=False)


def scan_bar_live(bar: str, *, right_edge_only: bool = True) -> list[dict[str, Any]]:
    """Return new threshold signals for one bar (not yet in signal_log)."""
    loaded = load_bar_model(bar)
    if loaded is None:
        return []
    booster, meta = loaded
    thr = float(meta["threshold_val_q90"])
    horizon = int(meta["horizon_bars"])
    frame = fetch_live_ohlc(bar, limit=max(LIVE_LOOKBACK, horizon + 250))
    if len(frame) < 250:
        return []
    enriched = add_indicators(frame)
    idxs = scan_candidates(enriched, horizon_bars=horizon, mode="expanded")
    if not idxs:
        return []
    if right_edge_only:
        # only signal bars in the last few closed candles
        cutoff = len(enriched) - 6
        idxs = [i for i in idxs if i >= cutoff]
    if not idxs:
        return []
    featured = add_features(enriched)
    feats = extract_feature_rows(featured, idxs)
    scores = booster.predict(feats[FEATURE_COLUMNS], num_iteration=int(meta["best_iteration"]))
    existing = _read_log()
    known = set()
    if not existing.empty:
        known = {
            (str(r.bar), str(r.signal_time))
            for r in existing.itertuples()
        }
    bar_td = bar_to_timedelta(bar)
    out: list[dict[str, Any]] = []
    for row_pos, signal_i in enumerate(idxs):
        score = float(scores[row_pos])
        if score < thr:
            continue
        entry_i = signal_i + 1
        if entry_i >= len(enriched):
            continue
        signal_time = pd.Timestamp(enriched["open_time"].iloc[signal_i])
        key = (bar, str(signal_time))
        if key in known:
            continue
        entry_price = float(enriched["open"].iloc[entry_i])
        atr = float(enriched["atr14"].iloc[signal_i])
        tp = entry_price + TP_MULT * atr
        sl = entry_price - SL_MULT * atr
        rec = {
            "source": SOURCE,
            "symbol": SYMBOL,
            "bar": bar,
            "side": "LONG",
            "signal_i": int(signal_i),
            "signal_time": str(signal_time),
            "entry_time": str(pd.Timestamp(enriched["open_time"].iloc[entry_i])),
            "entry_price": entry_price,
            "score": score,
            "threshold": thr,
            "atr14": atr,
            "atr_pct": float(enriched["atr_pct"].iloc[signal_i]),
            "tp_price": tp,
            "sl_price": sl,
            "status": "open",
            "channel": "eth_micro",
        }
        out.append(rec)
    return out


def notify_and_log(records: list[dict[str, Any]], *, dry_run: bool = False) -> int:
    n_ok = 0
    for rec in records:
        # annotate caption via notify_signal (uses entry/atr for TP SL)
        ok = True
        if dry_run:
            from src.notify_signal import format_signal_caption, render_signal_chart

            print(format_signal_caption(rec))
            # live frame may not be on disk — chart uses list_series; skip if missing
            try:
                render_signal_chart(rec)
            except Exception as exc:  # noqa: BLE001
                print(f"chart skip: {exc}")
        else:
            ok = notify_signal(rec, dry_run=False)
        _append_log(
            {
                "source": rec["source"],
                "symbol": rec["symbol"],
                "bar": rec["bar"],
                "signal_time": rec["signal_time"],
                "entry_time": rec["entry_time"],
                "entry_price": rec["entry_price"],
                "score": rec["score"],
                "threshold": rec["threshold"],
                "tp_price": rec.get("tp_price"),
                "sl_price": rec.get("sl_price"),
                "atr14": rec.get("atr14"),
                "status": rec.get("status", "open"),
                "notified_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if ok:
            n_ok += 1
    return n_ok


def run_once(*, dry_run: bool = False, bars: tuple[str, ...] = BARS) -> dict:
    ensure_dirs()
    summary: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "bars": {},
        "new_signals": 0,
        "notified_ok": 0,
    }
    all_new: list[dict] = []
    for bar in bars:
        try:
            news = scan_bar_live(bar)
            summary["bars"][bar] = {"new": len(news), "error": None}
            all_new.extend(news)
        except Exception as exc:  # noqa: BLE001
            summary["bars"][bar] = {"new": 0, "error": str(exc)}
            print(f"[eth_micro] {bar} error: {exc}", flush=True)
    summary["new_signals"] = len(all_new)
    if all_new:
        summary["notified_ok"] = notify_and_log(all_new, dry_run=dry_run)
    STATUS_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def run_loop(interval_sec: int = 60, *, dry_run: bool = False) -> None:
    print(f"[eth_micro] monitor loop interval={interval_sec}s dry_run={dry_run}", flush=True)
    while True:
        try:
            s = run_once(dry_run=dry_run)
            print(
                f"[eth_micro] {s['ts']} new={s['new_signals']} sent={s['notified_ok']} bars={s['bars']}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[eth_micro] loop error: {exc}", flush=True)
        time.sleep(interval_sec)
