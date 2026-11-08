"""Check the pre-existing far-distance threshold on incremental low-score E20 signals.

Admission uses touch-bar-close observations only: frozen score, recovery, distance past the
weekly level and the saved Monday target. Read the old stored score for overlapping dates.
Retain both Nautilus intrabar paths and the original 6x3 allocator and transaction costs.
"""
from concurrent.futures import ProcessPoolExecutor
import json

import pandas as pd

from yoyo.evaluation.live_wick_score_october import account, metrics
from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.monday_limit_live_october import EDGE, fills, load_fills
from yoyo.evaluation.range_framework import ROOT, dump


OUT = ROOT / "experiments/active/exp-live-wick-deep-score-20261109-v1"
FIELDS = ["event", "symbol", "side", "entry_t", "exit_t", "mae_pct", "net", "reason"]


def replay(task):
    symbol, job, events = task
    for path in ("OHLC", "OLHC"):
        result = fills((symbol, job, events, path, str(OUT)))
        if result["positions"] != len(events) or result["version"] != "1.231.0":
            raise ValueError(f"Incomplete native fills for {symbol} on {path}")
    return load_fills((str(OUT), symbol, job))


def main():
    committed("yoyo/evaluation/live_wick_deep_score.py", str((OUT / "config.json").relative_to(ROOT)))
    cfg = json.loads((OUT / "config.json").read_text())
    src = ROOT / cfg["source"]
    snapshot = pd.read_csv(src / "scored.csv.gz")
    clock = pd.to_datetime(snapshot.t, utc=True) + pd.Timedelta(minutes=5)
    start, oct_start, end = [pd.Timestamp(cfg[key], tz="UTC") for key in ["start", "october", "end_exclusive"]]
    facts = snapshot[(clock >= start) & (clock < end) & (snapshot.recovery < EDGE) &
                     (snapshot.beyond_pct > 0) & (snapshot.beyond_pct < 30) &
                     (snapshot.side * (snapshot.target - snapshot.close) > 0)].copy()
    facts["entry_t"] = clock.loc[facts.index]
    frozen = pd.read_csv(ROOT / cfg["score_archive"], usecols=["event", "stored"]).set_index("event")
    facts["score"] = facts.event.map(frozen.stored).fillna(facts.score_stored)
    base = pd.read_csv(ROOT / cfg["baseline"]).dropna(subset=["trade_id"]).rename(
        columns={"trade_id": "event", "net_bp": "net"})
    base["entry_t"] = pd.to_datetime(base.entry_t, utc=True)
    base["exit_t"] = pd.to_datetime(base.exit_t, utc=True)
    base = base[(base.entry_t >= start) & (base.entry_t < end)]
    if set(base.event) != set(facts[facts.score >= cfg["baseline_score"]].event):
        raise ValueError("Baseline coverage differs from the frozen six-slot ledger")
    extra = facts[(facts.score >= cfg["minimum_score"]) & (facts.score < cfg["baseline_score"]) &
                  (facts.beyond_pct >= cfg["minimum_beyond_pct"])]
    pre = extra[extra.entry_t < oct_start]
    jobs = json.loads((src / "jobs.json").read_text())
    with ProcessPoolExecutor(3) as pool:
        older = pd.concat(pool.map(replay, [(sym, jobs[sym], set(g.event)) for sym, g in pre.groupby("symbol")]))
    oct_fills = pd.read_csv(ROOT / cfg["october_fills"])
    later = oct_fills[oct_fills.event.isin(extra.loc[extra.entry_t >= oct_start, "event"])].copy()
    for col in ["entry_t", "exit_t"]:
        later[col] = pd.to_datetime(later[col], utc=True)
    added = pd.concat([older[FIELDS], later[FIELDS]], ignore_index=True)
    if set(added.event) != set(extra.event) or added.event.duplicated().any():
        raise ValueError("Incomplete marginal set")
    full = pd.concat([base[FIELDS].assign(added=False), added.assign(added=True)], ignore_index=True)
    full = full.merge(facts[["event", "score", "recovery", "beyond_pct"]], on="event", validate="one_to_one")
    summary, ledgers = {}, []
    for label, left, right in [("aug_sep", start, oct_start), ("oct_selection", oct_start, end)]:
        period = full[(full.entry_t >= left) & (full.entry_t < right)]
        old, led = account(period[~period.added], dict(cfg, start=str(left)))
        expanded, led_new = account(period, dict(cfg, start=str(left)))
        marginal = period[period.added]
        summary[label] = dict(baseline=old, expanded=expanded, added_metrics=metrics(marginal.net),
                              added_funded=int(led_new.trade_id.isin(marginal.event).sum()),
                              displaced=sorted(set(led.trade_id) - set(led_new.trade_id)))
        ledgers.append(led_new.assign(period=label))
    full.sort_values("entry_t").to_csv(OUT / "opportunities.csv.gz", index=False)
    pd.concat(ledgers).to_csv(OUT / "ledger.csv.gz", index=False)
    dump(OUT / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
