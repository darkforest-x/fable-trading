"""Single-body ETH5m admission, reusing frozen independent Nautilus outcomes.

Only entry_i, side and that completed 5m candle's open/close define admission.
Prior15m-watch candidates, entry clocks,5bar structure stops,0.05% buffer,
15m5R/4ATR,armedH1 reverse exits and20bp costs are unchanged. All existing
outcomes remain read-only. Controls pass their own body gate and are rematched
within segment/UTCweek/side/causal volatility bucket without replacement.
No future candle, new execution engine, fetching, Pine or production changes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_range_context import match_candidates, weekly_inference
from yoyo.evaluation.eth_5m_rejections import gain_inference
from yoyo.evaluation.range_double_confirm import load_frame
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_management_study import summary, subset

DEFAULT = ROOT/"experiments/active/exp-eth-5m-signal-body-20261020-v1/config.json"
CODE = ["yoyo/evaluation/range_signal_body.py",
        "tests/evaluation/test_range_signal_body.py"]


def body_gate(rows, frame):
    """Read only completed entry-bar open/close; both dojis and wrong bodies fail."""
    idx = rows.entry_i.to_numpy(int)
    if (idx < 0).any() or (idx >= len(frame)).any():
        raise ValueError("Entry index outside available closed candles")
    return rows.side.to_numpy(int)*(frame.close-frame.open).to_numpy()[idx] > 0


def pair_rows(signal, random):
    """Reuse the existing outcome-blind deterministic matcher."""
    matched = match_candidates(signal, random)
    si, ri = zip(*matched) if matched else ([], [])
    out = signal.loc[list(si), ["trade_id", "pair_id", "segment", "week",
                               "side", "bucket", "entry_i", "net_r",
                               "net_notional_bp"]].reset_index(drop=True)
    control = random.loc[list(ri)].reset_index(drop=True)
    out["control_id"] = control.trade_id
    for key in ("net_r", "net_notional_bp"):
        out[key+"_random"] = control[key]
        out["excess_"+key] = out[key]-out[key+"_random"]
    for key in ("segment", "week", "side", "bucket"):
        np.testing.assert_array_equal(out[key], control[key])
    return out


def run(path):
    started = time.perf_counter()
    cfg = json.loads(path.read_text())
    dest = path.parent
    if (dest/"receipt.json").exists():
        raise FileExistsError("Completed study exists; reuse summary.json")
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--",
                       *CODE, str(path.relative_to(ROOT))], cwd=ROOT).returncode:
        raise RuntimeError("Commit builder before generating")
    source = ROOT/cfg["source_experiment"]
    old_delivery = json.loads((source/"delivery_receipt.json").read_text())
    old_native = json.loads((source/"watch15/results/receipt.json").read_text())
    native_cfg = json.loads((source/"watch15/config.json").read_text())
    assert native_cfg == old_native["identity"]["config"]
    assert [file_stamp(ROOT/native_cfg[k]) for k in ("data", "entry_candidates")] == old_native["identity"]["inputs"]
    for rel in ("watch15/results/baseline/trades.csv",
                "watch15_double/results/baseline/trades.csv", "summary.csv"):
        file = source/rel
        prior = next(x for x in old_delivery["artifacts"]
                     if x["path"] == str(file.relative_to(ROOT)))
        actual = file_stamp(file)
        assert actual["bytes"] == prior["bytes"] and actual["mtime_ns"] == prior["mtime_ns"]
    frame = load_frame(ROOT/native_cfg["data"])
    rows = pd.read_csv(source/"watch15/results/baseline/trades.csv",
                       float_precision="round_trip")
    columns = list(rows)
    np.testing.assert_array_equal(
        pd.DatetimeIndex(pd.to_datetime(rows.entry_time, utc=True)).as_unit("ns").asi8,
        frame.index[rows.entry_i.to_numpy(int)].asi8)
    keep = body_gate(rows, frame)
    selected = rows.loc[keep].copy()
    pd.testing.assert_frame_equal(selected, rows.loc[keep, columns], check_exact=True)
    # Independent per-row price-sign oracle; no candle after entry is accessed.
    for r in rows.itertuples():
        candle = frame.iloc[r.entry_i]
        expected = candle.close > candle.open if r.side == 1 else candle.close < candle.open
        assert bool(keep[r.Index]) == bool(expected)
    signals = selected[selected.population == "signal"]
    random = selected[selected.population == "random"]
    base = rows[rows.population == "signal"]
    paired = pair_rows(signals, random)
    selected.to_csv(dest/"selected_trades.csv.gz", index=False)
    paired.to_csv(dest/"matched_pairs.csv.gz", index=False)
    rows[["trade_id", "entry_i", "side", "population"]].assign(
        admitted=keep).to_csv(dest/"decisions.csv.gz", index=False)
    records, infer, effects = [], [], []
    old_summary = pd.read_csv(source/"summary.csv")
    # Prior comparator columns are retained, not recalculated by a new engine.
    for seg in (-1, 0, 1, 2, -2):
        a, r, b, pp = (subset(x, seg) for x in (signals, random, base, paired))
        record = dict(arm="watch15_signal_body", segment=seg, population="signal",
                      **summary(a), mean_net_bp=float(a.net_notional_bp.mean()),
                      mean_fee_r=float(a.fee_r.mean()),
                      median_risk_pct=float((100*a.risk/a.entry).median()),
                      peak_10r=int((a.peak_r >= 10).sum()), peak_20r=int((a.peak_r >= 20).sum()),
                      paired_n=len(pp), matching_coverage=len(pp)/len(a),
                      random_eligible_n=len(r),
                      paired_signal_mean_r=float(pp.net_r.mean()),
                      paired_random_mean_r=float(pp.net_r_random.mean()),
                      paired_signal_mean_bp=float(pp.net_notional_bp.mean()),
                      paired_random_mean_bp=float(pp.net_notional_bp_random.mean()))
        for t in (10, 20):
            record[f"retain_{t}r"] = record[f"gross_{t}r"]/int((b.gross_r >= t-1e-7).sum())
        records.append(record)
        infer.append(dict(segment=seg,
            net=weekly_inference(a, "net_notional_bp", cfg),
            excess_bp=weekly_inference(pp, "excess_net_notional_bp", cfg),
            excess_r=weekly_inference(pp, "excess_net_r", cfg),
            gain_over_all=gain_inference(b, b.index.isin(a.index), cfg)))
        removed = b.loc[~b.index.isin(a.index)]
        effects.append(dict(segment=seg, removed=len(removed),
            removed_losses=int((removed.net_r < 0).sum()),
            removed_winners=int((removed.net_r > 1e-9).sum()),
            removed_net_r=float(removed.net_r.sum()),
            removed_10r=int((removed.gross_r >= 10-1e-7).sum()),
            removed_20r=int((removed.gross_r >= 20-1e-7).sum())))
    pd.DataFrame(records).to_csv(dest/"summary.csv", index=False)
    dump(dest/"inference.json", infer)
    dump(dest/"filter_effects.json", effects)
    # Causal same-bar gate must include every double-body source arrow.
    double = pd.read_csv(source/"watch15_double/results/baseline/trades.csv",
                         float_precision="round_trip").query("population == 'signal'")
    double["source_i"] = double.entry_i-1
    overlap = signals.merge(double, left_on=["entry_i", "side"],
        right_on=["source_i", "side"], suffixes=("_body", "_double"),
        how="left", validate="one_to_one", indicator=True)
    assert int((overlap._merge == "both").sum()) == len(double)
    overlap.to_csv(dest/"body_to_double.csv.gz", index=False)
    both = overlap._merge == "both"
    delay = dict(body_entries=len(signals), double_entries=len(double),
        body_only_entries=int((~both).sum()),
        surviving_body_mean_bp=float(overlap.loc[both, "net_notional_bp_body"].mean()),
        surviving_double_mean_bp=float(overlap.loc[both, "net_notional_bp_double"].mean()),
        note="Survivor attribution uses future confirmation; not executable early selection.")
    for t in (10, 20):
        was = overlap.gross_r_body >= t-1e-7
        now = overlap.gross_r_double >= t-1e-7
        delay[f"body_{t}r"] = int(was.sum())
        delay[f"rejected_by_second_{t}r"] = int((was & ~both).sum())
        delay[f"survive_source_{t}r"] = int((was & both).sum())
        delay[f"still_final_{t}r"] = int((was & now & both).sum())
        delay[f"new_final_{t}r"] = int((~was & now).sum())
    dump(dest/"delay_attribution.json", delay)
    comparison = pd.concat([
        old_summary.query("population == 'signal' and arm != 'h1_baseline'"),
        pd.DataFrame(records)], ignore_index=True)
    # Populate explicit paired comparator columns for the old 100% matched arms.
    for i, record in comparison[comparison.arm != "watch15_signal_body"].iterrows():
        rc = old_summary[(old_summary.arm == record.arm)&(old_summary.segment == record.segment)&
                         (old_summary.population == "random")].iloc[0]
        for key, value in dict(paired_n=record.n, matching_coverage=1.,
                              paired_signal_mean_r=record.mean_net_r,
                              paired_random_mean_r=rc.mean_net_r,
                              paired_signal_mean_bp=record.mean_net_bp,
                              paired_random_mean_bp=rc.mean_net_bp).items():
            comparison.loc[i, key] = value
    comparison.to_csv(dest/"comparison.csv", index=False)
    dump(dest/"receipt.json", dict(experiment_id=cfg["experiment_id"],
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        elapsed_seconds=time.perf_counter()-started, reused_native_rows=len(rows),
        preserved_native_fields=len(columns), independently_checked_body_rows=len(rows),
        source_stamps=[file_stamp(source/r) for r in
            ("delivery_receipt.json", "watch15/results/baseline/trades.csv",
             "watch15_double/results/baseline/trades.csv")]+[file_stamp(ROOT/native_cfg["data"])],
        native_replays=0, downloads=0, parameter_search=False,
        controls="Same body gate;cached watch-qualified random outcomes;rematched without replacement by "
                 "segment/week/side/volbucket. Incomplete coverage;compare paired subsets,not all-signal means.",
        limitations="Historical independent R,not account return;20bp excludes funding/spread/latency. "
                    "No trained score:AUC/top-decile inapplicable;weekly null controls used.",
        training_eligible=False, production_eligible=False))
    print(comparison.query("segment == -1")[
        ["arm", "n", "win_rate", "mean_net_r", "mean_net_bp", "gross_10r", "gross_20r",
         "paired_n", "paired_signal_mean_r", "paired_random_mean_r"]].to_string(index=False))
    print(json.dumps(delay), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    run(parser.parse_args().config.resolve())
