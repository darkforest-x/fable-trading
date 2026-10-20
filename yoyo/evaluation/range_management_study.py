"""Compare 5m ATR14 close management with cached 15m management, using Nautilus.

Owner 2026-10-20: keep H1 watch, independent 5m entries, initial stop and costs.
Two previously evaluated stop-buffer strata are separate paired comparisons:
0.05% current default and 0.2% candidate. No cross-stratum optimization.
Only management timeframe changes. Source ATR reads completed 5m OHLC via the
same Wilder ATR14 implementation; entry candidates and their matching buckets
are reused unchanged. No download, training or indicator-default modification.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_breakeven import scalar, assert_equal
from yoyo.evaluation.pepe_range_exits import inference
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, prepared

DEFAULT = ROOT/"experiments/active/exp-range-management5m-20261020-v1/config.json"


def holm(values):
    """Familywise correction, preserving the original hypothesis order."""
    values = np.asarray(values, dtype=float)
    order = np.argsort(values)
    corrected = np.maximum.accumulate(values[order]*(len(values)-np.arange(len(values))))
    output = np.empty(len(values))
    output[order] = np.minimum(corrected, 1.)
    return output.tolist()


def run(config):
    cfg = json.loads(config.read_text())
    started = time.perf_counter()
    preflight = []
    for spec in cfg["runs"]:
        current = json.loads((config.parent/spec["config"]).read_text())
        previous = json.loads((ROOT/spec["baseline_receipt"]).read_text())["identity"]
        actual = [file_stamp(ROOT/current[key]) for key in ("data", "entry_candidates")]
        if actual != previous["inputs"]:
            raise ValueError("Historical comparator input path/size/mtime changed")
        for key in ("data", "entry_candidates", "symbol", "tick", "market_tick",
                    "stop_window_5m", "buffer_percent", "maximum_risk_percent", "round_trip_cost"):
            if current[key] != previous["config"][key]:
                raise ValueError(f"Non-clock parameter changed: {key}")
        policy = current["policies"][0]
        keys = ("trail_arm_r", "trail_atr_multiple", "breakeven_r", "opposite_hour_exit")
        if not any(all(old.get(k) == policy[k] for k in keys)
                   for old in previous["config"]["policies"]):
            raise ValueError("Historical comparator exit parameters differ")
        preflight.append(dict(config=spec["config"], identical_inputs_by_path_size_mtime=True,
                              unchanged_entry_stop_cost_policy=True))
    dump(config.parent/"preflight.json", preflight)

    def one(spec):
        path = config.parent/spec["config"]
        log = path.parent/"run.log"
        with log.open("w") as handle:
            subprocess.run([str(ROOT/"scripts/backtest-range"), "--config", str(path)],
                           cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, check=True)
        receipt = json.loads((path.parent/"results/receipt.json").read_text())
        return dict(symbol=spec["symbol"], buffer_percent=spec["buffer_percent"],
                    elapsed_seconds=receipt["elapsed_seconds"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(one, cfg["runs"]))
    dump(config.parent/"run_receipt.json", dict(
        elapsed_seconds=time.perf_counter()-started, runs=runs,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
    ))
    print(json.dumps(runs), flush=True)


def subset(rows, segment):
    if segment == -1:
        return rows
    return rows[rows.segment > 0] if segment == -2 else rows[rows.segment == segment]


def summary(rows):
    losses, gains = -rows.loc[rows.net_r < 0, "net_r"].sum(), rows.loc[rows.net_r > 0, "net_r"].sum()
    return dict(n=len(rows), net_r=float(rows.net_r.sum()),
                gross_r=float(rows.gross_r.sum()), mean_net_r=float(rows.net_r.mean()),
                win_rate=float((rows.net_r > 1e-9).mean()),
                profit_factor=float(gains/losses) if losses else None,
                gross_10r=int((rows.gross_r >= 10-1e-7).sum()),
                gross_20r=int((rows.gross_r >= 20-1e-7).sum()),
                max_peak_r=float(rows.peak_r.max()), max_gross_r=float(rows.gross_r.max()),
                boundary_n=int((rows.reason == 0).sum()),
                natural_net_r=float(rows.loc[rows.reason != 0, "net_r"].sum()),
                fixed_1000_notional_pnl=float(rows.net_notional_bp.sum()/10))


def analyze(config):
    cfg = json.loads(config.read_text())
    tables, comparisons, audits, lineage = [], [], [], []
    for spec in cfg["runs"]:
        current_file = config.parent/spec["config"]
        current = json.loads(current_file.read_text())
        directory = current_file.parent
        baseline_path = ROOT/spec["baseline_ledger"]
        native_path = directory/"results/management5/trades.csv"
        old = pd.read_csv(baseline_path, float_precision="round_trip").set_index("trade_id")
        new = pd.read_csv(native_path, float_precision="round_trip").set_index("trade_id")
        assert set(old.index) == set(new.index)
        new = new.loc[old.index]
        # Entry populations, risk denominators and fees must not drift.
        for col in ("pair_id", "entry_i", "side", "end_i", "segment", "week", "bucket", "population"):
            np.testing.assert_array_equal(old[col], new[col], err_msg=col)
        for col in ("entry", "stop", "risk", "fee_r"):
            tolerance = 1e-7 if col == "fee_r" else current["tick"]*1e-7
            np.testing.assert_allclose(old[col], new[col], rtol=0, atol=tolerance, err_msg=col)
        ctx, _, _ = prepared(current, directory/".cache")
        np.testing.assert_array_equal(
            np.isfinite(ctx["atr"]), np.arange(len(ctx["frame"])) >= 13)
        # Only deterministic distributed and extreme probes are replayed;
        # prior full audits are not repeated.
        gain = (new.net_r-old.net_r).to_numpy()
        probes = np.unique(np.r_[np.linspace(0, len(new)-1, 12, dtype=int),
                                 np.argmax(new.peak_r), np.argmax(gain), np.argmin(gain)])
        columns = ["exit_i", "exit_price", "reason", "peak_r", "arm_i", "protection",
                   "ambiguous", "steps", "be_trigger_i", "be_effective_i",
                   "stop_kind", "be_price", "deferred_closes"]
        for i in probes:
            row = new.iloc[i]
            event = row[["entry_i", "side", "entry", "stop", "end_i"]].to_numpy(float)
            expected = scalar(ctx, event, "baseline", current["round_trip_cost"], current["tick"])
            actual = np.array([0 if key == "ambiguous" else row[key] for key in columns], dtype=float)
            assert_equal(actual, expected, current["tick"])
        audits.append(dict(symbol=spec["symbol"], buffer_percent=spec["buffer_percent"],
                           unchanged_entry_risk_fee_rows=len(new), scalar_paths=len(probes)))
        lineage.extend(file_stamp(p) for p in
                       (baseline_path, native_path, ROOT/spec["baseline_receipt"], current_file))
        identity = dict(symbol=spec["symbol"], venue=spec["venue"], buffer_percent=spec["buffer_percent"])
        for segment in (-1, 0, 1, 2, -2):
            a, b = subset(old, segment), subset(new, segment)
            for minutes, rows in ((15, a), (5, b)):
                for population in ("signal", "random"):
                    tables.append(dict(**identity, segment=segment, management_minutes=minutes,
                                       population=population, **summary(rows[rows.population == population])))
            s, r = b[b.population == "signal"], b[b.population == "random"]
            initial = a.loc[s.index]
            diff = s.net_r-initial.net_r
            treatment = s[["pair_id", "week", "net_r"]].copy()
            treatment["delta"] = diff
            treatment["net_r_random"] = initial.net_r
            controls = r[["pair_id", "net_r"]].copy()
            controls["delta_random"] = r.net_r-a.loc[r.index, "net_r"]
            paired = treatment.merge(controls, on="pair_id", suffixes=("", "_control"),
                                     validate="one_to_one")
            now_test = inference(paired.assign(net_r_random=paired.net_r_control), cfg["seed"])
            increment_test = inference(
                paired.assign(net_r=paired.delta, net_r_random=paired.delta_random), cfg["seed"])
            record = dict(**identity, segment=segment, n=len(s),
                          delta_net_r=float(diff.sum()),
                          improved=int((diff > 1e-7).sum()), worsened=int((diff < -1e-7).sum()),
                          earlier_exit=int((s.exit_i < initial.exit_i).sum()),
                          nominal_policy_gain=inference(treatment, cfg["seed"]),
                          signal_vs_random=now_test, matched_increment=increment_test)
            for threshold in (10, 20):
                was = initial.gross_r >= threshold-1e-7
                still = s.gross_r >= threshold-1e-7
                record[f"prior_{threshold}r"] = int(was.sum())
                record[f"retained_{threshold}r"] = int((was & still).sum())
                record[f"new_{threshold}r"] = int((~was & still).sum())
            comparisons.append(record)
        print(json.dumps(dict(**identity, paired_paths=len(new), scalar_probes=len(probes))), flush=True)
    for segment in (-1, 0, 1, 2, -2):
        records = [r for r in comparisons if r["segment"] == segment]
        for field in ("nominal_policy_gain", "matched_increment"):
            adjusted = holm([r[field]["p_one_sided"] for r in records])
            for row, pvalue in zip(records, adjusted):
                row[field]["holm_p_8_comparisons"] = pvalue
    pd.DataFrame(tables).to_csv(config.parent/"summary.csv", index=False)
    dump(config.parent/"comparisons.json", comparisons)
    dump(config.parent/"validation.json", dict(audits=audits, lineage=lineage,
         source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
         reused_15m_results=True, repeated_full_audit=False,
         note="Unchanged candidates, initial risk and fees. Management timeframe is the paired treatment."))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    parser.add_argument("--phase", choices=["run", "analyze"], required=True)
    args = parser.parse_args()
    (run if args.phase == "run" else analyze)(args.config.resolve())


if __name__ == "__main__":
    main()
