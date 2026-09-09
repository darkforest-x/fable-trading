"""Reproducible frozen eight-coin historical transfer experiment.

Decision definitions are in mainstream_features and the registered plan.
This runner reads only committed builders and verified 15m prefixes, creates
complete UTC bars, and evaluates the twelve predeclared long entry rules with
one fixed-quantity sleeve per coin. No fit, parameter search, live integration
or model inference. Future OHLC is passed exclusively to outcome accounting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.imacd_formation_research import aggregate, read_prefix, inference, ranking
from yoyo.evaluation.altcoin_transfer_research import combine_sleeves
from yoyo.evaluation.altcoin_trend_research import holm
from yoyo.evaluation.mainstream_features import ARMS, TIMEFRAMES, enrich, add_context, candidate_events
from yoyo.evaluation.mainstream_execution import evaluate_book

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analysis/output/mainstream_super_trend_20260910"
PLAN = "experiments/active/exp-mainstream-super-trend-20260910-v1/PROJECT_PLAN.md"
SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX")
HASHES = (
    "4989c77c81b164587208826081f38f38e4b9852b7695e735e291181c41476d8c",
    "856ba7c29b0471a8143b5bcac42e527775149475033ac0087448aa4a23ccc71e",
    "dc5ce9d209c5714a211a717d62d7bd9853522fd69771184c279b6e24b20b47f5",
    "90fcf7f368384c621a2055348def8b880f0b48f2ddbbf3a8e023d053d4b35f82",
    "c7723a5a232879b4091326cd637a817dcba280e7f090043cd73e449158c5aada",
    "e6243bd002b9ef23b8a166c4b9b84c78ebadc44c3ac4ee597e58de547f554e1d",
    "5ff637ee551ea5a6a7adb79608333b67eb33bffb0563b1bcfac28b9b71a3d315",
    "61c1b146638f60e639e899b3c1840b6b6f5e0ee2d52e233b14fb35ad3e15bba2")
END = pd.Timestamp("2026-09-08T16:00Z")
FOLDS = (("calibration", "2023-01-01", "2025-01-01"),
         ("validation", "2025-01-01", "2026-01-01"),
         ("audit_pre", "2026-01-01", "2026-05-04"),
         ("recent", "2026-05-04", "2026-09-08T16:00"))
BUILDERS = (PLAN, "yoyo/evaluation/mainstream_research.py", "yoyo/evaluation/mainstream_features.py",
    "yoyo/evaluation/mainstream_execution.py", "yoyo/data/altcoin_features.py",
    "yoyo/evaluation/altcoin_accounting.py", "yoyo/evaluation/imacd_formation_research.py",
    "yoyo/evaluation/altcoin_transfer_research.py", "yoyo/evaluation/altcoin_trend_research.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def committed_sources():
    records = []
    for name in BUILDERS:
        committed = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT)
        sha = digest(ROOT/name)
        if hashlib.sha256(committed).hexdigest() != sha:
            raise ValueError("Commit this builder before real computation: " + name)
        records.append(dict(path=name, sha256=sha))
    return records


def dump(value, path):
    def plain(v):
        if isinstance(v, dict): return {str(k): plain(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)): return [plain(x) for x in v]
        if isinstance(v, (np.integer, np.bool_)): return v.item()
        if isinstance(v, (float, np.floating)): return float(v) if np.isfinite(v) else None
        if isinstance(v, (pd.Timestamp, Path)): return str(v)
        return v
    Path(path).write_text(json.dumps(plain(value), indent=2, ensure_ascii=False) + "\n")


def curve_metrics(curve):
    peak = np.maximum.accumulate(np.r_[1., curve.to_numpy()])[1:]
    return dict(net_pct=100*(curve.iloc[-1]-1), mdd_pct=100*np.max(1-curve.to_numpy()/peak))


def trade_metrics(g):
    selected = g.loc[g.portfolio_selected.eq(True)]
    natural = selected.loc[selected.natural_exit.eq(True)]
    positive = natural.portfolio_net_pnl.clip(lower=0).sum()
    negative = -natural.portfolio_net_pnl.clip(upper=0).sum()
    return dict(candidates=len(g), trades=len(selected), natural_trades=len(natural),
        censored=int(selected.censored.eq(True).sum()),
        win_pct=100*natural.net_return.gt(0).mean(),
        pf=positive/negative if negative else (np.inf if positive else np.nan),
        mean_net_r=natural.net_r.mean(), win_3r=int(natural.net_r.ge(3).sum()),
        win_5r=int(natural.net_r.ge(5).sum()),
        top3_profit_share_pct=100*natural.portfolio_net_pnl.nlargest(3).clip(lower=0).sum()/positive if positive else np.nan)


def verify_cached_books(out):
    """Validate cached bytes against the exact committed generator contract."""
    manifest = json.loads((out/"manifest.json").read_text())
    current = committed_sources()
    if manifest["builders"] != current:
        raise ValueError("Cached builders differ from the current committed contract")
    fingerprint = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    for record in manifest["data"]:
        if digest(ROOT/record["path"]) != record["sha256"]:
            raise ValueError("Cached source changed")
        folder = out/record["symbol"]
        marker = json.loads((folder/"completion.json").read_text())
        if marker["fingerprint"] != fingerprint or marker["source_sha256"] != record["sha256"]:
            raise ValueError("Cached contract changed")
        for name, sha in marker["files"].items():
            if digest(folder/name) != sha:
                raise ValueError("Cached artifact changed: " + name)
    return manifest


def summarize(out):
    """Complete all frozen arms, including empty sleeves and adverse results."""
    verify_cached_books(out)
    evs, controls, diagnostics, all_curves = [], [], [], {}
    for symbol in SYMBOLS:
        p = out/symbol
        evs.append(pd.read_pickle(p/"events.pkl.gz", compression="gzip"))
        controls.append(pd.read_pickle(p/"controls.pkl.gz", compression="gzip"))
        diagnostics += json.loads((p/"diagnostics.json").read_text())
        all_curves[symbol] = pd.read_pickle(p/"curves.pkl.gz", compression="gzip")
    events = pd.concat(evs, ignore_index=True)
    events.to_csv(out/"events.csv.gz", index=False, compression="gzip")
    pd.concat(controls, ignore_index=True).to_csv(out/"controls.csv.gz", index=False, compression="gzip")
    pd.DataFrame(diagnostics).to_csv(out/"diagnostics.csv", index=False)
    rows, coin_rows, ranks, portfolios = [], [], [], {}
    for fold, start, end in FOLDS:
        for minutes in TIMEFRAMES:
            g = events.loc[events.fold.eq(fold) & events.minutes.eq(minutes)]
            baseline = g.loc[g.arm.eq("baseline") & g.portfolio_selected.eq(True) & g.natural_exit.eq(True)]
            winners = set(zip(baseline.loc[baseline.net_r.ge(5), "symbol"], baseline.loc[baseline.net_r.ge(5), "anchor_i"]))
            for arm in (*ARMS, "buy_hold"):
                key = f"{fold}|{minutes}|{arm}"
                sleeves = {s: all_curves[s][key] for s in SYMBOLS}
                curve = combine_sleeves(sleeves, sleeves[SYMBOLS[0]].index, len(SYMBOLS))
                portfolios[key] = curve
                q = g.loc[g.arm.eq(arm)]
                selected = q.loc[q.portfolio_selected.eq(True) & q.valid.eq(True)]
                match = selected.loc[selected.excess_bp.notna()]
                natural_match = match.loc[match.natural_exit.eq(True)]
                captures = set(zip(q.loc[q.portfolio_selected.eq(True), "symbol"], q.loc[q.portfolio_selected.eq(True), "anchor_i"]))
                row = dict(fold=fold, minutes=minutes, arm=arm, **curve_metrics(curve),
                    **trade_metrics(q), matched_n=len(match), **inference(match.excess_bp, match.month),
                    **{"natural_only_"+k:v for k,v in inference(natural_match.excess_bp, natural_match.month).items()},
                    positive_coins=sum(c.iloc[-1] > 1 for c in sleeves.values()),
                    baseline_5r_retained=len(winners & captures), baseline_5r_count=len(winners),
                    median_wait_bars=selected.wait_bars.median(), median_wait_price_bp=selected.wait_price_change_bp.median())
                if arm == "buy_hold":
                    for name in ("win_pct", "pf", "mean_net_r", "top3_profit_share_pct"):
                        row[name] = np.nan
                rows.append(row)
                for symbol, c in sleeves.items():
                    coin_rows.append(dict(fold=fold, minutes=minutes, arm=arm, symbol=symbol,
                        **curve_metrics(c), **trade_metrics(q.loc[q.symbol.eq(symbol)])))
                # Diagnostic ranking is among all valid naturally completed
                # candidates, not a traded top-decile portfolio or a fit score.
                natural = q.loc[q.valid.eq(True) & q.natural_exit.eq(True)]
                ranks.append(dict(fold=fold, minutes=minutes, arm=arm, **ranking(natural, "relative_volume")))
    summary = pd.DataFrame(rows)
    summary["holm_p"] = np.nan
    for fold, _, _ in FOLDS:
        family = summary.fold.eq(fold) & ~summary.arm.isin(["baseline", "buy_hold"])
        summary.loc[family, "holm_p"] = holm(summary.loc[family, "p"].to_numpy(float), family_size=22)
    summary.to_csv(out/"summary.csv", index=False)
    pd.DataFrame(coin_rows).to_csv(out/"per_coin.csv", index=False)
    pd.DataFrame(ranks).to_csv(out/"ranking.csv", index=False)
    pd.to_pickle(portfolios, out/"portfolio_curves.pkl.gz", compression="gzip")
    dump(dict(generated_at=pd.Timestamp.now(tz="UTC"), event_rows=len(events), books=64,
        builder_sha256=digest(__file__)), out/"completion.json")
    print(summary.loc[summary.fold.isin(["validation", "recent"]) & summary.arm.isin(
        ["baseline", "baseline_3r", "sequence", "sequence_wait", "buy_hold"]),
        ["fold", "minutes", "arm", "trades", "net_pct", "mdd_pct", "win_pct", "pf", "excess_bp", "holm_p"]].to_string(index=False), flush=True)


def run(out):
    provenance = committed_sources()
    out.mkdir(parents=True, exist_ok=True)
    manifest = dict(experiment_id="exp-mainstream-super-trend-20260910-v1", generated_at=pd.Timestamp.now(tz="UTC"),
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        builders=provenance, symbols=list(SYMBOLS), folds=FOLDS, arms=ARMS,
        end=END, starting_capital=100000, sleeves=8, roundtrip_cost=.002, initial_stop_atr=2,
        holdout_consumption=2, holdout_status="authorized historical transfer; accounting audit replay, no rule/parameter change; not untouched out-of-sample",
        funding="Not included: full historical actual funding coverage unavailable", data=[])
    # Verify all identities before reading any outcomes. Cached books may be
    # resumed only if both source and frozen builder fingerprints match.
    fingerprint = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    for symbol, expected_sha in zip(SYMBOLS, HASHES):
        source = ROOT/f"data/altcoin_trends_20260909_v1/history_full_verified/frozen_pool/{symbol}_USDT_SWAP_15m.csv"
        if digest(source) != expected_sha: raise ValueError("Source changed: " + symbol)
        manifest["data"].append(dict(symbol=symbol, path=str(source.relative_to(ROOT)), sha256=expected_sha))
    dump(manifest, out/"manifest.json")
    for s_i, source_meta in enumerate(manifest["data"]):
        symbol = source_meta["symbol"]
        folder = out/symbol
        marker = folder/"completion.json"
        if marker.exists():
            old = json.loads(marker.read_text())
            if old["fingerprint"] != fingerprint or old["source_sha256"] != source_meta["sha256"]:
                raise ValueError("Stale book; use a new output directory")
            for name, sha in old["files"].items():
                if digest(folder/name) != sha: raise ValueError("Cached bytes changed: " + name)
            print(symbol + " verified existing book", flush=True)
            continue
        print(symbol + " preparing closed features", flush=True)
        raw = read_prefix(ROOT/source_meta["path"], END)
        bars = {m: aggregate(raw, m) for m in (15, 60, 240, 1440)}
        features = {m: enrich(b) for m, b in bars.items()}
        folder.mkdir(parents=True, exist_ok=True)
        frames, control_frames, curves, diagnostics = [], [], {}, []
        for minutes, (lower, higher) in TIMEFRAMES.items():
            b = bars[minutes]
            b.attrs["period_seconds"] = minutes*60
            f = add_context(features[minutes], features[lower], features[higher], minutes)
            f.to_pickle(folder/f"features_{minutes}.pkl.gz", compression="gzip")
            b.to_pickle(folder/f"bars_{minutes}.pkl.gz", compression="gzip")
            for fold_i, (fold, start, end) in enumerate(FOLDS):
                start, end = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
                indexes = np.flatnonzero((b.index >= start) & (b.index+pd.Timedelta(minutes=minutes) <= end))
                first, last = int(indexes[0]), int(indexes[-1])
                if len(indexes) != int((end-start)/pd.Timedelta(minutes=minutes)):
                    raise ValueError("Incomplete fold: " + str((symbol, minutes, fold)))
                candidates = candidate_events(f, first, last, minutes)
                result = evaluate_book(b, f, candidates, first_i=first, last_i=last,
                    symbol=symbol, minutes=minutes, fold=fold, seed=20260910+s_i*100+minutes+fold_i, arms=ARMS)
                frames.append(result["events"])
                control_frames.append(result["controls"])
                diagnostics += result["diagnostics"]
                for arm, c in result["curves"].items(): curves[f"{fold}|{minutes}|{arm}"] = c
                buy_hold = b.close.iloc[first:last+1]/b.open.iloc[first]-.001
                buy_hold.index = buy_hold.index+pd.Timedelta(minutes=minutes)
                buy_hold.iloc[-1] -= .001
                curves[f"{fold}|{minutes}|buy_hold"] = buy_hold
                print(f"{symbol} {minutes}m {fold}: {sum(c['arm']=='baseline' for c in candidates)} baseline candidates", flush=True)
        pd.concat(frames, ignore_index=True).to_pickle(folder/"events.pkl.gz", compression="gzip")
        pd.concat(control_frames, ignore_index=True).to_pickle(folder/"controls.pkl.gz", compression="gzip")
        pd.to_pickle(curves, folder/"curves.pkl.gz", compression="gzip")
        dump(diagnostics, folder/"diagnostics.json")
        files = {p.name: digest(p) for p in sorted(folder.iterdir()) if p.is_file() and p != marker}
        dump(dict(fingerprint=fingerprint, source_sha256=source_meta["sha256"], files=files), marker)
    summarize(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    committed_sources()
    if args.summarize_only: summarize(args.out)
    else: run(args.out)


if __name__ == "__main__":
    main()
