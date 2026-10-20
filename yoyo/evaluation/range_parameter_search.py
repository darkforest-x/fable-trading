"""Bounded one-at-a-time 5m entry parameter research, with native verification.

Reuse exp-range-entry5m-20261020-v1's frozen candidate IDs, controls, continuous
OHLC, yearly exit boundaries and 20bp cost. Features read completed OHLC only:
last N 5m extrema, sparse completed 15m ATR14, and confirmed H1 arrows. Entry
matching's forward-filled ATR must never enter exit replay. Outcome paths may
read subsequent bars only through the candidate's existing segment boundary.
The existing C++ screening kernel is parameterized, not a new execution engine;
baseline paths must match cached Nautilus fills, and development-selected arms
must be replayed by Nautilus before acceptance. No untouched-data claim.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_breakeven import native, replay, scalar, assert_equal
from yoyo.evaluation.pepe_range_exits import result_frame, metrics, pairs, inference
from yoyo.evaluation.range_framework import ROOT, prepared, compare_golden, dump, file_stamp

DEFAULT = ROOT / "experiments/active/exp-range-5m-params-20261020-v1/config.json"
CODE = [
    "yoyo/evaluation/range_parameter_search.py",
    "yoyo/evaluation/pepe_range_breakeven.py",
    "yoyo/evaluation/pepe_range_be_native.cpp",
]
AXES = ("stop_window_5m", "buffer_percent", "trail_arm_r", "trail_atr_multiple")


def validate_grid(cfg):
    baseline = cfg["arms"][0]
    if baseline["name"] != "baseline" or cfg["round_trip_cost"] != .002:
        raise ValueError("Baseline and frozen 20bp cost required")
    names = set()
    for arm in cfg["arms"]:
        if arm["name"] in names or not arm["name"].replace("_", "").isalnum():
            raise ValueError("Unique safe arm names required")
        names.add(arm["name"])
        changes = [key for key in AXES if arm[key] != baseline[key]]
        if len(changes) != (0 if arm["name"] == "baseline" else 1):
            raise ValueError("Each treatment must change exactly one parameter")
        if any(not np.isfinite(arm[key]) or arm[key] <= 0 for key in AXES):
            raise ValueError("Positive finite parameters required")


def choose_development(summary, minimum):
    """Selection reads segment 0 only; later outcomes cannot affect the choice."""
    dev = summary[(summary.segment == 0) & (summary.n >= minimum)]
    if dev.empty:
        raise ValueError("No sufficiently populated development arm")
    return dev.sort_values(["net_r", "realized_r_drawdown", "arm"],
                           ascending=[False, True, True]).iloc[0].arm


def decorate(base, result, cfg, ctx, tick):
    rows = result_frame(base, result[:, :8], cfg)
    for col, name in enumerate(
            ("be_trigger_i", "be_effective_i", "stop_kind", "be_price", "deferred_closes"), 8):
        rows[name] = result[:, col]
    ii = rows.exit_i.to_numpy(int)
    gap = (rows.reason.isin([1, 2, 5]).to_numpy()
           & (rows.side.to_numpy()*(ctx["bars"][ii, 0]-rows.protection.to_numpy()) < -tick*1e-7))
    rows["exit_time"] = ctx["frame"].index[ii]-pd.to_timedelta(gap.astype(int)*5, unit="min")
    rows["net_r"] = rows.net_r.mask(rows.net_r.abs() < 1e-9, 0.)
    return rows


def summarize(rows, name):
    out = []
    for segment in (-1, 0, 1, 2, -2):
        p = rows if segment == -1 else rows[rows.segment > 0] if segment == -2 else rows[rows.segment == segment]
        signal, control = p[p.population == "signal"], p[p.population == "random"]
        matched = pairs(signal, control)
        out.append(dict(
            arm=name, segment=segment, **metrics(signal),
            mean_net_r=float(signal.net_r.mean()), paired_n=len(matched),
            random_net_r=float(control.net_r.sum()),
            paired_signal_net_r=float(matched.net_r.sum()),
            paired_random_net_r=float(matched.net_r_random.sum()),
            paired_excess_r=float((matched.net_r-matched.net_r_random).sum()),
            max_peak_r=float(signal.peak_r.max()),
            max_gross_r=float(signal.gross_r.max()),
        ))
    return out


def screen(config):
    cfg = json.loads(config.read_text())
    validate_grid(cfg)
    destination = config.parent
    if (destination/"screen_receipt.json").exists():
        raise RuntimeError("Screen already completed; preserve existing results")
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *CODE, str(config)], cwd=ROOT).returncode:
        raise RuntimeError("Commit builder/config before research")
    started, lib, summaries, selections, checks, inputs = time.perf_counter(), native(), [], {}, {}, []
    for symbol, source in cfg["source_configs"].items():
        old = json.loads((ROOT/source).read_text())
        work = destination/symbol.lower()
        work.mkdir(exist_ok=True)
        ledgers, symbol_summary, probe_count = {}, [], 0
        for arm in cfg["arms"]:
            current = dict(old, stop_window_5m=arm["stop_window_5m"],
                           buffer_percent=arm["buffer_percent"], maximum_risk_percent=0)
            ctx, base, _ = prepared(current, work/".cache")
            # This explicit assertion guards the candidate-ATR / exit-event distinction.
            assert np.isnan(ctx["atr"][ctx["frame"].index.minute % 15 != 0]).all()
            events = base[["entry_i", "side", "entry", "stop", "end_i"]].to_numpy()
            result = replay(ctx, events, "baseline", cfg["round_trip_cost"], old["tick"], lib,
                            arm_r=arm["trail_arm_r"], atr_multiple=arm["trail_atr_multiple"])
            probes = np.unique(np.r_[
                np.linspace(0, len(events)-1, 12, dtype=int),
                np.argmax(result[:, 3]), np.argmax(result[:, 7]),
            ])
            for i in probes:
                expected = scalar(ctx, events[i], "baseline", cfg["round_trip_cost"], old["tick"],
                                  arm_r=arm["trail_arm_r"], atr_multiple=arm["trail_atr_multiple"])
                assert_equal(result[i], expected, old["tick"])
                probe_count += 1
            rows = decorate(base, result, cfg, ctx, old["tick"])
            path = work/f"{arm['name']}.csv.gz"
            rows.to_csv(path, index=False)
            ledgers[arm["name"]] = rows
            if arm["name"] == "baseline":
                previous = ROOT/old["output"]/"baseline/trades.csv"
                prior = pd.read_csv(previous, float_precision="round_trip")
                checks[symbol] = dict(
                    cached_native_baseline_parity=compare_golden(prior, path, old["tick"]))
                inputs.extend([file_stamp(ROOT/source), file_stamp(ROOT/old["data"]),
                               file_stamp(ROOT/old["entry_candidates"]), file_stamp(previous)])
            else:
                np.testing.assert_array_equal(rows.trade_id, ledgers["baseline"].trade_id)
            symbol_summary.extend(summarize(rows, arm["name"]))
        table = pd.DataFrame(symbol_summary)
        selected = choose_development(table, cfg["minimum_development_trades"])
        descriptive = table[table.segment == -1].sort_values(
            ["net_r", "arm"], ascending=[False, True]).iloc[0].arm
        selections[symbol] = dict(development_selected=selected, descriptive_best=descriptive)
        baseline, chosen = ledgers["baseline"], ledgers[selected]
        infer = []
        for segment in (0, 1, 2, -2, -1):
            a = chosen if segment == -1 else chosen[chosen.segment > 0] if segment == -2 else chosen[chosen.segment == segment]
            b = baseline.loc[a.index]
            ss, rr = a[a.population == "signal"], a[a.population == "random"]
            paired = pairs(ss, rr)
            gain = ss[["pair_id", "week", "net_r"]].merge(
                b[b.population == "signal"][["pair_id", "net_r"]], on="pair_id", suffixes=("", "_random"))
            base_pair = pairs(b[b.population == "signal"], b[b.population == "random"])
            incremental = paired[["pair_id", "week", "net_r", "net_r_random"]].merge(
                base_pair[["pair_id", "net_r", "net_r_random"]], on="pair_id", suffixes=("", "_base"))
            incremental["net_r"] -= incremental.net_r_base
            incremental["net_r_random"] -= incremental.net_r_random_base
            infer.append(dict(
                segment=segment,
                signal_vs_random=inference(paired, cfg["seed"]),
                policy_gain_vs_baseline=inference(gain, cfg["seed"]),
                matched_increment=inference(incremental, cfg["seed"]),
            ))
        dump(work/"inference.json", infer)
        winner = next(a for a in cfg["arms"] if a["name"] == selected)
        native_cfg = dict(
            old, experiment_id=cfg["experiment_id"],
            stop_window_5m=winner["stop_window_5m"], buffer_percent=winner["buffer_percent"],
            policies=[dict(name=selected, trail_arm_r=winner["trail_arm_r"],
                           trail_atr_multiple=winner["trail_atr_multiple"], breakeven_r=None,
                           opposite_hour_exit=True,
                           golden_ledger=str((work/f"{selected}.csv.gz").relative_to(ROOT)))],
            output=str((work/"native").relative_to(ROOT)),
        )
        dump(work/"native_config.json", native_cfg)
        checks[symbol]["scalar_paths_checked"] = probe_count
        summaries.extend(dict(symbol=symbol, venue=cfg["venues"][symbol], **r) for r in symbol_summary)
        print(json.dumps(dict(symbol=symbol, selection=selections[symbol], scalar_paths=probe_count)), flush=True)
    pd.DataFrame(summaries).to_csv(destination/"summary.csv", index=False)
    dump(destination/"selection.json", selections)
    dump(destination/"screen_receipt.json", dict(
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        elapsed_seconds=time.perf_counter()-started, inputs=inputs, checks=checks,
        runtime_phase="screening; development-selected arms require native verification",
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        training_eligible=False, production_eligible=False,
    ))


def verify_native(config):
    cfg = json.loads(config.read_text())
    work = config.parent
    selected = json.loads((work/"selection.json").read_text())

    def one(symbol):
        directory = work/symbol.lower()
        with (directory/"native.log").open("w") as log:
            subprocess.run([sys.executable, "-m", "yoyo.evaluation.range_framework",
                            "--config", str(directory/"native_config.json")],
                           cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        result = json.loads((directory/"native/receipt.json").read_text())
        audit = result["results"][0]["audit"]
        assert audit["parity"]["status"] == "passed"
        return symbol, dict(name=selected[symbol]["development_selected"],
                            elapsed_seconds=result["elapsed_seconds"], audit=audit)

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = dict(pool.map(one, cfg["source_configs"]))
    dump(work/"native_validation.json", records)
    print(json.dumps({symbol: dict(name=r["name"], paths=r["audit"]["parity"]["rows"])
                      for symbol, r in records.items()}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    parser.add_argument("--phase", choices=["screen", "native"], required=True)
    args = parser.parse_args()
    (screen if args.phase == "screen" else verify_native)(args.config.resolve())


if __name__ == "__main__":
    main()
