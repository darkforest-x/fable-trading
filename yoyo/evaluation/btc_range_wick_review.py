"""Audit owner drawings and add Monday endpoints to the frozen BTC study.

Owner clarification on 2026-10-21: "no losses" meant favorable opportunity,
not realized fixed-target wins. Features reuse completed 15m OHLC, Stoch5/3/3,
and Monday levels known before the touch candle. Only outcome diagnostics read
later 5m OHLC. Excursion excludes the first stop-touch candle conservatively.
Reuse frozen bars and native executions by entry/side/end identity; replay only
new identities through the existing Nautilus adapter, without another engine.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_range_wick_fanshen import candidates, prefix_audit
from yoyo.evaluation.pepe_range_exits import pairs, inference
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run, prepared

WORK = ROOT / "experiments/active/exp-btc-range-wick-fanshen-20261021-v2"
OLD = ROOT / "experiments/active/exp-btc-range-wick-fanshen-20261021-v1"
KEY = ["entry_i", "side", "end_i"]
META = ["trade_id", "pair_id", "population", "family", "segment", "week",
        "bucket", "entry_time", "levels", "signal_q", "touch_q", "touch_lag"]


def index_keys(frame):
    return list(frame[KEY].itertuples(index=False, name=None))


def excursions(bars, rows):
    """Future-only descriptive extremes before initial SL, never exit PnL.

    For each completed entry, inspect subsequent 5m high/low until the first
    initial-stop touch or segment end. Exclude the entire stop-touch bar for
    the lower bound; include it only in an explicitly ambiguous upper bound.
    These hypothetical best exits are not an executable strategy.
    """
    results = []
    for row in rows.itertuples():
        path = bars[int(row.entry_i)+1:int(row.end_i)+1]
        adverse = path[:, 2] <= row.stop if row.side == 1 else path[:, 1] >= row.stop
        hit = np.flatnonzero(adverse)
        n = int(hit[0]) if len(hit) else len(path)
        favorable = (path[:, 1]-row.entry if row.side == 1 else row.entry-path[:, 2])
        move = max(0., float(favorable[:n].max())) if n else 0.
        upper = max(move, float(favorable[n])) if len(hit) else move
        results.append(dict(
            trade_id=row.trade_id, mfe_r=move/row.risk,
            mfe_bp=move/row.entry*10000, mfe_upper_r=upper/row.risk,
            bars_before_stop=n, initial_stop_hit=bool(len(hit)),
            stop_i=int(row.entry_i)+1+n if len(hit) else -1))
    return pd.DataFrame(results)


def prepare():
    cfg = json.loads((WORK/"config.json").read_text())
    if (WORK/"preparation.json").exists():
        receipt = json.loads((WORK/"preparation.json").read_text())
        assert receipt["config"] == cfg
        return receipt
    q = pd.read_csv(OLD/"quarter.csv.gz", index_col="close_time", parse_dates=True)
    q.index = q.index.as_unit("ns")
    frame = pd.read_csv(OLD/"candles.csv.gz")
    times = pd.DatetimeIndex(pd.to_datetime(frame.ts, unit="ms", utc=True)
                            + pd.Timedelta(minutes=5)).as_unit("ns")
    events, missing = candidates(q, cfg)
    events = events[events.family == "combined"].copy()
    assert not missing
    events["entry_i"] = times.get_indexer(q.index[events.entry_q])
    bounds = pd.to_datetime([cfg["split"], cfg["end"]], utc=True)
    events["end_i"] = [times.searchsorted(bounds[int(s)], side="right")-1
                       for s in events.segment]
    old = pd.read_csv(OLD/"candidates.csv")
    lookup = dict(zip(index_keys(old), old.trade_id))
    events["reuse_trade_id"] = [lookup.get(k, "") for k in index_keys(events)]
    pending = events[events.reuse_trade_id.eq("")].drop_duplicates(KEY).copy()
    pending_lookup = dict(zip(index_keys(pending), pending.trade_id))
    events["new_trade_id"] = [pending_lookup.get(k, "") for k in index_keys(events)]
    events.to_csv(WORK/"candidates.csv", index=False)
    pending.to_csv(WORK/"pending_candidates.csv", index=False)
    (WORK/"quarter.csv.gz").symlink_to(OLD/"quarter.csv.gz")
    for tag in ("0p1", "0p2"):
        native = json.loads((OLD/f"native_b{tag}.json").read_text())
        native.update(experiment_id=cfg["experiment_id"],
                      entry_candidates=str((WORK/"pending_candidates.csv").relative_to(ROOT)),
                      output=str((WORK/f"native_b{tag}").relative_to(ROOT)))
        dump(WORK/f"native_b{tag}.json", native)
    real = events[events.population.eq("signal")]
    previous = old[(old.family=="combined") & (old.population=="signal")]
    joined = real.merge(previous[["signal_q", "entry_i"]], on="signal_q",
                        suffixes=("", "_old"), how="left")
    receipt = dict(config=cfg, source_commit=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip(),
        input=file_stamp(OLD/"candles.csv.gz"), quarter=file_stamp(OLD/"quarter.csv.gz"),
        signals=len(real), random=int(events.population.eq("random").sum()),
        reused_rows=int(events.reuse_trade_id.ne("").sum()),
        new_unique_paths=len(pending), added_signals=int(joined.entry_i_old.isna().sum()),
        changed_entry=int((joined.entry_i.ne(joined.entry_i_old)
                           & joined.entry_i_old.notna()).sum()),
        prefixes=prefix_audit(WORK/"config.json"))
    dump(WORK/"preparation.json", receipt)
    return receipt


def analyze():
    cfg = json.loads((WORK/"config.json").read_text())
    plan = pd.read_csv(WORK/"candidates.csv").fillna(
        {"reuse_trade_id": "", "new_trade_id": ""})
    frame = pd.read_csv(OLD/"candles.csv.gz")
    bars = frame[["open", "high", "low", "close"]].to_numpy()
    summaries, opportunities, inference_rows, checks = [], [], [], []
    from yoyo.evaluation.pepe_range_breakeven import replay_search
    for tag in ("0p1", "0p2"):
        native_cfg = json.loads((WORK/f"native_b{tag}.json").read_text())
        # Verify the unchanged engine and input contract before reusing rows.
        old_receipt = json.loads((OLD/f"native_b{tag}/receipt.json").read_text())
        for stamp in old_receipt["identity"]["code"]:
            assert file_stamp(stamp["path"]) == stamp
        assert file_stamp(ROOT/native_cfg["data"]) == old_receipt["identity"]["inputs"][0]
        ctx, pending, _ = prepared(native_cfg, WORK/".cache")
        for target in cfg["target_r"]:
            old = pd.read_csv(OLD/f"native_b{tag}/tp{target}/trades.csv",
                              float_precision="round_trip").set_index("trade_id")
            fresh = pd.read_csv(WORK/f"native_b{tag}/tp{target}/trades.csv",
                                float_precision="round_trip").set_index("trade_id")
            check = fresh.loc[pending.trade_id]
            oracle = replay_search(ctx, pending[KEY[:2]+["entry", "stop", "end_i"]]
                                   .to_numpy(float), tick=cfg["tick"],
                                   target_r=target, reverse_exit=False)
            np.testing.assert_array_equal(check.exit_i, oracle[:, 0])
            np.testing.assert_array_equal(check.reason, oracle[:, 2])
            np.testing.assert_allclose(check.exit_price, oracle[:, 1], rtol=0, atol=1e-7)
            records = []
            for meta in plan.to_dict("records"):
                source_id = meta["reuse_trade_id"] or meta["new_trade_id"]
                source = old if meta["reuse_trade_id"] else fresh
                row = source.loc[source_id].to_dict()
                assert tuple(row[k] for k in KEY) == tuple(meta[k] for k in KEY)
                row.update({k: meta[k] for k in META})
                row["source_trade_id"] = source_id
                row["reused"] = bool(meta["reuse_trade_id"])
                records.append(row)
            rows = pd.DataFrame(records)
            np.testing.assert_allclose(rows.net_r, rows.side*(rows.exit_price-rows.entry)
                /rows.risk-cfg["round_trip_cost"]*rows.entry/rows.risk, atol=1e-8, rtol=0)
            arm = f"b{tag}_tp{target}"
            rows.to_csv(WORK/f"annotated_{arm}.csv.gz", index=False)
            checks.append(dict(arm=arm, new_oracle_paths=len(check),
                               reused_rows=int(rows.reused.sum())))
            masks = dict(full=np.ones(len(rows), bool), first_year=rows.segment.eq(0),
                         later_year=rows.segment.eq(1),
                         september_onward=pd.to_datetime(rows.entry_time, utc=True)
                         .ge(pd.Timestamp(cfg["recent_start"])))
            for period, mask in masks.items():
                real = rows[mask & rows.population.eq("signal")]
                random = rows[rows.population.eq("random") & rows.pair_id.isin(real.pair_id)]
                paired = pairs(real, random)
                summaries.append(dict(arm=arm, period=period, n=len(real),
                    wins=int(real.net_r.gt(0).sum()), win_rate=float(real.net_r.gt(0).mean()),
                    net_r=float(real.net_r.sum()), random_net_r=float(random.net_r.sum()),
                    mean_net_bp=float(real.net_notional_bp.mean())))
                inference_rows.append(dict(arm=arm, period=period,
                                           **inference(paired, cfg["seed"])))
            if target == cfg["target_r"][0]:
                excursion = excursions(bars, rows)
                diagnostic = rows[META+["entry_i","end_i","side","entry","stop","risk"]].merge(
                    excursion, on="trade_id", validate="one_to_one")
                diagnostic.to_csv(WORK/f"excursions_b{tag}.csv", index=False)
                for period, mask in masks.items():
                    real = diagnostic[mask & diagnostic.population.eq("signal")]
                    random = diagnostic[diagnostic.population.eq("random")
                                        & diagnostic.pair_id.isin(real.pair_id)]
                    for metric, threshold in [("mfe_bp", 0), ("mfe_bp", 20),
                                               ("mfe_r", 1), ("mfe_r", 2),
                                               ("mfe_r", 3), ("mfe_r", 5), ("mfe_r", 10)]:
                        success = real[metric].gt(0) if threshold==0 else real[metric].ge(threshold)
                        control = random[metric].gt(0) if threshold==0 else random[metric].ge(threshold)
                        opportunities.append(dict(buffer=tag, period=period, metric=metric,
                            threshold=threshold, n=len(real), reached=int(success.sum()),
                            rate=float(success.mean()), random_n=len(random),
                            random_reached=int(control.sum()), random_rate=float(control.mean())))
    pd.DataFrame(summaries).to_csv(WORK/"summary.csv", index=False)
    pd.DataFrame(opportunities).to_csv(WORK/"opportunity_summary.csv", index=False)
    dump(WORK/"inference.json", inference_rows)
    receipt = dict(source_commit=subprocess.check_output(
        ["git","rev-parse","HEAD"],text=True).strip(), checks=checks,
        native_new_paths=sum(c["new_oracle_paths"] for c in checks),
        cached_rows=sum(c["reused_rows"] for c in checks),
        note="Favorable opportunity is hypothetical best price before initial SL, not realized profit. Lower bound excludes the entire first stop-touch 5m bar.",
        training_eligible=False, production_eligible=False)
    dump(WORK/"delivery_receipt.json", receipt)
    return receipt


def drawings():
    """Reconcile the seven UI observations against frozen causal candidates."""
    observations = json.loads((WORK/"drawing_observations.json").read_text())["drawings"]
    old = pd.read_csv(OLD/"candidates.csv")
    old = old[(old.family=="combined") & (old.population=="signal")].set_index("signal_q")
    q = pd.read_csv(OLD/"quarter.csv.gz", index_col="close_time", parse_dates=True)
    records = []
    for tag in ("0p1", "0p2"):
        rows = pd.read_csv(WORK/f"excursions_b{tag}.csv")
        rows = rows[rows.population=="signal"]
        times = pd.to_datetime(rows.entry_time, utc=True)
        outcomes = {r: pd.read_csv(WORK/f"annotated_b{tag}_tp{r}.csv.gz")
                    .set_index("trade_id") for r in (2, 3, 5, 10)}
        for observed in observations:
            time = pd.Timestamp(observed["candidate_close_beijing"], tz="Asia/Shanghai")
            match = rows[times.eq(time) & rows.side.eq(observed["side"])]
            assert len(match)==1
            row = match.iloc[0]
            prior = old.loc[row.signal_q] if row.signal_q in old.index else None
            result = dict(observed, buffer=tag, trade_id=row.trade_id,
                entry_close_beijing=str(time), actual_entry=float(row.entry),
                actual_stop=float(row.stop), initial_risk=float(row.risk),
                mfe_percent=float(row.mfe_bp/100), mfe_r=float(row.mfe_r),
                mfe_upper_r=float(row.mfe_upper_r), levels=row.levels,
                covered_20bp=bool(row.mfe_bp>=20), bars_before_stop=int(row.bars_before_stop),
                signal_open_beijing=str((q.index[int(row.signal_q)]-pd.Timedelta(minutes=15))
                                        .tz_convert("Asia/Shanghai")),
                touch_open_beijing=str((q.index[int(row.touch_q)]-pd.Timedelta(minutes=15))
                                       .tz_convert("Asia/Shanghai")),
                old_entry_time=str(prior.entry_time) if prior is not None else "",
                prior_status="missing_monday_endpoint" if prior is None else
                    ("entry_advanced_by_endpoint" if int(prior.entry_i)!=int(row.entry_i)
                     else "same_event"))
            result.update({f"fixed_{r}r_net":float(outcomes[r].loc[row.trade_id,"net_r"])
                           for r in outcomes})
            records.append(result)
    result=pd.DataFrame(records)
    result.to_csv(WORK/"drawing_reconciliation.csv",index=False)
    return dict(drawings_per_buffer=7, reconciled=len(result),
                covers_cost=result.groupby("buffer").covered_20bp.sum().to_dict())


def sensitivity():
    """Reuse old stop-first outcomes; screen only new candidate identities."""
    from yoyo.evaluation.pepe_range_breakeven import replay_search
    cfg = json.loads((WORK/"config.json").read_text())
    summaries = []
    for tag in ("0p1", "0p2"):
        native_cfg = json.loads((WORK/f"native_b{tag}.json").read_text())
        ctx, pending, _ = prepared(native_cfg, WORK/".cache")
        for target in cfg["target_r"]:
            rows = pd.read_csv(WORK/f"annotated_b{tag}_tp{target}.csv.gz")
            old = pd.read_csv(OLD/f"annotated_b{tag}_tp{target}.csv.gz")
            old = old.set_index("trade_id").selection_net_r
            conservative = replay_search(
                ctx, pending[["entry_i", "side", "entry", "stop", "end_i"]].to_numpy(float),
                tick=cfg["tick"], target_r=target, reverse_exit=False, stop_first=True)
            net = (pending.side.to_numpy()*(conservative[:, 1]-pending.entry.to_numpy())
                   - cfg["round_trip_cost"]*pending.entry.to_numpy())/pending.risk.to_numpy()
            new = dict(zip(pending.trade_id, net))
            rows["stop_first_net_r"] = [
                old.loc[r.source_trade_id] if r.reused else new[r.source_trade_id]
                for r in rows.itertuples()]
            for period, mask in {
                "full": np.ones(len(rows), bool),
                "september_onward": pd.to_datetime(rows.entry_time, utc=True)
                    .ge(pd.Timestamp(cfg["recent_start"])),
            }.items():
                real = rows[mask & rows.population.eq("signal")]
                control = rows[rows.population.eq("random") & rows.pair_id.isin(real.pair_id)]
                summaries.append(dict(arm=f"b{tag}_tp{target}", period=period, n=len(real),
                    native_net_r=float(real.net_r.sum()),
                    stop_first_net_r=float(real.stop_first_net_r.sum()),
                    random_native_net_r=float(control.net_r.sum()),
                    random_stop_first_net_r=float(control.stop_first_net_r.sum()),
                    changed_signals=int((real.stop_first_net_r-real.net_r).abs().gt(1e-7).sum())))
            rows[["trade_id", "net_r", "stop_first_net_r"]].to_csv(
                WORK/f"stop_first_b{tag}_tp{target}.csv.gz", index=False)
    result = pd.DataFrame(summaries)
    result.to_csv(WORK/"sensitivity_summary.csv", index=False)
    return dict(arms=8, native_replays=0,
                changed_signals=int(result[result.period.eq("full")].changed_signals.sum()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "analyze", "drawings", "sensitivity"])
    args = parser.parse_args()
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", __file__,
                    str(WORK/"config.json")], check=True, capture_output=True)
    if args.action == "prepare":
        result = prepare()
    elif args.action == "run":
        result = [run(WORK/f"native_b{tag}.json") for tag in ("0p1", "0p2")]
        result = {"completed_buffers": len(result)}
    elif args.action == "drawings":
        result = drawings()
    elif args.action == "sensitivity":
        result = sensitivity()
    else:
        result = analyze()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
