"""Summarize the fixed V1+ default replay without changing its trade decisions.

Only authenticated replay ledgers and existing marked-account output are used.
Event periods follow entry time; account periods follow calendar close NAV.
No profit, MFE or future coin rank is an input to signal selection. Exact-entry
pairs distinguish retained opportunities from changed subsequent admissions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v7_v1_report import bools

BASELINE = "v1_display_both"
PLUS = "v1_plus_default_both"
START = pd.Timestamp("2024-09-10T00:00:00Z")
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
END = pd.Timestamp("2026-09-10T00:00:00Z")


def metrics(trades: pd.DataFrame) -> dict:
    """Describe realized unit-position events, excluding boundary estimates."""
    done = trades.loc[~bools(trades.censored)]
    net = pd.to_numeric(done.net_return, errors="raise")
    r = pd.to_numeric(done.net_r, errors="raise")
    if not np.isfinite(net).all() or not np.isfinite(r).all():
        raise ValueError("realized trades require finite returns")
    win, loss = net.gt(0), net.lt(0)
    positive, negative = float(net[win].sum()), float(-net[loss].sum())
    duration = (pd.to_datetime(done.exit_time, utc=True) - pd.to_datetime(done.entry_time, utc=True)).dt.total_seconds() / 3600
    if duration.lt(0).any():
        raise ValueError("negative holding time")
    return dict(entries=len(trades), realized=len(done), censored=len(trades)-len(done),
                wins=int(win.sum()), win_rate=float(win.mean()),
                event_pf=positive/negative if negative else np.nan,
                positive_return_sum=positive, negative_return_sum=negative,
                net_r_sum=float(r.sum()), mean_net_r=float(r.mean()),
                median_net_r=float(r.median()), mean_net_return=float(net.mean()),
                mean_winner_r=float(r[win].mean()), mean_loser_r=float(r[loss].mean()),
                realized_ge5r=int(r.ge(5).sum()), realized_ge10r=int(r.ge(10).sum()),
                mfe_ge10r=int(pd.to_numeric(done.mfe_r).ge(10).sum()),
                median_holding_hours=float(duration.median()))


def event_tables(trades: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """All-period and chronological year views; side attribution is not a new account."""
    t = trades.copy()
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
    out = []
    for period, a, b in (("full", START, END), ("development", START, SPLIT), ("validation", SPLIT, END)):
        window = t.loc[t.entry_time.ge(a) & t.entry_time.lt(b)]
        for group, part in window.groupby(keys, dropna=False, sort=True):
            group = group if isinstance(group, tuple) else (group,)
            for side in ("both", "long", "short"):
                selected = part if side == "both" else part.loc[part.side.eq(1 if side == "long" else -1)]
                out.append({**dict(zip(keys, group)), "period": period, "side_group": side, **metrics(selected)})
    return pd.DataFrame(out)


def exact_pairs(trades: pd.DataFrame) -> pd.DataFrame:
    """Outer pair by source stream, actual signal bar and direction, never return."""
    keys = ["stream_key", "signal_bar_open", "side"]
    fields = keys + ["entry_time", "entry_price", "net_r", "net_return", "mfe_r", "censored", "exit_reason"]
    t = trades.copy()
    t["signal_bar_open"] = pd.to_datetime(t.signal_bar_open, utc=True)
    t["censored"] = bools(t.censored)
    a = t.loc[t.cohort.eq(BASELINE), fields]
    b = t.loc[t.cohort.eq(PLUS), fields]
    if a.duplicated(keys).any() or b.duplicated(keys).any():
        raise ValueError("ambiguous exact signal pair")
    pair = a.merge(b, on=keys, how="outer", suffixes=("_base", "_plus"), indicator=True, validate="one_to_one")
    pair["both_realized"] = pair._merge.eq("both") & pair.censored_base.eq(False) & pair.censored_plus.eq(False)
    pair["realized_net_r_change"] = (pair.net_r_plus-pair.net_r_base).where(pair.both_realized)
    pair["baseline_realized_ge10r"] = pair.censored_base.eq(False) & pair.net_r_base.ge(10)
    pair["same_entry_retained_ge10r"] = pair.baseline_realized_ge10r & pair.censored_plus.eq(False) & pair.net_r_plus.ge(10)
    return pair


def account_comparison(accounts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pair independent accounts; an average return is never a pooled portfolio."""
    keys = ["stream_key", "period"]
    values = ["net_return", "max_close_drawdown", "valid"]
    a = accounts.loc[accounts.cohort.eq(BASELINE), keys + values]
    b = accounts.loc[accounts.cohort.eq(PLUS), keys + values]
    paired = a.merge(b, on=keys, suffixes=("_base", "_plus"), validate="one_to_one", how="outer", indicator=True)
    paired["comparable"] = paired._merge.eq("both") & paired.valid_base.eq(True) & paired.valid_plus.eq(True)
    paired["return_change"] = (paired.net_return_plus-paired.net_return_base).where(paired.comparable)
    paired["drawdown_change"] = (paired.max_close_drawdown_plus-paired.max_close_drawdown_base).where(paired.comparable)
    ids = accounts[["stream_key", "venue", "timeframe_min"]].drop_duplicates()
    if ids.stream_key.duplicated().any():
        raise ValueError("stream identity changed")
    paired = paired.merge(ids, on="stream_key", how="left", validate="many_to_one")
    out = []
    for key, group in paired.groupby(["period", "timeframe_min"], sort=True):
        ok = group.loc[group.comparable]
        out.append(dict(period=key[0], timeframe_min=key[1], accounts=len(group), comparable=len(ok),
                        mean_base_return=float(ok.net_return_base.mean()), mean_plus_return=float(ok.net_return_plus.mean()),
                        median_base_return=float(ok.net_return_base.median()), median_plus_return=float(ok.net_return_plus.median()),
                        mean_base_drawdown=float(ok.max_close_drawdown_base.mean()), mean_plus_drawdown=float(ok.max_close_drawdown_plus.mean()),
                        profitable_base=int(ok.net_return_base.gt(0).sum()), profitable_plus=int(ok.net_return_plus.gt(0).sum()),
                        improved_accounts=int(ok.return_change.gt(0).sum()), worsened_accounts=int(ok.return_change.lt(0).sum())))
    return paired, pd.DataFrame(out)
