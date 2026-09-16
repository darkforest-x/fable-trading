"""Frozen BB/Stoch grid selection and sequential pre-May chronological recheck.

Entry features consume closed OHLC only. Matching volatility uses TR-SMA14 /
close versus preceding120 ratios; the selection reads development outcomes
only. A committed selection receipt is required before reading later prices.
Ranking uses fixed1ETH net liquidation-mark cash, never a varying stop-R unit.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import itertools
import json
import subprocess
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation import bb_stoch_replay as legacy
from yoyo.evaluation.bb_stoch_parameter_replay import ParamSpec, compute_features, prepare, replay_entry
from yoyo.evaluation.eth_bb_stoch_study import enrich, match_context, draw_controls, stats, save, sha

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-eth-bb-stoch-optimization-20260916-v1"
BUILDERS = ["yoyo/evaluation/bb_stoch_optimization.py",
            "yoyo/evaluation/bb_stoch_parameter_replay.py",
            "yoyo/evaluation/bb_stoch_optimization_report.py",
            "yoyo/evaluation/bb_stoch_replay.py",
            "yoyo/evaluation/eth_bb_stoch_study.py",
            "yoyo/evaluation/spike_fanshen_exit.py",
            "yoyo/data/spike_fanshen_prefix.py", "yoyo/data/release_eth_prefix.py",
            "yoyo/contracts/holdout.py",
            "yoyo/evaluation/pine/eth_bb_stoch_strategy_v2.pine",
            str((EXP/"config.json").relative_to(ROOT)), str((EXP/"PROJECT_PLAN.md").relative_to(ROOT))]


def identity(spec):
    return f"bb{spec.bb_length}_m{spec.bb_mult:g}_sl{spec.stop_fraction*100:g}"


def use_experiment(path):
    """Point this optimizer at another experiment directory.

    The grid, the selection receipt and the recheck gate are instrument
    agnostic; only the directory and the two per-experiment files change. The
    default stays the ETH run so its frozen receipts still verify.
    """
    global EXP, BUILDERS
    EXP = Path(path).resolve()
    tail = [str((EXP/"config.json").relative_to(ROOT)), str((EXP/"PROJECT_PLAN.md").relative_to(ROOT))]
    BUILDERS = BUILDERS[:-2] + tail
    return EXP


def parameter_grid(cfg):
    for n, mult, stop in itertools.product(cfg["bb_lengths"], cfg["bb_multiples"], cfg["stop_fractions"]):
        yield ParamSpec(bb_length=n, bb_mult=mult, stop_fraction=stop,
                        stoch_length=cfg["stoch_length"], k_smooth=cfg["k_smooth"],
                        d_smooth=cfg["d_smooth"], oversold=cfg["oversold"],
                        partial_fraction=cfg.get("partial_fraction", 0.5),
                        be_cost_fraction=cfg.get("be_cost_fraction", 0.0))


def require_committed(path, head):
    name = str(Path(path).relative_to(ROOT))
    current = Path(path).read_bytes()
    committed = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=ROOT)
    if current != committed: raise ValueError(f"Must commit before price read: {name}")
    return hashlib.sha256(current).hexdigest()


def code_receipt(cfg, stage):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = {name: require_committed(ROOT/name, head) for name in BUILDERS}
    if files[cfg["pine_source"]] != cfg["pine_sha256"]: raise ValueError("Original Pine changed")
    selection = None
    if stage == "recheck":
        selection_path = EXP/"selection.json"
        selection_hash = require_committed(selection_path, head)
        require_committed(EXP/"development_summary.json", head)
        require_committed(EXP/"development_code_receipt.json", head)
        require_committed(EXP/"development_source_receipt.json", head)
        selection = json.loads(selection_path.read_text())
        if selection["config_sha256"] != sha(EXP/"config.json"): raise ValueError("Selection/config mismatch")
        if selection["development_summary_sha256"] != sha(EXP/"development_summary.json"):
            raise ValueError("Selected development outcomes changed")
        development_receipt = json.loads((EXP/"development_code_receipt.json").read_text())
        development_source = json.loads((EXP/"development_source_receipt.json").read_text())
        if selection["development_source_sha256"] != development_source["prefix_sha256"]:
            raise ValueError("Development data receipt changed")
        if selection["source_commit"] != development_receipt["head"] or files != development_receipt["files"]:
            raise ValueError("Development builders changed before recheck")
        subprocess.run(["git", "merge-base", "--is-ancestor", selection["source_commit"], head], cwd=ROOT, check=True)
        files[str(selection_path.relative_to(ROOT))] = selection_hash
    return dict(head=head, files=files, generated_at=pd.Timestamp.now(tz="UTC"),
                stage=stage, frozen_before_price_read=True), selection


def serial(frame, prepared, context, mode, reference=False):
    candidates = np.flatnonzero((frame.signal.to_numpy() != 0) & context["valid"])
    rows, earliest = [], 0
    for i in candidates:
        if i < earliest: continue
        raw = legacy.replay_entry(frame, int(i), path_mode=mode) if reference else replay_entry(prepared, int(i), path_mode=mode)
        if raw is None: raise AssertionError("Eligible signal returned no trade")
        row = enrich(raw, frame)
        rows.append(row)
        if row["censored"]: break
        earliest = int(row["exit_i"])
    return rows


def liquidation_curve(frame, rows, start_i, initial):
    """Closed-bar fixed1ETH equity from executed fills, with open exit-fee reserve.

    Only outcome prices are read. Each bar consumes fills already reached on
    the frozen path; no end-of-trade return is backdated into earlier bars.
    """
    size = len(frame)
    cash_delta = np.zeros(size)
    gross_delta = np.zeros(size)
    unrealized = np.zeros(size)
    reserve = np.zeros(size)
    close = frame.close.to_numpy(float)
    for row in rows:
        entry, side = row["entry_price"], row["side"]
        qty_change = {}
        for pos, fill in enumerate(row["fills"]):
            i, qty, price = int(fill["i"]), fill["qty"], fill["price"]
            pnl = 0. if pos == 0 else side*(price-entry)*qty
            cash_delta[i] += pnl-price*qty*.001
            gross_delta[i] += pnl
            qty_change[i] = qty_change.get(i, 0.) + (qty if pos == 0 else -qty)
        remaining = 0.
        for i in range(int(row["entry_i"]), int(row["exit_i"])+1):
            remaining += qty_change.get(i, 0.)
            unrealized[i] = remaining*side*(close[i]-entry)
            reserve[i] = remaining*close[i]*.001
    equity = initial + cash_delta.cumsum()+unrealized-reserve
    gross = initial + gross_delta.cumsum()+unrealized
    curve = np.r_[initial, equity[start_i:]]
    dd = np.maximum.accumulate(curve)-curve
    expected = sum(r["net_pnl"] for r in rows)
    for r in rows:
        if r["censored"]:
            left = 1.-sum(f["qty"] for f in r["fills"][1:])
            expected -= left*r["exit_price"]*.001
    if abs(equity[-1]-initial-expected) > 1e-7: raise AssertionError("Close-MTM endpoint ledger")
    return curve, dict(net_liquidation_mark_usdt=float(equity[-1]-initial),
                       gross_mark_usdt=float(gross[-1]-initial),
                       close_mtm_drawdown_usdt=float(dd.max()),
                       close_mtm_drawdown_percent=float((dd/np.maximum.accumulate(curve)).max()*100),
                       ending_equity=float(equity[-1]))


def audit_rows(rows):
    for r in rows:
        entry = r["entry_price"]
        fees = sum(f["price"]*f["qty"]*.001 for f in r["fills"])
        qty = sum(f["qty"] for f in r["fills"][1:])
        gross = sum(r["side"]*(f["price"]-entry)*f["qty"] for f in r["fills"][1:])
        if r["censored"]: gross += r["side"]*(r["exit_price"]-entry)*(1.-qty)
        if not r["censored"] and abs(qty-1.) > 1e-12: raise AssertionError("Unbalanced exit quantity")
        for a, b in [(fees, r["fees"]), (gross, r["gross_pnl"]),
                     (gross-fees, r["net_pnl"]), ((gross-fees)/r["initial_risk"], r["net_r"])]:
            if abs(a-b)>1e-7: raise AssertionError("Fill-ledger mismatch")


def control_summary(rows, controls, cfg):
    groups = {int(r["signal_i"]): [] for r in rows}
    for r in controls: groups[int(r["actual_signal_i"])].append(r)
    pairs, all_excess = [], []
    for r in rows:
        group = groups[int(r["signal_i"])]
        if len(group) != cfg["controls_per_trade"]: continue
        real_bp = r["net_pnl"]/r["entry_price"]*10000
        control_bp = float(np.mean([c["net_pnl"]/c["entry_price"]*10000 for c in group]))
        all_excess.append(real_bp-control_bp)
        if not r["censored"] and not any(c["censored"] for c in group):
            pairs.append(dict(month=r["month"], real=real_bp, random=control_bp, excess=real_bp-control_bp))
    blocks = pd.DataFrame(pairs).groupby("month").excess.sum().to_numpy() if pairs else np.array([])
    null = np.array(list(itertools.product([-1, 1], repeat=len(blocks)))) @ blocks if len(blocks) else np.array([])
    return dict(paired_n=len(pairs), unpaired_n=len(rows)-len(pairs), drawn=len(controls),
                censored=sum(r["censored"] for r in controls),
                paired_actual_mean_bp=float(np.mean([p["real"] for p in pairs])) if pairs else None,
                random_mean_bp=float(np.mean([p["random"] for p in pairs])) if pairs else None,
                excess_mean_bp=float(np.mean([p["excess"] for p in pairs])) if pairs else None,
                month_blocks=len(blocks), p=float(np.mean(null>=blocks.sum()-1e-12)) if len(blocks) else None,
                all_boundary_mark_excess_bp=float(np.mean(all_excess)) if all_excess else None)


def run_arm(raw, spec, cfg, start, baseline_check=False):
    frame = compute_features(raw, spec)
    prepared = prepare(frame, spec)
    context = match_context(frame)
    context["valid"] &= (context["confirmed"]>=pd.Timestamp(start))
    context["valid"] &= (np.arange(len(frame))>=max(cfg["bb_lengths"])-1)
    start_i = int(frame.index.searchsorted(pd.Timestamp(start)))
    result, ledgers, curves = {}, {}, {}
    for mode in cfg["path_modes"]:
        rows = serial(frame, prepared, context, mode)
        if baseline_check:
            old = legacy.compute_features(raw)
            for col in ("upper", "lower", "k", "d", "signal", "target_upper", "target_lower"):
                np.testing.assert_allclose(frame[col], old[col], equal_nan=True, atol=1e-10, rtol=1e-12)
            reference = serial(old, None, context, mode, reference=True)
            if rows != reference: raise AssertionError("Original default trade ledger parity")
        draws = {int(r["signal_i"]): draw_controls(context, int(r["signal_i"]), int(r["side"]), cfg) for r in rows}
        controls = []
        for r in rows:
            for number, j in enumerate(draws[int(r["signal_i"])], 1):
                control = enrich(replay_entry(prepared, int(j), side_override=int(r["side"]), path_mode=mode), frame)
                control.update(actual_signal_i=int(r["signal_i"]), draw_number=number,
                               vol_bucket=int(context["bucket"][j]))
                controls.append(control)
        audit_rows([*rows, *controls])
        curve, equity = liquidation_curve(frame, rows, start_i, cfg["initial_equity"])
        st = stats(rows)
        cash_wins = sum(r["net_pnl"] for r in rows if not r["censored"] and r["net_pnl"]>0)
        cash_losses = -sum(r["net_pnl"] for r in rows if not r["censored"] and r["net_pnl"]<0)
        st["cash_profit_factor"] = cash_wins/cash_losses if cash_losses else None
        st["month_natural_counts"] = dict(Counter(r["month"] for r in rows if not r["censored"]))
        result[mode] = dict(stats=st, equity=equity, control=control_summary(rows, controls, cfg))
        ledgers[mode] = dict(actual=rows, controls=controls, draws=draws)
        curves[mode] = curve
    return dict(params=asdict(spec), modes=result, signal_count=int(((frame.signal!=0) & context["valid"]).sum())), ledgers, curves


def select_candidates(results, cfg):
    months = pd.date_range(cfg["development_start"], cfg["development_end"], freq="MS", inclusive="left").strftime("%Y-%m")
    eligible = {}
    for key, arm in results.items():
        ok = all(v["stats"]["natural"]>=cfg["minimum_natural_trades"] and
                 all(v["stats"]["month_natural_counts"].get(month, 0)>=cfg["minimum_trades_each_month"] for month in months)
                 for v in arm["modes"].values())
        arm["eligible"] = bool(ok)
        arm["worst_net_mark"] = min(v["equity"]["net_liquidation_mark_usdt"] for v in arm["modes"].values())
        arm["worst_close_mtm_drawdown"] = max(v["equity"]["close_mtm_drawdown_usdt"] for v in arm["modes"].values())
        if ok: eligible[key] = arm
    order = lambda key: (-eligible[key]["worst_net_mark"], eligible[key]["worst_close_mtm_drawdown"], key)
    peak = min(eligible, key=order) if eligible else None
    axes = [("bb_length", "bb_lengths"), ("bb_mult", "bb_multiples"), ("stop_fraction", "stop_fractions")]
    for key, arm in eligible.items():
        neighbors = [key]
        for field, axis in axes:
            values = cfg[axis]
            index = values.index(arm["params"][field])
            for offset in (-1, 1):
                if 0<=index+offset<len(values):
                    values_copy = dict(arm["params"]); values_copy[field] = values[index+offset]
                    other = identity(ParamSpec(**values_copy))
                    if other in eligible: neighbors.append(other)
        arm["eligible_neighbors"] = neighbors
        arm["neighborhood_median_usdt"] = float(np.median([eligible[n]["worst_net_mark"] for n in neighbors]))
    robust = [key for key, arm in eligible.items() if len(arm["eligible_neighbors"])>=cfg["minimum_eligible_neighbors_including_self"]]
    primary = min(robust, key=lambda k: (-eligible[k]["neighborhood_median_usdt"], *order(k))) if robust else peak
    finalists = list(dict.fromkeys([identity(ParamSpec()), *([primary] if primary else []), *([peak] if peak else [])]))
    return dict(primary=primary, peak=peak, finalists=finalists, eligible_count=len(eligible),
                fallback_no_eligible_neighborhood=bool(primary and not robust),
                parameters={key: results[key]["params"] for key in finalists})


def write_grid(path, results):
    records = []
    for key, arm in results.items():
        row = dict(id=key, **arm["params"], eligible=arm.get("eligible"),
                   neighborhood_median_usdt=arm.get("neighborhood_median_usdt"),
                   worst_net_mark=arm.get("worst_net_mark"))
        for mode, values in arm["modes"].items():
            for section in ("stats", "equity", "control"):
                for field, value in values[section].items():
                    if not isinstance(value, dict): row[f"{mode}_{section}_{field}"] = value
        records.append(row)
    pd.DataFrame(records).to_csv(path, index=False)


def main(stage):
    cfg = json.loads((EXP/"config.json").read_text())
    # Matched controls rank nothing during the grid; they validate the chosen
    # finalists. Drawing five per trade across 315 specs on a long series costs
    # hours and buys no selection information, so a config may switch them off
    # for the development stage only.
    if stage == "development" and "grid_controls_per_trade" in cfg:
        cfg = dict(cfg, controls_per_trade=cfg["grid_controls_per_trade"])
    frozen = dict(partial_fraction=.5, fee_per_notional=.001, full_quantity_eth=1.,
                  slippage=0, funding="not modeled", v1_gate=False,
                  stoch_length=5, k_smooth=3, d_smooth=3, oversold=20)
    if any(cfg.get(k)!=v for k,v in frozen.items()):
        raise ValueError("Frozen execution/Stoch contract changed")
    if list(EXP.glob("recheck_*")) or (stage=="development" and (EXP/"selection.json").exists()):
        raise ValueError("One-shot stage already recorded; preserve artifacts and review before any rerun")
    receipt, selection = code_receipt(cfg, stage)
    save(EXP/f"{stage}_code_receipt.json", receipt)
    end = cfg["development_end"] if stage=="development" else cfg["recheck_end"]
    start = cfg["development_start"] if stage=="development" else cfg["recheck_start"]
    raw, source = read_prefix(ROOT/cfg["source"], 5, end)
    save(EXP/f"{stage}_source_receipt.json", source)
    specs = list(parameter_grid(cfg)) if stage=="development" else [ParamSpec(**selection["parameters"][key]) for key in selection["finalists"]]
    results, curves = {}, {}
    ledger_path = EXP/f"{stage}_ledger.jsonl.gz"
    with ledger_path.open("wb") as base, gzip.GzipFile(fileobj=base, mode="wb", filename="", mtime=0) as zipped, io.TextIOWrapper(zipped, encoding="utf8") as stream:
        for number, spec in enumerate(specs, 1):
            key = identity(spec)
            arm, ledger, arm_curves = run_arm(raw, spec, cfg, start, baseline_check=spec==ParamSpec())
            results[key] = arm
            curves.update({key+"__"+mode: value for mode, value in arm_curves.items()})
            stream.write(json.dumps(dict(id=key, params=asdict(spec), modes=ledger), separators=(",", ":"))+"\n")
            if number%20==0 or number==len(specs): print(stage, number, "/",len(specs), key, flush=True)
    if stage=="development":
        selected = select_candidates(results, cfg)
        save(EXP/"development_summary.json", results)
        selected.update(config_sha256=sha(EXP/"config.json"), development_summary_sha256=sha(EXP/"development_summary.json"),
                        development_source_sha256=source["prefix_sha256"], source_commit=receipt["head"],
                        generated_at=pd.Timestamp.now(tz="UTC"), selected_without_recheck_price_read=True)
        save(EXP/"selection.json", selected)
        keep = selected["finalists"]
        print("DEVELOPMENT_SELECTION", json.dumps(selected, default=str), flush=True)
    else:
        save(EXP/"recheck_summary.json", results)
        keep = selection["finalists"]
    timestamps = raw.index[int(raw.index.searchsorted(pd.Timestamp(start))):].asi8
    np.savez_compressed(EXP/f"{stage}_selected_curves.npz", timestamps_ns=timestamps,
                        **{key:value for key,value in curves.items() if key.split("__")[0] in keep})
    write_grid(EXP/f"{stage}_grid.csv", results)
    save(EXP/f"{stage}_validation.json", dict(passed=True, candidate_count=len(results),
        default_legacy_ledger_parity=True, both_paths=True, all_actual_and_control_fills_reconciled=True,
        boundary_prices_excluded=True, holdout_consumed=False,
        total_replayed_events=sum(v["stats"]["total_entries"]+v["control"]["drawn"] for a in results.values() for v in a["modes"].values())))


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("stage", choices=["development", "recheck"])
    parser.add_argument("--exp", default=None, help="experiment directory; defaults to the ETH run")
    args=parser.parse_args()
    if args.exp: use_experiment(args.exp)
    main(args.stage)
