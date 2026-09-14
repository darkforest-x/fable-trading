"""Period settlement and cluster-aware diagnostics for the ETH release study.

All future prices here are outcomes after an entry, never selection features.
Administrative settlement ends every account/control at the same declared
period boundary; natural trades remain separately counted. Random controls
are independent events, not a capital portfolio. Inference clusters by signal
month because overlapping trades are not independent samples.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd

from yoyo.layers.l3_backtest.pine_allin_v7 import auc_from_scores

SEED = 20260914
N_RESAMPLES = 10000


def settle(result: dict, frame: pd.DataFrame, end: int, fee: float, minutes: int) -> dict:
    """Explicit last-close cash settlement, including both sides of costs."""
    result["marked_final_equity"] = result["final_equity"]
    trades = result["trades"].copy()
    if not trades.empty:
        trades["administrative_exit"] = False
    p = result["open_position"]
    result["boundary_open_position"] = None if p is None else dict(p)
    if p is not None:
        price = float(frame.close.iloc[end - 1])
        exit_fee = p["qty"] * price * fee
        gross = p["direction"] * p["qty"] * (price - p["entry_price"])
        notional = p["qty"] * p["entry_price"]
        net = gross - p["entry_fee"] - exit_fee
        record = dict(p, exit_i=end - 1,
                      exit_time=frame.open_time.iloc[end - 1] + pd.Timedelta(minutes=minutes),
                      exit_price=price, exit_reason="period_end", gross_pnl=gross,
                      net_pnl=net, gross_return=gross / notional, net_return=net / notional,
                      fees=p["entry_fee"] + exit_fee, holding_bars=end - 1 - p["entry_i"],
                      administrative_exit=True)
        trades = pd.concat([trades, pd.DataFrame([record])], ignore_index=True)
        result["fees"] += exit_fee
        result["final_equity"] -= exit_fee
        result["cash"] = result["final_equity"]
        result["open_position"] = None
        if len(result["equity"]):
            result["equity"].loc[result["equity"].index[-1], ["equity", "cash", "position"]] = [result["cash"], result["cash"], 0]
    if not trades.empty:
        trades["holding_hours"] = (pd.to_datetime(trades.exit_time, utc=True) - pd.to_datetime(trades.entry_time, utc=True)).dt.total_seconds() / 3600
        trades["initial_risk_fraction"] = (trades.entry_price - trades.initial_stop).abs() / trades.entry_price
        trades["net_r"] = trades.net_return / trades.initial_risk_fraction.replace(0, np.nan)
    result["trades"] = trades
    final = float(result["final_equity"])
    result["nonpositive_equity_seen"] |= final <= 0
    for kind in ("path", "close"):
        peak = float(result.get(f"{kind}_peak", max(500., final)))
        result[f"max_drawdown_{kind}"] = max(result[f"max_drawdown_{kind}"], (peak - final) / peak)
    if len(result["equity"]):
        values = np.r_[500., result["equity"].equity.to_numpy(float)]
        dd = 1. - values / np.maximum.accumulate(values)
        result["max_drawdown_close"] = max(float(dd.max()), result["max_drawdown_close"])
        result["max_drawdown_path"] = max(result["max_drawdown_path"], result["max_drawdown_close"])
    return result


def profit_factor(values) -> float:
    a = np.asarray(values, dtype=float)
    profit, loss = a[a > 0].sum(), -a[a < 0].sum()
    return float(profit / loss) if loss > 0 else (float("inf") if profit > 0 else float("nan"))


def monthly_returns(equity: pd.DataFrame) -> pd.DataFrame:
    """Assign a close timestamp to the bar just closed, not the next month."""
    if equity.empty:
        return pd.DataFrame(columns=["month", "end_equity", "return_pct"])
    t = pd.to_datetime(equity.time, utc=True) - pd.Timedelta(nanoseconds=1)
    groups = pd.Series(equity.equity.to_numpy(float), index=t).groupby(t.dt.strftime("%Y-%m").to_numpy()).last()
    previous = np.r_[500., groups.to_numpy()[:-1]]
    return pd.DataFrame({"month": groups.index, "end_equity": groups.to_numpy(),
                         "return_pct": (groups.to_numpy() / previous - 1) * 100})


def trade_stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0, "natural_trades": 0, "win_rate_pct": None, "net_bp": None,
                "gross_bp": None, "matched_n": 0, "matched_case_bp": None,
                "control_bp": None, "excess_bp": None}
    matched = t[t.get("matched", pd.Series(False, index=t.index)).fillna(False)]
    positive = t.loc[t.net_pnl > 0, "net_pnl"]
    natural = ~t.administrative_exit.astype(bool)
    answer = {
        "trades": len(t), "natural_trades": int(natural.sum()), "boundary_exits": int((~natural).sum()),
        "win_rate_pct": 100 * float((t.net_return > 0).mean()),
        "gross_win_rate_pct": 100 * float((t.gross_return > 0).mean()),
        "natural_win_rate_pct": 100 * float((t.loc[natural, "net_return"] > 0).mean()) if natural.any() else None,
        "gross_bp": float(t.gross_return.mean() * 1e4), "net_bp": float(t.net_return.mean() * 1e4),
        "median_net_bp": float(t.net_return.median() * 1e4),
        "pf_currency": profit_factor(t.net_pnl), "pf_unit": profit_factor(t.net_return),
        "worst_trade_pct": float(t.net_return.min() * 100), "mean_net_r": float(t.net_r.mean()),
        "holding_hours_mean": float(t.holding_hours.mean()), "holding_hours_median": float(t.holding_hours.median()),
        "max_leverage": float(t.leverage.max()),
        "firstbar_unprotected_touches": int(t.firstbar_unprotected_touch.astype(bool).sum()),
        "gross_winners_turned_net_losers": int(((t.gross_return > 0) & (t.net_return <= 0)).sum()),
        "matched_n": len(matched), "matched_coverage_pct": 100 * len(matched) / len(t),
        "matched_case_bp": float(matched.net_return.mean() * 1e4) if len(matched) else None,
        "control_bp": float(matched.control_mean.mean() * 1e4) if len(matched) else None,
        "excess_bp": float(matched.paired_excess.mean() * 1e4) if len(matched) else None,
        "top1_positive_pnl_share_pct": float(100 * positive.nlargest(1).sum() / positive.sum()) if len(positive) else None,
        "top5_positive_pnl_share_pct": float(100 * positive.nlargest(5).sum() / positive.sum()) if len(positive) else None,
        "net_bp_excluding_top1": float(t.net_return.drop(t.net_return.idxmax()).mean() * 1e4) if len(t) > 1 else None,
        "total_net_pnl_without_best_dollar_trade": float(t.net_pnl.sum() - t.net_pnl.max()),
    }
    for multiple in (1, 3, 10):
        answer[f"mfe_{multiple}r_count"] = int((t.mfe_r >= multiple).sum())
        answer[f"realized_{multiple}r_count"] = int((t.net_r >= multiple).sum())
    return answer


def inference(t: pd.DataFrame) -> dict:
    """Month-cluster randomization/CI plus explicitly diagnostic score test."""
    if len(t) < 2:
        return {"ranking_p": None, "matched_p": None, "matched_months": 0, "auc": None}
    t = t.copy()
    if "matched" not in t:
        t["matched"] = False
    for col in ("control_mean", "paired_excess"):
        if col not in t:
            t[col] = np.nan
    t["matched"] = t.matched.fillna(False).astype(bool) & t.paired_excess.notna() & t.control_mean.notna()
    score, net = t.score.to_numpy(float), t.net_return.to_numpy(float)
    gross = t.gross_return.to_numpy(float)
    rank = np.argsort(-score, kind="stable")[:max(1, math.ceil(len(t) * .1))]
    rng = np.random.default_rng(SEED)
    observed = net[rank].mean()
    extreme = sum(float(net[rng.permutation(len(t))[:len(rank)]].mean()) >= observed for _ in range(N_RESAMPLES))
    matched = t[t.matched].copy()
    matched["month"] = pd.to_datetime(matched.signal_time, utc=True).dt.strftime("%Y-%m")
    by_month = matched.groupby("month").paired_excess.agg(["sum", "count"])
    sums, counts = by_month["sum"].to_numpy(float), by_month["count"].to_numpy(float)
    m = len(by_month)
    p, lower, upper = None, None, None
    if m:
        obs = sums.sum() / counts.sum()
        if m <= 16:
            signs = np.array(list(itertools.product((-1., 1.), repeat=m)))
            p = float(((signs * sums).sum(axis=1) / counts.sum() >= obs - 1e-15).mean())
        else:
            signs = rng.choice((-1., 1.), size=(N_RESAMPLES, m))
            p = float((1 + ((signs * sums).sum(axis=1) / counts.sum() >= obs - 1e-15).sum()) / (N_RESAMPLES + 1))
        if m >= 2:
            picks = rng.integers(0, m, size=(N_RESAMPLES, m))
            means = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
            lower, upper = np.quantile(means * 1e4, (.025, .975)).tolist()
    top = t.iloc[rank]
    return {"auc": auc_from_scores(score, net > 0), "top_decile_n": len(rank),
            "top_decile_gross_bp": float(gross[rank].mean() * 1e4),
            "top_decile_net_bp": float(observed * 1e4),
            "top_decile_control_bp": float(top.loc[top.matched, "control_mean"].mean() * 1e4) if top.matched.any() else None,
            "top_decile_matched_case_bp": float(top.loc[top.matched, "net_return"].mean() * 1e4) if top.matched.any() else None,
            "top_decile_matched_excess_bp": float(top.loc[top.matched, "paired_excess"].mean() * 1e4) if top.matched.any() else None,
            "top_decile_matched_n": int(top.matched.sum()),
            "ranking_p": (extreme + 1) / (N_RESAMPLES + 1), "matched_p": p,
            "matched_months": m, "excess_ci95_lower_bp": lower, "excess_ci95_upper_bp": upper,
            "matched_support_status": "available" if len(matched) else "unavailable_no_eligible_matches",
            "resamples": N_RESAMPLES, "normality_assumption": "none; month clusters may still share regime dependence"}


def account_stats(result: dict) -> dict:
    monthly = monthly_returns(result["equity"])
    events = result["events"]
    t = result["trades"]
    return {"return_pct": (result["final_equity"] / 500 - 1) * 100,
            "marked_return_pct": (result["marked_final_equity"] / 500 - 1) * 100,
            "path_dd_pct": result["max_drawdown_path"] * 100,
            "close_dd_pct": result["max_drawdown_close"] * 100,
            "final_equity": result["final_equity"], "fees": result["fees"],
            "nonpositive_equity_seen": bool(result["nonpositive_equity_seen"]),
            "exposure_pct": result["exposure_fraction"] * 100,
            "profitable_months": int((monthly.return_pct > 0).sum()), "months": len(monthly),
            "worst_month_pct": float(monthly.return_pct.min()) if len(monthly) else None,
            "cooldown_skips": int((events.event == "cooldown_skip").sum()) if len(events) else 0,
            "turnover_notional": float((t.qty * (t.entry_price + t.exit_price)).sum()) if len(t) else 0,
            **trade_stats(t)}


def holm(pvalues: list[float | None]) -> list[float]:
    """Missing tests remain in the planned family as p=1, never disappear."""
    values = np.array([1. if x is None or not np.isfinite(x) else x for x in pvalues])
    order = np.argsort(values)
    adjusted = np.maximum.accumulate(values[order] * np.arange(len(values), 0, -1))
    out = np.empty(len(values))
    out[order] = np.minimum(adjusted, 1.)
    return out.tolist()
