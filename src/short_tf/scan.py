"""Tip-only rules scan on 1m/5m majors — no YOLO, no mainline forward_log."""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from src.judgment.candidates import MIN_GAP_BARS, WARMUP_BARS, add_indicators, strict_mask
from src.judgment.forward_types import SL_MULT, TP_MULT
from src.short_tf.config import (
    BARS,
    FRESH_MIN,
    LIVE_LOOKBACK,
    SCORE_TOP_FRAC,
    SIGNAL_COLUMNS,
    SIGNAL_LOG,
    SOURCE,
    STATUS_JSON,
    LATEST_JSON,
    SYMBOLS,
    TIP_BARS,
    ensure_dirs,
)

OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"
HEADERS = {
    "User-Agent": "Mozilla/5.0 fable-short-tf/1.0",
    "Accept": "application/json",
}


def fetch_ohlc(symbol: str, bar: str, limit: int = LIVE_LOOKBACK) -> pd.DataFrame:
    inst = symbol.replace("_", "-")
    rows: list[list] = []
    after = None
    remaining = limit
    while remaining > 0:
        page_lim = min(300, remaining)
        url = f"{OKX_CANDLES}?instId={inst}&bar={bar}&limit={page_lim}"
        if after is not None:
            url += f"&after={after}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX {symbol} {bar}: {payload.get('msg')}")
        page = payload.get("data") or []
        if not page:
            break
        rows.extend(page)
        after = int(page[-1][0])
        remaining -= len(page)
        if len(page) < page_lim:
            break
        time.sleep(0.08)
    if not rows:
        return pd.DataFrame()
    uniq = {int(r[0]): r for r in rows}
    ordered = [uniq[k] for k in sorted(uniq)]
    return pd.DataFrame(
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


def _read_log() -> pd.DataFrame:
    if not SIGNAL_LOG.exists():
        return pd.DataFrame(columns=list(SIGNAL_COLUMNS))
    return pd.read_csv(SIGNAL_LOG)


def _append_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ensure_dirs()
    new = pd.DataFrame(rows)
    if SIGNAL_LOG.exists() and SIGNAL_LOG.stat().st_size > 0:
        df = pd.read_csv(SIGNAL_LOG)
        df = pd.concat([df, new], ignore_index=True)
    else:
        df = new
    if len(df) > 5000:
        df = df.iloc[-5000:]
    df.to_csv(SIGNAL_LOG, index=False)


def scan_candidates_live(enriched: pd.DataFrame, *, mode: str = "expanded") -> list[int]:
    """Like scan_candidates but only requires next-bar entry exists (no full horizon).

    Offline scan_candidates drops bars that lack horizon+1 future rows — that is
    correct for labeling, but **kills every live tip signal**. Live only needs
    entry_i = signal_i+1 to exist on closed candles.
    """
    import numpy as np

    if len(enriched) < WARMUP_BARS + 3:
        return []
    mask = strict_mask(enriched, mode).fillna(False)
    idx = np.flatnonzero(mask.to_numpy())
    idx = idx[(idx >= WARMUP_BARS) & (idx + 1 < len(enriched))]
    if len(idx) == 0:
        return []
    scores = enriched["shape_score"].to_numpy()
    selected: list[int] = []
    for signal_i in sorted(idx, key=lambda i: scores[i], reverse=True):
        if all(abs(int(signal_i) - p) >= MIN_GAP_BARS for p in selected):
            selected.append(int(signal_i))
    return sorted(selected)


def scan_symbol_bar(symbol: str, bar: str) -> list[dict[str, Any]]:
    """Rules dense candidates with signal bar in tip window."""
    frame = fetch_ohlc(symbol, bar)
    if len(frame) < 260:
        return []
    enriched = add_indicators(frame)
    idxs = scan_candidates_live(enriched, mode="expanded")
    if not idxs:
        return []
    tip_n = TIP_BARS.get(bar, 3)
    cutoff = len(enriched) - tip_n
    tip_idxs = [i for i in idxs if i >= cutoff and i + 1 < len(enriched)]
    if not tip_idxs:
        return []

    scores = [float(enriched["shape_score"].iloc[i]) for i in tip_idxs]
    # Rank within this symbol/bar tip batch
    order = np.argsort(scores)[::-1]
    keep_n = max(1, int(np.ceil(len(order) * SCORE_TOP_FRAC)))
    keep = set(int(tip_idxs[j]) for j in order[:keep_n])

    existing = _read_log()
    known: set[tuple[str, str, str]] = set()
    if not existing.empty:
        for r in existing.itertuples():
            known.add((str(r.symbol), str(r.bar), str(r.signal_time)))

    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for signal_i in tip_idxs:
        if signal_i not in keep:
            continue
        entry_i = signal_i + 1
        signal_time = pd.Timestamp(enriched["open_time"].iloc[signal_i])
        if signal_time.tzinfo is None:
            signal_time = signal_time.tz_localize("UTC")
        key = (symbol, bar, str(signal_time))
        if key in known:
            continue
        bar_min = {"1m": 1, "5m": 5}.get(bar, 5)
        # Age from bar *close* (open + duration); open_time alone overstates lag.
        close_ts = signal_time.to_pydatetime() + __import__("datetime").timedelta(minutes=bar_min)
        if close_ts.tzinfo is None:
            from datetime import timezone as _tz

            close_ts = close_ts.replace(tzinfo=_tz.utc)
        lag_min = max(0.0, (now - close_ts).total_seconds() / 60.0)
        max_age = FRESH_MIN.get(bar, 18)
        if lag_min > max_age:
            continue
        entry_price = float(enriched["open"].iloc[entry_i])
        atr = float(enriched["atr14"].iloc[signal_i])
        score = float(enriched["shape_score"].iloc[signal_i])
        thr = float(np.nanpercentile(scores, 65)) if scores else score
        out.append(
            {
                "source": SOURCE,
                "symbol": symbol,
                "bar": bar,
                "side": "LONG",
                "signal_i": int(signal_i),
                "signal_time": str(signal_time),
                "entry_time": str(pd.Timestamp(enriched["open_time"].iloc[entry_i])),
                "entry_price": entry_price,
                "score": score,
                "threshold": thr,
                "atr14": atr,
                "atr_pct": float(enriched["atr_pct"].iloc[signal_i])
                if "atr_pct" in enriched.columns
                else (atr / entry_price if entry_price else 0.0),
                "tp_price": entry_price + TP_MULT * atr,
                "sl_price": entry_price - SL_MULT * atr,
                "lag_min": round(lag_min, 2),
                "status": "open",
                "channel": "short_tf",
            }
        )
    return out


def run_once(
    *,
    symbols: tuple[str, ...] = SYMBOLS,
    bars: tuple[str, ...] = BARS,
    dry_run: bool = False,
    notify: bool = False,
) -> dict[str, Any]:
    """Scan all symbols×bars; log tip-fresh rules hits; optional TG."""
    ensure_dirs()
    summary: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel": "short_tf",
        "bars": list(bars),
        "n_symbols": len(symbols),
        "by_bar": {},
        "new_signals": 0,
        "notified_ok": 0,
        "errors": [],
        "note": "规则 tip 密集 · 与 15m YOLO 主线隔离 · 默认不接实盘 executor",
    }
    all_new: list[dict[str, Any]] = []
    for bar in bars:
        bar_hits = 0
        for sym in symbols:
            try:
                hits = scan_symbol_bar(sym, bar)
                bar_hits += len(hits)
                all_new.extend(hits)
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(f"{sym}/{bar}: {exc}")
                print(f"[short_tf] {sym} {bar}: {exc}", flush=True)
            time.sleep(0.05)
        summary["by_bar"][bar] = {"new": bar_hits}
    summary["new_signals"] = len(all_new)

    log_rows = []
    for rec in all_new:
        log_rows.append(
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
                "atr_pct": rec.get("atr_pct"),
                "lag_min": rec.get("lag_min"),
                "status": "open",
                "notified_at": "",
                "channel": "short_tf",
            }
        )

    if dry_run:
        summary["dry_run"] = True
        summary["preview"] = all_new[:20]
    else:
        if log_rows:
            _append_rows(log_rows)
        if notify and all_new:
            n_ok = 0
            try:
                from src.notify_signal import notify_signal

                for rec in all_new:
                    if notify_signal(rec, dry_run=False):
                        n_ok += 1
                        # stamp last row notified
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(f"notify: {exc}")
            summary["notified_ok"] = n_ok
            if n_ok and SIGNAL_LOG.exists():
                df = _read_log()
                if not df.empty and "notified_at" in df.columns:
                    # mark matching tails
                    now_s = datetime.now(timezone.utc).isoformat()
                    for rec in all_new:
                        m = (
                            (df["symbol"] == rec["symbol"])
                            & (df["bar"] == rec["bar"])
                            & (df["signal_time"] == rec["signal_time"])
                        )
                        df.loc[m, "notified_at"] = now_s
                    df.to_csv(SIGNAL_LOG, index=False)

    STATUS_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_JSON.write_text(
        json.dumps(
            {
                "ts": summary["ts"],
                "new_signals": summary["new_signals"],
                "by_bar": summary["by_bar"],
                "signals": all_new[:50],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return summary


def run_loop(interval_sec: int = 60, *, dry_run: bool = False, notify: bool = False) -> None:
    print(f"[short_tf] loop interval={interval_sec}s dry_run={dry_run} notify={notify}", flush=True)
    while True:
        try:
            s = run_once(dry_run=dry_run, notify=notify)
            print(
                f"[short_tf] new={s['new_signals']} by_bar={s['by_bar']} errors={len(s['errors'])}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[short_tf] loop error: {exc}", flush=True)
        time.sleep(max(15, interval_sec))
