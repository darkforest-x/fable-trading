"""Aggregate immutable High-R Entry V2 receipts without reading price history.

This is an entry-rule intervention: no outcome ranks select
trades. Calendar-month blocks retain simultaneous cross-asset moves. Early
periods require both entry and exit before the fixed split; cross-boundary
positions remain explicit. R uses unchanged original risk, never leverage.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_high_r_entry_study import completed, digest, dump
from yoyo.evaluation.spike_six_filter_statistics import strict_bool

SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
SEED = 9212026


def periods(frame):
    yield "full", frame
    yield "earlier", frame.loc[(frame.entry_time < SPLIT) & (frame.exit_time < SPLIT)]
    yield "later", frame.loc[frame.entry_time >= SPLIT]
    yield "crossing", frame.loc[(frame.entry_time < SPLIT) & ~(frame.exit_time < SPLIT)]


def block_test(sums):
    """One-sided month sign flip, plus total-sum month bootstrap interval."""
    a = np.asarray(sums, dtype=float)
    if len(a) < 2:
        return dict(blocks=len(a), p=None, total_ci_low=None, total_ci_high=None)
    rng = np.random.default_rng(SEED)
    if len(a) <= 16:
        signs = np.array(list(itertools.product((-1., 1.), repeat=len(a))))
        values = (signs * a).sum(axis=1)
        p = float(np.mean(values >= a.sum()-1e-12))
    else:
        values = (rng.choice([-1., 1.], size=(9999, len(a))) * a).sum(axis=1)
        p = float((1+np.sum(values >= a.sum()-1e-12))/10000)
    boot = a[rng.integers(0, len(a), size=(2000, len(a)))].sum(axis=1)
    lo, hi = np.quantile(boot, [.025, .975])
    return dict(blocks=len(a), p=p, total_ci_low=float(lo), total_ci_high=float(hi))


def metrics(frame):
    c = frame.loc[~frame.censored].sort_values(["exit_time", "entry_time", "event_key"])
    if not np.isfinite(c[["net_r", "net_return", "gross_r", "gross_return"]].to_numpy(float)).all():
        raise ValueError("nonfinite closed economic outcome")
    r = c.net_r.to_numpy(float)
    curve = np.r_[0., np.cumsum(r)]
    loss = -r[r<0].sum()
    return dict(events=len(frame), closed=len(c), censored=int(frame.censored.sum()),
        assets=frame.asset.nunique(), gt5=int((r>5).sum()), gt10=int((r>10).sum()), gt20=int((r>20).sum()),
        gt10_rate=float((r>10).mean()) if len(r) else None,
        recorded_mfe_gt10=int(c.mfe_r.gt(10).sum()), total_r=float(r.sum()),
        gross_r=float(c.gross_r.sum()), mean_r=float(r.mean()) if len(r) else None,
        mean_gross_bp=float(c.gross_return.mean()*10000), mean_net_bp=float(c.net_return.mean()*10000),
        total_nominal_return=float(c.net_return.sum()), win_rate=float((r>0).mean()) if len(r) else None,
        pf_r=float(r[r>0].sum()/loss) if loss>0 else None,
        event_drawdown_r=float((np.maximum.accumulate(curve)-curve).max()),
        total_r_ex_top5=float(r.sum()-np.sort(r)[-5:].sum()) if len(r)>=5 else None)


def control_metrics(frame, controls, period):
    c = frame[["arm", "event_key", "net_r", "net_return"]].merge(controls, on=["arm", "event_key"], validate="one_to_one")
    if len(c) != len(frame):
        raise ValueError("missing control receipt")
    if not np.allclose(c.net_r, c.target_net_r, equal_nan=True):
        raise ValueError("control target outcome drift")
    mask = c.matched & ~c.target_censored & ~c.control_censored
    if period == "earlier":
        mask &= (c.control_entry_time < SPLIT) & (c.control_exit_time < SPLIT)
    elif period == "later":
        mask &= c.control_entry_time >= SPLIT
    p = c.loc[mask].copy()
    p["delta"] = p.target_net_r-p.control_net_r
    test = block_test(p.groupby(p.entry_time.dt.strftime("%Y-%m")).delta.sum())
    return dict(matched_pairs=len(p), unmatched=len(c)-len(p), control_mean_r=float(p.control_net_r.mean()),
        paired_target_mean_r=float(p.target_net_r.mean()), paired_excess_r=float(p.delta.mean()),
        paired_excess_bp=float((p.target_net_return-p.control_net_return).mean()*10000),
        control_gt10=int(p.control_net_r.gt(10).sum()), paired_target_gt10=int(p.target_net_r.gt(10).sum()),
        random_p=test["p"], random_blocks=test["blocks"])


def load(run):
    m = json.loads((run / "manifest.json").read_text())
    if not m["complete"] or m["completed"] != m["expected"] or m["failures"]:
        raise ValueError("full replay is incomplete")
    if len(m["receipts"]) != m["expected"]:
        raise ValueError("manifest receipt coverage mismatch")
    ts, cs = [], []
    for key, sha in sorted(m["receipts"].items()):
        folder = run / "streams" / key
        if digest(folder / "completion.json") != sha:
            raise ValueError("completion hash drift")
        r = completed(folder, m["run_identity"])
        if not r["baseline_parity"]:
            raise ValueError("unverified baseline")
        ts.append(pd.read_csv(folder / "trades.csv.gz"))
        cs.append(pd.read_csv(folder / "controls.csv.gz"))
    t, c = pd.concat(ts, ignore_index=True), pd.concat(cs, ignore_index=True)
    for frame in (t, c):
        for name in ("entry_time", "exit_time", "signal_bar_open", "control_entry_time", "control_exit_time"):
            if name in frame:
                frame[name] = pd.to_datetime(frame[name], utc=True)
        for name in ("censored", "matched", "target_censored", "control_censored"):
            if name in frame:
                frame[name] = strict_bool(frame[name])
        if frame.duplicated(["arm", "event_key"]).any():
            raise ValueError("duplicate aggregate identity")
    return t, c



def auc_score(label, score):
    """Tie-aware ROC AUC for a fixed causal score; no learned classifier."""
    label, score = np.asarray(label, bool), np.asarray(score, float)
    mask = np.isfinite(score)
    label, score = label[mask], score[mask]
    positives = int(label.sum())
    negatives = len(label) - positives
    if not positives or not negatives:
        return None
    ranks = pd.Series(score).rank(method="average").to_numpy()
    return float((ranks[label].sum()-positives*(positives+1)/2)/(positives*negatives))


def rate_contrast(frame):
    """Paired calendar-month bootstrap of aggregate rates, preserving denominators.

    Empty months on one arm contribute zero counts, not a zero success rate.
    Bootstrap samples resample the same months in both arms. Unknown ratios
    are discarded and the usable draw count is reported. Sign-flip p-values
    refer separately to equal-weight monthly rate differences on shared months.
    """
    c = frame.loc[~frame.censored].copy()
    c["month"] = c.entry_time.dt.strftime("%Y-%m")
    c["win"] = c.net_r.gt(0).astype(int)
    c["tail"] = c.net_r.gt(10).astype(int)
    by = c.groupby(["month", "arm"]).agg(n=("net_r", "size"), win=("win", "sum"), tail=("tail", "sum"))
    months = sorted(c.month.unique())
    arms = ("baseline", "high_r_entry_v2")
    counts = np.array([by.reindex(pd.MultiIndex.from_product([months, [arm]], names=["month", "arm"]), fill_value=0).to_numpy(float) for arm in arms])
    result = {"months": len(months)}
    if not months:
        return result
    rng = np.random.default_rng(SEED)
    selected = rng.integers(0, len(months), size=(2000, len(months)))
    total = counts.sum(axis=1)
    draws = counts[:, selected, :].sum(axis=2)
    valid = (draws[0, :, 0] > 0) & (draws[1, :, 0] > 0)
    shared = (counts[0, :, 0] > 0) & (counts[1, :, 0] > 0)
    result.update(valid_bootstrap_draws=int(valid.sum()), shared_months=int(shared.sum()))
    for field, col in (("win_rate", 1), ("gt10_rate", 2)):
        if (total[:, 0] <= 0).any():
            result.update({field+"_delta": None, field+"_ci_low": None, field+"_ci_high": None})
            continue
        delta = total[1, col]/total[1, 0]-total[0, col]/total[0, 0]
        diffs = draws[1, valid, col]/draws[1, valid, 0]-draws[0, valid, col]/draws[0, valid, 0]
        lo, hi = np.quantile(diffs, [.025, .975]) if len(diffs) else (np.nan, np.nan)
        monthly = counts[1, shared, col]/counts[1, shared, 0]-counts[0, shared, col]/counts[0, shared, 0]
        test = block_test(monthly)
        result.update({field+"_delta":float(delta), field+"_ci_low":float(lo), field+"_ci_high":float(hi),
                       field+"_monthly_signflip_p":test["p"]})
    return result


def ranking_diagnostics(long, controls):
    """Score-only top-decile diagnostics on original entries, never executable gates.

    Ranking uses completed-signal scores. Outcomes only supply evaluation labels.
    This batch diagnostic compares the proposed score with two prespecified
    one-feature references; it must not choose a new threshold or rule.
    """
    baseline = long.loc[long.arm.eq("baseline")].copy()
    rows = []
    for period, part in periods(baseline):
        closed = part.loc[~part.censored].copy()
        for feature, score in (("breakout_score", closed.score),
                               ("negative_reference_risk_fraction", -closed.reference_risk_fraction),
                               ("volume_ratio", closed.volume_ratio)):
            scored = closed.loc[np.isfinite(score)].copy()
            scored["ranking_score"] = score.loc[scored.index]
            ordered = scored.sort_values(["ranking_score", "event_key"], ascending=[False, True])
            top = ordered.head(int(np.ceil(len(ordered)*.1)))
            rows.append(dict(period=period, feature=feature, eligible=len(scored), unknown=len(closed)-len(scored),
                auc_gt10=auc_score(scored.net_r.gt(10), scored.ranking_score),
                top_decile_n=len(top), **metrics(top), **control_metrics(top, controls, period)))
    return pd.DataFrame(rows)


def evaluate_gate(summary, rates):
    """Apply the preregistered later-period gate without selecting new parameters."""
    rows = summary.loc[summary.dimension.eq("all") & summary.period.eq("later")].set_index("arm")
    old, new = rows.loc["baseline"], rows.loc["high_r_entry_v2"]
    rate = rates.loc[rates.period.eq("later")].iloc[0]
    checks = {
        "win_rate_increases": bool(new.win_rate > old.win_rate),
        "gt10_precision_increases": bool(new.gt10_rate > old.gt10_rate),
        "win_rate_ci_positive": bool(rate.win_rate_ci_low > 0),
        "gt10_precision_ci_positive": bool(rate.gt10_rate_ci_low > 0),
        "mean_net_bp_positive": bool(new.mean_net_bp > 0),
        "matched_excess_positive": bool(new.paired_excess_r > 0),
        "matched_random_p_below_001": bool(new.random_p < .01),
    }
    return dict(status="supported_research_only" if all(checks.values()) else "rejected",
                checks=checks, failed_checks=[key for key, passed in checks.items() if not passed],
                production_eligible=False, training_eligible=False)


def run(source, output):
    if output.exists():
        raise ValueError("refuse to overwrite statistics")
    t, c = load(source)
    output.mkdir(parents=True)
    long = t.loc[t.side.eq(1)].copy()
    rows = []
    groups = [("all", "all", long)]
    for col in ("timeframe_min", "venue"):
        groups.extend((col, str(key), part) for key, part in long.groupby(col))
    for dimension, group, table in groups:
        for arm, one_arm in table.groupby("arm"):
            for period, part in periods(one_arm):
                rows.append(dict(dimension=dimension, group=group, arm=arm, period=period,
                                 **metrics(part), **control_metrics(part, c, period)))
    summary = pd.DataFrame(rows)
    summary.to_csv(output / "summary.csv", index=False)
    contrasts = []
    for period, p in periods(long):
        closed = p.loc[~p.censored].copy()
        closed["month"] = closed.entry_time.dt.strftime("%Y-%m")
        for name, values in (("gt10", closed.net_r.gt(10).astype(float)), ("net_r", closed.net_r),
                             ("net_return", closed.net_return)):
            totals = closed.assign(value=values).groupby(["month", "arm"]).value.sum().unstack(fill_value=0)
            totals = totals.reindex(columns=["baseline", "high_r_entry_v2"], fill_value=0)
            delta = totals.high_r_entry_v2-totals.baseline
            contrasts.append(dict(period=period, metric=name, delta=float(delta.sum()), **block_test(delta)))
    pd.DataFrame(contrasts).to_csv(output / "contrasts.csv", index=False)
    rate_rows = pd.DataFrame([dict(period=name, **rate_contrast(part)) for name, part in periods(long)])
    rate_rows.to_csv(output / "rate_contrasts.csv", index=False)
    dump(output / "verdict.json", evaluate_gate(summary, rate_rows))
    ranking_diagnostics(long, c).to_csv(output / "ranking_diagnostics.csv", index=False)
    fields = ["event_key", "asset", "venue", "symbol", "timeframe_min", "entry_time", "exit_time", "censored", "net_r", "mfe_r", "net_return", "exit_reason", "score", "gate_known", "gate_passed", "reference_risk_fraction", "volume_ratio"]
    b = long.loc[long.arm.eq("baseline"), fields]
    n = long.loc[long.arm.eq("high_r_entry_v2"), fields]
    pair = b.merge(n, on="event_key", suffixes=("_old", "_new"), how="outer", validate="one_to_one", indicator=True)
    old10 = pair.censored_old.eq(False) & pair.net_r_old.gt(10)
    new10 = pair.censored_new.eq(False) & pair.net_r_new.gt(10)
    pair["tail_status"] = np.select([old10 & new10, old10 & ~new10, ~old10 & new10],
                                   ["retained", "lost", "gained"], default="neither")
    pair.to_csv(output / "paired_long_events.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
    pair.loc[old10 | new10].to_csv(output / "high_r_changes.csv", index=False)
    long.to_csv(output / "long_trades.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
    c.to_csv(output / "controls.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
    asset = []
    for (name, arm), p in long.groupby(["asset", "arm"]):
        asset.append(dict(asset=name, arm=arm, **metrics(p)))
    pd.DataFrame(asset).to_csv(output / "per_asset.csv", index=False)
    diagnostic = dict(original_closed_longs=int((~b.censored).sum()), original_winners=int((~b.censored & b.net_r.gt(0)).sum()),
        original_net_gt10=int((~b.censored & b.net_r.gt(10)).sum()), recorded_mfe_gt10=int((~b.censored & b.mfe_r.gt(10)).sum()),
        tail_status=pair.tail_status.value_counts().to_dict(), short_events=t.loc[t.side.eq(-1)].groupby("arm").size().to_dict(),
        metric_limit="Strict realized netR>10 is an outcome label, never an entry input; ranking diagnostics are not live admission thresholds")
    dump(output / "diagnostic.json", diagnostic)
    dump(output / "receipt.json", dict(source_manifest_sha256=digest(source / "manifest.json"),
        builder_sha256=digest(Path(__file__)), generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        files={p.name:digest(p) for p in output.iterdir()}))
    print(summary.loc[(summary.dimension=="all") & summary.period.isin(["full", "later"]),
          ["arm", "period", "closed", "win_rate", "gt10", "gt10_rate", "total_r", "mean_net_bp", "paired_excess_r", "random_p"]].to_string(index=False))
    print(json.dumps(diagnostic, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.run, a.output)
