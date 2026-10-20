"""Single-variable 3R cost-breakeven experiment on the frozen PEPE V2.8 ledger.

Inputs and causal features are reused from pepe_range_exits: completed 5m OHLC,
15m ATR14 only on completed 15m bars, and confirmed H1 arrows. Outcome replay
reads future bars only up to each entry's frozen segment boundary. BE uses a
completed 15m close, never the earlier wick of its activation bar. Raw data and
earlier results stay unchanged. All trade R denominators are original risks.
"""
import ctypes
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import (
    load_context, result_frame, metrics, pairs, inference, assert_path_equal,
)

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/"experiments/active/exp-pepe-range-be3-20261020-v1"
CPP = ROOT/"yoyo/evaluation/pepe_range_be_native.cpp"
ARMS = {"legacy": 0, "baseline": 1, "be3": 2}


def native():
    path = EXP/"build/pepe_range_be.dylib"
    path.parent.mkdir(exist_ok=True)
    if not path.exists() or path.stat().st_mtime < CPP.stat().st_mtime:
        subprocess.run(["c++","-O3","-std=c++17","-shared","-fPIC",str(CPP),"-o",str(path)], check=True)
    lib = ctypes.CDLL(str(path))
    array = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
    lib.replay_be.argtypes = [array,array,array,array,ctypes.c_int,ctypes.c_int,
                             ctypes.c_double,ctypes.c_double,ctypes.c_double,
                             ctypes.c_double,array]
    lib.replay_be.restype = None
    lib.replay_search.argtypes = [array,array,array,array,ctypes.c_int,
        ctypes.c_double,ctypes.c_double,ctypes.c_double,ctypes.c_double,
        ctypes.c_double,ctypes.c_int,ctypes.c_double,ctypes.c_int,array]
    lib.replay_search.restype = None
    lib.replay_timed.argtypes = [array,array,array,array,array,ctypes.c_int,
        ctypes.c_double,ctypes.c_double,ctypes.c_int,array]
    lib.replay_timed.restype = None
    return lib


def replay_timed(ctx, events, *, delay=25, cost=.002, tick=.01, lib=None):
    """Delay is in execution bars; BE checks only the supplied 5m close clock."""
    lib = native() if lib is None else lib
    events = np.ascontiguousarray(events,dtype=float)
    output = np.empty((len(events),13),dtype=float)
    lib.replay_timed(ctx["bars"],ctx["atr"],ctx["hour"],
        np.ascontiguousarray(ctx["be_clock"],dtype=float),events,len(events),
        cost,tick,delay,output)
    return output


def replay_search(ctx, events, *, cost=.002, tick=.01, arm_r=5.,
                  atr_multiple=4., be_r=None, reverse_exit=True,
                  target_r=None, stop_first=False, lib=None):
    """Parameterized screening, including native O-H-L-C target precedence.

    All management uses the caller's sparse completed-close ATR. stop_first
    supplies a conservative collision sensitivity, never the native claim.
    """
    lib = native() if lib is None else lib
    events = np.ascontiguousarray(events, dtype=float)
    output = np.empty((len(events),13), dtype=float)
    lib.replay_search(ctx["bars"],ctx["atr"],ctx["hour"],events,len(events),
        cost,tick,arm_r,atr_multiple,-1. if be_r is None else be_r,
        int(reverse_exit),0. if target_r is None else target_r,
        int(stop_first),output)
    return output


def replay(ctx, events, arm="be3", cost=.002, tick=1e-9, lib=None,
           arm_r=5., atr_multiple=4.):
    """Reuse the corrected screening kernel with explicit trailing parameters."""
    if not all(np.isfinite(x) and x > 0 for x in (arm_r, atr_multiple)):
        raise ValueError("Trailing parameters must be finite and positive")
    lib = native() if lib is None else lib
    events = np.ascontiguousarray(events, dtype=float)
    result = np.empty((len(events),13), dtype=float)
    lib.replay_be(ctx["bars"],ctx["atr"],ctx["hour"],events,len(events),
                  ARMS[arm],cost,tick,arm_r,atr_multiple,result)
    return result


def scalar(ctx, event, arm="be3", cost=.002, tick=1e-9,
           arm_r=5., atr_multiple=4.):
    """Readable signed-price oracle, independent of the native implementation."""
    first, side, entry, initial, last = event
    first, side, last = int(first), int(side), int(last)
    risk = side*(entry-initial)
    be = (np.ceil((entry+side*cost*entry)/tick-1e-8) if side > 0
          else np.floor((entry+side*cost*entry)/tick+1e-8))*tick
    stop, peak, price, end = initial, 0., entry, last
    trailing, triggered, protected, source, reason, deferred = -1,-1,-1,0,0,0
    eps, steps = tick*1e-7, 0
    for i in range(first+1,last+1):
        steps += 1
        o,h,l,c = ctx["bars"][i]
        bad, good = (l,h) if side > 0 else (h,l)
        if (side > 0 and l <= stop+eps) or (side < 0 and h >= stop-eps):
            price = o if side*(o-stop) < 0 else stop
            reason = 5 if source == 1 else 2 if trailing >= 0 else 1
            end = i
            break
        peak = max(peak, side*(good-entry)/risk)
        quarter = np.isfinite(ctx["atr"][i]) and ctx["atr"][i] > 0
        if quarter:
            above = (side*(c-entry)/risk >= arm_r if arm == "legacy"
                     else side*(c-entry)+eps >= arm_r*risk)
            if trailing < 0 and above:
                trailing = i
            if arm == "be3":
                if triggered < 0 and side*(c-entry)+eps >= 3*risk:
                    triggered = i
                if triggered >= 0 and protected < 0:
                    if side*(c-be)+eps >= tick:
                        protected = i
                        if side*(be-stop) > eps:
                            stop, source = be, 1
                    else:
                        deferred += 1
            if trailing >= 0:
                raw = (c-side*atr_multiple*ctx["atr"][i])/tick
                candidate = (np.floor(raw+1e-8) if side > 0 else np.ceil(raw-1e-8))*tick
                if side*(candidate-stop) > 0:
                    stop, source = candidate, 2
        if trailing >= 0 and ctx["hour"][i] == -side:
            price, end, reason = c, i, 3
            break
        price = c
    return np.array([end,price,reason,peak,trailing,stop,0,steps,
                     triggered,protected,source,be,deferred], dtype=float)


def assert_equal(actual, expected, tick):
    assert_path_equal(actual[:8], expected[:8], tick)
    np.testing.assert_array_equal(actual[[8,9,10,12]], expected[[8,9,10,12]])
    np.testing.assert_allclose(actual[11],expected[11],rtol=0,atol=tick*1e-7)


def dump(path, value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+"\n")


def compare(treatment, baseline, seed):
    columns = ["pair_id","week","segment","reason","gross_r","net_r","peak_r","exit_i","net_notional_bp"]
    delta = treatment[columns].merge(baseline[columns], on="pair_id", suffixes=("","_baseline"), validate="one_to_one")
    delta["delta_net_r"] = delta.net_r-delta.net_r_baseline
    delta["delta_notional_bp"] = delta.net_notional_bp-delta.net_notional_bp_baseline
    saved = (delta.net_r_baseline < -1e-8) & (delta.net_r >= -1e-8)
    lost = (delta.net_r_baseline > 1e-8) & (delta.net_r <= 1e-8)
    improved, worsened = delta.delta_net_r > 1e-8, delta.delta_net_r < -1e-8
    test = inference(delta.assign(net_r_random=delta.net_r_baseline), seed)
    output = dict(n=len(delta), delta_net_r=float(delta.delta_net_r.sum()),
        delta_notional_bp=float(delta.delta_notional_bp.sum()),
        improved_n=int(improved.sum()), worsened_n=int(worsened.sum()),
        unchanged_n=int((~improved & ~worsened).sum()),
        improvement_r=float(delta.loc[improved,"delta_net_r"].sum()),
        deterioration_r=float(delta.loc[worsened,"delta_net_r"].sum()),
        baseline_losers_rescued=int(saved.sum()), baseline_winners_to_nonpositive=int(lost.sum()),
        saved_losers_gain_r=float(delta.loc[saved,"delta_net_r"].sum()),
        sacrificed_winners_loss_r=float(delta.loc[lost,"delta_net_r"].sum()),
        nominal_week_block_inference=test)
    for threshold in (10,20):
        winners = delta.gross_r_baseline >= threshold-1e-7
        kept = delta.gross_r >= threshold-1e-7
        output[f"baseline_gross_{threshold}r"] = int(winners.sum())
        output[f"retained_gross_{threshold}r"] = int((winners & kept).sum())
        output[f"lost_gross_{threshold}r"] = int((winners & ~kept).sum())
        output[f"lost_{threshold}r_delta_net_r"] = float(delta.loc[winners & ~kept,"delta_net_r"].sum())
    return output, delta


def main():
    cfg = json.loads((EXP/"config.json").read_text())
    old = pd.read_csv(ROOT/cfg["source_ledger"],float_precision="round_trip")
    source = old[["pair_id","entry_i","side","segment","week","bucket","end_i",
                  "entry_time","entry","stop","risk","population"]].copy()
    events = source[["entry_i","side","entry","stop","end_i"]].to_numpy()
    ctx, lib = load_context(), native()
    out = EXP/"results"
    out.mkdir(exist_ok=True)
    ledgers, summaries, audit_count = {}, [], 0
    for arm in cfg["arms"]:
        result = replay(ctx,events,arm,cfg["round_trip_cost"],cfg["tick"],lib)
        for row, actual in zip(events, result):
            expected = scalar(ctx,row,arm,cfg["round_trip_cost"],cfg["tick"])
            assert_equal(actual,expected,cfg["tick"])
            audit_count += 1
        if arm == "legacy":
            columns = ["exit_i","exit_price","reason","peak_r","arm_i","protection","ambiguous","steps"]
            for actual, original in zip(result,old[columns].to_numpy()):
                assert_path_equal(actual[:8],original,cfg["tick"])
        ledger = result_frame(source,result[:,:8],cfg)
        for i,key in enumerate(("be_trigger_i","be_effective_i","stop_kind","be_price","deferred_closes"),8):
            ledger[key] = result[:,i]
        ledger["arm"] = arm
        indices = ledger.exit_i.to_numpy(dtype=int)
        stop_exit = ledger.reason.isin([1,2,5]).to_numpy()
        gaps = stop_exit & (ledger.side.to_numpy()*(ctx["bars"][indices,0]-ledger.protection.to_numpy()) < -cfg["tick"]*1e-7)
        ledger["exit_time"] = ctx["frame"].index[indices]-pd.to_timedelta(gaps.astype(int)*5,unit="min")
        ledger["entry_shanghai"] = pd.to_datetime(ledger.entry_time).dt.tz_convert("Asia/Shanghai")
        ledger["exit_shanghai"] = pd.to_datetime(ledger.exit_time).dt.tz_convert("Asia/Shanghai")
        ledger["net_r"] = ledger.net_r.mask(ledger.net_r.abs() < 1e-9,0.)
        ledger.to_csv(out/f"trades_{arm}.csv",index=False)
        ledgers[arm] = ledger
        for seg in (-1,0,1,2):
            subset = ledger if seg == -1 else ledger[ledger.segment == seg]
            signal, random = subset[subset.population == "signal"], subset[subset.population == "random"]
            matched = pairs(signal,random)
            summaries.append(dict(arm=arm,segment=seg,**metrics(signal),
                be_triggered=int((signal.be_trigger_i >= 0).sum()),
                be_effective=int((signal.be_effective_i >= 0).sum()),
                be_stop_exits=int((signal.reason == 5).sum()),
                be_gap_losses=int(((signal.reason == 5)&(signal.net_r < -1e-8)).sum()),
                deferred_trades=int((signal.deferred_closes > 0).sum()),
                matched_n=len(matched),matched_signal_net_r=float(matched.net_r.sum()),
                matched_random_net_r=float(matched.net_r_random.sum()),
                matched_excess_inference=inference(matched,cfg["seed"])))
        print(f"audited {arm}: {len(ledger)} paths",flush=True)
    comparisons, differences = [], []
    for arm, baseline_arm in (("baseline","legacy"),("be3","baseline")):
        for seg in (-1,0,1,2):
            a,b = ledgers[arm],ledgers[baseline_arm]
            if seg >= 0:
                a,b = a[a.segment == seg],b[b.segment == seg]
            result,delta = compare(a[a.population=="signal"],b[b.population=="signal"],cfg["seed"])
            result.update(arm=arm,baseline=baseline_arm,segment=seg)
            # Difference-in-differences: policy gain on matched signals minus
            # the same policy gain on the original matched random controls.
            ra,rb = a[a.population=="random"],b[b.population=="random"]
            random_delta = ra[["pair_id","net_r"]].merge(rb[["pair_id","net_r"]],on="pair_id",suffixes=("","_base"))
            random_delta["random_policy_gain"] = random_delta.net_r-random_delta.net_r_base
            paired_delta = delta.merge(random_delta[["pair_id","random_policy_gain"]],on="pair_id")
            test_rows = paired_delta.assign(net_r=paired_delta.delta_net_r,net_r_random=paired_delta.random_policy_gain)
            result["matched_policy_increment"] = dict(n=len(test_rows),**inference(test_rows,cfg["seed"]))
            comparisons.append(result)
            if arm == "be3" and seg == -1:
                delta.to_csv(out/"signal_policy_differences.csv",index=False)
                differences = delta
    dump(out/"summary.json",summaries)
    dump(out/"comparisons.json",comparisons)
    be = ledgers["be3"]
    signal = be[be.population=="signal"]
    for label,frame in (
        ("largest_sacrifices",differences.sort_values("delta_net_r").head(20)),
        ("largest_rescues",differences.sort_values("delta_net_r",ascending=False).head(20))):
        full = signal.merge(frame[["pair_id","delta_net_r","gross_r_baseline","net_r_baseline"]],
                            on="pair_id").sort_values("delta_net_r",ascending=label=="largest_sacrifices")
        full.to_csv(out/f"{label}.csv",index=False)
    # Explicitly prove adding BE has not changed any entry/risk/cost or moved
    # an outcome beyond the original segment boundary.
    for arm,ledger in ledgers.items():
        assert (ledger.exit_i <= ledger.end_i).all()
        assert (ledger.exit_i > ledger.entry_i).all()
        np.testing.assert_array_equal(ledger.entry,old.entry)
        np.testing.assert_array_equal(ledger.risk,old.risk)
        np.testing.assert_allclose(ledger.fee_r,old.fee_r,rtol=0,atol=1e-10)
    dump(out/"audit.json",dict(builder_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        scalar_paths=audit_count,legacy_paths_matched=len(old),
        source_ledger=cfg["source_ledger"],source_data=cfg["source_data"],
        source_5m_rows=len(ctx["bars"]),signal_n=int((old.population=="signal").sum()),
        random_n=int((old.population=="random").sum()),
        frozen_entries_risk_cost_and_boundaries=True,config=cfg,
        reproduce=".venv/bin/python -m yoyo.evaluation.pepe_range_breakeven"))
    print(json.dumps([x for x in comparisons if x["segment"]==-1],indent=2))


if __name__ == "__main__":
    main()
