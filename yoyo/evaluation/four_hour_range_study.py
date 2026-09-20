"""Frozen BTC/ETH first-New-York-range study and matched timing null.

Source and execution decisions are pre-registered in the experiment's
PROJECT_PLAN.md. No parameters are optimized. Random entries match symbol,
side, New York month/hour, a strictly causal volatility quintile, period and
the parent's known initial risk percentage. Controls use the same 2R barriers,
20bp cost and day-end exit; they are not a serial portfolio. Monthly resampling
preserves within-month dependence, but cannot establish a causal market effect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.four_hour_range import prepare, simulate, trace_trade
from yoyo.contracts.costs import LEGACY_P0_ROUND_TRIP as COST

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-four-hour-range-20260920-v1"
NY = "America/New_York"
SEED = 20260920
BAR = pd.Timedelta(minutes=5)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def dump(path, value):
    Path(path).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n")


def committed_identity(paths):
    """Reject uncommitted builders before any economic output is generated."""
    result = {}
    for path in paths:
        path = Path(path).resolve()
        rel = str(path.relative_to(ROOT))
        committed = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=ROOT)
        if committed != path.read_bytes():
            raise ValueError(f"builder/config differs from committed HEAD: {rel}")
        result[rel] = sha(path)
    return result


def read_source(spec):
    path = ROOT / spec["path"]
    if sha(path) != spec["sha256"]:
        raise ValueError(f"source SHA changed: {path}")
    d = pd.read_csv(path)
    if "open_time" in d:
        index = pd.to_datetime(d.pop("open_time"), utc=True)
    elif "ts" in d:
        index = pd.to_datetime(d.pop("ts"), unit="ms", utc=True)
    else:
        raise ValueError("source requires open_time or millisecond ts")
    d.index = pd.DatetimeIndex(index)
    return d[["open", "high", "low", "close", "volume"]]


def month_bootstrap(values, counts, seed=SEED):
    """Resample whole New York calendar months, never individual trades."""
    if len(values) < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), (10000, len(values)))
    draws = np.asarray(values)[indices].sum(axis=1) / np.asarray(counts)[indices].sum(axis=1)
    return np.quantile(draws, [.025, .975]).tolist()


def describe(trades):
    if len(trades) == 0:
        return {"n": 0}
    net = trades.net_r.to_numpy(float)
    bp = trades.net_return.to_numpy(float) * 10000
    curve = np.r_[0., np.cumsum(net)]
    streak = longest = 0
    for loss in net < 0:
        streak = streak + 1 if loss else 0
        longest = max(longest, streak)
    sums = trades.assign(month=trades.entry_time.dt.tz_convert(NY).dt.strftime("%Y-%m"))
    blocks = sums.groupby("month").net_r.agg(["sum", "count"])
    pf = net[net > 0].sum() / -net[net < 0].sum() if (net < 0).any() else None
    pf_bp = bp[bp > 0].sum() / -bp[bp < 0].sum() if (bp < 0).any() else None
    return dict(n=len(trades), gross_win_rate=float((trades.gross_r > 0).mean()),
        net_win_rate=float((net > 0).mean()), gross_r=float(trades.gross_r.sum()),
        net_r=float(net.sum()), mean_net_r=float(net.mean()),
        gross_bp_mean=float(trades.gross_return.mean() * 10000),
        net_bp_mean=float(bp.mean()), net_bp_sum=float(bp.sum()), profit_factor_r=pf,
        profit_factor_bp=pf_bp, max_closed_drawdown_r=float((np.maximum.accumulate(curve) - curve).max()),
        longest_net_loss_streak=longest, median_risk_pct=float(trades.risk_pct.median()),
        p10_risk_pct=float(trades.risk_pct.quantile(.1)), p90_risk_pct=float(trades.risk_pct.quantile(.9)),
        mean_cost_r=float((COST / trades.risk_pct).mean()),
        cost_r=float((COST / trades.risk_pct).sum()),
        holding_minutes_median=float(trades.holding_bars.median() * 5),
        dual_touch=int(trades.dual_touch.sum()), censored=int(trades.censored.sum()),
        min_net_r=float(net.min()), max_net_r=float(net.max()),
        mean_net_r_month_bootstrap_95=month_bootstrap(blocks["sum"].to_numpy(), blocks["count"].to_numpy()),
        exit_reasons=trades.reason.value_counts().to_dict())


def matched_controls(ctx, trades, start, split, end, seed=SEED):
    """Use previous closed-bar volatility; never select on future trade exits."""
    ix = ctx["frame"].index
    local = ix.tz_convert(NY)
    month = np.asarray(local.strftime("%Y-%m"))
    hour = np.asarray(local.hour)
    bucket = np.r_[-1, np.asarray(ctx["vol_bucket"], dtype=int)[:-1]]
    phase = np.where(ix < split, "early", "late")
    eligible = np.asarray(ctx["entry_eligible"], bool) & (ix >= start) & (ix < end) & (bucket >= 0)
    pools = {}
    for i in np.flatnonzero(eligible):
        key = (month[i], int(hour[i]), int(bucket[i]), phase[i])
        pools.setdefault(key, []).append(int(i))
    pools = {k: np.asarray(v, dtype=int) for k, v in pools.items()}
    rng = np.random.default_rng(seed)
    rows, coverage = [], []
    end_i = int(ix.searchsorted(end))
    for row in trades.itertuples():
        ei = int(row.entry_i)
        key = (month[ei], int(hour[ei]), int(bucket[ei]), phase[ei])
        pool = pools.get(key, np.array([], dtype=int))
        pool = pool[pool != ei]
        chosen = rng.choice(pool, size=min(50, len(pool)), replace=False)
        coverage.append(dict(parent_trade_id=row.trade_id, eligible_controls=len(pool),
                             selected_controls=len(chosen), vol_bucket=int(bucket[ei])))
        for ci in chosen:
            r = trace_trade(ctx, int(ci), int(row.side), risk_pct=float(row.risk_pct), end_i=end_i)
            if not np.isfinite(r["net_r"]):
                raise ValueError("preselected control has non-finite result; do not resample")
            rows.append(dict(parent_trade_id=row.trade_id, parent_entry_time=row.entry_time,
                control_entry_i=int(ci), control_entry_time=ix[ci], month=month[ei],
                ny_hour=int(hour[ei]), vol_bucket=int(bucket[ei]), phase=phase[ei], side=int(row.side),
                risk_pct=float(row.risk_pct), actual_net_r=float(row.net_r),
                actual_net_return=float(row.net_return), control_net_r=float(r["net_r"]),
                control_net_return=float(r["net_return"]), control_exit_i=r["exit_i"],
                control_reason=r["reason"], control_censored=bool(r["censored"])))
    columns = ["parent_trade_id", "parent_entry_time", "control_entry_i", "control_entry_time",
               "month", "ny_hour", "vol_bucket", "phase", "side", "risk_pct", "actual_net_r",
               "actual_net_return", "control_net_r", "control_net_return", "control_exit_i",
               "control_reason", "control_censored"]
    return pd.DataFrame(rows, columns=columns), pd.DataFrame(coverage)


def control_summary(c):
    if len(c) == 0:
        return {"matched_trades": 0, "controls": 0}
    pairs = c.groupby("parent_trade_id", sort=False).agg(
        month=("month", "first"), actual_r=("actual_net_r", "first"),
        control_r=("control_net_r", "mean"), actual_ret=("actual_net_return", "first"),
        control_ret=("control_net_return", "mean"), n=("control_net_r", "size"))
    pairs["diff_r"] = pairs.actual_r - pairs.control_r
    pairs["diff_bp"] = (pairs.actual_ret - pairs.control_ret) * 10000
    blocks = pairs.groupby("month").agg(r=("diff_r", "sum"), bp=("diff_bp", "sum"), n=("diff_r", "size"))
    rng = np.random.default_rng(SEED)
    values = blocks.r.to_numpy(float)
    observed = float(pairs.diff_r.mean())
    if len(blocks) >= 2:
        signs = rng.choice([-1, 1], (20000, len(blocks)))
        null = (signs * values[None, :]).sum(axis=1) / len(pairs)
        p = float((1 + (null >= observed).sum()) / (1 + len(null)))
    else:
        p = None
    return dict(matched_trades=len(pairs), controls=len(c),
        unique_control_times=int(c.control_entry_time.nunique()), months=len(blocks),
        minimum_matches=int(pairs.n.min()), actual_matched_net_r_mean=float(pairs.actual_r.mean()),
        control_net_r_mean=float(pairs.control_r.mean()), excess_r_mean=observed,
        control_net_bp_mean=float(pairs.control_ret.mean() * 10000),
        excess_bp_mean=float(pairs.diff_bp.mean()),
        excess_r_month_bootstrap_95=month_bootstrap(values, blocks.n.to_numpy()),
        excess_bp_month_bootstrap_95=month_bootstrap(blocks.bp.to_numpy(), blocks.n.to_numpy()),
        month_sign_permutation_p_greater=p, censored_controls=int(c.control_censored.sum()))


def group_results(trades, controls, split):
    groups = [("period", "all", np.ones(len(trades), bool)),
              ("period", "early", trades.entry_time < split),
              ("period", "late", trades.entry_time >= split),
              ("side", "long", trades.side == 1), ("side", "short", trades.side == -1)]
    for side, side_name in ((1, "long"), (-1, "short")):
        groups.append(("period_side", "late_" + side_name, (trades.entry_time >= split) & (trades.side == side)))
    for group, labels in (("month", trades.entry_time.dt.tz_convert(NY).dt.strftime("%Y-%m")),
                          ("year", trades.entry_time.dt.tz_convert(NY).dt.strftime("%Y")),
                          ("target_inside_range", trades.target_inside_range.astype(str))):
        for value in labels.unique():
            groups.append((group, str(value), labels == value))
    rows = []
    for group, label, mask in groups:
        subset = trades.loc[mask]
        c = controls.loc[controls.parent_trade_id.isin(subset.trade_id)]
        rows.append(dict(group=group, label=label, **describe(subset), **control_summary(c)))
    return rows


def marked_curve(ctx, trades, start, end):
    """Close-marked unit-risk curve; charge the full 20bp allowance at entry."""
    frame = ctx["frame"]
    completed = np.zeros(len(frame))
    floating = np.zeros(len(frame))
    for row in trades.itertuples():
        a, b = int(row.entry_i), int(row.exit_i)
        completed[b] += row.net_r
        prices = ctx["close"][a:b]
        floating[a:b] = (int(row.side) * (prices / row.entry_price - 1) - COST) / row.risk_pct
    result = pd.DataFrame({"closed_net_r": np.cumsum(completed),
                           "marked_net_r": np.cumsum(completed) + floating}, index=frame.index + BAR)
    result.index.name = "close_time"
    return result.loc[(result.index > start) & (result.index <= end)]


def plot_results(results, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), constrained_layout=True)
    for col, (symbol, item) in enumerate(results.items()):
        tr, curve = item["trades"], item["curve"]
        ax = axes[0, col]
        ax.plot(tr.exit_time, tr.gross_r.cumsum(), label="Gross R", alpha=.75)
        ax.plot(curve.index, curve.marked_net_r, label="Net R, 20bp", linewidth=1)
        ax.axhline(0, color="gray", linewidth=.7)
        ax.set(title=f"{symbol} | New York 00-04 range", ylabel="Cumulative risk units")
        ax.legend()
        monthly = tr.assign(month=tr.entry_time.dt.tz_convert(NY).dt.strftime("%Y-%m")).groupby("month").net_r.sum()
        axes[1, col].bar(monthly.index, monthly.values, color=np.where(monthly.values >= 0, "#268c79", "#c25c65"))
        axes[1, col].set(title="Monthly net R (not account return)", ylabel="Net R")
        axes[1, col].tick_params(axis="x", rotation=90, labelsize=6)
    fig.suptitle("Fixed 2R / excursion stop / next open / NY day-end exit | OKX perpetuals")
    fig.savefig(output / "overview.png", dpi=150)
    plt.close(fig)


def run(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    cfg = json.loads(config_path.read_text())
    builders = [ROOT / p for p in cfg["builders"]] + [config_path]
    identity = committed_identity(builders)
    if output.exists():
        raise FileExistsError("Use a new output directory; previous runs are immutable")
    start, split, end = [pd.Timestamp(cfg[k]) for k in ("start", "split", "end")]
    if not start < split < end:
        raise ValueError("invalid chronological split")
    for stamp in (start, split, end):
        if stamp.tz is None or stamp.tz_convert(NY).time().isoformat() != "00:00:00":
            raise ValueError("study bounds must be timezone-aware New York midnight")
    frames = {symbol: read_source(spec) for symbol, spec in cfg["sources"].items()}
    if set(frames) != {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}:
        raise ValueError("study requires both requested OKX symbols")
    output.mkdir(parents=True)
    dump(output / "identity.json", dict(generated_at=pd.Timestamp.now(tz="UTC"),
         git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
         builders=identity, config=cfg))
    results, rows, summary = {}, [], {}
    for number, (symbol, frame) in enumerate(frames.items()):
        if frame.index[0] > start - pd.Timedelta(days=31) or frame.index[-1] + BAR < end:
            raise ValueError(f"source lacks full evaluation period or 31-day warmup: {symbol}")
        print(f"{symbol}: prepare {len(frame)} source bars", flush=True)
        ctx = prepare(frame)
        replay = simulate(ctx, start, end)
        tr = replay["trades"]
        print(f"{symbol}: {len(tr)} trades, matching controls", flush=True)
        controls, coverage = matched_controls(ctx, tr, start, split, end, SEED + number)
        curve = marked_curve(ctx, tr, start, end)
        out = output / symbol
        out.mkdir()
        for name, table in [("trades", tr), ("events", replay["events"]),
                            ("daily_ranges", replay["daily_ranges"]),
                            ("controls", controls), ("control_coverage", coverage)]:
            table.to_csv(out / f"{name}.csv.gz", index=False, compression=dict(method="gzip", mtime=0))
        tr.to_csv(out / "trades.csv", index=False)
        rename = {"side": "方向", "entry_time": "入场时间UTC", "signal_time": "确认时间UTC",
                  "exit_time": "退出时间UTC", "entry_price": "入场价", "stop_price": "止损价",
                  "target_price": "止盈价", "exit_price": "退出价", "risk_pct": "初始风险比例",
                  "gross_r": "毛R", "net_r": "净R", "net_return": "净价格收益比例",
                  "reason": "退出原因", "dual_touch": "同根双触", "target_inside_range": "目标位于区间内"}
        tr.rename(columns=rename).to_csv(out / "逐笔交易.csv", index=False, encoding="utf-8-sig")
        curve.to_csv(out / "marked_curve.csv.gz", compression=dict(method="gzip", mtime=0))
        grouped = group_results(tr, controls, split)
        rows.extend([dict(symbol=symbol, **row) for row in grouped])
        basic = next(x for x in grouped if x["group"] == "period" and x["label"] == "all")
        marked = np.r_[0., curve.marked_net_r.to_numpy(float)]
        basic = dict(basic, max_marked_drawdown_r=float((np.maximum.accumulate(marked) - marked).max()),
                     source_rows=len(frame), evaluation_bars=int(((frame.index >= start) & (frame.index < end)).sum()),
                     candidate_count=len(replay["events"]), event_statuses=replay["events"].status.value_counts().to_dict(),
                     unmatched_trades=int((coverage.selected_controls == 0).sum()) if len(coverage) else 0,
                     days=len(replay["daily_ranges"]))
        summary[symbol] = dict(all=basic,
            early=next(x for x in grouped if x["group"] == "period" and x["label"] == "early"),
            late=next(x for x in grouped if x["group"] == "period" and x["label"] == "late"))
        results[symbol] = dict(trades=tr, curve=curve)
        print(json.dumps(clean(dict(symbol=symbol, **basic)), ensure_ascii=False), flush=True)
    valid = [(k, v["late"].get("month_sign_permutation_p_greater")) for k, v in summary.items()]
    valid = sorted((k, p) for k, p in valid if p is not None)
    ordered = sorted(valid, key=lambda item: item[1])
    previous = 0.
    for rank, (key, p) in enumerate(ordered):
        adjusted = min(1., max(previous, (len(ordered) - rank) * p))
        summary[key]["late"]["holm_p_two_symbols"] = adjusted
        previous = adjusted
    pd.DataFrame([clean(row) for row in rows]).to_csv(output / "grouped_metrics.csv", index=False)
    dump(output / "summary.json", summary)
    plot_results(results, output)
    files = {str(p.relative_to(ROOT)): dict(sha256=sha(p), size_bytes=p.stat().st_size)
             for p in sorted(output.rglob("*")) if p.is_file()}
    dump(output / "manifest.json", dict(generated_at=pd.Timestamp.now(tz="UTC"), builders=identity, files=files))
    print(f"Completed: {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.config, args.output)
