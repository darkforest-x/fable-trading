"""Evaluate boundary-armed trailing exits on the frozen, selected 12h population."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation.monday_weekly_boundary_trailing import trail_fill
from yoyo.evaluation.monday_weekly_wick_census3r import HOUR_MS, load_frozen_tape

EXP = Path(__file__).resolve().parents[2] / "experiments/active/exp-monday-wick-12h-high-boundary-trail-20261116-v1"


def native_returns(rows, cfg):
    """Price the independent completed fills, including taker costs on both sides."""
    if rows.empty:
        return np.empty(0)
    prices = np.vstack([rows.entry.to_numpy(), rows.exit_price.to_numpy()])
    size = np.vstack([rows.side, -rows.side])
    fees = np.array([[cfg["fees"]["entry_taker"]], [cfg["fees"]["stop_exit_taker"]]])
    portfolio = vbt.Portfolio.from_orders(
        prices, size=size, price=prices, fees=fees, direction="both",
        cash_sharing=False, init_cash=4 * prices.max(axis=0),
        raise_reject=True, allow_partial=False,
    )
    records = portfolio.trades.records.sort_values("col")
    assert len(records) == len(rows) and records.status.eq(1).all()
    np.testing.assert_array_equal(records.col, np.arange(len(rows)))
    return records.pnl.to_numpy() / rows.risk_abs.to_numpy()


def original_cohort(cfg):
    source = EXP.parent / cfg["source"]
    scores = pd.read_csv(source / "test_scores.csv.gz")
    ids = scores.loc[scores.eligible & scores.tf.eq(12) & scores.band.eq("top"), "event_id"]
    keys = ["event_id", "symbol", "week", "side", "entry_ms", "entry", "stop", "risk_abs", "target_price",
            "outcome", "exit_ms", "exit_price", "net_r", "mark_ms"]
    original = pd.read_csv(source / "outcomes.csv.gz", usecols=keys).set_index("event_id", verify_integrity=True)
    cohort = original.loc[ids].copy()
    assert ids.is_unique and len(cohort) == 646 and cohort.net_r.notna().sum() == 645
    assert (cohort.outcome.eq("tp") == cohort.net_r.gt(0)).all() and cohort.net_r.gt(0).sum() == 347
    assert cohort.mark_ms.lt(pd.Timestamp(cfg["cutoff_exclusive"]).value // 10**6).all()
    return cohort


def replay(cohort, cfg):
    target_first = cohort[cohort.outcome.eq("tp")]
    changes = []
    distances = [cfg["primary_distance"], *cfg["sensitivity_distances"]]
    for symbol, group in target_first.groupby("symbol"):
        bars = load_frozen_tape(symbol)
        stamps = bars[:, 0].astype("int64")
        for event_id, row in group.iterrows():
            # mark_ms is the frozen last observable hour before any gap/lifecycle end.
            start, end = stamps.searchsorted([row.entry_ms, row.mark_ms])
            path = bars[start:end + 1]
            assert path[0, 0] == row.entry_ms and path[-1, 0] == row.mark_ms
            assert np.isclose(path[0, 1], row.entry, rtol=1e-12, atol=0) and np.all(np.diff(path[:, 0]) == HOUR_MS)
            for distance in distances:
                idx, price, kind, touch = trail_fill(path, row.entry, int(row.side), row.target_price, row.stop, distance)
                if touch >= 0:
                    assert path[touch, 0] == (int(row.exit_ms) // HOUR_MS) * HOUR_MS
                if idx >= 0:
                    assert path[idx, 0] >= (int(row.exit_ms) // HOUR_MS) * HOUR_MS
                changes.append(dict(event_id=event_id, distance=distance, entry=row.entry, side=row.side,
                                    risk_abs=row.risk_abs, exit_price=price, outcome=kind,
                                    exit_ms=path[idx, 0] if idx >= 0 else np.nan))
    changed = pd.DataFrame(changes)
    closed = changed.outcome.ne("censored")
    changed["net_r"] = np.nan
    changed.loc[closed, "net_r"] = native_returns(changed[closed], cfg)
    base = cohort.rename(columns={"exit_price": "last_exit_price"}).copy()
    base["strategy"] = "boundary"
    frames = [base]
    for distance in distances:
        full = base.copy()
        revised = changed[changed.distance.eq(distance)].set_index("event_id", verify_integrity=True)
        assert set(revised.index) == set(target_first.index)
        for name in ["outcome", "net_r", "exit_ms", "last_exit_price"]:
            underlying = "exit_price" if name == "last_exit_price" else name
            full.loc[revised.index, name] = revised[underlying]
        full["strategy"] = f"trail_{distance:.0%}"
        frames.append(full)
        if distance == cfg["primary_distance"]:
            # Linear, unlevered independent-R accounting: each leg's native fill
            # and fee scales with quantity. Last exit time/price refer to the runner.
            half = full.copy()
            fraction = cfg["partial_fraction_at_boundary"]
            half["net_r"] = fraction * base.net_r + (1 - fraction) * full.net_r
            half.loc[revised.index, "outcome"] = "partial+" + full.loc[revised.index, "outcome"]
            half["strategy"] = "partial_and_trail"
            frames.append(half)
    return pd.concat(frames).reset_index()


def summarize(ledger, cfg):
    base = ledger[ledger.strategy.eq("boundary")].set_index("event_id").sort_index()
    stats = []
    for name, group in ledger.groupby("strategy", sort=False):
        group = group.set_index("event_id").loc[base.index]
        r = group.net_r.dropna()
        wins = r[r > 0]
        losses = r[r < 0]
        pair = pd.concat([base.net_r.rename("old"), group.net_r.rename("new"), group.week], axis=1).dropna()
        diff = pair.new - pair.old
        prior_wins = pair.old.gt(0)
        weekly = diff.groupby(pair.week).agg(["sum", "count"]).to_numpy()
        rng = np.random.default_rng(cfg["seed"])
        indices = rng.integers(len(weekly), size=(cfg["week_bootstrap_draws"], len(weekly)))
        draws = weekly[indices].sum(axis=1)
        lower, upper = np.quantile(draws[:, 0] / draws[:, 1], [0.025, 0.975])
        stats.append(dict(strategy=name, entries=len(group), settled=len(r), censored=len(group)-len(r),
                          wins=len(wins), win_rate=len(wins)/len(r), avg_win_r=wins.mean(),
                          avg_loss_r=losses.mean(), payoff=-wins.mean()/losses.mean(), mean_net_r=r.mean(),
                          paired_n=len(pair), paired_delta_r=diff.mean(), paired_low95=lower, paired_high95=upper,
                          original_wins_more=int((prior_wins & diff.gt(1e-10)).sum()),
                          original_wins_less=int((prior_wins & diff.lt(-1e-10)).sum()),
                          original_wins_to_nonpositive=int((prior_wins & pair.new.le(0)).sum()),
                          original_wins_unsettled=int((base.net_r.gt(0) & group.net_r.isna()).sum())))
    return pd.DataFrame(stats)


def main():
    cfg = json.loads((EXP / "config.json").read_text())
    ledger = replay(original_cohort(cfg), cfg)
    results = summarize(ledger, cfg)
    ledger.to_csv(EXP / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    results.to_csv(EXP / "comparison.csv", index=False)
    print(results.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
