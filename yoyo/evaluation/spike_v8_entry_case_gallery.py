"""Frozen V8 validation case gallery with causal same-stream loser comparators.

This is an explanatory renderer, never an entry filter.  It selects one
maximum-net-R scoring-closed validation trade for each 30m/1H/4H and side,
then a non-reused loser from the same stream, UTC month, side and exact
pre-signal ATR/close bucket.  A separately labelled exploratory nearest-bucket
comparator may be shown only when that primary match is absent. Figures render authenticated Python cache bars, rather than
TradingView screenshots; values after confirmation are visibly separated as
outcomes and are not presented as entry features.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_exit_policy_study import load_verified_stream

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-v8-entry-evidence-20260914-v1"
CASES = EXP / "cases"
EVIDENCE = ROOT / "experiments/active/exp-spike-v8-entry-process-20260913-v1/results/full_v1/same_entry_evidence.csv.gz"
EVENT_STATES = ROOT / "experiments/active/exp-spike-v8-ma-cycle-20260913-v1/results/full_v1/event_states.csv.gz"
RAW = ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3"
REPLAY = ROOT / "experiments/active/exp-spike-v8-noise-filter-20260913-v1/replay_v1"
EVIDENCE_SHA = "9c904b2d21a348dbbcca4b6276aefad599f57518508ff3e7ae08e1596db8b4c9"
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
GRID = ((30, 1), (30, -1), (60, 1), (60, -1), (240, 1), (240, -1))
MA = ("s20", "e20", "s60", "e60", "s120", "e120")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: object) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC") if not isinstance(value, pd.Timestamp) else value.tz_convert("UTC")


def _closed_validation() -> pd.DataFrame:
    if sha256(EVIDENCE) != EVIDENCE_SHA:
        raise ValueError("frozen same-entry evidence hash mismatch")
    evidence = pd.read_csv(EVIDENCE)
    state = pd.read_csv(EVENT_STATES)
    for table in (evidence, state):
        table["signal_bar_open"] = pd.to_datetime(table.signal_bar_open, utc=True)
        table["entry_time"] = pd.to_datetime(table.entry_time, utc=True)
    keys = ["stream_key", "signal_bar_open", "side", "trade_id"]
    fields = keys + ["period", "calendar_month", "causal_volatility_bucket", "entry_price", "initial_stop", "initial_risk",
                     "state", "state_age", "slope_votes", "rope_distance_atr"]
    state = state.loc[:, [x for x in fields if x in state]].copy()
    joined = evidence.merge(state, on=keys, how="left", validate="one_to_one", suffixes=("", "_state"))
    if joined.causal_volatility_bucket.isna().any():
        raise ValueError("state join missing causal volatility bucket")
    out = joined.loc[joined.scoring_closed.astype(bool) & joined.entry_time.ge(SPLIT)].copy()
    if len(out) != 57607:
        raise ValueError(f"unexpected validation scoring population: {len(out)}")
    out["utc_month"] = out.entry_time.dt.strftime("%Y-%m")
    return out


def select_cases(events: pd.DataFrame) -> pd.DataFrame:
    """Choose fixed primary matches; optional relaxed rows cannot replace them."""
    selected: list[dict[str, object]] = []
    used_controls: set[str] = set()
    for minutes, side in GRID:
        pool = events.loc[(events.timeframe_min.eq(minutes)) & (events.side.eq(side))].copy()
        if pool.empty:
            raise ValueError(f"missing validation cell {minutes}/{side}")
        winner = pool.sort_values(["net_r", "entry_time", "trade_id"], ascending=[False, True, True]).iloc[0]
        base = pool.loc[(pool.stream_key.eq(winner.stream_key)) & (pool.utc_month.eq(winner.utc_month)) & pool.net_r.lt(0)].copy()
        exact = base.loc[base.causal_volatility_bucket.eq(winner.causal_volatility_bucket) & ~base.trade_id.isin(used_controls)]
        if len(exact):
            delta = (exact.entry_time - winner.entry_time).abs()
            control = exact.assign(_delta=delta).sort_values(["_delta", "entry_time", "trade_id"]).iloc[0]
            used_controls.add(str(control.trade_id))
            row = {"case_id": f"{minutes}m_{'long' if side == 1 else 'short'}", "timeframe_min": minutes, "side": side,
                   "winner_trade_id": winner.trade_id, "loser_trade_id": control.trade_id,
                   "match_status": "matched_exact_stream_month_side_causal_volatility_bucket",
                   "match_bucket_distance": 0, "match_time_distance_seconds": float(control._delta.total_seconds())}
        else:
            row = {"case_id": f"{minutes}m_{'long' if side == 1 else 'short'}", "timeframe_min": minutes, "side": side,
                   "winner_trade_id": winner.trade_id, "loser_trade_id": pd.NA,
                   "match_status": "unmatched_no_unused_loser_in_exact_stream_month_side_causal_volatility_bucket",
                   "match_bucket_distance": math.nan, "match_time_distance_seconds": math.nan}
        for prefix, values in (("winner", winner), *(([("loser", control)] if len(exact) else []))):
            for key in ("stream_key", "venue", "asset", "symbol", "signal_bar_open", "signal_confirm_time", "entry_time", "exit_time", "exit_reason",
                        "net_r", "mfe_r", "net_return", "gross_r", "rope_distance_atr", "causal_volatility_bucket", "state",
                        "state_age", "slope_votes", "entry_price", "initial_stop", "initial_risk", "signal_i_local", "holding_bars"):
                row[f"{prefix}_{key}"] = values.get(key)
        # This relaxed diagnostic never fills loser_* or match_status. It is
        # visible only as exploratory in the output and chart caption.
        relaxed = base.loc[~base.trade_id.isin(used_controls) & base.causal_volatility_bucket.ge(0)].copy()
        if len(relaxed) and int(winner.causal_volatility_bucket) >= 0:
            delta = (relaxed.entry_time - winner.entry_time).abs()
            bucket_delta = (relaxed.causal_volatility_bucket - winner.causal_volatility_bucket).abs()
            exploratory = relaxed.assign(_bucket_delta=bucket_delta, _delta=delta).sort_values(["_bucket_delta", "_delta", "entry_time", "trade_id"]).iloc[0]
            row["exploratory_relaxed_trade_id"] = exploratory.trade_id
            row["exploratory_relaxed_bucket_distance"] = int(exploratory._bucket_delta)
            row["exploratory_relaxed_time_distance_seconds"] = float(exploratory._delta.total_seconds())
            for key in ("stream_key", "venue", "asset", "symbol", "signal_bar_open", "signal_confirm_time", "entry_time", "exit_time", "exit_reason",
                        "net_r", "mfe_r", "net_return", "gross_r", "rope_distance_atr", "causal_volatility_bucket", "state",
                        "state_age", "slope_votes", "entry_price", "initial_stop", "initial_risk", "signal_i_local", "holding_bars"):
                row[f"exploratory_relaxed_{key}"] = exploratory.get(key)
        selected.append(row)
    result = pd.DataFrame(selected)
    # All six cells may lack an exact loser.  Retain a stable nullable schema
    # so the renderer treats that outcome as a single-panel case, rather than
    # treating an absent column as a data-dependent implementation failure.
    fields = ("stream_key", "venue", "asset", "symbol", "signal_bar_open", "signal_confirm_time", "entry_time", "exit_time", "exit_reason",
              "net_r", "mfe_r", "net_return", "gross_r", "rope_distance_atr", "causal_volatility_bucket", "state",
              "state_age", "slope_votes", "entry_price", "initial_stop", "initial_risk", "signal_i_local", "holding_bars")
    for prefix in ("loser", "exploratory_relaxed"):
        for field in fields:
            column = f"{prefix}_{field}"
            if column not in result:
                result[column] = pd.NA
    return result


def _trade_geometry(case: pd.Series, prefix: str, context) -> dict[str, object]:
    trade_id = case.get(f"{prefix}_trade_id")
    if pd.isna(trade_id):
        return {}
    receipt = REPLAY / "streams" / f"{context.key}.json"
    receipt_data = json.loads(receipt.read_text())
    path = REPLAY / "streams" / f"{context.key}.trades.csv.gz"
    if sha256(path) != receipt_data["files"][path.name]:
        raise ValueError(f"V8 trade receipt mismatch: {context.key}")
    trades = pd.read_csv(path)
    trade = trades.loc[(trades.arm.eq("v8")) & trades.trade_id.eq(trade_id)]
    if len(trade) != 1:
        raise ValueError(f"missing/nonunique V8 trade: {trade_id}")
    row = trade.iloc[0]
    for name in ("entry_price", "initial_stop", "initial_risk"):
        expected = float(case[f"{prefix}_{name}"])
        if not math.isclose(float(row[name]), expected, rel_tol=0, abs_tol=1e-10):
            raise ValueError(f"frozen geometry mismatch {trade_id}: {name}")
    signal_open = _utc(case[f"{prefix}_signal_bar_open"])
    expected_confirmation = _utc(case[f"{prefix}_signal_confirm_time"])
    confirmation = signal_open + pd.Timedelta(minutes=context.minutes)
    signal_i = context.cache["bars"].index.get_indexer([signal_open])[0]
    if signal_i < 0 or confirmation != expected_confirmation:
        raise ValueError(f"frozen confirmation clock mismatch: {trade_id}")
    entry_time, exit_time = _utc(row.entry_time), _utc(row.exit_time)
    entry_i = context.cache["bars"].index.get_indexer([entry_time])[0]
    exit_i = context.cache["bars"].index.get_indexer([exit_time])[0]
    if entry_i < 0 or exit_i < 0:
        raise ValueError(f"frozen execution time absent from authenticated cache: {trade_id}")
    if entry_time != _utc(case[f"{prefix}_entry_time"]) or exit_time != _utc(case[f"{prefix}_exit_time"]):
        raise ValueError(f"frozen execution clock mismatch: {trade_id}")
    return {"trade_receipt_sha256": sha256(receipt), "trade_file_sha256": sha256(path),
            "signal_i": int(signal_i), "signal_bar_open": str(signal_open), "signal_confirm_time": str(confirmation),
            "entry_i": int(entry_i), "exit_i": int(exit_i), "entry_i_global": int(row.entry_i), "exit_i_global": int(row.exit_i),
            "entry_time": str(entry_time), "exit_time": str(exit_time), "entry_price": float(row.entry_price),
            "initial_stop": float(row.initial_stop), "initial_risk": float(row.initial_risk), "exit_price": float(row.exit_price),
            "exit_reason": str(row.exit_reason), "net_r": float(row.net_r), "mfe_r": float(row.mfe_r)}


def _anomaly(context, geometry: dict[str, object]) -> dict[str, object]:
    bars = context.cache["bars"]
    entry_i, exit_i = int(geometry["entry_i"]), int(geometry["exit_i"])
    entry_bar = bars.iloc[entry_i]
    side = 1 if geometry["entry_price"] > geometry["initial_stop"] else -1
    signal_close = float(bars.close.iloc[entry_i - 1])
    gap_r = side * (float(geometry["entry_price"]) - signal_close) / float(geometry["initial_risk"])
    window = bars.iloc[entry_i:exit_i + 1]
    values = window[["open", "high", "low", "close"]].to_numpy(float)
    ohlc_valid = np.isfinite(values).all(axis=1) & (values[:,2] > 0) & (values[:,1] >= np.maximum(values[:,0], values[:,3])) & (values[:,2] <= np.minimum(values[:,0], values[:,3]))
    expected_step = pd.Timedelta(minutes=context.minutes).value
    gaps = int((np.diff(window.index.asi8) != expected_step).sum()) if len(window) > 1 else 0
    prior_close = bars.close.iloc[max(0, entry_i-1):exit_i].to_numpy(float)
    opens = bars.open.iloc[entry_i:exit_i+1].to_numpy(float)
    jump_r = np.abs(opens - prior_close) / float(geometry["initial_risk"])
    exit_bar = bars.iloc[exit_i]
    return {"entry_open_inside_ohlc": bool(float(entry_bar.low) <= geometry["entry_price"] <= float(entry_bar.high)),
            "entry_gap_from_signal_close_r": gap_r, "unusually_large_entry_gap_abs_gt_3r": bool(abs(gap_r) > 3),
            "exit_before_entry": bool(exit_i < entry_i), "cache_ohlc_valid_entry": bool(entry_bar.low <= min(entry_bar.open, entry_bar.close) <= entry_bar.high),
            "side_from_geometry": side, "holding_window_bars": int(len(window)), "holding_window_invalid_ohlc_bars": int((~ohlc_valid).sum()),
            "holding_window_timestamp_gaps": gaps, "holding_window_max_open_jump_r": float(np.nanmax(jump_r)),
            "holding_window_unusual_open_jump_abs_gt_3r": bool(np.nanmax(jump_r) > 3),
            "exit_price_inside_exit_ohlc": bool(float(exit_bar.low) <= geometry["exit_price"] <= float(exit_bar.high)),
            "stop_like_exit": bool("stop" in geometry["exit_reason"]),
            "stop_like_fill_outside_exit_ohlc": bool("stop" in geometry["exit_reason"] and not (float(exit_bar.low) <= geometry["exit_price"] <= float(exit_bar.high)))}


def _candles(ax, bars: pd.DataFrame, start: int, end: int) -> None:
    x = mdates.date2num(bars.index[start:end].to_pydatetime())
    width = (x[1] - x[0]) * .65 if len(x) > 1 else .02
    for stamp, row in zip(x, bars.iloc[start:end].itertuples()):
        color = "#1b9e77" if row.close >= row.open else "#d95f02"
        ax.vlines(stamp, row.low, row.high, color=color, linewidth=.45, alpha=.7)
        ax.add_patch(plt.Rectangle((stamp - width / 2, min(row.open, row.close)), width, max(abs(row.close-row.open), 1e-12), color=color, alpha=.65))


def _plot_trade(ax_full, ax_zoom, context, geom: dict[str, object], title: str, minutes: int) -> None:
    bars = context.cache["bars"]
    entry_i, exit_i = int(geom["entry_i"]), int(geom["exit_i"])
    start, end = max(0, entry_i - 60), min(len(bars), exit_i + 13)
    x = bars.index[start:end]
    ax_full.plot(x, bars.close.iloc[start:end], color="#111111", linewidth=.8, label="close")
    colors = ("#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628", "#f781bf")
    for column, color in zip(MA, colors):
        ax_full.plot(x, bars[column].iloc[start:end], color=color, linewidth=.5, alpha=.8, label=column)
    close = bars.close; basis = close.rolling(200, min_periods=200).mean(); std = close.rolling(200, min_periods=200).std(ddof=0)
    ax_full.plot(x, (basis+2*std).iloc[start:end], color="#777777", linewidth=.45, linestyle="--", label="BB200 ±2σ")
    ax_full.plot(x, (basis-2*std).iloc[start:end], color="#777777", linewidth=.45, linestyle="--")
    signal_i = int(geom["signal_i"])
    confirm_time = _utc(geom["signal_confirm_time"])
    ax_full.axvline(confirm_time, color="#6a3d9a", linewidth=.8, label="confirmation close")
    ax_full.scatter([confirm_time], [float(bars.close.iloc[signal_i])], marker="o", s=15, color="#6a3d9a", zorder=5)
    ax_full.axvline(bars.index[entry_i], color="#1b9e77", linewidth=.8, label="next-open entry")
    ax_full.scatter([bars.index[entry_i]], [geom["entry_price"]], marker="^", s=18, color="#1b9e77", zorder=5)
    ax_full.axvline(bars.index[exit_i], color="#d95f02", linewidth=.8, label="exit")
    ax_full.axhline(geom["entry_price"], color="#1b9e77", linewidth=.55); ax_full.axhline(geom["initial_stop"], color="#e41a1c", linewidth=.55, linestyle=":")
    ax_full.axvspan(bars.index[entry_i], bars.index[min(end-1, exit_i+12)], color="#eeeeee", alpha=.18, label="outcome window")
    ax_full.set_title(title, fontsize=8); ax_full.grid(alpha=.15); ax_full.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    z0,z1=max(0,entry_i-60),min(len(bars),entry_i+80)
    _candles(ax_zoom,bars,z0,z1)
    for column, color in zip(MA, colors): ax_zoom.plot(bars.index[z0:z1],bars[column].iloc[z0:z1],color=color,linewidth=.55)
    ax_zoom.plot(bars.index[z0:z1], (basis+2*std).iloc[z0:z1], color="#777777", linewidth=.45, linestyle="--")
    ax_zoom.plot(bars.index[z0:z1], (basis-2*std).iloc[z0:z1], color="#777777", linewidth=.45, linestyle="--")
    ax_zoom.axvline(confirm_time,color="#6a3d9a",linewidth=.8); ax_zoom.scatter([confirm_time],[float(bars.close.iloc[signal_i])],marker="o",s=15,color="#6a3d9a",zorder=5)
    ax_zoom.axvline(bars.index[entry_i],color="#1b9e77",linewidth=.8); ax_zoom.scatter([bars.index[entry_i]],[geom["entry_price"]],marker="^",s=18,color="#1b9e77",zorder=5)
    ax_zoom.axhline(geom["entry_price"],color="#1b9e77",linewidth=.55); ax_zoom.axhline(geom["initial_stop"],color="#e41a1c",linewidth=.55,linestyle=":")
    ax_zoom.axvspan(bars.index[entry_i],bars.index[z1-1],color="#eeeeee",alpha=.15); ax_zoom.set_title("entry vicinity: purple=confirmation, green=entry",fontsize=7);ax_zoom.grid(alpha=.15);ax_zoom.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))


def _render_case(case: pd.Series, contexts: dict[str, object], geometries: dict[tuple[str, str], dict[str, object]], output: Path) -> None:
    subjects = [("winner", "winner")]
    if pd.notna(case.loser_trade_id):
        subjects.append(("loser", "primary exact-bucket matched loser"))
    elif pd.notna(case.get("exploratory_relaxed_trade_id")):
        subjects.append(("exploratory_relaxed", "EXPLORATORY nearest-bucket loser; not the primary match"))
    fig, axes = plt.subplots(len(subjects), 2, figsize=(14, 4.2*len(subjects)), constrained_layout=True)
    axes = np.atleast_2d(axes)
    for row, (prefix, role) in enumerate(subjects):
        context, geom = contexts[str(case[f"{prefix}_stream_key"])], geometries[(case.case_id, prefix)]
        label = f"{role}: {case[f'{prefix}_asset']} {case.timeframe_min}m {'long' if case.side == 1 else 'short'} | netR={geom['net_r']:.3f}, MFE={geom['mfe_r']:.3f}"
        _plot_trade(axes[row,0], axes[row,1], context, geom, label, int(case.timeframe_min))
    handles, labels = axes[0,0].get_legend_handles_labels(); fig.legend(handles,labels,loc="upper center",ncol=6,fontsize=7)
    fig.savefig(output / f"{case.case_id}_winner_loser.png", dpi=170); plt.close(fig)


def run(output: Path) -> None:
    """Produce the six fixed comparisons without scanning unrelated cache streams."""
    if output.exists(): raise FileExistsError(output)
    events = _closed_validation(); cases = select_cases(events)
    keys = sorted(set(cases.winner_stream_key.dropna()) | set(cases.loser_stream_key.dropna()))
    if len(keys) > 12: raise ValueError("case selection exceeded the 12-stream cap")
    contexts = {key: load_verified_stream(RAW / "streams" / key) for key in keys}
    geometries: dict[tuple[str, str], dict[str, object]] = {}; facts: list[dict[str, object]] = []
    for _, case in cases.iterrows():
        for prefix in ("winner", "loser", "exploratory_relaxed"):
            if pd.isna(case.get(f"{prefix}_trade_id")): continue
            context = contexts[str(case[f"{prefix}_stream_key"])]
            geom = _trade_geometry(case, prefix, context); geometries[(case.case_id, prefix)] = geom
            facts.append({"case_id": case.case_id, "role": prefix, "stream_key": context.key, **geom, **_anomaly(context, geom),
                          "entry_known_fields": "rope_distance_atr, causal_volatility_bucket, state/state_age/slope_votes, entry price and frozen stop",
                          "outcome_only_fields": "exit price/reason, netR and MFE; none is an entry feature"})
    output.mkdir(parents=True)
    cases.to_csv(output / "fixed_case_selection.csv", index=False)
    pd.DataFrame(facts).to_csv(output / "case_facts.csv", index=False)
    for _, case in cases.iterrows(): _render_case(case, contexts, geometries, output)
    notes = ["# V8 entry case gallery", "", "This is a fixed explanatory sample, not a threshold search or full-pool performance claim.",
             "Six winners are the maximum netR scoring-closed V8 trades in the reused validation segment, one per 30m/1H/4H × long/short cell.",
             "Primary losers use the same stream (therefore venue/asset/timeframe), UTC month, direction and exact causal pre-signal volatility bucket. Missing primary controls remain missing. A nearest-bucket row, where present, is explicitly exploratory and never replaces the primary match.",
             "The global winner selection does not deduplicate assets; any repeated asset is intentional and stated in fixed_case_selection.csv.",
             "Figures are rendered from authenticated Python frozen price caches, not TradingView screenshots. Purple/green/orange marks are confirmation/entry/exit; grey shading is the future outcome window.",
             "NetR and MFE are outcomes. MA arrangement, BB200 and state fields shown at confirmation are descriptive case facts only; later movement is not an entry feature.",
             "Holdout-era use: owner-authorized explanatory gallery configuration #1 on reused nonblind history; no model, production or live rule changed."]
    (output / "notes.md").write_text("\n".join(notes)+"\n")
    lines = ["# Per-case facts", "", "Values listed as outcomes were not known at entry.  The holding-window checks are integrity checks, not a trade-quality filter.", ""]
    for fact in facts:
        lines.extend([f"## {fact['case_id']} · {fact['role']}", "",
                      f"- At confirmation: entry={fact['entry_price']}, initial stop={fact['initial_stop']}, initial risk={fact['initial_risk']}.",
                      f"- Outcome only: exit={fact['exit_price']} ({fact['exit_reason']}), netR={fact['net_r']:.6f}, MFE={fact['mfe_r']:.6f}.",
                      f"- Integrity: invalid OHLC bars={fact['holding_window_invalid_ohlc_bars']}, timestamp gaps={fact['holding_window_timestamp_gaps']}, max open jump={fact['holding_window_max_open_jump_r']:.6f}R, stop-fill-outside-OHLC={fact['stop_like_fill_outside_exit_ohlc']}.", ""])
    (output / "case_notes.md").write_text("\n".join(lines))
    receipt = {"complete": True, "source_sha256": sha256(Path(__file__)), "same_entry_evidence_sha256": sha256(EVIDENCE),
               "event_states_sha256": sha256(EVENT_STATES), "raw_manifest_sha256": sha256(RAW / "manifest.json"),
               "v8_replay_manifest_sha256": sha256(REPLAY / "manifest.json"), "streams_loaded": keys,
               "holdout_era_use": "configuration-specific use #1; owner authorized 2026-09-14 explanatory chart review on reused nonblind data"}
    receipt["output_sha256"] = {path.name: sha256(path) for path in output.iterdir() if path.is_file() and path.name != "receipt.json"}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--out", type=Path, default=CASES); args = parser.parse_args(); run(args.out)


if __name__ == "__main__": main()
