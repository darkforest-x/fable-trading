"""Causal 1H MA-sequence facts for the owner-provided COMP July 2026 chart.

Input is the specified OKX 15-minute OHLCV CSV.  A 1H bar is accepted only if
it has exactly the four expected UTC quarter-hour opens.  SMA is a trailing
simple mean; EMA is pandas' recursive EMA (adjust=False); ATR14 is a recursive
Wilder-style EWM of True Range whose initial seed differs from Pine's SMA seed.
With about 10,000 completed 1H bars before this case, that initialization
difference has decayed, but this script is not byte-for-byte Pine parity.
Every feature at timestamp t uses t and prior
completed 1H bars only.  Rows t+1... and forward MA-touch/order results are
explicit outcomes/confirmations, never inputs available at entry t.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "data/kline_fetched/okx_COMP_USDT_SWAP_15m_41912.csv"
OUT = ROOT / "experiments/active/exp-comp-ma-sequence-20260913-v1/results"
TZ = "Asia/Shanghai"
EVENTS = [
    {"id": "long_17_61", "side": "long", "entry": 17.61, "stop": 17.14,
     "entry_open_shanghai": "2026-07-24 00:00:00+08:00"},
    {"id": "long_17_43", "side": "long", "entry": 17.43, "stop": 17.26,
     "entry_open_shanghai": "2026-07-26 12:00:00+08:00"},
    {"id": "short_17_20", "side": "short", "entry": 17.20, "stop": 17.51,
     "entry_open_shanghai": "2026-07-27 16:00:00+08:00"},
]


def load_hourly() -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(INPUT)
    raw["time"] = pd.to_datetime(raw["ts"], unit="ms", utc=True)
    assert raw["time"].is_unique, "duplicate raw bars"
    assert (pd.to_datetime(raw["open_time"], utc=True) == raw["time"]).all()
    raw = raw.sort_values("time").set_index("time")
    expected = raw.index.floor("h")
    rows = []
    partial_hours = 0
    for hour, group in raw.groupby(expected):
        wanted = pd.date_range(hour, periods=4, freq="15min", tz="UTC")
        complete = len(group) == 4 and group.index.equals(wanted)
        if not complete:
            partial_hours += 1
        rows.append({
            "time": hour,
            "open": group["open"].iloc[0], "high": group["high"].max(),
            "low": group["low"].min(), "close": group["close"].iloc[-1],
            "volume": group["volume"].sum(), "quarters": len(group),
            "complete_1h": complete,
        })
    h = pd.DataFrame(rows).set_index("time").sort_index()
    h.loc[~h.complete_1h, ["open", "high", "low", "close", "volume"]] = np.nan
    quality = {
        "raw_rows": int(len(raw)), "raw_start_utc": raw.index.min().isoformat(),
        "raw_end_utc": raw.index.max().isoformat(), "hour_groups": int(len(h)),
        "complete_hour_groups": int(h.complete_1h.sum()),
        "partial_or_gapped_hour_groups": int(partial_hours),
        "entry_warmup_complete_hours_before_first": int(h.loc[:"2026-07-23 16:00:00+00:00", "complete_1h"].sum() - 1),
    }
    return h, quality


def derive(h: pd.DataFrame) -> pd.DataFrame:
    prior_close = h.close.shift(1)
    h["tr"] = pd.concat([h.high - h.low, (h.high - prior_close).abs(), (h.low - prior_close).abs()], axis=1).max(axis=1)
    h["atr14"] = h.tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    h["prev_atr14"] = h.atr14.shift(1)
    for n in (20, 60, 120):
        h[f"sma{n}"] = h.close.rolling(n, min_periods=n).mean()
        h[f"ema{n}"] = h.close.ewm(span=n, adjust=False, min_periods=n).mean()
        h[f"pair{n}"] = (h[f"sma{n}"] + h[f"ema{n}"]) / 2
        h[f"pair{n}_slope3"] = h[f"pair{n}"] - h[f"pair{n}"].shift(3)
        h[f"pair{n}_slope3_atr"] = h[f"pair{n}_slope3"] / h.prev_atr14
    lines = [f"{kind}{n}" for n in (20, 60, 120) for kind in ("sma", "ema")]
    h["line_min"] = h[lines].min(axis=1)
    h["line_max"] = h[lines].max(axis=1)
    h["ropewidth"] = h.line_max - h.line_min
    h["ropewidth_close"] = h.ropewidth / h.close
    h["ropewidth_atr14"] = h.ropewidth / h.atr14
    h["gap_fast_mid"] = h.pair20 - h.pair60
    h["gap_mid_slow"] = h.pair60 - h.pair120
    h["gap_fast_mid_atr"] = h.gap_fast_mid / h.prev_atr14
    h["gap_mid_slow_atr"] = h.gap_mid_slow / h.prev_atr14
    pairs = ((20, 60), (20, 120), (60, 120))
    bull = sum((h[f"{a}{x}"] > h[f"{b}{y}"]).astype("Int64")
               for x, y in pairs for a in ("sma", "ema") for b in ("sma", "ema"))
    bear = sum((h[f"{a}{x}"] < h[f"{b}{y}"]).astype("Int64")
               for x, y in pairs for a in ("sma", "ema") for b in ("sma", "ema"))
    h["bull_order_score_12"] = bull.where(h[lines].notna().all(axis=1))
    h["bear_order_score_12"] = bear.where(h[lines].notna().all(axis=1))
    h["body_spans_six"] = (h[["open", "close"]].min(axis=1) <= h.line_min) & (h[["open", "close"]].max(axis=1) >= h.line_max)
    h["body_cross_up_six"] = (h.open <= h.line_min) & (h.close >= h.line_max)
    h["body_cross_down_six"] = (h.open >= h.line_max) & (h.close <= h.line_min)
    # Owner's 'stands above/below' definition: the body remains entirely
    # outside the six-line envelope. Wicks are intentionally ignored.
    h["body_above_six"] = h[["open", "close"]].min(axis=1) > h.line_max
    h["body_below_six"] = h[["open", "close"]].max(axis=1) < h.line_min
    h["relvolume_prior20"] = h.volume / h.volume.shift(1).rolling(20, min_periods=20).mean()
    h["tr_prevatr"] = h.tr / h.prev_atr14
    h["prior12_high"] = h.high.shift(1).rolling(12, min_periods=12).max()
    h["prior12_low"] = h.low.shift(1).rolling(12, min_periods=12).min()
    h["breakout_up_prior12"] = h.close > h.prior12_high
    h["breakout_down_prior12"] = h.close < h.prior12_low
    return h


def event_rows(h: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    fields = [
        "open", "high", "low", "close", "volume", "atr14", "prev_atr14", "tr", "tr_prevatr",
        "ropewidth", "ropewidth_close", "ropewidth_atr14", "bull_order_score_12", "bear_order_score_12",
        "pair20_slope3", "pair60_slope3", "pair120_slope3", "pair20_slope3_atr", "pair60_slope3_atr", "pair120_slope3_atr",
        "gap_fast_mid", "gap_mid_slow", "gap_fast_mid_atr", "gap_mid_slow_atr", "body_spans_six", "body_cross_up_six", "body_cross_down_six", "body_above_six", "body_below_six",
        "relvolume_prior20", "prior12_high", "prior12_low", "breakout_up_prior12", "breakout_down_prior12",
    ]
    snapshots, summaries, json_events = [], [], []
    for event in EVENTS:
        t = pd.Timestamp(event["entry_open_shanghai"]).tz_convert("UTC")
        if t not in h.index or not bool(h.loc[t, "complete_1h"]):
            raise ValueError(f"Entry hour missing or incomplete: {event['id']} {t}")
        anchor_atr = h.loc[t, "atr14"]
        for offset in (0, 1, 2, 3, 6):
            u = t + pd.Timedelta(hours=offset)
            row = h.loc[u, fields].copy()
            row["event_id"] = event["id"]
            row["side"] = event["side"]
            row["offset_hours"] = offset
            row["time_utc"] = u.isoformat()
            row["time_shanghai"] = u.tz_convert(TZ).isoformat()
            row["closed_at_shanghai"] = (u + pd.Timedelta(hours=1)).tz_convert(TZ).isoformat()
            row["ropewidth_anchor_atr"] = row["ropewidth"] / anchor_atr
            row["available_at_entry"] = offset == 0
            snapshots.append(row)
        directional = 1 if event["side"] == "long" else -1
        score = "bull_order_score_12" if directional == 1 else "bear_order_score_12"
        forward = h.loc[t:, ["close", score]].copy()
        full = forward[forward[score] == 12]
        first = full.index[0] if len(full) else pd.NaT
        first_close = float(h.loc[first, "close"]) if pd.notna(first) else np.nan
        risk = abs(event["entry"] - event["stop"])
        touch_window = h.loc[t + pd.Timedelta(hours=1): t + pd.Timedelta(hours=6)]
        touches = (touch_window.low <= touch_window.line_max) & (touch_window.high >= touch_window.line_min)
        def run_length(score_name: str) -> int:
            """Number of contiguous score==12 bars ending at t, with no lookahead."""
            count = 0
            for value in h.loc[:t, score_name].iloc[::-1]:
                if value != 12:
                    break
                count += 1
            return count

        bull_run = run_length("bull_order_score_12")
        bear_run = run_length("bear_order_score_12")
        entry = {
            **event, "entry_open_utc": t.isoformat(), "anchor_atr14": anchor_atr,
            "first_full_cross_period_order_utc": first.isoformat() if pd.notna(first) else None,
            "first_full_cross_period_order_shanghai": first.tz_convert(TZ).isoformat() if pd.notna(first) else None,
            "order_delay_hours": int((first - t) / pd.Timedelta(hours=1)) if pd.notna(first) else None,
            "first_order_close": first_close,
            "directional_price_cost": directional * (first_close - event["entry"]) if pd.notna(first) else None,
            "directional_price_cost_R": directional * (first_close - event["entry"]) / risk if pd.notna(first) else None,
            "bull12_run_bars_ending_entry": bull_run,
            "bear12_run_bars_ending_entry": bear_run,
            "entry_direction_full_order_run_bars": bull_run if directional == 1 else bear_run,
            "touches_six_ma_envelope_next_6h": bool(touches.any()),
            "first_touch_six_ma_envelope_next_6h_utc": touches.index[touches.argmax()].isoformat() if touches.any() else None,
        }
        summaries.append(entry)
        json_events.append(entry)
    snap = pd.DataFrame(snapshots)
    # First columns are plotting keys, then numerical/boolean facts.
    snap = snap[["event_id", "side", "offset_hours", "time_utc", "time_shanghai", "closed_at_shanghai", "available_at_entry"] + fields + ["ropewidth_anchor_atr"]]
    return snap, pd.DataFrame(summaries), json_events


def width_diagnostics(h: pd.DataFrame) -> pd.DataFrame:
    """Pre-entry only width context; windows end at the hour before each entry."""
    rows = []
    for event in EVENTS:
        t = pd.Timestamp(event["entry_open_shanghai"]).tz_convert("UTC")
        prior12 = h.loc[t - pd.Timedelta(hours=12): t - pd.Timedelta(hours=1), "ropewidth"]
        prior120 = h.loc[t - pd.Timedelta(hours=120): t - pd.Timedelta(hours=1), "ropewidth"]
        rows.append({
            "event_id": event["id"], "side": event["side"], "entry_open_shanghai": event["entry_open_shanghai"],
            "prior12_ropewidth_min": prior12.min(), "prior12_ropewidth_mean": prior12.mean(),
            "prior120_ropewidth_min": prior120.min(), "prior120_ropewidth_mean": prior120.mean(),
            "prior12_count": len(prior12), "prior120_count": len(prior120),
        })
    return pd.DataFrame(rows)


def main() -> None:
    h, quality = load_hourly()
    raw_hourly = h.copy()
    h = derive(h)
    context = h.loc["2026-07-01":"2026-08-04"]
    assert context.complete_1h.all()
    assert (context.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()
    # A full-series computation must equal the prefix-only entry features.
    for event in EVENTS:
        t = pd.Timestamp(event["entry_open_shanghai"]).tz_convert("UTC")
        prefix = derive(raw_hourly.loc[:t].copy())
        pd.testing.assert_series_equal(h.loc[t], prefix.loc[t], check_exact=True)
        assert abs(h.loc[t, "close"] - event["entry"]) < 1e-10
    quality["prefix_invariance_checks"] = len(EVENTS)
    quality["source_sha256"] = hashlib.sha256(INPUT.read_bytes()).hexdigest()
    quality["builder_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    snapshots, summaries, json_events = event_rows(h)
    width = width_diagnostics(h)
    snapshots.to_csv(OUT / "case_snapshots_t_t1_t2_t3_t6.csv", index=False, float_format="%.10g")
    summaries.to_csv(OUT / "entry_and_order_delay.csv", index=False, float_format="%.10g")
    width.to_csv(OUT / "pre_entry_width_diagnostics.csv", index=False, float_format="%.10g")
    # Complete causal context for the plotted known-outcome case.
    sequence = h.loc["2026-07-22 00:00:00+00:00":"2026-08-04 00:00:00+00:00"].copy()
    sequence.insert(0, "time_shanghai", sequence.index.tz_convert(TZ).astype(str))
    sequence.insert(0, "time_utc", sequence.index.astype(str))
    sequence.insert(2, "closed_at_shanghai", (sequence.index + pd.Timedelta(hours=1)).tz_convert(TZ).astype(str))
    sequence_cols = [
        "time_utc", "time_shanghai", "closed_at_shanghai", "complete_1h", "quarters", "open", "high", "low", "close", "volume",
        "tr", "atr14", "prev_atr14", "tr_prevatr",
        "sma20", "ema20", "sma60", "ema60", "sma120", "ema120", "line_min", "line_max",
        "ropewidth", "ropewidth_close", "ropewidth_atr14",
        "pair20", "pair60", "pair120", "pair20_slope3", "pair60_slope3", "pair120_slope3",
        "pair20_slope3_atr", "pair60_slope3_atr", "pair120_slope3_atr",
        "gap_fast_mid", "gap_mid_slow", "gap_fast_mid_atr", "gap_mid_slow_atr",
        "bull_order_score_12", "bear_order_score_12", "body_spans_six", "body_cross_up_six", "body_cross_down_six", "body_above_six", "body_below_six",
        "relvolume_prior20", "prior12_high", "prior12_low", "breakout_up_prior12", "breakout_down_prior12",
    ]
    sequence[sequence_cols].to_csv(OUT / "comp_1h_causal_features_20260722_20260804.csv", index=False, float_format="%.10g")
    # A plotting surface after 30 July: no threshold is imposed to label a 'first' recompaction.
    view = h.loc["2026-07-30 00:00:00+00:00":"2026-08-04 00:00:00+00:00"].copy()
    view.insert(0, "time_shanghai", view.index.tz_convert(TZ).astype(str))
    view.insert(0, "time_utc", view.index.astype(str))
    view.insert(2, "closed_at_shanghai", (view.index + pd.Timedelta(hours=1)).tz_convert(TZ).astype(str))
    cols = ["time_utc", "time_shanghai", "closed_at_shanghai", "open", "high", "low", "close", "volume", "atr14", "ropewidth", "ropewidth_close", "ropewidth_atr14", "bull_order_score_12", "bear_order_score_12", "gap_fast_mid", "gap_mid_slow", "pair20_slope3", "pair60_slope3", "pair120_slope3", "body_spans_six", "body_cross_up_six", "body_cross_down_six", "body_above_six", "body_below_six", "relvolume_prior20", "tr_prevatr", "breakout_up_prior12", "breakout_down_prior12"]
    view[cols].to_csv(OUT / "post_jul30_all_hourly_compaction_surface.csv", index=False, float_format="%.10g")
    with (OUT / "case_metadata.json").open("w") as f:
        json.dump({"data_quality": quality, "events": json_events,
                   "files": {"complete_sequence": "comp_1h_causal_features_20260722_20260804.csv", "snapshots": "case_snapshots_t_t1_t2_t3_t6.csv", "entry_confirmation": "entry_and_order_delay.csv", "pre_entry_width": "pre_entry_width_diagnostics.csv", "post_jul30_surface": "post_jul30_all_hourly_compaction_surface.csv"}, "definitions": {
            "time": "source ts and aggregation are UTC; time_shanghai is UTC+08:00 bar opening time",
            "ropewidth": "max(SMA/EMA20,60,120) - min(SMA/EMA20,60,120)",
            "ropewidth_anchor_atr": "each snapshot's ropewidth divided by ATR14 fixed at that event's t; descriptive normalization, not a model input",
            "ordering": "12 cross-period SMA/EMA comparisons only: 20>60, 20>120, 60>120 for bullish; reverse for bearish; internal SMA-vs-EMA pairs excluded",
            "body_spans_six": "legacy retained field: min(open,close) <= line_min and max(open,close) >= line_max, so one body spans/crosses the envelope; wick is ignored",
            "body_above_six": "owner definition: min(open,close) > line_max, so the whole body closes/opens above every MA; wick is allowed",
            "body_below_six": "owner definition: max(open,close) < line_min, so the whole body closes/opens below every MA; wick is allowed",
            "six_ma_touch_outcome": "future high >= future line_min AND future low <= future line_max; reported only after that future bar closes",
            "relvolume_prior20": "raw contract-volume divided by prior 20 completed 1H raw-volume mean; it is not USDT turnover",
            "prior12_breakout": "close outside maximum high/minimum low of preceding 12 completed 1H bars",
            "atr_initialization": "recursive EWM begins from the first True Range, unlike Pine's SMA seed; long pre-case warmup makes this non-parity initialization difference negligible for the case, but no byte-level Pine parity is claimed",
        }}, f, indent=2)
    print(json.dumps({"quality": quality, "events": json_events}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    INPUT, OUT = args.input, args.output
    OUT.mkdir(parents=True, exist_ok=True)
    main()
