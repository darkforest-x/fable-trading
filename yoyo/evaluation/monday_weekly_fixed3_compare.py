"""Compare realized trade outcomes on frozen entry IDs, resampling complete NY weeks."""
from pathlib import Path
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-binance-monday-wick-fixed3-near-20261116-v1"
METRICS = ["win_rate", "payoff", "mean_net_r"]
COUNTS = ["events", "settled", "wins", "losses", "positive_sum", "negative_sum"]


def read_ledger(path):
    df = pd.read_csv(path, float_precision="round_trip").set_index("event_id", verify_integrity=True)
    return df.sort_index()


def audit(old, new, cfg):
    pd.testing.assert_index_equal(old.index, new.index)
    frozen = ["symbol", "week", "tf", "side", "anchor_ready_ms", "first_breach_ms",
              "bar_ms", "close_ms", "entry_ms", "entry", "target_price", "target"]
    pd.testing.assert_frame_equal(old[frozen], new[frozen], check_dtype=False)
    np.testing.assert_allclose(new.stop, new.entry * (1 - cfg["stop_frac"] * new.side), atol=0, rtol=1e-12)
    np.testing.assert_allclose(new.risk_abs, new.entry * cfg["stop_frac"], atol=0, rtol=1e-12)
    np.testing.assert_allclose(new.risk_frac, cfg["stop_frac"], atol=1e-12)
    assert new.outcome.isin(["tp", "sl", "censored"]).all()
    assert (((new.target_price - new.entry) * new.side) > 0).all()
    closed = new[new.outcome.isin(["tp", "sl"])]
    assert closed.net_r.notna().all() and closed.exit_ms.ge(closed.entry_ms).all()
    assert new.loc[new.outcome.eq("censored"), ["net_r", "exit_ms"]].isna().all().all()
    fees = cfg["fees"]
    exit_fee = np.where(closed.outcome.eq("tp"), fees["tp_exit_maker"], fees["stop_exit_taker"])
    pnl = (closed.exit_price - closed.entry) * closed.side - closed.entry * fees["entry_taker"] - closed.exit_price * exit_fee
    np.testing.assert_allclose(closed.net_r, pnl / closed.risk_abs, atol=1e-9, rtol=1e-9)
    np.testing.assert_allclose(closed.net_return, closed.net_r * cfg["stop_frac"], atol=1e-11)


def weekly_totals(group, weeks):
    r = group.net_r
    values = pd.DataFrame(dict(events=1, settled=r.notna().astype(int),
                               wins=r.gt(0).astype(int), losses=r.lt(0).astype(int),
                               positive_sum=r.clip(lower=0).fillna(0),
                               negative_sum=-r.clip(upper=0).fillna(0)), index=group.index)
    return values.groupby(group.week).sum().reindex(weeks, fill_value=0)[COUNTS].to_numpy()


def rates(totals):
    _, n, w, l, pos, neg = totals.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.column_stack([w / n, (pos / w) / (neg / l), (pos - neg) / n])


def metrics(totals):
    total = totals.sum(axis=0)
    n, settled, wins, losses, pos, neg = total
    return dict(events=int(n), settled=int(settled), censored=int(n - settled), wins=int(wins),
                avg_win_r=pos / wins if wins else None, avg_loss_r=-neg / losses if losses else None,
                **dict(zip(METRICS, rates(total.reshape(1, -1))[0])))


def main():
    cfg = json.loads((EXP / "config.json").read_text())
    parent = EXP.parent / cfg["parent"]
    old = read_ledger(parent / "outcomes_near.csv.gz")
    new = read_ledger(EXP / "outcomes.csv.gz")
    audit(old, new, cfg)
    lo = pd.Timestamp(cfg["test_start"]).value // 10**6
    hi = pd.Timestamp(cfg["cutoff_exclusive"]).value // 10**6
    new = new[new.close_ms.ge(lo) & new.close_ms.lt(hi)]
    old = old.loc[new.index]
    old_scores = read_ledger(parent / "test_scores.csv.gz")
    new_scores = read_ledger(EXP / "test_scores.csv.gz")
    assert new.index.isin(old_scores.index).all() and new.index.isin(new_scores.index).all()
    old_high = old_scores.loc[new.index, "band"].eq("top10")
    high = new_scores.loc[new.index, "band"].eq("top")
    geometry = new_scores.loc[new.index, "baseline_band"].eq("top")
    assert new_scores.loc[new.index, "eligible"].all()
    assert set(new_scores.band.dropna().unique()) == {"top", "rest"}
    np.testing.assert_allclose(new_scores.loc[new.index, "net_r"], new.net_r, equal_nan=True, atol=1e-11)
    assert new_scores.loc[new.index, "known_by_cutoff"].equals(new.net_r.notna())
    groups = dict(wick_all=old, fixed_all=new, wick_old_selection=old[old_high],
                  fixed_old_selection=new[old_high], fixed_new_selection=new[high],
                  fixed_geometry=new[geometry])
    pairs = [("fixed_all", "wick_all"), ("fixed_old_selection", "wick_old_selection"),
             ("fixed_new_selection", "fixed_all"), ("fixed_new_selection", "fixed_geometry"),
             ("fixed_new_selection", "fixed_old_selection")]
    rows, intervals = [], []
    for tf in cfg["timeframes"]:
        weeks = sorted(new.loc[new.tf.eq(tf), "week"].unique())
        totals = {k: weekly_totals(v[v.tf.eq(tf)], weeks) for k, v in groups.items()}
        # Paired draws give both strategies the same realized market weeks.
        weights = np.random.default_rng(cfg["seed"] + tf).multinomial(
            len(weeks), np.full(len(weeks), 1 / len(weeks)), size=cfg["draws"])
        replications = {k: rates(weights @ v) for k, v in totals.items()}
        for k, v in totals.items():
            rows.append(dict(tf=tf, group=k, **metrics(v)))
        for a, b in pairs:
            diff = replications[a] - replications[b]
            point = rates(totals[a].sum(axis=0, keepdims=True))[0] - rates(totals[b].sum(axis=0, keepdims=True))[0]
            for i, name in enumerate(METRICS):
                low, high_ci = np.nanquantile(diff[:, i], [.025, .975])
                intervals.append(dict(tf=tf, group=a, reference=b, metric=name, difference=point[i],
                                      lower95=low, upper95=high_ci, weeks=len(weeks)))
    pd.DataFrame(rows).to_csv(EXP / "comparison.csv", index=False)
    pd.DataFrame(intervals).to_csv(EXP / "paired_week_intervals.csv", index=False)


if __name__ == "__main__":
    main()
