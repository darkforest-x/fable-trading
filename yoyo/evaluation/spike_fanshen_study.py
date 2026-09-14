"""Frozen V8 entries with owner's Stoch/WVF exit signals on six OKX periods.

All features use the current closed bar or earlier history. No model training,
parameter search, live integration, or holdout reading occurs. Matched controls
condition on source timeframe, direction, UTC month and ATR/close relative to
the preceding 120 observations; future prices are used only as exit outcomes.
"""
import hashlib
import json
import pickle
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix, aggregate
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v8_lowtf_study import v8_mask
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _data_gap
from yoyo.evaluation.spike_v7_fast import simulate_v6_variant
from yoyo.evaluation.spike_recovery_exit import prepare, replay_entry
from yoyo.evaluation.spike_recovery_cash import eligible
from yoyo.evaluation.spike_net_recovery_cash import simulate_recovery
from yoyo.evaluation.spike_fanshen_exit import compute_signals, prepare_signals, replay_fanshen_entry

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-fanshen-exit-multitf-20260914-v1"
BUILDERS = ["yoyo/evaluation/spike_fanshen_study.py", "yoyo/evaluation/spike_fanshen_exit.py",
            "yoyo/data/spike_fanshen_prefix.py", str((EXP/"config.json").relative_to(ROOT)),
            str((EXP/"PROJECT_PLAN.md").relative_to(ROOT)),
            str((EXP/"source/owner_fanshen_v1_20250706.pine").relative_to(ROOT))]
POLICIES = {"original": None}
for trigger in ("arrows", "alerts"):
    for mode in ("augment", "replace_trail"):
        for profit in (True, False):
            POLICIES[f"{trigger}_{mode}_{'profit' if profit else 'any'}"] = dict(
                trigger=trigger, exit_mode=mode, profitable_only=profit)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    def clean(x):
        if isinstance(x, dict): return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)): return [clean(v) for v in x]
        if isinstance(x, np.generic): return clean(x.item())
        if isinstance(x, float) and not np.isfinite(x): return None
        if isinstance(x, pd.Timestamp): return x.isoformat()
        return x
    Path(path).write_text(json.dumps(clean(obj), ensure_ascii=False, indent=2)+"\n")


def replay(prepared, signals, i, policy, side=None):
    if policy is None:
        return replay_entry(prepared, int(i), take_profit_r=None, side_override=side)
    return replay_fanshen_entry(prepared, signals, int(i), side_override=side, **policy)


def serial(rows):
    result = []
    previous = None
    for row in rows:
        if eligible(row, previous):
            result.append(row)
            previous = row
    return result


def longest(flags):
    run = best = 0
    for value in flags:
        run = run+1 if value else 0
        best = max(best, run)
    return best


def stats(rows):
    natural = [r for r in rows if not r["censored"]]
    n = len(natural)
    r = np.array([t["net_r"] for t in natural], dtype=float)
    g = np.array([t["gross_r"] for t in natural], dtype=float)
    if not np.isfinite(r).all(): raise ValueError("unresolved natural returns")
    reasons = [str(t["exit_reason"]) for t in natural]
    mfe = np.array([t["mfe_r"] for t in natural], dtype=float)
    equity = np.r_[0., r.cumsum()]
    return dict(natural=n, open=len(rows)-n, wins=int((r>1e-9).sum()), losses=int((r < -1e-9).sum()),
                net_zero=int((abs(r)<=1e-9).sum()), win_rate=float((r>1e-9).mean()) if n else None,
                sum_gross_r=float(g.sum()), sum_net_r=float(r.sum()),
                mean_net_r=float(r.mean()) if n else None,
                mean_cost_r=float((g-r).mean()) if n else None,
                profit_factor=float(r[r>0].sum() / -r[r<0].sum()) if (r<0).any() else None,
                max_net_loss_streak=longest(r < -1e-9),
                max_initial_stop_streak=longest([x.startswith("initial_stop") for x in reasons]),
                initial_stops=sum(x.startswith("initial_stop") for x in reasons),
                fanshen_exits=sum(x.startswith("fanshen") for x in reasons),
                gross3r=int((g>=3).sum()), net3r=int((r>=3).sum()),
                legacy_mfe1r=int((mfe>=1).sum()),
                legacy_mfe1r_then_net_loss=int(((mfe>=1)&(r < -1e-9)).sum()),
                max_realized_drawdown_r=float((np.maximum.accumulate(equity)-equity).max()),
                boundary_net_r=float(sum(t["net_r"] for t in rows if t["censored"])))


def pair_delta(original, rows):
    baseline = {int(t["signal_i"]): t for t in original}
    detail = []
    for t in rows:
        old = baseline.get(int(t["signal_i"]))
        if old is None: continue
        natural = not t["censored"] and not old["censored"]
        detail.append(dict(signal_i=t["signal_i"], side=t["side"], entry_time=str(t["entry_time"]),
            original_exit=str(old["exit_time"]), new_exit=str(t["exit_time"]),
            original_net_r=old["net_r"], new_net_r=t["net_r"], delta_net_r=t["net_r"]-old["net_r"],
            original_reason=old["exit_reason"], new_reason=t["exit_reason"], both_natural=natural,
            original_gross_r=old["gross_r"], new_gross_r=t["gross_r"]))
    df = pd.DataFrame(detail)
    valid = df.loc[df.both_natural] if len(df) else df
    return df, dict(paired_n=len(valid), paired_delta_net_r=float(valid.delta_net_r.sum()) if len(valid) else 0.,
                    paired_improved=int((valid.delta_net_r>1e-9).sum()) if len(valid) else 0,
                    paired_worsened=int((valid.delta_net_r < -1e-9).sum()) if len(valid) else 0,
                    saved_losers=int(((valid.original_net_r<0)&(valid.new_net_r>0)).sum()) if len(valid) else 0,
                    lost_3r_winners=int(((valid.original_gross_r>=3)&(valid.new_gross_r<3)).sum()) if len(valid) else 0)


def controls(frame, prepared, signals, accepted, policy, cfg, start, seed_offset):
    vol = frame.atr/frame.close
    prior = vol.shift().rolling(120, min_periods=120)
    q1, q2 = prior.quantile(1/3), prior.quantile(2/3)
    buckets = np.where(vol<=q1, 0, np.where(vol<=q2, 1, 2))
    confirmed = frame.index+pd.Timedelta(minutes=frame.attrs["minutes"])
    months = confirmed.strftime("%Y-%m").to_numpy()
    valid = q1.notna().to_numpy() & (confirmed>=start) & (np.arange(len(frame))+1<len(frame))
    pools = {}
    detail = []
    for t in accepted:
        if t["censored"]: continue
        i, side = int(t["signal_i"]), int(t["side"])
        group = (months[i], int(buckets[i]))
        if group not in pools:
            pools[group] = np.flatnonzero(valid & (months==group[0]) & (buckets==group[1]))
        rng = np.random.default_rng(np.random.SeedSequence([cfg["seed"], seed_offset, i, side+1]))
        draws = []
        attempts = rejected_censored = invalid = 0
        for j in rng.permutation(pools[group]):
            if j == i: continue
            attempts += 1
            row = replay(prepared, signals, j, policy, side)
            if row is None or not np.isfinite(row["net_r"]):
                invalid += 1
                continue
            if row["censored"]:
                rejected_censored += 1
                continue
            draws.append(dict(signal_i=int(j), net_r=float(row["net_r"])))
            if len(draws) == cfg["controls_per_trade"]: break
        mean = float(np.mean([x["net_r"] for x in draws])) if draws else np.nan
        detail.append(dict(signal_i=i, side=side, month=months[i], vol_bucket=int(buckets[i]),
            actual_net_r=t["net_r"], random_net_r=mean, excess_net_r=t["net_r"]-mean,
            matched=len(draws), attempts=attempts, rejected_censored=rejected_censored, invalid=invalid,
            draws=json.dumps(draws)))
    df = pd.DataFrame(detail)
    if not len(df): return df, dict(matched_n=0, matched_missing=0, random_mean_net_r=None, excess_net_r=None, p=None)
    full = df.loc[df.matched == cfg["controls_per_trade"]]
    blocks = full.groupby("month").excess_net_r.sum().to_numpy()
    rng = np.random.default_rng(cfg["seed"])
    null = (rng.choice([-1,1], size=(cfg["permutations"], len(blocks)))*blocks).sum(axis=1)
    return df, dict(matched_n=len(full), matched_missing=len(df)-len(full), months=len(blocks),
        random_mean_net_r=float(full.random_net_r.mean()), excess_net_r=float(full.excess_net_r.mean()),
        p=float((1+(null>=blocks.sum()).sum())/(1+len(null))) if len(blocks) else None)


def assert_parity(frame, raw, indices, actual):
    allowed = pd.Series(False, index=frame.index)
    allowed.iloc[indices] = True
    _, reference = simulate_v6_variant(frame, raw, admission=allowed, variant="v8",
                                       data_gap=_data_gap(frame, frame.attrs["minutes"]),
                                       spec=ExecutionSpec(tick=.01))
    if len(reference) != len(actual): raise AssertionError(f"baseline count {len(reference)} != {len(actual)}")
    if len(actual):
        observed = pd.DataFrame(actual)
        for col in ["signal_i","entry_i","exit_i","side","entry_price","initial_stop","exit_price","gross_r","net_r"]:
            np.testing.assert_allclose(reference[col], observed[col], rtol=1e-12, atol=1e-12, err_msg=col)
        for col in ["exit_reason","censored"]:
            if reference[col].tolist() != observed[col].tolist(): raise AssertionError(col)
    return dict(rows=len(actual), numeric_fields=9, categorical_fields=2, passed=True)


def contexts(cfg):
    path = ROOT/cfg["context_3m"]
    if sha(path) != cfg["context_3m_sha256"]: raise ValueError("3m context identity changed")
    with path.open("rb") as handle: ctx = pickle.load(handle)
    frame = ctx["frame"].copy()
    if frame.index.max()+pd.Timedelta(minutes=3) > pd.Timestamp(cfg["end"]):
        raise ValueError("3m context exceeds pre-only boundary")
    frame.attrs["minutes"] = 3
    yield 3, frame, ctx["raw"], pd.Series(ctx["mask"], index=frame.index), dict(
        type="hash_pinned_pre_only_context", source=cfg["context_3m"], sha256=sha(path),
        rows=len(frame), first_open=str(frame.index[0]), last_close=str(frame.index[-1]+pd.Timedelta(minutes=3)),
        holdout_consumed=False)
    for native in [5, 15]:
        source, receipt = read_prefix(ROOT/cfg["sources"][str(native)], native, cfg["end"])
        for minutes in ([5] if native==5 else [15,30,60,240]):
            bars, aggregation = aggregate(source, native, minutes)
            f = features(bars)
            f.attrs["minutes"] = minutes
            raw, _, mask = v8_mask(f, minutes)
            yield minutes, f, raw, mask, dict(receipt, aggregation=aggregation)


def run():
    cfg = json.loads((EXP/"config.json").read_text())
    for rel in BUILDERS:
        subprocess.run(["git","cat-file","-e","HEAD:"+rel], cwd=ROOT, check=True)
        subprocess.run(["git","diff","--exit-code","HEAD","--",rel], cwd=ROOT, check=True)
    out = EXP/"results"
    out.mkdir(exist_ok=False)
    receipt = dict(builder_commit=subprocess.check_output(["git","rev-parse","HEAD"], cwd=ROOT, text=True).strip(),
                   builders={x:sha(ROOT/x) for x in BUILDERS}, holdout_consumed=False,
                   holdout_consumption_number=0, no_parameter_selection=True)
    save(out/"run_receipt.json", receipt)
    summaries = []
    cash_summaries = []
    input_receipts = []
    parity = []
    for minutes, frame, raw, admission, input_receipt in contexts(cfg):
        input_receipts.append(dict(minutes=minutes, **input_receipt))
        prepared = prepare(frame, raw)
        signal_table = compute_signals(frame)
        signals = prepare_signals(signal_table)
        signal_min = max(pd.Timestamp(cfg["available_start"]),
                         frame.index[cfg["minimum_warmup_bars"]]+pd.Timedelta(minutes=minutes))
        if signal_min > pd.Timestamp(cfg["common_start"]):
            raise ValueError(f"{minutes}m insufficient common-window warmup {signal_min}")
        confirmed = frame.index+pd.Timedelta(minutes=minutes)
        frame_signals = signal_table.copy()
        frame_signals["v8_admitted"] = admission
        frame_signals.loc[confirmed>=signal_min].to_csv(out/f"{minutes}m_signal_ledger.csv.gz")
        starts = {"common":pd.Timestamp(cfg["common_start"]), "available":signal_min}
        all_indices = np.flatnonzero(admission.to_numpy(bool) & (confirmed>=signal_min) &
                                     (confirmed<pd.Timestamp(cfg["end"])))
        opp = {}
        for name, policy in POLICIES.items():
            rows = []
            for i in all_indices:
                t = replay(prepared, signals, i, policy)
                if t is None: continue
                if not np.isfinite(t["net_r"]): raise ValueError("unresolved source gap")
                rows.append(t)
            opp[name] = rows
            pd.DataFrame(rows).to_csv(out/f"{minutes}m_{name}_opportunities.csv.gz", index=False)
        for window, start in starts.items():
            selected = {name:[t for t in rows if confirmed[int(t["signal_i"])]>=start] for name,rows in opp.items()}
            accepted = {name:serial(rows) for name,rows in selected.items()}
            ix = np.array([i for i in all_indices if confirmed[i]>=start], dtype=int)
            parity.append(dict(minutes=minutes, window=window, **assert_parity(frame,raw,ix,accepted["original"])))
            for name, policy in POLICIES.items():
                tag = f"{minutes}m_{window}_{name}"
                rows = accepted[name]
                pd.DataFrame(rows).to_csv(out/(tag+"_trades.csv.gz"), index=False)
                pairs, paired = pair_delta(accepted["original"], selected[name])
                pairs.to_csv(out/(tag+"_paired.csv.gz"), index=False)
                matched, control = controls(frame, prepared, signals, rows, policy, cfg, start, minutes)
                matched.to_csv(out/(tag+"_controls.csv.gz"), index=False)
                summary = dict(minutes=minutes, window=window, start=str(start), end=cfg["end"], policy=name,
                               candidates=len(selected[name]), **stats(rows), **paired, **control)
                summaries.append(summary)
                for schedule in ["fixed","double"]:
                    cash = simulate_recovery(selected[name], schedule=schedule, **cfg["cash"])
                    save(out/(tag+f"_{schedule}_cash.json"), cash)
                    cash_summaries.append(dict(minutes=minutes, window=window, policy=name, schedule=schedule, **cash["summary"]))
                print(tag, "natural",summary["natural"],"netR",round(summary["sum_net_r"],3),flush=True)
        save(out/"input_receipts.json", input_receipts)
        save(out/"baseline_parity.json", parity)
    summary = pd.DataFrame(summaries)
    summary["p_holm"] = np.nan
    for window in summary.window.unique():
        subset = summary.loc[(summary.window==window)&summary.p.notna()].sort_values("p")
        if len(subset):
            adjusted = np.minimum(1, np.maximum.accumulate(subset.p.to_numpy()*np.arange(len(subset),0,-1)))
            summary.loc[subset.index,"p_holm"] = adjusted
    summary.to_csv(out/"summary.csv", index=False)
    pd.DataFrame(cash_summaries).to_csv(out/"cash_summary.csv", index=False)
    save(out/"manifest.json",dict(**receipt, files={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}))
    print("COMPLETE",len(summary),"summary rows",len(parity),"baseline parity groups",flush=True)


if __name__ == "__main__":
    run()
