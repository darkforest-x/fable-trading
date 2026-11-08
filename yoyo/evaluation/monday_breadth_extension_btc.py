"""Causal BTC higher-timeframe gates on the locked freq36_BTC opportunity clock.

The 246 source alerts and their six-day VectorBT returns are immutable inputs.
Each gate accepts or skips an alert in place; skipped alerts remain zero-return
opportunities. 2026 matched controls are rebuilt from the existing BTC/month/
6-hour/1h-volatility/1h-trend/weekday/monitor strata and then gated by the same
completed 4h/daily EMA condition as the actual alert.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_controls as matcher
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_match_exits as match_stats
from yoyo.evaluation import monday_breadth_opt_payoffs as quotes
from yoyo.evaluation import monday_breadth_reversal_window as fixed_engine


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1"
OUTPUT = ROOT / "experiments/active/exp-monday-breadth-extension-20261108-v1/btc"
OPPORTUNITIES = SOURCE / "fixed/locked_events.parquet"
BTC_SYMBOL = "BTCUSDT"
TAKER = 0.0005
HOLD_HOURS = 144
HOLD_BARS = HOLD_HOURS * 12
SIGNAL_CUTOFF = pd.Timestamp("2026-10-28", tz="UTC")
PERIODS = {
    "2020_2022": (pd.Timestamp("2020-01-01", tz="UTC"), pd.Timestamp("2023-01-01", tz="UTC")),
    "2023_2024": (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "2025": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")),
    "2026_to_ledger_end": (pd.Timestamp("2026-01-01", tz="UTC"), SIGNAL_CUTOFF),
}
RULES = (
    "4h_ema20",
    "4h_ema50",
    "1d_ema20",
    "1d_ema50",
    "4h_reclaim_ema20",
)
MINIMUMS = {"2020_2022": 41, "2023_2024": 40, "2025": 24}


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _read_opportunities() -> pd.DataFrame:
    if not OPPORTUNITIES.is_file():
        raise FileNotFoundError(OPPORTUNITIES)
    events = pd.read_parquet(OPPORTUNITIES)
    events = events.loc[events.candidate_id.eq("freq36_BTC")].copy()
    if len(events) != 246:
        raise ValueError(f"Expected the 246 locked freq36_BTC opportunities, found {len(events)}")
    events["known"] = pd.to_datetime(events.known, utc=True)
    events["decision"] = pd.to_datetime(events.decision, utc=True)
    events["net_pct"] = pd.to_numeric(events.net_pct, errors="coerce")
    events = events.sort_values("decision", kind="stable").reset_index(drop=True)
    if events.decision.duplicated().any() or events.net_pct.isna().any():
        raise ValueError("Locked BTC opportunity times must be unique and have complete six-day returns")
    if not (events.decision == events.known + pd.Timedelta(minutes=10)).all():
        raise ValueError("freq36_BTC opportunity decision timing changed")
    if not events.asset.eq("BTC").all() or not events.minutes.eq(720).all() or not events.hours.eq(144).all():
        raise ValueError("freq36_BTC lock no longer describes BTC / 720m / 144h")
    events["event_id"] = np.arange(len(events), dtype=np.int64)
    events["period"] = events.decision.map(_period_for)
    events["year"] = events.decision.dt.year.astype(int)
    events["month"] = events.decision.dt.strftime("%Y-%m")
    events["decision_bjt"] = events.decision.dt.tz_convert("Asia/Shanghai")
    return events


def _period_for(stamp: Any) -> str:
    value = _utc(stamp)
    for name, (start, end) in PERIODS.items():
        if start <= value < end:
            return name
    if value >= SIGNAL_CUTOFF:
        return "after_ledger_end"
    return "before_scope"


def _load_btc(events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    first = events.decision.min() - pd.Timedelta(days=60)
    last_exit = events.decision.max() + pd.Timedelta(hours=HOLD_HOURS)
    # Read registered local BTC USD-M sources only. No REST repair or fill replay.
    frame, receipt = quotes.load_full_ohlc(
        BTC_SYMBOL, first, last_exit, repair_missing=False,
    )
    if frame.empty:
        raise ValueError("No registered BTC OHLC source is available")
    if frame.index.tz is None or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("BTC OHLC must have a sorted, unique UTC candle-open index")
    return frame.loc[:, ["open", "high", "low", "close"]].copy(), receipt


def _higher_timeframe(frame: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Build complete UTC higher candles and causal EMA values from 5m closes.

    Each output timestamp is the candle close time. The aggregation uses 5m
    candles whose open times lie in [bar_open, bar_close); incomplete candles
    are unavailable and restart EMA warmup. EMA inputs use only completed HTF
    closes, with min_periods equal to the EMA span.
    """
    if hours not in (4, 24):
        raise ValueError(f"Unsupported higher timeframe: {hours}h")
    grid_index = pd.date_range(frame.index.min().floor("5min"), frame.index.max().floor("5min"), freq="5min")
    dense = frame[["open", "high", "low", "close"]].reindex(grid_index)
    frequency = f"{hours}h"
    groups = dense.resample(frequency, closed="left", label="right", origin="epoch")
    bars = groups.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    counts = groups.close.count()
    complete = counts.eq(hours * 12)
    bars.loc[~complete, ["open", "high", "low", "close"]] = np.nan
    bars["complete"] = complete
    bars["block"] = (~complete).cumsum().astype(np.int64)
    for span in (20, 50):
        bars[f"ema{span}"] = bars.close.groupby(bars.block).transform(
            lambda series: series.ewm(span=span, adjust=False, min_periods=span).mean()
        )
    bars["prior_close"] = bars.close.shift(1)
    bars["prior_ema20"] = bars.ema20.shift(1)
    bars["reclaim_ema20"] = (
        bars.close.gt(bars.ema20) & bars.prior_close.le(bars.prior_ema20)
    ).where(bars.complete & bars.ema20.notna() & bars.prior_close.notna() & bars.prior_ema20.notna())
    bars["bar_open_time"] = bars.index - pd.Timedelta(hours=hours)
    bars.index.name = "bar_close_time"
    return bars


def _asof_features(times: pd.DatetimeIndex | pd.Series, htf: dict[int, pd.DataFrame]) -> pd.DataFrame:
    query = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    result = pd.DataFrame(index=np.arange(len(query)))
    result["query_time"] = query
    for hours, bars in htf.items():
        prefix = "1d" if hours == 24 else f"{hours}h"
        ix = bars.index.searchsorted(query, side="right") - 1
        valid = ix >= 0
        names = {
            f"{prefix}_bar_close_time": pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]"),
            f"{prefix}_bar_open_time": pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]"),
            f"{prefix}_close": pd.Series(np.nan, index=result.index, dtype=float),
            f"{prefix}_ema20": pd.Series(np.nan, index=result.index, dtype=float),
            f"{prefix}_ema50": pd.Series(np.nan, index=result.index, dtype=float),
            f"{prefix}_reclaim_ema20": pd.Series(np.nan, index=result.index, dtype=float),
            f"{prefix}_complete": pd.Series(False, index=result.index, dtype=bool),
        }
        valid_rows = np.flatnonzero(valid)
        if len(valid_rows):
            selected = bars.iloc[ix[valid_rows]]
            names[f"{prefix}_bar_close_time"].iloc[valid_rows] = selected.index.to_numpy()
            names[f"{prefix}_bar_open_time"].iloc[valid_rows] = selected.bar_open_time.to_numpy()
            names[f"{prefix}_close"].iloc[valid_rows] = selected.close.to_numpy(dtype=float)
            names[f"{prefix}_ema20"].iloc[valid_rows] = selected.ema20.to_numpy(dtype=float)
            names[f"{prefix}_ema50"].iloc[valid_rows] = selected.ema50.to_numpy(dtype=float)
            names[f"{prefix}_reclaim_ema20"].iloc[valid_rows] = selected.reclaim_ema20.to_numpy(dtype=float)
            names[f"{prefix}_complete"].iloc[valid_rows] = selected.complete.to_numpy(dtype=bool)
        for name, values in names.items():
            result[name] = values.to_numpy()
    return result


def _gate(features: pd.DataFrame, rule: str) -> np.ndarray:
    if rule not in RULES:
        raise ValueError(f"Unknown trend rule: {rule}")
    if rule == "4h_reclaim_ema20":
        return features["4h_reclaim_ema20"].fillna(0).astype(bool).to_numpy()
    hours, ema = rule.split("_")
    close = pd.to_numeric(features[f"{hours}_close"], errors="coerce")
    value = pd.to_numeric(features[f"{hours}_{ema}"], errors="coerce")
    return close.gt(value).fillna(False).to_numpy(dtype=bool)


def _attach_opportunity_returns(events: pd.DataFrame, frame: pd.DataFrame,
                                htf: dict[int, pd.DataFrame]) -> pd.DataFrame:
    out = events.copy()
    out["exit_time"] = out.decision + pd.Timedelta(hours=HOLD_HOURS)
    out["entry_open"] = frame.open.reindex(pd.DatetimeIndex(out.decision)).to_numpy(dtype=float)
    out["exit_open"] = frame.open.reindex(pd.DatetimeIndex(out.exit_time)).to_numpy(dtype=float)
    if not np.isfinite(out[["entry_open", "exit_open"]].to_numpy(dtype=float)).all():
        raise ValueError("A locked six-day opportunity has no local entry or exit open")
    out["gross_price_pct"] = 100 * (out.exit_open / out.entry_open - 1)
    out["entry_fee_points"] = TAKER * out.entry_open
    out["exit_fee_points"] = TAKER * out.exit_open
    out["total_fee_points"] = out.entry_fee_points + out.exit_fee_points
    out["total_fee_pct"] = 100 * out.total_fee_points / out.entry_open
    reconstructed = out.gross_price_pct - out.total_fee_pct
    difference = (reconstructed - out.net_pct).abs()
    if difference.max() > 1e-6:
        raise ValueError(f"Locked VectorBT returns differ from their documented two-taker-fill costs by {difference.max():.9g} pp")
    out["fee_reconstruction_diff_pp"] = reconstructed - out.net_pct
    for hours in (4, 24):
        asof = _asof_features(out.decision, {hours: htf[hours]})
        for name in asof.columns:
            if name != "query_time":
                out[name] = asof[name].to_numpy()
    for rule in RULES:
        out[f"accept_{rule}"] = _gate(out, rule)
    return out


def _equity_metrics(returns: pd.Series | np.ndarray) -> dict[str, float]:
    values = pd.to_numeric(pd.Series(returns), errors="coerce").dropna().to_numpy(dtype=float)
    if not len(values):
        return {"growth_pct": np.nan, "max_drawdown_pct": np.nan}
    if (values <= -100).any():
        return {"growth_pct": -100.0, "max_drawdown_pct": 100.0}
    equity = np.r_[1.0, np.cumprod(1.0 + values / 100.0)]
    drawdown = 1.0 - equity / np.maximum.accumulate(equity)
    return {
        "growth_pct": float((equity[-1] - 1.0) * 100.0),
        "max_drawdown_pct": float(drawdown.max() * 100.0),
    }


def _mean_win_metrics(values: pd.Series) -> dict[str, float]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return {
        "mean_net_pct": float(clean.mean()) if len(clean) else np.nan,
        "median_net_pct": float(clean.median()) if len(clean) else np.nan,
        "win_pct": float(100 * clean.gt(0).mean()) if len(clean) else np.nan,
    }


def _monthly_p(differences: pd.Series) -> float:
    return float(match_stats.paired_month_p(differences)) if differences.notna().any() else np.nan


def _holm(values: pd.Series, family_size: int) -> pd.Series:
    p = values.fillna(1.0).clip(0.0, 1.0).to_numpy(dtype=float)
    if family_size < len(p):
        raise ValueError("Holm family size is too small")
    order = np.argsort(p, kind="stable")
    adjusted = np.empty(len(p), dtype=float)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (family_size - rank) * p[index]))
        adjusted[index] = running
    return pd.Series(adjusted, index=values.index)


def _funding_history() -> tuple[pd.DataFrame, dict[str, Any]]:
    history = funding.rates(BTC_SYMBOL, history=False)
    if history.empty:
        raise FileNotFoundError("The registered 2026 BTC funding history is unavailable")
    receipt = {
        "path": str((funding.ROOT / "funding_2026/normalized/binance/BTCUSDT_funding.csv.gz").relative_to(ROOT)),
        "rows": int(len(history)),
        "first_settlement_utc": history.index.min().isoformat(),
        "last_settlement_utc": history.index.max().isoformat(),
        "missing_mark_prices": int(history.mark_price.isna().sum()),
        "price_proxy_rows": int(history.get("price_proxy", pd.Series(False, index=history.index)).fillna(False).sum()),
    }
    return history, receipt


def _funding_points(rows: pd.DataFrame, history: pd.DataFrame) -> np.ndarray:
    if rows.empty:
        return np.empty(0, dtype=float)
    fill_rows = pd.DataFrame({
        "entry_time": pd.to_datetime(rows.entry_time, utc=True),
        "exit_time": pd.to_datetime(rows.exit_time, utc=True),
        "first_exit_time": pd.to_datetime(rows.exit_time, utc=True),
        "exit_legs": 1,
        "policy_id": 0,
    })
    result = funding.charged(fill_rows, history, funding.funded_policies())
    return np.asarray(result, dtype=float)


def _attach_funding(events: pd.DataFrame, history: pd.DataFrame, frame: pd.DataFrame,
                    *, return_col: str, entry_time_col: str = "decision") -> pd.DataFrame:
    result = events.copy()
    result["entry_time"] = pd.to_datetime(result[entry_time_col], utc=True)
    result["entry_open"] = frame.open.reindex(pd.DatetimeIndex(result.entry_time)).to_numpy(dtype=float)
    result["funding_points_cost"] = _funding_points(result, history)
    result["funded_net_pct"] = pd.to_numeric(result[return_col], errors="coerce") - (
        100 * result.funding_points_cost / result.entry_open
    )
    if result.funding_points_cost.isna().any() or result.funded_net_pct.isna().any():
        raise ValueError("2026 funding history does not cover every fixed 144h event")
    return result


def _month_inputs(events: pd.DataFrame, frame: pd.DataFrame, htf: dict[int, pd.DataFrame]) -> tuple[
    dict[str, pd.DataFrame], dict[str, Any]
]:
    lower_touches, shocks = matcher._ledger_controls()
    signals = matcher._signal_lookup()
    min_warmup = pd.Timedelta(minutes=max(720 + 10, 65) + 5)
    rows_by_rule: dict[str, list[dict[str, Any]]] = {rule: [] for rule in RULES}
    source_opportunities = events[events.decision.ge(PERIODS["2026_to_ledger_end"][0]) & events.decision.lt(SIGNAL_CUTOFF)]
    for rule in RULES:
        selected = source_opportunities[source_opportunities[f"accept_{rule}"]].copy()
        for month, month_events in selected.groupby(selected.decision.dt.strftime("%Y-%m"), sort=True):
            month_start = pd.Timestamp(f"{month}-01", tz="UTC")
            month_end = month_start + pd.DateOffset(months=1)
            monitor_grid, monitor_prefix = matcher._monitor_prefix(month_start - min_warmup, month_end)
            for event in month_events.itertuples(index=False):
                signal = signals.get((int(event.minutes), _utc(event.known), int(event.wait_min)))
                if signal is None:
                    raise ValueError(f"No locked monitor schedule for {event.decision}")
                event_id = int(event.event_id)
                task = pd.Series({
                    "candidate_id": f"{event.candidate_id}|{rule}",
                    "rule_event_id": f"freq36_BTC|{rule}|{event.decision.isoformat()}",
                    "candidate_trade_id": f"{event.event_id}",
                    "decision": _utc(event.decision),
                    "known": _utc(event.known),
                    "asset": "BTC",
                    "source": f"btc_ema_{rule}",
                    "role": "frequency_primary",
                    "policy_id": "six_day_time_exit",
                    "epoch": int(event.epoch),
                    "epoch_rank": 3,
                    "minutes": int(event.minutes),
                    "wait_min": int(event.wait_min),
                    "hold_hours": HOLD_HOURS,
                    "match_horizon_hours": HOLD_HOURS,
                    "symbol": BTC_SYMBOL,
                    "weight": 1.0,
                    "expected_symbols": 1,
                    "draw_limit": matcher.MAX_FIXED_DRAWS,
                    "candidate_monitor_fraction": float(signal["monitor_fraction"]),
                    "monitor_fraction": float(signal["monitor_fraction"]),
                })
                pool, context, status = matcher._candidate_pool(
                    task, frame, lower_touches, shocks, monitor_grid, monitor_prefix, past_only=True,
                )
                pool_times = frame.index.take(pool)
                gate = _gate(_asof_features(pool_times, htf), rule) if len(pool) else np.empty(0, dtype=bool)
                trend_pool = pool[gate]
                sampled = matcher._draw_positions(trend_pool, task, matcher.MAX_FIXED_DRAWS) if len(trend_pool) else np.empty(0, dtype=np.int64)
                common = {
                    "rule": rule,
                    "event_id": event_id,
                    "decision": _utc(event.decision),
                    "month": str(month),
                    "candidate_vol_bucket": context.get("candidate_vol_bucket"),
                    "candidate_trend_bucket_1h": context.get("candidate_trend_bucket"),
                    "unfiltered_match_pool_n": int(len(pool)),
                    "trend_filtered_match_pool_n": int(len(trend_pool)),
                    "draw_count": int(len(sampled)),
                    "match_status": status if len(trend_pool) else "no_exact_stratum_control_times_after_trend_gate",
                }
                rows_by_rule[rule].append(common)
                for draw, pos in enumerate(sampled):
                    rows_by_rule[rule].append({
                        **common,
                        "draw": int(draw),
                        "control_position": int(pos),
                        "random_entry": frame.index[int(pos)],
                        "random_exit": frame.index[int(pos)] + pd.Timedelta(hours=HOLD_HOURS),
                    })
        print(f"prepared gated 2026 BTC control pools for {rule}: {len(selected)} accepted alerts", flush=True)
    coverage = pd.concat(
        [pd.DataFrame([row for row in rows if "draw" not in row]) for rows in rows_by_rule.values()],
        ignore_index=True,
    ) if any(rows_by_rule.values()) else pd.DataFrame()
    drafts = pd.concat(
        [pd.DataFrame([row for row in rows if "draw" in row]) for rows in rows_by_rule.values() if any("draw" in row for row in rows)],
        ignore_index=True,
    ) if any(any("draw" in row for row in rows) for rows in rows_by_rule.values()) else pd.DataFrame()
    if not drafts.empty:
        positions = np.sort(drafts.control_position.unique().astype(int))
        net = fixed_engine.trade_returns(frame.open.to_numpy(dtype=float), positions, hold_bars=HOLD_BARS)
        lookup = net.to_dict()
        drafts["random_entry_open"] = frame.open.reindex(pd.DatetimeIndex(drafts.random_entry)).to_numpy(dtype=float)
        drafts["random_exit_open"] = frame.open.reindex(pd.DatetimeIndex(drafts.random_exit)).to_numpy(dtype=float)
        drafts["random_gross_price_pct"] = 100 * (drafts.random_exit_open / drafts.random_entry_open - 1)
        drafts["random_entry_fee_points"] = TAKER * drafts.random_entry_open
        drafts["random_exit_fee_points"] = TAKER * drafts.random_exit_open
        drafts["random_fee_pct"] = 100 * (drafts.random_entry_fee_points + drafts.random_exit_fee_points) / drafts.random_entry_open
        drafts["random_net_pct"] = drafts.control_position.map(lookup)
        if not np.allclose(
            drafts.random_net_pct.to_numpy(),
            (drafts.random_gross_price_pct - drafts.random_fee_pct).to_numpy(),
            rtol=0, atol=1e-6,
        ):
            raise ValueError("Matched random returns disagree with the same two-taker-fill VectorBT accounting")
        draft_features = _asof_features(pd.DatetimeIndex(drafts.random_entry), htf)
        for column in draft_features.columns:
            if column != "query_time":
                drafts[column] = draft_features[column].to_numpy()
        for rule in RULES:
            rule_rows = drafts.rule.eq(rule)
            if rule_rows.any() and not _gate(drafts.loc[rule_rows], rule).all():
                raise ValueError(f"A {rule} random control failed the trend gate")
    receipts = {
        "local_ohlc": "source receipt written separately",
        "random_control_engine": "monday_breadth_reversal_window.trade_returns (VectorBT from_orders), 144h fixed market exit",
        "draws_per_signal_max": matcher.MAX_FIXED_DRAWS,
        "match_pool_rule": "same BTC, UTC decision month, UTC 6h block, prior 1h volatility bucket, prior 1h trend sign, NY weekdays, monitor fraction; past-only shock and lower-touch rules; no overlap with actual holding interval; then same HTF gate",
        "control_pool_filter_applied_before_draw": True,
    }
    return {"draws": drafts, "coverage": coverage}, receipts


def _control_summaries(controls: dict[str, pd.DataFrame], events: pd.DataFrame,
                       funding_history: pd.DataFrame, frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    draws = controls["draws"].copy()
    coverage = controls["coverage"].copy()
    actual = events[
        events.decision.ge(PERIODS["2026_to_ledger_end"][0])
        & events.decision.lt(SIGNAL_CUTOFF)
    ].copy()
    summaries = []
    control_event_rows = []
    for rule in RULES:
        accepted = actual[actual[f"accept_{rule}"]].copy()
        if accepted.empty:
            summaries.append({"rule": rule, "accepted_2026_n": 0, "matched_events_n": 0,
                              "mean_actual_funded_pct": np.nan, "mean_random_funded_pct": np.nan,
                              "mean_paired_edge_funded_pp": np.nan, "p_month": np.nan,
                              "matched_months": 0})
            continue
        subset = draws[draws.rule.eq(rule)].copy() if not draws.empty else pd.DataFrame()
        if not subset.empty:
            subset = _attach_funding(subset, funding_history, frame,
                                     return_col="random_net_pct", entry_time_col="random_entry")
            group = subset.groupby("event_id", as_index=False).agg(
                random_net_mean_pct=("random_net_pct", "mean"),
                random_funded_mean_pct=("funded_net_pct", "mean"),
                random_draws=("draw", "nunique"),
                random_unique_times=("random_entry", "nunique"),
                random_pool_n=("trend_filtered_match_pool_n", "first"),
                random_unfiltered_pool_n=("unfiltered_match_pool_n", "first"),
            )
        else:
            group = pd.DataFrame(columns=["event_id", "random_net_mean_pct", "random_funded_mean_pct",
                                          "random_draws", "random_unique_times", "random_pool_n",
                                          "random_unfiltered_pool_n"])
        a2026 = accepted[accepted.period.eq("2026_to_ledger_end")].copy()
        a2026 = _attach_funding(a2026, funding_history, frame, return_col="net_pct")
        paired = a2026.merge(group, on="event_id", how="left", validate="one_to_one")
        paired["funded_edge_pp"] = paired.funded_net_pct - paired.random_funded_mean_pct
        paired["price_edge_pp"] = paired.net_pct - paired.random_net_mean_pct
        coverage_part = coverage[coverage.rule.eq(rule)][["event_id", "unfiltered_match_pool_n",
            "trend_filtered_match_pool_n", "draw_count", "match_status"]] if not coverage.empty else pd.DataFrame()
        if not coverage_part.empty:
            paired = paired.drop(columns=[c for c in ("random_pool_n", "random_unfiltered_pool_n") if c in paired])
            paired = paired.merge(coverage_part, on="event_id", how="left", validate="one_to_one")
        paired["rule"] = rule
        paired["paired_month"] = paired.decision.dt.strftime("%Y-%m")
        control_event_rows.append(paired)
        good = paired.dropna(subset=["funded_edge_pp"])
        p_month = _monthly_p(good.set_index("decision").funded_edge_pp) if len(good) else np.nan
        summaries.append({
            "rule": rule,
            "accepted_2026_n": int(len(paired)),
            "matched_events_n": int(len(good)),
            "matched_months": int(good.paired_month.nunique()) if len(good) else 0,
            "mean_actual_price_net_pct": float(paired.net_pct.mean()) if len(paired) else np.nan,
            "mean_random_price_net_pct": float(paired.random_net_mean_pct.mean()) if paired.random_net_mean_pct.notna().any() else np.nan,
            "mean_paired_edge_price_pp": float(paired.price_edge_pp.mean()) if paired.price_edge_pp.notna().any() else np.nan,
            "mean_actual_funded_pct": float(good.funded_net_pct.mean()) if len(good) else np.nan,
            "mean_random_funded_pct": float(good.random_funded_mean_pct.mean()) if len(good) else np.nan,
            "mean_paired_edge_funded_pp": float(good.funded_edge_pp.mean()) if len(good) else np.nan,
            "p_month": p_month,
            "events_without_eligible_trend_matched_control": int(len(paired) - len(good)),
            "mean_filtered_candidate_pool_n": float(paired.trend_filtered_match_pool_n.mean()) if "trend_filtered_match_pool_n" in paired else np.nan,
            "mean_base_candidate_pool_n": float(paired.unfiltered_match_pool_n.mean()) if "unfiltered_match_pool_n" in paired else np.nan,
        })
    summary = pd.DataFrame(summaries)
    summary["p_holm_five_rules"] = _holm(summary.p_month, family_size=len(RULES))
    control_events = pd.concat(control_event_rows, ignore_index=True) if control_event_rows else pd.DataFrame()
    if not draws.empty:
        draws = draws.drop(columns=["control_position"], errors="ignore")
        draws = _attach_funding(draws, funding_history, frame, return_col="random_net_pct", entry_time_col="random_entry")
    controls["draws"] = draws
    return summary, control_events


def run() -> dict[str, Path]:
    events = _read_opportunities()
    raw, price_receipt = _load_btc(events)
    htf = {4: _higher_timeframe(raw, 4), 24: _higher_timeframe(raw, 24)}
    events = _attach_opportunity_returns(events, raw, htf)
    if events.period.value_counts().to_dict() != {
        "2020_2022": 81, "2023_2024": 80, "2025": 45, "2026_to_ledger_end": 40,
    }:
        raise ValueError(f"The locked historical opportunity split changed: {events.period.value_counts().to_dict()}")
    if (events.decision.diff().dropna() < pd.Timedelta(hours=HOLD_HOURS)).any():
        raise ValueError("The original six-day alert clock contains overlapping held positions")

    price_differences: dict[str, float] = {}
    development_differences: dict[str, float] = {}
    development_events = events[events.period.isin(["2020_2022", "2023_2024", "2025"])]
    for rule in RULES:
        price_differences[rule] = _monthly_p(
            (events.net_pct.where(events[f"accept_{rule}"], 0) - events.net_pct).set_axis(events.decision)
        )
        development_differences[rule] = _monthly_p(
            (development_events.net_pct.where(development_events[f"accept_{rule}"], 0)
             - development_events.net_pct).set_axis(development_events.decision)
        )
    price_p = pd.Series(price_differences)
    price_holm = _holm(price_p, family_size=len(RULES))
    development_p = pd.Series(development_differences)
    development_holm = _holm(development_p, family_size=len(RULES))

    funding_history, funding_receipt = _funding_history()
    y2026 = events[events.period.eq("2026_to_ledger_end")].copy()
    y2026 = _attach_funding(y2026, funding_history, raw, return_col="net_pct")
    if y2026.funding_points_cost.isna().any() or y2026.funded_net_pct.isna().any():
        raise ValueError("Every 2026 baseline event needs complete realized funding coverage")
    funded_p = {}
    for rule in RULES:
        funded_p[rule] = _monthly_p(
            (y2026.funded_net_pct.where(y2026[f"accept_{rule}"], 0) - y2026.funded_net_pct)
            .set_axis(y2026.decision)
        )
    funded_p_series = pd.Series(funded_p)
    funded_holm = _holm(funded_p_series, family_size=len(RULES))

    control_data, control_receipt = _month_inputs(events, raw, htf)
    control_summary, control_events = _control_summaries(control_data, events, funding_history, raw)

    opportunity_rows = events.copy()
    for rule in RULES:
        opportunity_rows[f"skipped_{rule}"] = ~opportunity_rows[f"accept_{rule}"]
        opportunity_rows[f"per_opportunity_{rule}_net_pct"] = opportunity_rows.net_pct.where(opportunity_rows[f"accept_{rule}"], 0.0)
    opportunity_rows["accept_any_filter"] = opportunity_rows[[f"accept_{rule}" for rule in RULES]].any(axis=1)
    local_dates = opportunity_rows.decision_bjt.dt.strftime("%Y-%m-%d")
    september = opportunity_rows[local_dates.isin(["2026-09-16", "2026-09-17"])].copy()
    september["september_clock_scope"] = "freq36_BTC opportunity"

    monthly_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for rule in RULES:
        take = events[f"accept_{rule}"].to_numpy(dtype=bool)
        base_all = events.net_pct
        per_all = events.net_pct.where(take, 0.0)
        selected = events.loc[take]
        row: dict[str, Any] = {
            "rule": rule,
            "n_original_opportunities": int(len(events)),
            "n_accepted_all_history": int(take.sum()),
            "accepted_pct_all_history": float(100 * take.mean()),
            "mean_accepted_net_pct_all_history": float(selected.net_pct.mean()) if len(selected) else np.nan,
            "mean_per_original_opportunity_net_pct_skips_zero_all_history": float(per_all.mean()),
            "baseline_mean_net_pct_all_history": float(base_all.mean()),
            "incremental_mean_per_original_opportunity_pp_all_history": float(per_all.mean() - base_all.mean()),
            "active_months_all_history": int(selected.month.nunique()),
            "development_2020_2025_month_p_vs_baseline": development_p.loc[rule],
            "development_2020_2025_month_p_holm_five_rules": development_holm.loc[rule],
            "price_month_p_vs_baseline_all_history": price_p.loc[rule],
            "price_month_p_holm_five_rules_all_history": price_holm.loc[rule],
            "2026_funded_month_p_vs_baseline": funded_p_series.loc[rule],
            "2026_funded_month_p_holm_five_rules": funded_holm.loc[rule],
        }
        row.update({f"accepted_all_history_{k}": v for k, v in _mean_win_metrics(selected.net_pct).items()})
        row.update({f"accepted_all_history_{k}": v for k, v in _equity_metrics(selected.net_pct).items()})
        row.update({f"zero_skip_all_history_{k}": v for k, v in _equity_metrics(per_all).items()})
        improvement_values = []
        for period in ("2020_2022", "2023_2024", "2025", "2026_to_ledger_end"):
            part = events[events.period.eq(period)].copy()
            part_take = part[f"accept_{rule}"]
            chosen = part.loc[part_take]
            per_op = part.net_pct.where(part_take, 0.0)
            metric = _mean_win_metrics(chosen.net_pct)
            equity = _equity_metrics(chosen.net_pct)
            zero_equity = _equity_metrics(per_op)
            baseline_mean = float(part.net_pct.mean()) if len(part) else np.nan
            accepted_mean = metric["mean_net_pct"]
            delta = accepted_mean - baseline_mean if np.isfinite(accepted_mean) and np.isfinite(baseline_mean) else np.nan
            if period in MINIMUMS:
                improvement_values.append(delta)
            row.update({
                f"{period}_n_original": int(len(part)),
                f"{period}_n_accepted": int(part_take.sum()),
                f"{period}_acceptance_pct": float(100 * part_take.mean()) if len(part) else np.nan,
                f"{period}_mean_accepted_net_pct": accepted_mean,
                f"{period}_median_accepted_net_pct": metric["median_net_pct"],
                f"{period}_win_pct_accepted": metric["win_pct"],
                f"{period}_baseline_mean_net_pct": baseline_mean,
                f"{period}_accepted_mean_lift_vs_baseline_per_fill_pp": delta,
                f"{period}_mean_net_pct_per_original_opportunity_skips_zero": float(per_op.mean()) if len(part) else np.nan,
                f"{period}_incremental_mean_per_original_opportunity_pp": float(per_op.mean() - baseline_mean) if len(part) else np.nan,
                f"{period}_active_months": int(chosen.month.nunique()),
                f"{period}_accepted_growth_pct": equity["growth_pct"],
                f"{period}_accepted_max_drawdown_pct": equity["max_drawdown_pct"],
                f"{period}_zero_skip_growth_pct": zero_equity["growth_pct"],
                f"{period}_zero_skip_max_drawdown_pct": zero_equity["max_drawdown_pct"],
            })
            month_stats = part.assign(accepted=part_take.to_numpy(), zero_skip_return=per_op.to_numpy())
            for month, month_part in month_stats.groupby("month", sort=True):
                monthly_rows.append({
                    "rule": rule,
                    "month": month,
                    "period": period,
                    "original_opportunities": int(len(month_part)),
                    "accepted_opportunities": int(month_part.accepted.sum()),
                    "skipped_opportunities": int((~month_part.accepted).sum()),
                    "accepted_mean_net_pct": float(month_part.loc[month_part.accepted, "net_pct"].mean()) if month_part.accepted.any() else np.nan,
                    "baseline_net_pct_sum": float(month_part.net_pct.sum()),
                    "zero_skip_net_pct_sum": float(month_part.zero_skip_return.sum()),
                    "incremental_net_pct_sum_vs_baseline": float(month_part.zero_skip_return.sum() - month_part.net_pct.sum()),
                    "mean_per_original_opportunity_net_pct_skips_zero": float(month_part.zero_skip_return.mean()),
                })
        row["worst_three_period_accepted_mean_lift_pp"] = float(np.nanmin(improvement_values))
        row["frequency_gate_pass"] = all(
            int(row[f"{period}_n_accepted"]) >= minimum for period, minimum in MINIMUMS.items()
        )
        row["frequency_gate_reason"] = "pass" if row["frequency_gate_pass"] else ";".join(
            f"{period}:{row[f'{period}_n_accepted']}<{minimum}"
            for period, minimum in MINIMUMS.items() if int(row[f"{period}_n_accepted"]) < minimum
        )

        funded_part = y2026.copy()
        funded_selected = funded_part[funded_part[f"accept_{rule}"]]
        funded_zero = funded_part.funded_net_pct.where(funded_part[f"accept_{rule}"], 0.0)
        row.update({
            "2026_original_n": int(len(funded_part)),
            "2026_accepted_n": int(funded_part[f"accept_{rule}"].sum()),
            "2026_funding_active_months": int(funded_selected.month.nunique()),
            "2026_baseline_mean_funded_pct": float(funded_part.funded_net_pct.mean()),
            "2026_accepted_mean_funded_pct": float(funded_selected.funded_net_pct.mean()) if len(funded_selected) else np.nan,
            "2026_accepted_mean_funded_lift_vs_baseline_per_fill_pp": float(funded_selected.funded_net_pct.mean() - funded_part.funded_net_pct.mean()) if len(funded_selected) else np.nan,
            "2026_mean_funded_per_original_opportunity_skips_zero": float(funded_zero.mean()),
            "2026_incremental_mean_funded_per_original_opportunity_pp": float(funded_zero.mean() - funded_part.funded_net_pct.mean()),
            "2026_total_funding_cost_points_per_BTC_baseline": float(funded_part.funding_points_cost.sum()),
            "2026_mean_funding_cost_points_per_BTC_baseline": float(funded_part.funding_points_cost.mean()),
        })
        row.update({f"2026_funded_accepted_{k}": v for k, v in _mean_win_metrics(funded_selected.funded_net_pct).items()})
        row.update({f"2026_funded_accepted_{k}": v for k, v in _equity_metrics(funded_selected.funded_net_pct).items()})
        row.update({f"2026_funded_zero_skip_{k}": v for k, v in _equity_metrics(funded_zero).items()})
        summary_rows.append(row)

    summary = pd.DataFrame(summary_rows)
    summary = summary.sort_values(
        ["frequency_gate_pass", "worst_three_period_accepted_mean_lift_pp", "rule"],
        ascending=[False, False, True], kind="stable",
    ).reset_index(drop=True)
    summary["priority_rank_within_frequency_eligible"] = np.nan
    eligible_idx = summary.index[summary.frequency_gate_pass]
    summary.loc[eligible_idx, "priority_rank_within_frequency_eligible"] = np.arange(1, len(eligible_idx) + 1)
    control_summary = control_summary.rename(columns={"mean_actual_funded_pct": "2026_control_mean_actual_funded_pct"})
    summary = summary.merge(control_summary, on="rule", how="left", validate="one_to_one")

    monthly = pd.DataFrame(monthly_rows)
    if not control_data["coverage"].empty:
        control_coverage = control_data["coverage"].copy()
        monthly = monthly.merge(
            control_coverage.groupby(["rule", "month"], as_index=False).agg(
                accepted_events_with_pool=("event_id", "nunique"),
                mean_unfiltered_control_pool_n=("unfiltered_match_pool_n", "mean"),
                mean_trend_filtered_control_pool_n=("trend_filtered_match_pool_n", "mean"),
                control_draws=("draw_count", "sum"),
            ), on=["rule", "month"], how="left", validate="many_to_one",
        )
    if not control_events.empty:
        funded_month = control_events.groupby(["rule", "paired_month"], as_index=False).agg(
            funded_2026_actual_mean=("funded_net_pct", "mean"),
            funded_2026_random_mean=("random_funded_mean_pct", "mean"),
            funded_2026_paired_edge_mean=("funded_edge_pp", "mean"),
            funded_2026_control_matched_events=("funded_edge_pp", "count"),
        ).rename(columns={"paired_month": "month"})
        monthly = monthly.merge(funded_month, on=["rule", "month"], how="left", validate="many_to_one")
    control_summary = control_summary.merge(
        control_data["coverage"].groupby("rule", as_index=False).agg(
            accepted_alerts_with_candidate_pool=("event_id", "nunique"),
            total_trend_eligible_pool_entries=("trend_filtered_match_pool_n", "sum"),
            total_unfiltered_pool_entries=("unfiltered_match_pool_n", "sum"),
            total_random_draws=("draw_count", "sum"),
        ) if not control_data["coverage"].empty else pd.DataFrame({"rule": RULES}),
        on="rule", how="left", validate="one_to_one",
    )

    y2026_by_rule = []
    for rule in RULES:
        part = y2026.copy()
        part["rule"] = rule
        part["accepted"] = part[f"accept_{rule}"]
        part["per_original_opportunity_funded_pct_skips_zero"] = part.funded_net_pct.where(part.accepted, 0.0)
        y2026_by_rule.append(part)
    y2026_rules = pd.concat(y2026_by_rule, ignore_index=True)
    september_file = OUTPUT / "sep16_17_bjt_opportunities.csv"

    OUTPUT.mkdir(parents=True, exist_ok=True)
    paths = {
        "opportunities": OUTPUT / "opportunities.csv",
        "rule_summary": OUTPUT / "rule_summary.csv",
        "monthly_summary": OUTPUT / "monthly_summary.csv",
        "funded_2026_opportunities": OUTPUT / "funded_2026_opportunities.csv",
        "control_coverage_2026": OUTPUT / "control_coverage_2026.csv",
        "control_events_2026": OUTPUT / "control_events_2026.csv",
        "control_draws_2026": OUTPUT / "control_draws_2026.parquet",
        "control_summary_2026": OUTPUT / "control_summary_2026.csv",
        "sep16_17_bjt_opportunities": september_file,
        "price_receipt": OUTPUT / "price_receipt.json",
        "funding_receipt": OUTPUT / "funding_receipt.json",
        "run_manifest": OUTPUT / "run_manifest.json",
    }
    opportunity_rows.to_csv(paths["opportunities"], index=False)
    summary.to_csv(paths["rule_summary"], index=False)
    monthly.to_csv(paths["monthly_summary"], index=False)
    y2026_rules.to_csv(paths["funded_2026_opportunities"], index=False)
    control_data["coverage"].to_csv(paths["control_coverage_2026"], index=False)
    control_events.to_csv(paths["control_events_2026"], index=False)
    control_data["draws"].to_parquet(paths["control_draws_2026"], index=False)
    control_summary.to_csv(paths["control_summary_2026"], index=False)
    september.to_csv(paths["sep16_17_bjt_opportunities"], index=False)
    paths["price_receipt"].write_text(json.dumps(price_receipt, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    paths["funding_receipt"].write_text(json.dumps(funding_receipt, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    manifest = {
        "experiment_id": "exp-monday-breadth-extension-20261108-v1",
        "builder": str(Path(__file__).relative_to(ROOT)),
        "git_revision": _git_revision(),
        "opportunity_input": str(OPPORTUNITIES.relative_to(ROOT)),
        "original_fill_source": "experiments/active/exp-monday-breadth-frequency-20261108-v1/fixed/locked_events.parquet (cached VectorBT 144h fixed-time outcomes; no baseline replay)",
        "source_candidate_id": "freq36_BTC",
        "expected_and_observed_original_alerts": int(len(events)),
        "baseline_period_counts": {k: int(v) for k, v in events.period.value_counts().items()},
        "trigger_clock": "The 246 locked alert times remain fixed. No cooldown reset, replacement entry, or additional fill is created after filtering.",
        "entry_exit": "Entry at the locked 5m decision open; fixed 144h exit at the corresponding 5m open from the original locked VectorBT result.",
        "trend_asof": "For each 5m entry time, use the most recent complete UTC 4h or 1d candle whose close time is <= entry time. Higher-timeframe candles require all 5m OHLC bars; EMAs require 20/50 consecutive complete candles and use only those closed candles.",
        "rules": {
            "4h_ema20": "last completed 4h close > EMA20",
            "4h_ema50": "last completed 4h close > EMA50",
            "1d_ema20": "last completed 1d close > EMA20",
            "1d_ema50": "last completed 1d close > EMA50",
            "4h_reclaim_ema20": "last completed 4h close > EMA20 and immediately prior completed 4h close <= its EMA20",
        },
        "frequency_gate": MINIMUMS,
        "ranking": "Among frequency-eligible gates, sort by the smallest accepted-mean lift versus the same-period original opportunities across 2020-2022, 2023-2024, and 2025. 2026 does not select the gates.",
        "costs": {
            "entry_fee": TAKER,
            "exit_fee": TAKER,
            "fee_type": "taker / market on each fill",
            "slippage": 0,
            "fee_validation": "Reconstructed from local entry and 144h exit opens, then compared with the immutable locked VectorBT net_pct; maximum difference must be <= 1e-6 percentage points.",
        },
        "2026_funding": "Realized BTC USD-M 2026 funding history and mark prices from the existing normalized local cache; funding cost is applied to the same fixed 144h entry/exit interval for actuals and random controls.",
        "controls": control_receipt,
        "selection_inference": "Two-sided UTC-month block sign-flip p-values with Holm correction across the five pre-specified rules, separately for 2020-2025 development-period price-only incremental return, all-history price-only incremental return, 2026 funded incremental return, and 2026 funded matched-control edge.",
        "primary_output_fields": [
            "accept_<rule>", "net_pct", "gross_price_pct", "total_fee_pct", "period",
            "per_original_opportunity_<rule>_net_pct", "funded_net_pct", "funding_points_cost",
            "trend_filtered_match_pool_n", "random_net_pct", "random_funded_net_pct",
            "worst_three_period_accepted_mean_lift_pp", "frequency_gate_pass",
            "price_month_p_holm_five_rules", "2026_funded_month_p_holm_five_rules",
        ],
        "production_eligible": False,
    }
    paths["run_manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(summary[["rule", "frequency_gate_pass", "worst_three_period_accepted_mean_lift_pp",
                   "2020_2022_n_accepted", "2023_2024_n_accepted", "2025_n_accepted",
                   "2026_accepted_n", "2026_incremental_mean_funded_per_original_opportunity_pp",
                   "2026_funded_month_p_holm_five_rules"]].to_string(index=False), flush=True)
    print(control_summary[["rule", "accepted_2026_n", "matched_events_n",
                           "mean_paired_edge_funded_pp", "p_holm_five_rules"]].to_string(index=False), flush=True)
    return paths


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run",), nargs="?", default="run")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
