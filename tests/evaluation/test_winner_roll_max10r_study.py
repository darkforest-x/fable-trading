"""Focused runner checks; market caches are intentionally not replayed here."""
from types import SimpleNamespace

import pandas as pd
import pytest

from yoyo.evaluation import winner_roll_max10r_study as study


def _row(**overrides):
    base = dict(arm="v9", side=1, censored=False, net_r=10.1, stream_key="binance_60m_a",
                event_key="event", entry_time="2025-01-01T01:00:00Z", exit_time="2025-01-01T04:00:00Z",
                entry_price=101.0, entry_i=5154, exit_i=5157, venue="binance", symbol="AAAUSDT",
                asset="AAA", timeframe_min=60)
    base.update(overrides)
    return base


def _context(*, gap=False):
    index = pd.date_range("2025-01-01T00:00:00Z", periods=8, freq="1h")
    bars = pd.DataFrame({"open": [100., 101., 102., 103., 104., 105., 106., 107.],
                         "high": [101., 102., 103., 104., 105., 106., 107., 108.],
                         "low": [99., 100., 101., 102., 103., 104., 105., 106.],
                         "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5]}, index=index)
    data_gap = pd.Series(False, index=index)
    if gap:
        data_gap.iloc[2] = True
    return SimpleNamespace(key="binance_60m_a", minutes=60,
                           identity={"venue": "binance", "symbol": "AAAUSDT", "asset": "AAA", "timeframe_min": 60},
                           cache={"bars": bars, "data_gap": data_gap, "tick": .01})


def test_selection_is_strict_and_does_not_filter_on_status_or_win_label():
    rows = [_row(event_key="keep", status="unknown", win=False), _row(event_key="ten", net_r=10),
            _row(event_key="wrong-arm", arm="v8"), _row(event_key="short", side=-1),
            _row(event_key="censored", censored=True), _row(event_key="text", censored="false", net_r=11)]
    selected = study.select_winners(pd.DataFrame(rows))
    assert selected.event_key.tolist() == ["keep", "text"]


def test_clock_join_checks_source_ordinal_distance_but_excludes_original_exit_bar():
    context = _context()
    original = context.cache["bars"].copy(deep=True)
    row = _row(entry_i=5154, exit_i=5157)
    path, cache_entry, cache_exit = study.frame_for_trade(context, row)
    assert (cache_entry, cache_exit) == (1, 4)  # cache ordinal differs from old source ordinal
    assert path.index.tolist() == context.cache["bars"].index[1:4].tolist()
    pd.testing.assert_frame_equal(context.cache["bars"], original)


def test_gap_or_ordinal_disagreement_is_an_explicit_failure():
    with pytest.raises(ValueError, match="data gap"):
        study.frame_for_trade(_context(gap=True), _row())
    with pytest.raises(ValueError, match="ordinal distance"):
        study.frame_for_trade(_context(), _row(exit_i=5160))


def test_per_asset_keeps_none_and_one_from_the_same_best_two_arm_trade():
    rows = []
    for event, asset, two in (("a-low", "AAA", 120.), ("a-best", "AAA", 140.), ("b-only", "BBB", 130.)):
        for arm, balance in (("none", 110.), ("one", 115.), ("two", two)):
            rows.append({"event_key": event, "asset": asset, "arm": arm,
                         "status": "conditional_optimal", "final_balance": balance})
    selected = pd.DataFrame([{"event_key": event, "asset": asset} for event, asset, _ in
                             (("a-low", "AAA", 120.), ("a-best", "AAA", 140.), ("b-only", "BBB", 130.))])
    result = study._per_asset(pd.DataFrame(rows), selected, pd.DataFrame())
    assert result.loc[result.asset.eq("AAA"), "event_key"].unique().tolist() == ["a-best"]
    assert result.groupby("asset").arm.apply(set).to_dict() == {"AAA": {"none", "one", "two"}, "BBB": {"none", "one", "two"}}


def test_per_asset_marks_any_asset_with_a_failed_selected_trade_incomplete():
    selected = pd.DataFrame([{"event_key": "ok", "asset": "AAA"}, {"event_key": "bad", "asset": "AAA"}])
    trades = pd.DataFrame([{ "event_key": "ok", "asset": "AAA", "arm": arm,
                            "status": "conditional_optimal", "final_balance": 120.} for arm in ("none", "one", "two")])
    result = study._per_asset(trades, selected, pd.DataFrame([{ "event_key": "bad"}]))
    assert result.asset_complete.eq(False).all()
    assert result.status.tolist() == ["incomplete_failure"]


def test_per_asset_all_failures_produces_only_explicit_incomplete_rows():
    selected = pd.DataFrame([{"event_key": "bad-a", "asset": "AAA"}, {"event_key": "bad-b", "asset": "BBB"}])
    failures = pd.DataFrame([{"event_key": "bad-a"}, {"event_key": "bad-b"}])
    result = study._per_asset(pd.DataFrame(), selected, failures)
    assert result[["asset", "selected_trades", "failed_trades", "status"]].to_dict("records") == [
        {"asset": "AAA", "selected_trades": 1, "failed_trades": 1, "status": "incomplete_failure"},
        {"asset": "BBB", "selected_trades": 1, "failed_trades": 1, "status": "incomplete_failure"},
    ]
