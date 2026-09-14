"""Causal one-time four-hour failure exit on frozen V8 serial execution.

The rule changes no admission, stop, raw V6 reverse, or 0.2% round-trip cost.
After the first *completed* bar at least four hours after a real next-open
fill, it schedules one next-open exit only when observed MFE is below 1R and
that bar's gross close R is non-positive.  Cached authenticated streams only.
"""
from __future__ import annotations

import argparse, hashlib, json, math
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as common

EXP = Path("experiments/active/exp-spike-v8-six-filters-20260914-v1")
OUT = EXP / "exit"
RAW = common.RAW
SAVED = Path("experiments/active/exp-spike-v1-v8-be05-20260914-v1/common/results/full_v3/streams")
POLICY = "four_hour_mfe_lt1_close_nonpositive_next_open"
KEY = common.KEY
EXTRA = ["four_hour_checked", "four_hour_triggered", "four_hour_check_bar_open", "four_hour_mfe_r", "four_hour_close_gross_r"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close(pos: dict[str, Any], *, i: int, stamp: pd.Timestamp, price: float, reason: str,
           phase: str, context: base.StreamContext, fills: list[dict[str, Any]], trades: list[dict[str, Any]]) -> int:
    side, fraction = int(pos["side"]), float(pos["qty_remaining"])
    gross = fraction * side * (price / float(pos["entry_price"]) - 1.)
    pos["qty_realized"] += fraction; pos["qty_remaining"] = 0.
    pos["realized_gross_return"] += gross; pos["realized_net_return"] += gross - fraction * base.EXIT_COST
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, price, reason
    base._append_fill(fills, pos, leg_no=int(pos.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                      phase=phase, precision="bar_open" if phase == "open" else "within_bar", kind="exit",
                      fraction=fraction, price=price, cost=fraction * base.EXIT_COST, reason=reason, context=context)
    trades.append(_row(pos, censored=False, precision="bar_open_or_intrabar_window")); return side


def _row(pos: dict[str, Any], *, censored: bool, precision: str) -> dict[str, Any]:
    return base._trade_row(pos, censored=censored, precision=precision) | {key: pos.get(key) for key in EXTRA}


def _check_four_hour(pos: dict[str, Any], *, stamp: pd.Timestamp, minutes: int, high: float, low: float, close: float) -> bool:
    """Evaluate exactly once at the first completed bar reaching four hours."""
    if bool(pos["four_hour_checked"]) or stamp + pd.Timedelta(minutes=minutes) < pd.Timestamp(pos["entry_time"]) + pd.Timedelta(hours=4):
        return False
    side, entry, risk = int(pos["side"]), float(pos["entry_price"]), float(pos["initial_risk"])
    pos["four_hour_checked"] = True; pos["four_hour_check_bar_open"] = stamp
    pos["four_hour_mfe_r"] = float(pos["mfe_r"])
    pos["four_hour_close_gross_r"] = side * (close - entry) / risk
    pos["four_hour_triggered"] = bool(pos["mfe_r"] < 1.0 and pos["four_hour_close_gross_r"] <= 0.)
    return bool(pos["four_hour_triggered"])


def _new(made: dict[str, Any], *, context: base.StreamContext, policy: str, trade_id: str) -> dict[str, Any]:
    pos = base._new_trade(made, trade_id=trade_id, cohort="v7_both", policy=policy, context=context)
    pos.update(mfe_r=0., trail_armed=False, four_hour_checked=False, four_hour_triggered=False,
               four_hour_check_bar_open=pd.NaT, four_hour_mfe_r=math.nan, four_hour_close_gross_r=math.nan)
    return pos


def replay_serial(context: base.StreamContext, *, candidate: bool) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Full V8 serial replay; candidate exit can change later entries causally."""
    prepared = common.prepare_arm(context, arm="v8")
    context, frame, spec = prepared.context, prepared.frame, prepared.spec
    oa, ha, la, ca, aa = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    gap, allowed, raw = prepared.gap, prepared.allowed, prepared.raw_side
    ordinal = prepared.ordinal; policy = POLICY if candidate else "baseline"
    trades: list[dict[str, Any]] = []; fills: list[dict[str, Any]] = []; events: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None; pending_entry: tuple[int, int] | None = None
    pending_reverse: tuple[int, int] | None = None; pending_four: tuple[int, int] | None = None; next_id = 0
    for i, stamp in enumerate(frame.index):
        ended = 0
        if gap[i]:
            if pos is not None:
                pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = i, stamp, "data_gap_censored"
                trades.append(_row(pos, censored=True, precision="unknown_gap"))
            pos = None; pending_entry = pending_reverse = pending_four = None; continue
        if pos is not None:
            side, stop = int(pos["side"]), float(pos["protection"])
            if oa[i] <= stop if side == 1 else oa[i] >= stop:
                reason = "trailing_stop_gap" if stop != float(pos["initial_stop"]) else "initial_stop_gap"
                ended = _close(pos, i=i, stamp=stamp, price=float(oa[i]), reason=reason, phase="open", context=context, fills=fills, trades=trades)
                pos = None; pending_reverse = pending_four = None
        # Raw V6 reverse retains its original next-open precedence.
        if pos is not None and pending_reverse is not None:
            _, old_side = pending_reverse
            if int(pos["side"]) == old_side:
                ended = _close(pos, i=i, stamp=stamp, price=float(oa[i]), reason="opposite_v6_next_open", phase="open", context=context, fills=fills, trades=trades)
                pos = None
            pending_reverse = None; pending_four = None
        if pos is not None and pending_four is not None:
            _, owner = pending_four
            if int(pos["side"]) == owner:
                ended = _close(pos, i=i, stamp=stamp, price=float(oa[i]), reason="four_hour_mfe_lt1_close_nonpositive_next_open", phase="open", context=context, fills=fills, trades=trades)
                pos = None
            pending_four = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if pos is None and side != ended:
                made = base._initial_position_fast(frame.index, oa, ha, la, ca, aa, gap, signal_i, side, spec)
                if made is not None:
                    made["initial_risk_frac"] = float(made["initial_risk"]) / float(made["entry_price"])
                    original = ordinal.get(frame.index[signal_i])
                    if original is not None: made["signal_i"], made["entry_i"], made["frozen_index_offset"] = original, original + 1, original - signal_i
                    next_id += 1; pos = _new(made, context=context, policy=policy, trade_id=f"{context.key}:v8:{policy}:{next_id}")
                    base._append_fill(fills, pos, leg_no=1, bar_open=stamp, event_time=stamp, phase="open", precision="bar_open", kind="entry", fraction=1., price=float(pos["entry_price"]), cost=base.ENTRY_COST, reason="next_open_entry", context=context)
            pending_entry = None
        if pos is not None:
            side, stop = int(pos["side"]), float(pos["protection"])
            if la[i] <= stop if side == 1 else ha[i] >= stop:
                price = min(oa[i], stop) if side == 1 else max(oa[i], stop)
                reason = "trailing_stop" if stop != float(pos["initial_stop"]) else "initial_stop"
                if oa[i] <= stop if side == 1 else oa[i] >= stop: reason += "_gap"
                ended = _close(pos, i=i, stamp=stamp, price=float(price), reason=reason, phase="open" if reason.endswith("_gap") else "intrabar", context=context, fills=fills, trades=trades)
                pos = None; pending_reverse = pending_four = None
            else:
                common._close_updates(pos, high=float(ha[i]), low=float(la[i]), close=float(ca[i]), atr=float(aa[i]), spec=spec, enable_be=False, events=[], context=context, arm="v8", policy=policy, i=i)
                if candidate and _check_four_hour(pos, stamp=stamp, minutes=context.minutes, high=float(ha[i]), low=float(la[i]), close=float(ca[i])):
                    pending_four = (i, int(pos["side"])); events.append({"trade_id": pos["trade_id"], "bar_open": stamp, "event_kind": "exit_scheduled", "reason": POLICY})
        signal_side = int(raw[i])
        if signal_side:
            if pos is not None and signal_side != int(pos["side"]):
                pending_reverse = (i, int(pos["side"]))
                if allowed[i]: pending_entry = (i, signal_side)
            elif pos is None and signal_side != ended and allowed[i]: pending_entry = (i, signal_side)
    if pos is not None:
        last, stamp = len(frame)-1, frame.index[-1]
        pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = last, stamp, float(ca[last]), "boundary_mark"
        trades.append(_row(pos, censored=True, precision="last_complete_close"))
    return pd.DataFrame(trades, columns=[*base.TRADE_COLUMNS, *EXTRA]), pd.DataFrame(fills, columns=base.FILL_COLUMNS), pd.DataFrame(events)


def validate_saved_baseline(context: base.StreamContext, actual: pd.DataFrame) -> str:
    """Require exact baseline parity with the completed common BE05 serial file."""
    from pandas.testing import assert_frame_equal
    path = SAVED / context.key / "v8.serial_baseline.csv.gz"
    old = pd.read_csv(path); got = actual.loc[~actual.censored.astype(bool)]
    assert_frame_equal(old.loc[~old.censored.astype(bool)].reindex(columns=KEY).sort_values("signal_i").reset_index(drop=True), got.reindex(columns=KEY).sort_values("signal_i").reset_index(drop=True), check_dtype=False, rtol=1e-9, atol=1e-9)
    return sha256(path)


def _fixed(context: base.StreamContext, row: pd.Series, *, candidate: bool) -> dict[str, Any]:
    """Fixed original-entry path; raw reverse remains a next-open exit feed."""
    prepared = common.prepare_arm(context, arm="v8"); frame, spec = prepared.frame, prepared.spec
    oa, ha, la, ca, aa, gap, raw = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr, prepared.gap, prepared.raw_side
    start = int(frame.index.get_loc(pd.Timestamp(row.entry_time)))
    data = {k: row[k] for k in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price", "initial_stop", "initial_risk", "initial_risk_frac")}
    data["frozen_index_offset"] = int(row.entry_i) - start
    pos = _new(data, context=prepared.context, policy=POLICY if candidate else "baseline", trade_id=f"{context.key}:v8:fixed")
    pos["protection"] = float(row.initial_stop); pending_reverse = False; pending_four = False
    for i in range(start, len(frame)):
        stamp = frame.index[i]
        if gap[i]: pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = i, stamp, "data_gap_censored"; return _row(pos, censored=True, precision="unknown_gap")
        side, stop = int(pos["side"]), float(pos["protection"])
        if oa[i] <= stop if side == 1 else oa[i] >= stop:
            price = float(oa[i]); reason = "trailing_stop_gap" if stop != float(pos["initial_stop"]) else "initial_stop_gap"
            pos["qty_realized"], pos["qty_remaining"], pos["realized_gross_return"], pos["realized_net_return"] = 1., 0., side*(price/float(pos["entry_price"])-1), side*(price/float(pos["entry_price"])-1)-.002
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, price, reason; return _row(pos,censored=False,precision="bar_open_or_intrabar_window")
        if pending_reverse or pending_four:
            price=float(oa[i]); reason="opposite_v6_next_open" if pending_reverse else POLICY
            pos["qty_realized"], pos["qty_remaining"], pos["realized_gross_return"], pos["realized_net_return"] = 1.,0.,side*(price/float(pos["entry_price"])-1),side*(price/float(pos["entry_price"])-1)-.002
            pos["last_exit_i"],pos["last_exit_time"],pos["last_exit_price"],pos["last_exit_reason"]=i,stamp,price,reason; return _row(pos,censored=False,precision="bar_open_or_intrabar_window")
        if la[i] <= stop if side == 1 else ha[i] >= stop:
            price=min(oa[i],stop) if side==1 else max(oa[i],stop); reason="trailing_stop" if stop!=float(pos["initial_stop"]) else "initial_stop"
            pos["qty_realized"],pos["qty_remaining"],pos["realized_gross_return"],pos["realized_net_return"]=1.,0.,side*(price/float(pos["entry_price"])-1),side*(price/float(pos["entry_price"])-1)-.002
            pos["last_exit_i"],pos["last_exit_time"],pos["last_exit_price"],pos["last_exit_reason"]=i,stamp,price,reason; return _row(pos,censored=False,precision="bar_open_or_intrabar_window")
        common._close_updates(pos, high=float(ha[i]), low=float(la[i]), close=float(ca[i]), atr=float(aa[i]), spec=spec, enable_be=False, events=[], context=prepared.context, arm="v8", policy=pos["policy"], i=i)
        if candidate and _check_four_hour(pos, stamp=stamp, minutes=prepared.context.minutes, high=float(ha[i]), low=float(la[i]), close=float(ca[i])): pending_four=True
        if int(raw[i]) == -side: pending_reverse=True
    pos["last_exit_i"],pos["last_exit_time"],pos["last_exit_price"],pos["last_exit_reason"]=len(frame)-1,frame.index[-1],float(ca[-1]),"boundary_mark"; return _row(pos,censored=True,precision="last_complete_close")


def _gzip(path: Path, data: pd.DataFrame) -> None:
    data.to_csv(path, index=False, compression={"method":"gzip","mtime":0})


def run(output: Path, *, limit: int | None = None) -> dict[str, Any]:
    folders = sorted(p for p in (RAW / "streams").iterdir() if (p / "completion.json").is_file())
    if len(folders) != 3531: raise ValueError("authenticated source stream count drift")
    folders = folders if limit is None else folders[:limit]; output.mkdir(parents=True, exist_ok=True); streams = output / "streams"; streams.mkdir(exist_ok=True)
    started=perf_counter(); all_serial=[]; all_fixed=[]; hashes=[]
    for n, folder in enumerate(folders,1):
        context=base.load_verified_stream(folder); baseline,_,_=replay_serial(context,candidate=False); old_sha=validate_saved_baseline(context,baseline)
        candidate,_,_=replay_serial(context,candidate=True)
        fixed_base=pd.DataFrame([_fixed(context,row,candidate=False) for _,row in baseline.iterrows()]); fixed_candidate=pd.DataFrame([_fixed(context,row,candidate=True) for _,row in baseline.iterrows()])
        common.validate_fixed_baseline(baseline, fixed_base)
        _gzip(streams/f"{context.key}.serial_baseline.csv.gz",baseline); _gzip(streams/f"{context.key}.serial_four_hour.csv.gz",candidate)
        _gzip(streams/f"{context.key}.fixed_baseline.csv.gz",fixed_base); _gzip(streams/f"{context.key}.fixed_four_hour.csv.gz",fixed_candidate)
        hashes.append({"stream_key":context.key,"saved_baseline_sha256":old_sha}); all_serial += [baseline,candidate]; all_fixed += [fixed_base,fixed_candidate]
        if n%25==0 or n==len(folders): print(json.dumps({"streams":n,"target":len(folders),"seconds":round(perf_counter()-started,1)}),flush=True)
    serial=pd.concat(all_serial,ignore_index=True); fixed=pd.concat(all_fixed,ignore_index=True)
    _gzip(output/"serial_trades.csv.gz",serial); _gzip(output/"fixed_original_entries.csv.gz",fixed); pd.DataFrame(hashes).to_csv(output/"baseline_sha_manifest.csv",index=False)
    cand=serial.loc[serial.policy.eq(POLICY)]; receipt={"status":"complete","streams":len(folders),"baseline_parity":True,"builder_sha256":sha256(Path(__file__)),"source_raw_manifest_sha256":sha256(RAW/"manifest.json"),"saved_common_manifest_sha256":sha256(SAVED.parent/"manifest.json"),"cost":.002,"rule":"first completed bar at least 4h after entry; observed MFE<1R and gross closeR<=0; next open once","candidate_serial_trades":len(cand),"four_hour_checked":int(cand.four_hour_checked.fillna(False).sum()),"four_hour_triggered":int(cand.four_hour_triggered.fillna(False).sum()),"ended_before_four_hour":int((~cand.four_hour_checked.fillna(False)).sum()),"elapsed_seconds":perf_counter()-started}
    receipt["files"]={x:sha256(output/x) for x in ("serial_trades.csv.gz","fixed_original_entries.csv.gz","baseline_sha_manifest.csv")}; (output/"receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n"); return receipt


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--output",type=Path,default=OUT); p.add_argument("--limit",type=int); a=p.parse_args(); print(json.dumps(run(a.output,limit=a.limit),indent=2))
if __name__ == "__main__": main()
