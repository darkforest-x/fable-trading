"""Summarize the fixed V1+ default replay without changing its trade decisions.

Only authenticated replay ledgers and existing marked-account output are used.
Event periods follow entry time; account periods follow calendar close NAV.
No profit, MFE or future coin rank is an input to signal selection. Exact-entry
pairs distinguish retained opportunities from changed subsequent admissions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import argparse
import hashlib
import json
from pathlib import Path

from yoyo.evaluation.spike_v7_v1_report import bools
from yoyo.evaluation.spike_exit_policy_study import load_verified_stream
from yoyo.evaluation.spike_exit_accounts import marked_account, period_summary

BASELINE = "v1_display_both"
PLUS = "v1_plus_default_both"
START = pd.Timestamp("2024-09-10T00:00:00Z")
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
END = pd.Timestamp("2026-09-10T00:00:00Z")
EXP = Path(__file__).resolve().parents[2] / "experiments/active/exp-spike-v1-plus-backtest-20260912-v1"
NAMED_ASSETS = {"BTC", "ETH", "SOL", "ZEC", "PEPE", "1000PEPE", "WIF", "TAO", "SOPH", "USELESS", "BICO", "DOGE", "SUI"}


def metrics(trades: pd.DataFrame) -> dict:
    """Describe realized unit-position events, excluding boundary estimates."""
    done = trades.loc[~bools(trades.censored)]
    net = pd.to_numeric(done.net_return, errors="raise")
    r = pd.to_numeric(done.net_r, errors="raise")
    if not np.isfinite(net).all() or not np.isfinite(r).all():
        raise ValueError("realized trades require finite returns")
    win, loss = net.gt(0), net.lt(0)
    positive, negative = float(net[win].sum()), float(-net[loss].sum())
    duration = (pd.to_datetime(done.exit_time, utc=True) - pd.to_datetime(done.entry_time, utc=True)).dt.total_seconds() / 3600
    if duration.lt(0).any():
        raise ValueError("negative holding time")
    return dict(entries=len(trades), realized=len(done), censored=len(trades)-len(done),
                wins=int(win.sum()), win_rate=float(win.mean()),
                event_pf=positive/negative if negative else np.nan,
                positive_return_sum=positive, negative_return_sum=negative,
                net_r_sum=float(r.sum()), mean_net_r=float(r.mean()),
                median_net_r=float(r.median()), mean_net_return=float(net.mean()),
                mean_winner_r=float(r[win].mean()), mean_loser_r=float(r[loss].mean()),
                realized_ge5r=int(r.ge(5).sum()), realized_ge10r=int(r.ge(10).sum()),
                mfe_ge10r=int(pd.to_numeric(done.mfe_r).ge(10).sum()),
                median_holding_hours=float(duration.median()))


def event_tables(trades: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """All-period and chronological year views; side attribution is not a new account."""
    t = trades.copy()
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
    out = []
    for period, a, b in (("full", START, END), ("development", START, SPLIT), ("validation", SPLIT, END)):
        window = t.loc[t.entry_time.ge(a) & t.entry_time.lt(b)]
        for group, part in window.groupby(keys, dropna=False, sort=True):
            group = group if isinstance(group, tuple) else (group,)
            for side in ("both", "long", "short"):
                selected = part if side == "both" else part.loc[part.side.eq(1 if side == "long" else -1)]
                out.append({**dict(zip(keys, group)), "period": period, "side_group": side, **metrics(selected)})
    return pd.DataFrame(out)


def exact_pairs(trades: pd.DataFrame) -> pd.DataFrame:
    """Outer pair by source stream, actual signal bar and direction, never return."""
    keys = ["stream_key", "signal_bar_open", "side"]
    fields = keys + ["entry_time", "entry_price", "net_r", "net_return", "mfe_r", "censored", "exit_reason"]
    t = trades.copy()
    t["signal_bar_open"] = pd.to_datetime(t.signal_bar_open, utc=True)
    t["censored"] = bools(t.censored)
    a = t.loc[t.cohort.eq(BASELINE), fields]
    b = t.loc[t.cohort.eq(PLUS), fields]
    if a.duplicated(keys).any() or b.duplicated(keys).any():
        raise ValueError("ambiguous exact signal pair")
    pair = a.merge(b, on=keys, how="outer", suffixes=("_base", "_plus"), indicator=True, validate="one_to_one")
    pair["both_realized"] = pair._merge.eq("both") & pair.censored_base.eq(False) & pair.censored_plus.eq(False)
    pair["realized_net_r_change"] = (pair.net_r_plus-pair.net_r_base).where(pair.both_realized)
    pair["baseline_realized_ge10r"] = pair.censored_base.eq(False) & pair.net_r_base.ge(10)
    pair["same_entry_retained_ge10r"] = pair.baseline_realized_ge10r & pair.censored_plus.eq(False) & pair.net_r_plus.ge(10)
    return pair


def signal_tables(signals: pd.DataFrame) -> pd.DataFrame:
    """Count decision-close raw/visible events independently of executable fills."""
    s = signals.copy()
    s["confirm_time"] = pd.to_datetime(s.bar_open, utc=True) + pd.to_timedelta(s.timeframe_min, unit="m")
    for key in ("raw_signal", "signal"):
        s[key] = bools(s[key])
    out = []
    for period,a,b in (("full",START,END),("development",START,SPLIT),("validation",SPLIT,END)):
        window=s.loc[s.confirm_time.ge(a)&s.confirm_time.lt(b)]
        for key,g in window.groupby(["cohort","timeframe_min"]):
            out.append(dict(cohort=key[0],timeframe_min=key[1],period=period,
                            raw_confirmations=int(g.raw_signal.sum()),visible_signals=int(g.signal.sum()),
                            accepted_references=int((g.signal&g.signal_reason.eq("accepted")).sum()),
                            overheat_rejected=int(g.signal_reason.eq("overheat_rejected").sum())))
    return pd.DataFrame(out)


def account_comparison(accounts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pair independent accounts; an average return is never a pooled portfolio."""
    keys = ["stream_key", "period"]
    values = ["net_return", "max_close_drawdown", "valid"]
    a = accounts.loc[accounts.cohort.eq(BASELINE), keys + values]
    b = accounts.loc[accounts.cohort.eq(PLUS), keys + values]
    paired = a.merge(b, on=keys, suffixes=("_base", "_plus"), validate="one_to_one", how="outer", indicator=True)
    paired["comparable"] = paired._merge.eq("both") & paired.valid_base.eq(True) & paired.valid_plus.eq(True)
    paired["return_change"] = (paired.net_return_plus-paired.net_return_base).where(paired.comparable)
    paired["drawdown_change"] = (paired.max_close_drawdown_plus-paired.max_close_drawdown_base).where(paired.comparable)
    ids = accounts[["stream_key", "venue", "timeframe_min"]].drop_duplicates()
    if ids.stream_key.duplicated().any():
        raise ValueError("stream identity changed")
    paired = paired.merge(ids, on="stream_key", how="left", validate="many_to_one")
    out = []
    for key, group in paired.groupby(["period", "timeframe_min"], sort=True):
        ok = group.loc[group.comparable]
        out.append(dict(period=key[0], timeframe_min=key[1], accounts=len(group), comparable=len(ok),
                        mean_base_return=float(ok.net_return_base.mean()), mean_plus_return=float(ok.net_return_plus.mean()),
                        median_base_return=float(ok.net_return_base.median()), median_plus_return=float(ok.net_return_plus.median()),
                        mean_base_drawdown=float(ok.max_close_drawdown_base.mean()), mean_plus_drawdown=float(ok.max_close_drawdown_plus.mean()),
                        worst_base_drawdown=float(ok.max_close_drawdown_base.max()), worst_plus_drawdown=float(ok.max_close_drawdown_plus.max()),
                        profitable_base=int(ok.net_return_base.gt(0).sum()), profitable_plus=int(ok.net_return_plus.gt(0).sum()),
                        improved_accounts=int(ok.return_change.gt(0).sum()), worsened_accounts=int(ok.return_change.lt(0).sum())))
    return paired, pd.DataFrame(out)


def concentration_table(trades: pd.DataFrame) -> pd.DataFrame:
    """Retrospective sensitivity only; removal never changes any signal or account."""
    t = trades.loc[~bools(trades.censored)].copy()
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
    rows = []
    for period, left, right in (("full", START, END), ("development", START, SPLIT), ("validation", SPLIT, END)):
        window = t.loc[t.entry_time.ge(left) & t.entry_time.lt(right)]
        for (cohort, minutes), group in window.groupby(["cohort", "timeframe_min"]):
            positive = group.loc[group.net_r.gt(0)]
            total = float(group.net_r.sum())
            largest = positive.net_r.nlargest(10)
            asset_profit = positive.assign(underlying=positive.asset.replace({"1000PEPE":"PEPE"})).groupby("underlying").net_r.sum()
            pos = float(positive.net_r.sum())
            rows.append(dict(period=period, cohort=cohort, timeframe_min=minutes,
                total_net_r=total, without_top1_net_r=total-float(largest.head(1).sum()),
                without_top5_net_r=total-float(largest.head(5).sum()),
                without_top10_net_r=total-float(largest.sum()),
                top_asset_positive_r_share=float(asset_profit.max()/pos) if pos else np.nan,
                top_asset=str(asset_profit.idxmax()) if pos else ""))
    return pd.DataFrame(rows)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, converters={name:str for name in ("asset", "symbol", "venue", "cohort", "stream_key")})
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def run(engine: Path, output: Path) -> None:
    """Read every completed arm, verify hashes, and build independent accounts."""
    config = json.loads((EXP / "config.json").read_text())
    manifest = json.loads((engine / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("completed_streams") != config["expected_streams"]:
        raise ValueError("full economic report requires the complete frozen pool")
    if manifest["identity"]["config_sha256"] != _hash(EXP / "config.json"):
        raise ValueError("configuration differs from completed replay")
    raw = Path(config["raw"])
    if _hash(raw / "manifest.json") != config["raw_manifest_sha256"]:
        raise ValueError("source manifest changed")
    output.mkdir(parents=True, exist_ok=True)
    all_trades, all_fills, all_signals, account_rows, control_frames = [], [], [], [], []
    dirs = sorted((engine / "streams").glob("*"))
    dirs = [p for p in dirs if p.is_dir() and not p.name.startswith(".")]
    if len(dirs) != config["expected_streams"]:
        raise ValueError("stream directory count differs from frozen pool")
    for n, folder in enumerate(dirs, 1):
        receipt = json.loads((folder / "completion.json").read_text())
        if receipt.get("identity") != manifest["identity"] or receipt.get("status") != "complete":
            raise ValueError(f"stream receipt not complete: {folder.name}")
        for name in ("trades", "fills", "signals", "events"):
            if _hash(folder / f"{name}.csv.gz") != receipt["files"][name]:
                raise ValueError(f"output hash mismatch: {folder.name}/{name}")
        t, f, s = (_read(folder / f"{name}.csv.gz") for name in ("trades", "fills", "signals"))
        context = load_verified_stream(raw / "streams" / folder.name)
        if receipt["source_sha256"] != context.receipt["source_sha256"]:
            raise ValueError("new replay and account source differ")
        if float(receipt["tick"]) != float(context.cache["tick"]):
            raise ValueError(f"frozen price tick differs: {folder.name}")
        for table in (t, f, s):
            if len(table):
                if not table.stream_key.eq(context.key).all():
                    raise ValueError(f"stream key differs: {folder.name}")
                for name, value in context.identity.items():
                    if not table[name].eq(value).all():
                        raise ValueError(f"stream {name} differs: {folder.name}")
        for variant in (BASELINE, PLUS):
            vt = t.loc[t.cohort.eq(variant)].copy() if len(t) else t.copy()
            vf = f.loc[f.cohort.eq(variant)].copy() if len(f) else f.copy()
            path, allocations, metadata = marked_account(context.cache["bars"], vt, vf, risk_fraction=config["risk_fraction"],
                notional_cap=config["entry_notional_cap"], initial=config["initial_account"], roundtrip_cost=config["roundtrip_cost"],
                start=config["window_start"], end=config["window_end"], minutes=context.minutes)
            for row in period_summary(path, initial=config["initial_account"], start=config["window_start"],
                                      split=config["development_end"], end=config["window_end"]):
                account_rows.append({"stream_key":folder.name, **context.identity, "cohort":variant, **row, **metadata})
            if context.identity["asset"] in NAMED_ASSETS:
                target = output / "named_account_paths" / folder.name
                target.mkdir(parents=True, exist_ok=True)
                path.to_csv(target / f"{variant}.nav.csv.gz", index=False, compression="gzip")
                allocations.to_csv(target / f"{variant}.allocations.csv.gz", index=False, compression="gzip")
        if len(t):
            all_trades.append(t)
            from yoyo.evaluation.spike_v1_plus_controls import matched_benchmark
            control_frames.append(matched_benchmark(context, t, config))
        if len(f): all_fills.append(f)
        if len(s): all_signals.append(s)
        if n % 100 == 0 or n == len(dirs): print(f"post {n}/{len(dirs)}", flush=True)
    trades = pd.concat(all_trades, ignore_index=True)
    fills = pd.concat(all_fills, ignore_index=True)
    signals = pd.concat(all_signals, ignore_index=True)
    accounts = pd.DataFrame(account_rows)
    pairs = exact_pairs(trades)
    account_pairs, account_summary = account_comparison(accounts)
    controls = pd.concat(control_frames, ignore_index=True) if control_frames else pd.DataFrame()
    from yoyo.evaluation.spike_v1_plus_controls import benchmark_summary
    tables = {"trades.csv.gz":trades, "fills.csv.gz":fills, "signals.csv.gz":signals,
              "signal_summary.csv":signal_tables(signals),
              "event_summary.csv":event_tables(trades, ["cohort", "timeframe_min"]),
              "venue_event_summary.csv":event_tables(trades, ["venue", "cohort", "timeframe_min"]),
              "coin_event_summary.csv":event_tables(trades, ["venue", "symbol", "asset", "cohort", "timeframe_min"]),
              "independent_accounts.csv":accounts, "account_pairs.csv":account_pairs,
              "account_summary.csv":account_summary, "same_entry_pairs.csv.gz":pairs,
              "concentration.csv":concentration_table(trades),
              "matched_benchmark.csv.gz":controls, "matched_benchmark_summary.csv":benchmark_summary(controls),
              "exit_reasons.csv":trades.groupby(["cohort", "timeframe_min", "side", "exit_reason", "censored"]).agg(
                  trades=("trade_id", "size"), net_r=("net_r", "sum")).reset_index()}
    for name, table in tables.items(): table.to_csv(output / name, index=False, compression="infer")
    result = dict(complete=True, config_sha256=_hash(EXP/"config.json"), engine_manifest_sha256=_hash(engine/"manifest.json"),
                  source_code_sha256={str(p):_hash(p) for p in (Path(__file__),Path(__file__).with_name("spike_v1_plus_controls.py"),
                      Path(__file__).with_name("spike_exit_accounts.py"),Path(__file__).with_name("spike_v1_plus_replay.py"))}, streams=len(dirs), simulated_trades=len(trades),
                  outputs={name:_hash(output/name) for name in tables},
                  caveat="Independent accounts; close drawdown. Matched null omits reverse exits symmetrically and is separate from actual strategy returns.")
    (output/"post_manifest.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.engine, args.output)
