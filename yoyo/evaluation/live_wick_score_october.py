"""Fixed October live-wick admission comparison using the existing native E20 entry and exit rules.

Eligibility uses the saved score, beyond_pct and recovery at the touch 5m close and the
previously fixed weekly target. Baseline fills are read from the frozen October study.
Only incremental fills are requested from its NautilusTrader adapter.
"""
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.monday_limit_live_october import EDGE, fills, load_fills
from yoyo.evaluation.monday_limit_return_opt import frac
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.strategy_booklet_paths import Trace


OUT = ROOT / "experiments/active/exp-live-wick-score040-oct-20261109-v1"


def fill_symbol(task):
    symbol, job, event_ids = task
    for path in ("OHLC", "OLHC"):
        meta = fills((symbol, job, event_ids, path, str(OUT)))
        if meta["positions"] != len(event_ids) or meta["version"] != "1.231.0":
            raise ValueError(f"Incomplete native fills for {symbol} on {path}")
    return load_fills((str(OUT), symbol, job))


def metrics(net):
    wins, losses = net[net > 0], net[net < 0]
    return dict(trades=len(net), wins=len(wins), win_pct=100 * len(wins) / len(net),
                avg_win_bp=float(wins.mean()) if len(wins) else None,
                avg_loss_bp=float(losses.mean()) if len(losses) else None,
                payoff=float(-wins.mean() / losses.mean()) if len(wins) and len(losses) else None,
                profit_factor=float(-wins.sum() / losses.sum()) if len(losses) else None,
                mean_net_bp=float(net.mean()))


def account(rows, cfg):
    rows = rows.sort_values("entry_t").reset_index(drop=True).copy()
    for side in ["entry", "exit"]:
        rows[side + "_ns"] = rows[side + "_t"].dt.as_unit("ns").astype("int64")
    tr = Trace(pd.Timestamp(cfg["start"], tz="UTC").value)
    acc = frac(rows, 6, 3, cfg["seed"], trace=tr)
    funded = pd.DataFrame(tr.rows[1:])
    return dict(**acc, funded_trade_metrics=metrics(funded.net_bp)), funded


def main():
    committed("yoyo/evaluation/live_wick_score_october.py", str((OUT / "config.json").relative_to(ROOT)))
    cfg = json.loads((OUT / "config.json").read_text())
    source = ROOT / cfg["source"]
    facts = pd.read_csv(source / "scored.csv.gz")
    entry = pd.to_datetime(facts.t, utc=True) + pd.Timedelta(minutes=5)
    subset = facts[(entry >= pd.Timestamp(cfg["start"], tz="UTC")) &
                   (entry < pd.Timestamp(cfg["end_exclusive"], tz="UTC")) &
                   (facts.score_stored >= cfg["minimum_score"]) & (facts.recovery < EDGE) &
                   (facts.beyond_pct > 0) & (facts.beyond_pct < 30) &
                   (facts.side * (facts.target - facts.close) > 0)].copy()
    if subset.event.duplicated().any():
        raise ValueError("Duplicate scored touch")
    base = subset[subset.score_stored >= cfg["baseline_score"]]
    extra = subset[subset.score_stored < cfg["baseline_score"]]
    jobs = json.loads((source / "jobs.json").read_text())
    with ProcessPoolExecutor(3) as pool:
        new = pd.concat(pool.map(fill_symbol, [(s, jobs[s], set(g.event)) for s, g in extra.groupby("symbol")]))
        cached = pd.concat(pool.map(load_fills, [(str(source), s, jobs[s]) for s in sorted(set(base.symbol))]))
    trades = pd.concat([cached[cached.event.isin(base.event)], new], ignore_index=True)
    trades = subset[["event", "score_stored", "recovery", "beyond_pct"]].merge(trades, on="event", validate="one_to_one")
    if set(trades.event) != set(subset.event):
        raise ValueError("Missing candidate fill")
    reference, ledger = account(trades[trades.event.isin(base.event)], cfg)
    expected = json.loads((source / "summary.json").read_text())["results"]["live-wick6x3"]
    if reference["taken"] != expected["funded"] or abs(reference["final_multiple"] * 1000 - expected["final"]) > 1e-6:
        raise ValueError("Frozen October account mismatch")
    expanded, ledger_expanded = account(trades, cfg)
    added = trades[trades.event.isin(extra.event)]
    summary = dict(baseline=reference, expanded=expanded, incremental=metrics(added.net),
                   incremental_exit_counts={str(k):int(n) for k,n in added.reason.value_counts().items()},
                   funded_incremental=len(set(ledger_expanded.trade_id) & set(extra.event)),
                   displaced_baseline=sorted(set(ledger.trade_id) - set(ledger_expanded.trade_id)))
    trades.sort_values("entry_t").to_csv(OUT / "paired_opportunities.csv.gz", index=False)
    ledger_expanded.to_csv(OUT / "expanded_ledger.csv.gz", index=False)
    dump(OUT / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
