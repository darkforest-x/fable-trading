"""Revalue the frozen 2026 spacing fills on the same five-minute clock as the booklet.

Run from the repository root with PYTHONPATH=. and vectorbt-python after building events.csv.
Input prices are the recorded candle closes and fee-paid exit opens. Only post-entry realized
funding inside each holding interval is charged, with adverse ordering on its two boundaries.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.data.altseason_funding import FundingClient, normalize
from yoyo.evaluation import monday_breadth_extension_risk as risk
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_payoffs as prices
from yoyo.evaluation.monday_breadth_extension_alt import _interval_history_complete
from yoyo.evaluation.monday_breadth_extension_funding_check import interval_charge
from yoyo.evaluation.monday_breadth_extension_partial_marks import accumulated_cost
from yoyo.evaluation.monday_breadth_opt_exit_study import member_bridge

OUT = Path(__file__).resolve().parent


def btc_curves(grid):
    btc = pd.read_parquet(risk.EXP / "risk/integrated_btc_events.parquet").query("epoch == 3")
    quotes, _ = prices.load_full_ohlc("BTCUSDT", btc.decision.min(), btc.exit_time.max(), repair_missing=False)
    fills = pd.read_parquet(funding.FUNDED_CACHE, filters=[("policy_id", "=", 4)]).set_index("entry_time")
    fee = risk.btc_marks(btc, quotes, fills)
    history = funding.rates("BTCUSDT", history=True, local_only=True)
    settlements = history.realized_rate * history.mark_price.fillna(quotes.open.reindex(history.index))
    funded = {}
    for r in btc.itertuples():
        times = pd.date_range(r.decision, r.exit_time, freq="5min")
        cost = accumulated_cost(settlements, r.decision, r.exit_time, times)
        if r.exit_legs == 2:
            cost = (cost + accumulated_cost(settlements, r.decision, r.first_exit_time, times)) / 2
        np.testing.assert_allclose(cost[-1], r.funding_points, atol=1e-6)
        funded[r.decision] = fee[r.decision] - cost / r.entry_open
    return [risk.sleeve(btc, paths, grid, 1, 0) for paths in (fee, funded)]


def direct_funding(client, symbol, start, end):
    """Fetch only missing exchange settlements, including three days of adjacent clocks."""
    lo = (start - pd.Timedelta(days=3)).value // 10**6
    hi = (end + pd.Timedelta(days=3)).value // 10**6
    payload, _ = client.get("/fapi/v1/fundingRate", {
        "symbol": symbol, "startTime": lo, "endTime": hi-1, "limit": 1000,
    })
    if len(payload) >= 1000:
        raise ValueError("Truncated funding history")
    rows = normalize("binance", symbol, payload, lo, hi)
    rows.index = pd.to_datetime(rows.funding_time, unit="ms", utc=True).dt.floor("5min")
    if rows.index.has_duplicates:
        raise ValueError(f"Duplicate funding clocks on {symbol}")
    return rows.sort_index()


def incremental_paths(new):
    client = FundingClient("binance", Path("data/research/monday_breadth_alt_spacing_20261108/funding"))
    members = new.merge(member_bridge(), on=["minutes", "known"])
    if not members.groupby("decision").size().equals(new.set_index("decision").member_count.astype(int).sort_index()):
        raise ValueError("New event membership disagrees with native fills")
    fees, funded, missing = {}, {}, {}
    for row, group in members.groupby("decision"):
        times = pd.date_range(row, periods=73, freq="5min")
        fee_paths, funded_paths, uncovered = [], [], []
        for symbol in group.symbol:
            end = times[-1]
            quotes, _ = prices.load_full_ohlc(symbol, row, end, repair_missing=False)
            entry = quotes.open.at[row]
            path = np.r_[1., quotes.close.reindex(times[:-1]).to_numpy()/entry] - .0005
            path[-1] = quotes.open.at[end]/entry * (1-.0005) - .0005
            if not np.isfinite(path).all():
                raise ValueError(f"Incomplete price path for {symbol} at {row}")
            fee_paths.append(path)
            months = sorted({row.strftime("%Y-%m"), end.strftime("%Y-%m")})
            history = funding.rates(symbol, months=months, local_only=True)
            ok = _interval_history_complete(history, row, end)
            if not ok and not history.empty:
                try:
                    ok = interval_charge(SimpleNamespace(symbol=symbol, decision=row, selected_exit_time=end, entry_open_6h=entry)) is not None
                except FileNotFoundError:
                    ok = False
            if not ok or history.loc[row:end].price_proxy.any():
                history = direct_funding(client, symbol, row, end)
                ok = _interval_history_complete(history, row, end)
            if not ok:
                uncovered.append(symbol)
                continue
            charges = accumulated_cost(history.realized_rate*history.mark_price, row, end, times)
            funded_paths.append(path-charges/entry)
        fees[row] = np.mean(fee_paths, axis=0)
        np.testing.assert_allclose(fees[row][-1], 1+group.net_pct.iloc[0]/100, atol=5e-7)
        if uncovered:
            missing[row] = uncovered
        else:
            funded[row] = np.mean(funded_paths, axis=0)
    return fees, funded, missing


def run():
    events = pd.read_csv(OUT / "events.csv", parse_dates=["known", "decision"]).query("year == 2026").copy()
    selected = events.loc[events[["selected_24h", "selected_12h", "selected_6h"]].any(axis=1)].copy()
    selected["exit_time"] = selected.decision + pd.Timedelta(hours=6)
    selected["minutes"] = 5
    original = pd.read_parquet(risk.PARENT / "audit/fixed/all_rule_and_account_events.parquet").query(
        'epoch == 3 and candidate_id == "freq36_ALT"')
    assert set(original.decision) == set(selected[selected.selected_24h].decision)
    archived = pd.read_parquet(risk.EXP / "sleeves/equity_5m.parquet", columns=["ALT"])
    grid = archived.index
    assert selected.decision.min() >= grid[0] and selected.exit_time.max() <= grid[-1]
    btc_fee, btc_funded = btc_curves(grid)
    with np.load(risk.EXP / "risk/alt_6h_marks.npz") as cached:
        fee = dict(zip(pd.to_datetime(cached["timestamps_ns"], utc=True), cached["unit_fee_values"], strict=True))
    funded = risk.funded_relative(original, archived.ALT)
    new_fee, new_funded, missing = incremental_paths(selected[~selected.selected_24h])
    fee.update(new_fee)
    funded.update(new_funded)
    rows = []
    for hours in [24, 12, 6]:
        group = selected[selected[f"selected_{hours}h"]]
        nav = (btc_fee + risk.sleeve(group, fee, grid, 1, 0))/2
        stats = risk.stats(nav)
        row = dict(minimum_gap_hours=hours, events=len(group), fee_growth_pct=stats["growth_pct"],
                   fee_marked_dd_pct=stats["max_dd_pct"], funded_events=sum(t in funded for t in group.decision),
                   funding_missing=" ".join(f"{t.isoformat()}:{','.join(missing[t])}" for t in group.decision if t in missing))
        if row["funded_events"] == len(group):
            nav = (btc_funded + risk.sleeve(group, funded, grid, 1, 0))/2
            stats = risk.stats(nav)
            row.update(funded_growth_pct=stats["growth_pct"], funded_marked_dd_pct=stats["max_dd_pct"])
        rows.append(row)
    result = pd.DataFrame(rows).set_index("minimum_gap_hours")
    reference = pd.read_csv(risk.EXP / "risk/accounts.csv").query(
        'strategy == "below_plus_case" and scope == "2026_funded" and sleeve_deployment == 1 and extra_roundtrip_pp == 0').iloc[0]
    np.testing.assert_allclose(result.loc[24, ["funded_growth_pct", "funded_marked_dd_pct"]].astype(float),
                               [reference.growth_pct, reference.max_dd_pct], atol=1e-7)
    result.to_csv(OUT / "marks_2026.csv")


if __name__ == "__main__":
    run()
