"""Summarize one native fill ledger. All arm assignments are pre-price flags.

R uses actual entry minus the common protective stop. A wave is an observation
of maximum price in one disjoint bullish flip cycle, not an extra TP simulation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def measure_paths(
    fills: pd.DataFrame, bars: pd.DataFrame, config: dict
) -> pd.DataFrame:
    """Measure maxima until scheduled exit using open-time bars, excluding exit-bar high."""
    out = fills.sort_values("entry_time").reset_index(drop=True).copy()
    entry = out["entry_price"].to_numpy(dtype=float)
    exit_price = out["exit_price"].to_numpy(dtype=float)
    risk = entry - out["initial_stop"].to_numpy(dtype=float)
    if np.any(risk <= 0):
        raise ValueError("A funded entry must have positive initial risk")
    size = out["qty"].to_numpy(dtype=float)
    native_fee = (out["entry_fee"] + out["exit_fee"]).to_numpy(dtype=float)
    expected_fee = (entry + exit_price) * size * config["fees"]["taker"]
    np.testing.assert_allclose(native_fee, expected_fee, rtol=1e-8, atol=1e-7)
    out["gross_return"] = (exit_price - entry) / entry
    out["fee_return"] = native_fee / (size * entry)
    out["net_return"] = out["gross_return"] - out["fee_return"]
    out["net_usdt"] = config["notional_usdt_per_trade"] * out["net_return"]
    out["net_r"] = out["net_return"] * entry / risk
    starts = bars.index.get_indexer(out.entry_time)
    ends = bars.index.get_indexer(out.exit_time)
    if np.any(starts < 0) or np.any(ends <= starts):
        raise ValueError("Missing or unordered price window")
    highs = bars.high.to_numpy()
    opens = bars.open.to_numpy()
    peak = np.array([max(highs[a:b].max(), opens[b]) for a, b in zip(starts, ends)])
    out["wave_r"] = (peak - entry) / risk
    out["wave_return"] = peak / entry - 1
    return out


def stats(rows, major):
    pnl = rows.net_usdt.to_numpy()
    r = rows.net_r
    equity = np.r_[0.0, pnl.cumsum()]
    gross_loss = -pnl[pnl < 0].sum()
    return {
        "trades": len(rows),
        "weeks_traded": int(rows.ny_week.nunique()),
        "stopouts": int(rows.exit_reason.eq("hard_stop").sum()),
        "losing_trades": int((pnl < 0).sum()),
        "win_rate": float((pnl > 0).mean()) if len(rows) else None,
        "gross_usdt": float(rows.gross_usdt.sum()),
        "fees_usdt": float(rows.fees_usdt.sum()),
        "net_usdt": float(pnl.sum()),
        "ending_1000u_1x_reinvested": float(1000 * (1 + rows.net_return).prod()),
        "mean_bp": float(1e4 * rows.net_return.mean()) if len(rows) else None,
        "sum_net_r": float(r.sum()),
        "profit_factor_flat_notional": float(pnl[pnl > 0].sum() / gross_loss)
        if gross_loss > 0
        else None,
        "closed_trade_drawdown_usdt": float(
            (np.maximum.accumulate(equity) - equity).max()
        ),
        "large_waves": int((rows.wave_r >= major).sum()),
        "large_wave_weeks": int(rows.loc[rows.wave_r >= major, "ny_week"].nunique()),
        "large_winners": int((r >= major).sum()),
        "ten_r_winners": int((r >= 10).sum()),
        "two_percent_waves": int((rows.wave_return >= 0.02).sum()),
    }


def matched_control(selected, population, config):
    """Exchange labels within week and pre-entry volatility bins; same native exits and fees."""
    reps = int(config["replicates"])
    rng = np.random.default_rng(config["seed"])
    samples = np.zeros(reps)
    expectation = 0.0
    interchangeable = 0
    by = ["ny_week", "vol_bin"]
    groups = {key: group.net_usdt.to_numpy() for key, group in population.groupby(by)}
    for key, n in selected.groupby(by).size().items():
        vals = groups[key]
        if n > len(vals):
            raise ValueError("Matched pool too small")
        expectation += n * vals.mean()
        if n == len(vals):
            samples += vals.sum()
            continue
        interchangeable += n
        keys = rng.random((reps, len(vals)))
        idx = keys.argpartition(n - 1, axis=1)[:, :n]
        samples += vals[idx].sum(axis=1)
    observed = selected.net_usdt.sum()
    return {
        "matched_expected_usdt": float(expectation),
        "observed_minus_expected_usdt": float(observed - expectation),
        "matched_total_95pct": np.quantile(samples, [0.025, 0.975]).tolist(),
        "one_sided_p_higher_net": float(
            (1 + (samples >= observed - 1e-10).sum()) / (1 + reps)
        ),
        "interchangeable": int(interchangeable),
        "matched": len(selected),
    }


def summarize(ledger, weeks, config):
    base = ledger[ledger.setup_armed].copy()
    first = ~base.ny_week.duplicated()
    policies = {
        "all_flips": np.ones(len(base), bool),
        "bottom": base.bottom_pass,
        "trend": base.trend_pass,
        "first_then_bottom": first | base.bottom_pass,
        "first_then_trend": first | base.trend_pass,
    }
    years, weekly, by_arm = [], [], {}
    major = config["major_multiple_r"]
    allstats = stats(base, major)
    for arm, keep in policies.items():
        selected = base.loc[keep]
        s = stats(selected, major)
        missed = base.loc[~np.asarray(keep)]
        s["missed_5r_waves_that_baseline_stopped"] = int(
            ((missed.wave_r >= major) & missed.exit_reason.eq("hard_stop")).sum()
        )
        s["avoided_stopouts"] = allstats["stopouts"] - s["stopouts"]
        s["missed_large_waves"] = allstats["large_waves"] - s["large_waves"]
        s["missed_large_winners"] = allstats["large_winners"] - s["large_winners"]
        s["missed_ten_r_winners"] = allstats["ten_r_winners"] - s["ten_r_winners"]
        s["missed_two_percent_waves"] = (
            allstats["two_percent_waves"] - s["two_percent_waves"]
        )
        s["foregone_net_of_skipped_trades"] = float(
            base.loc[~np.asarray(keep), "net_usdt"].sum()
        )
        if arm in ("all_flips", "bottom", "trend"):
            s["matched_random"] = matched_control(
                selected, ledger if arm == "all_flips" else base, config
            )
        for year in sorted(set(w[:4] for w in weeks)):
            rows = selected[selected.ny_week.str.startswith(year)]
            years.append({"arm": arm, "period": year, **stats(rows, major)})
        for period, early in [("2023_2024", True), ("2025_2026", False)]:
            rows = selected[(selected.ny_week < config["time_split_ny"]) == early]
            years.append({"arm": arm, "period": period, **stats(rows, major)})
        week_groups = selected.groupby("ny_week")
        weekly_net = week_groups.net_usdt.sum().reindex(weeks, fill_value=0)
        weekly_count = week_groups.size().reindex(weeks, fill_value=0)
        for week in weeks:
            weekly.append(
                {
                    "arm": arm,
                    "ny_week": week,
                    "trades": int(weekly_count[week]),
                    "net_usdt": float(weekly_net[week]),
                }
            )
        by_arm[arm] = s
    if not np.isclose(
        allstats["net_usdt"], allstats["gross_usdt"] - allstats["fees_usdt"]
    ):
        raise ValueError("Ledger net does not reconcile")
    weekly = pd.DataFrame(weekly)
    pivot = weekly.pivot(index="ny_week", columns="arm", values="net_usdt")
    rng = np.random.default_rng(config["seed"])
    idx = rng.integers(0, len(pivot), size=(config["replicates"], len(pivot)))
    for arm, s in by_arm.items():
        delta = (pivot[arm] - pivot["all_flips"]).to_numpy()
        s["net_delta_to_all_95pct_week_bootstrap"] = np.quantile(
            delta[idx].sum(axis=1), [0.025, 0.975]
        ).tolist()
    return by_arm, pd.DataFrame(years), weekly
