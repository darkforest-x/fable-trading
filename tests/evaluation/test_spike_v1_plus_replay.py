"""Regression checks for V1+ default reference/execution clocks."""
from __future__ import annotations

import pandas as pd

import yoyo.evaluation.spike_v1_plus_replay as replay
from yoyo.evaluation.spike_v1_plus_replay import _path_plus, _simulate_next_open_series, simulate_next_open
from yoyo.evaluation.spike_v1_plus_study import SUMMARY_COLUMNS, _summarize


def _bars(rows):
    index = pd.date_range("2025-01-01", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, index=index)


def _refs(index, *, side=1, signal=True, reverse=False, active=90.0):
    return pd.DataFrame({"signal": [signal] + [False] * (len(index)-1),
                         "signal_reason": ["accepted"] + [""] * (len(index)-1),
                         "raw_signal": [signal] + [False] * (len(index)-1),
                         "side": [side] + [0] * (len(index)-1),
                         "signal_i": [0] + [float("nan")] * (len(index)-1),
                         "signal_close": [100.0] + [float("nan")] * (len(index)-1),
                         "reference_initial_stop": [90.0] + [float("nan")] * (len(index)-1),
                         "reference_risk": [10.0] + [float("nan")] * (len(index)-1),
                         "reference_exit": [False, reverse] + [False] * max(0, len(index)-2),
                         "reference_exit_reason": ["", "opposite_reference" if reverse else ""] + [""] * max(0, len(index)-2),
                         "active_reference_protection": [float("nan"), active] + [active] * max(0, len(index)-2),
                         "reference_protection_after_close": [active] * len(index),
                         "trend_side": [side] * len(index)}, index=index)


def test_default_stages_and_master_off_differ_only_after_close():
    # Close at +1R latches stage one and makes -0.5R active only next bar.
    got = _path_plus(1, 100, 10, 90, 0, False, False, False, 100, 111, 101, 110, 2, 1, True)
    assert got[0] and got[1] == 95 and got[6] and not got[7]
    # Master-off retains the old stop even though the close reaches +1R.
    base = _path_plus(1, 100, 10, 90, 0, False, False, False, 100, 111, 101, 110, 2, 1, False)
    assert base[1] == 90 and not base[6]


def test_stop_is_checked_before_same_bar_high_and_gap_is_adverse():
    stopped = _path_plus(1, 100, 10, 95, 3, True, True, False, 94, 130, 90, 120, 2, 1, True)
    assert not stopped[0] and stopped[4] == 94 and stopped[2] == 3
    bars = _bars([{"open":100,"high":101,"low":99,"close":100}, {"open":100,"high":110,"low":99,"close":105}, {"open":89,"high":110,"low":88,"close":105}])
    trades, fills = simulate_next_open(bars, _refs(bars.index), tick=1)
    assert len(trades) == 1 and trades.iloc[0].exit_price == 89
    assert fills.iloc[-1].execution_phase == "open"


def test_short_stop_and_opposite_reference_next_open_are_auditable():
    # The extreme high occurs after the known next-open reverse exit. It must
    # not be allowed to rewrite that market exit as a stop.
    bars = _bars([{"open":100,"high":101,"low":99,"close":100}, {"open":100,"high":101,"low":98,"close":99}, {"open":97,"high":120,"low":96,"close":97}])
    # A short uses 90 as operative protection here only to exercise side-aware
    # handling; reverse exits close it at the next observed open.
    refs = _refs(bars.index, side=-1, reverse=True, active=110)
    refs.loc[refs.index[0], "reference_initial_stop"] = 110.0
    trades, fills = simulate_next_open(bars, refs, tick=1)
    assert trades.iloc[0].side == -1
    assert trades.iloc[0].exit_reason == "opposite_reference_next_open"
    assert trades.iloc[0].exit_price == 97
    assert set(fills.kind) == {"entry", "exit"}


def test_entry_can_stop_on_its_fill_bar():
    bars = _bars([{"open":100,"high":101,"low":99,"close":100}, {"open":100,"high":105,"low":89,"close":95}])
    trades, fills = simulate_next_open(bars, _refs(bars.index), tick=1)
    assert len(trades) == 1
    assert trades.iloc[0].entry_time == bars.index[1]
    assert trades.iloc[0].exit_reason == "protective_stop"
    assert trades.iloc[0].exit_price == 90
    assert fills.iloc[-1].execution_phase == "intrabar"


def test_trade_ids_are_scoped_to_the_caller_stream_and_arm():
    bars = _bars([{"open":100,"high":101,"low":99,"close":100}, {"open":100,"high":101,"low":99,"close":100}])
    trades, fills = simulate_next_open(bars, _refs(bars.index), tick=1, trade_id_prefix="okx_eth_60:v1_plus_default_both")
    assert trades.iloc[0].trade_id.startswith("okx_eth_60:v1_plus_default_both:")
    assert fills.iloc[0].trade_id == trades.iloc[0].trade_id


def test_ghost_reference_reverse_cannot_exit_a_later_actual_entry():
    # Evaluation starts flat even though the full Pine reference had an older
    # opposite position. The queued exit is consumed before the new fill.
    bars = _bars([{"open":100,"high":101,"low":99,"close":100},
                  {"open":100,"high":104,"low":99,"close":103},
                  {"open":103,"high":105,"low":102,"close":104}])
    refs = _refs(bars.index)
    refs.loc[refs.index[0], ["reference_exit", "reference_exit_reason"]] = [True, "opposite_reference"]
    trades, fills = simulate_next_open(bars, refs, tick=1)
    assert len(trades) == 1
    assert trades.iloc[0].entry_time == bars.index[1]
    assert trades.iloc[0].exit_reason == "boundary_mark"
    assert set(fills.kind) == {"entry", "censor"}


def test_zero_trade_summary_is_schema_bearing_and_counted():
    rows = _summarize(pd.DataFrame(), identity={"stream_key": "s", "venue": "okx", "symbol": "X",
                       "asset": "X", "timeframe_min": 60, "segment": "full"}, variant="v1_display_both")
    frame = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    assert list(frame.columns) == list(SUMMARY_COLUMNS)
    assert len(frame) == 1 and frame.iloc[0].trades == 0


def test_array_execution_matches_series_reference_on_reverse_and_stop_path():
    bars = _bars([{"open":100,"high":101,"low":99,"close":100},
                  {"open":100,"high":106,"low":99,"close":105},
                  {"open":104,"high":120,"low":103,"close":105},
                  {"open":105,"high":106,"low":89,"close":90}])
    refs = _refs(bars.index, reverse=True)
    refs.loc[refs.index[2], ["signal", "signal_reason", "side", "signal_i", "signal_close",
                             "reference_initial_stop", "reference_risk"]] = [True, "accepted", -1, 2, 105., 115., 10.]
    refs.loc[refs.index[2], ["reference_exit", "reference_exit_reason"]] = [True, "opposite_reference"]
    old = _simulate_next_open_series(bars, refs, tick=1, trade_id_prefix="parity")
    new = simulate_next_open(bars, refs, tick=1, trade_id_prefix="parity")
    pd.testing.assert_frame_equal(old[0], new[0])
    pd.testing.assert_frame_equal(old[1], new[1])


def test_prefix_execution_is_identical_after_unaffected_prefix():
    rows=[{"open":100,"high":101,"low":99,"close":100}, {"open":101,"high":103,"low":100,"close":102}, {"open":104,"high":105,"low":103,"close":104}, {"open":106,"high":107,"low":105,"close":106}]
    bars=_bars(rows); refs=_refs(bars.index, active=90)
    whole,_=simulate_next_open(bars,refs,tick=1)
    suffix,_=simulate_next_open(bars.iloc[:],refs.iloc[:],tick=1)
    assert whole[["entry_price","exit_price","net_r"]].equals(suffix[["entry_price","exit_price","net_r"]])


def test_joint_overheat_consumes_raw_event_but_master_off_keeps_baseline(monkeypatch):
    index=pd.date_range("2025-01-01",periods=15,freq="h",tz="UTC")
    frame=pd.DataFrame({"open":95.,"high":100.,"low":90.,"close":95.,"md":0.,"sb":0.,"middle":0.,"atr":1.,"pastWidth":0.,"pastCrosses":20.,"ropeHigh":100.,"ropeLow":90.,"recentLow":90.,"recentHigh":100.,"rv":1.,"expansion":1.,"ready":True},index=index)
    frame.loc[index[13],["open","high","low","close","md","sb","middle","rv","expansion"]]=[100,120,99,119,1,0,1,60,11]
    monkeypatch.setattr(replay,"features",lambda _:frame)
    treated=replay.replay_references(pd.DataFrame(index=index),1.,enable_plus=True)
    baseline=replay.replay_references(pd.DataFrame(index=index),1.,enable_plus=False)
    assert treated.iloc[13].raw_signal and not treated.iloc[13].signal
    assert treated.iloc[13].signal_reason == "overheat_rejected"
    assert baseline.iloc[13].signal and baseline.iloc[13].side == 1


def test_stop_bar_allows_only_the_opposite_reference_side(monkeypatch):
    """This is Pine's ``not endedThisBar or side != exitSide`` exception."""
    index = pd.date_range("2025-01-01", periods=28, freq="h", tz="UTC")
    frame = pd.DataFrame({"open":95., "high":100., "low":90., "close":95., "md":0., "sb":0.,
                          "middle":0., "atr":1., "pastWidth":0., "pastCrosses":20.,
                          "ropeHigh":100., "ropeLow":90., "recentLow":90., "recentHigh":100.,
                          "rv":1., "expansion":1., "ready":True}, index=index)
    # Enter after the first quiet episode, then form a separate quiet episode
    # before the stop/downward release bar.
    frame.loc[index[13], ["open", "high", "low", "close", "md", "middle"]] = [100, 120, 99, 119, 1, 1]
    frame.loc[index[26], ["open", "high", "low", "close", "md", "middle"]] = [110, 111, 80, 85, -2, -2]
    monkeypatch.setattr(replay, "features", lambda _: frame)
    monkeypatch.setattr(replay, "price_burst", lambda side, o, h, l, c, *_: side == 1 and c == 119)
    monkeypatch.setattr(replay, "burst", lambda side, *_: side == -1)
    def stop_only_on_down_release(*args):
        # args[8] is the current open in _path_plus's explicit signature.
        if args[8] == 110:
            return (False, 90., 0., -1., 90., False, False, False)
        return (True, args[3], args[4], 0., float("nan"), args[5], args[6], args[7])
    monkeypatch.setattr(replay, "_path_plus", stop_only_on_down_release)
    got = replay.replay_references(pd.DataFrame(index=index), 1., enable_plus=True)
    assert got.iloc[26].reference_exit
    assert got.iloc[26].reference_exit_side == 1
    assert got.iloc[26].signal and got.iloc[26].side == -1
