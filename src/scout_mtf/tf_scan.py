"""Per-timeframe scan: dense MA structure + regime vote (no YOLO / no retrain)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.judgment.candidates import EXPANDED_THRESHOLDS, add_indicators, strict_mask
from src.scout_mtf.http import get_json

# Branch timeframes (owner request).
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m")

# How many bars to pull per TF (need ~288 warmup for full indicators; use 320).
BARS_NEEDED = 320

# Composite weights — 15m/30m carry the system; low TF are weak votes (H7 history).
TF_WEIGHTS: dict[str, float] = {
    "1m": 0.10,
    "3m": 0.12,
    "5m": 0.13,
    "15m": 0.35,
    "30m": 0.30,
}


@dataclass
class TfVote:
    bar: str
    ok: bool
    vote: float              # 0..1
    dense: bool
    order_score: float | None
    ma_spread_pct: float | None
    atr_pct: float | None
    above_ema55: bool | None
    close: float | None
    note: str


def fetch_candles(inst_id: str, bar: str, limit: int = BARS_NEEDED) -> pd.DataFrame:
    """Recent candles via public market/candles (newest first from API)."""
    limit = min(max(limit, 50), 300)  # OKX candles max 300
    path = f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"
    payload = get_json(path)
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"candles {inst_id} {bar}: {payload.get('msg')}")
    raw = payload.get("data") or []
    if not raw:
        return pd.DataFrame()
    # API: [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm] newest first
    rows = []
    for r in raw:
        if len(r) > 8 and str(r[8]) == "0":
            continue
        rows.append(
            {
                "ts": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
        )
    df = pd.DataFrame(rows).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def _vote_from_frame(bar: str, frame: pd.DataFrame) -> TfVote:
    if frame is None or len(frame) < 80:
        return TfVote(bar, False, 0.0, False, None, None, None, None, None, "too_few_bars")

    en = add_indicators(frame)
    # Use expanded thresholds — radar wants recall over purity.
    try:
        mask = strict_mask(en, mode="expanded")
    except Exception:
        mask = pd.Series(False, index=en.index)

    i = len(en) - 1
    row = en.iloc[i]
    dense_now = bool(mask.iloc[i]) if i < len(mask) else False
    # recent dense in last 6 bars still counts as "forming / hot"
    recent = bool(mask.iloc[max(0, i - 5) : i + 1].any()) if len(mask) else False
    dense = dense_now or recent

    order = float(row["order_score"]) if pd.notna(row.get("order_score")) else 0.0
    spread = float(row["ma_spread_pct"]) if pd.notna(row.get("ma_spread_pct")) else None
    atr = float(row["atr_pct"]) if pd.notna(row.get("atr_pct")) else None
    close = float(row["close"])
    ema55 = float(row["ema55"]) if pd.notna(row.get("ema55")) else None
    above = bool(close >= ema55) if ema55 is not None else None
    ext = float(row["ext_up"]) if pd.notna(row.get("ext_up")) else 0.0

    # Score components in [0,1]
    parts = []
    parts.append(1.0 if dense else 0.25)
    parts.append(min(1.0, max(0.0, order / 4.0)))
    if spread is not None and np.isfinite(spread):
        # tighter spread → higher
        parts.append(float(np.clip(1.0 - spread / EXPANDED_THRESHOLDS["fast_spread_max"], 0, 1)))
    else:
        parts.append(0.3)
    if above is True:
        parts.append(0.85)
    elif above is False:
        parts.append(0.25)
    else:
        parts.append(0.5)
    # penalize already-extended breakouts
    if ext > 0.01:
        parts.append(0.2)
    elif ext > 0.005:
        parts.append(0.45)
    else:
        parts.append(0.8)
    if atr is not None and np.isfinite(atr):
        # too quiet or insane vol both bad
        if atr < 0.001:
            parts.append(0.3)
        elif atr > 0.04:
            parts.append(0.35)
        else:
            parts.append(0.75)
    else:
        parts.append(0.5)

    vote = float(np.mean(parts))
    note = "dense" if dense else ("order" if order >= 3 else "weak")
    if above is False:
        note += "|below_ema55"
    if ext > 0.01:
        note += "|extended"
    return TfVote(
        bar=bar,
        ok=True,
        vote=round(vote, 4),
        dense=dense,
        order_score=round(order, 2),
        ma_spread_pct=round(spread, 6) if spread is not None and np.isfinite(spread) else None,
        atr_pct=round(atr, 6) if atr is not None and np.isfinite(atr) else None,
        above_ema55=above,
        close=close,
        note=note,
    )


def scan_symbol_all_tf(inst_id: str, bars: tuple[str, ...] = TIMEFRAMES) -> list[TfVote]:
    votes: list[TfVote] = []
    for bar in bars:
        try:
            df = fetch_candles(inst_id, bar, BARS_NEEDED)
            votes.append(_vote_from_frame(bar, df))
        except Exception as exc:  # noqa: BLE001 — one TF fail must not kill symbol
            votes.append(
                TfVote(bar, False, 0.0, False, None, None, None, None, None, f"err:{type(exc).__name__}")
            )
        time_sleep_light()
    return votes


def time_sleep_light() -> None:
    import time

    time.sleep(0.08)


def composite_from_votes(
    votes: list[TfVote],
    *,
    rank_side: str,
    rank: int,
    chg24h_pct: float,
) -> dict[str, Any]:
    """Weighted multi-TF score + grade A/B/C."""
    wsum = 0.0
    vsum = 0.0
    detail = {}
    for v in votes:
        w = TF_WEIGHTS.get(v.bar, 0.1)
        detail[v.bar] = asdict(v)
        if not v.ok:
            continue
        wsum += w
        vsum += w * v.vote
    base = (vsum / wsum) if wsum > 0 else 0.0

    # Rank boost: light — top-3 gainer +0.05, top-3 loser slight penalty on longs
    # major/volume: no momentum boost (structure-only grade for always-on names)
    boost = 0.0
    if rank_side == "gain":
        boost = max(0.0, 0.06 - 0.01 * (rank - 1))
    elif rank_side == "loss":
        # losers: structure can still be good for bounce, but damp momentum chase
        boost = max(-0.04, -0.02 + 0.005 * (rank - 1))
    # major / volume → boost stays 0

    # Alignment bonuses
    tf_map = {v.bar: v for v in votes if v.ok}
    v15 = tf_map.get("15m")
    v30 = tf_map.get("30m")
    v1h_like = tf_map.get("30m")  # no 1h in this branch; 30m is highest
    align = 0.0
    if v15 and v30 and v15.above_ema55 and v30.above_ema55:
        align += 0.04
    if v15 and v15.dense and v30 and v30.dense:
        align += 0.03
    # low-TF all dense while high TF below = chop risk
    lows = [tf_map[b] for b in ("1m", "3m", "5m") if b in tf_map]
    if lows and v30 and v30.above_ema55 is False and sum(1 for x in lows if x.dense) >= 2:
        align -= 0.05

    final = float(np.clip(base + boost + align, 0, 1))

    # Grade
    dense_high = bool(v15 and v15.dense) or bool(v30 and v30.dense)
    bull_high = bool(v15 and v15.above_ema55) and bool(v30 and v30.above_ema55 if v30 else True)
    if final >= 0.68 and dense_high and bull_high:
        grade = "A"
    elif final >= 0.52:
        grade = "B"
    else:
        grade = "C"

    return {
        "composite": round(final, 4),
        "base": round(base, 4),
        "rank_boost": round(boost, 4),
        "align": round(align, 4),
        "grade": grade,
        "tf": detail,
        "weights": TF_WEIGHTS,
        "chg24h_pct": chg24h_pct,
        "rank_side": rank_side,
        "rank": rank,
    }
