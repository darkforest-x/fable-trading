"""Synthetic contracts for causal SPIKE market-breadth accounting."""
from __future__ import annotations

import gzip
import hashlib
import numpy as np
import pandas as pd
import pytest

import yoyo.evaluation.spike_market_breadth_study as study
from yoyo.evaluation.spike_market_breadth_study import (
    _base_deduplicated_trades, _load_bars, _prefix_rows, _variant_block_rows, attach_context, canonical_asset,
    causal_asset_features, complete_aggregate_30m, development_asof_clock, launch_density_from_events, summarize_slice,
)


def test_canonical_asset_removes_only_exchange_contract_multipliers():
    assert canonical_asset("1000BONK") == "BONK"
    assert canonical_asset("1000000MOG") == "MOG"
    assert canonical_asset("BTC") == "BTC"


def test_causal_features_do_not_change_when_future_bars_are_appended():
    index = pd.date_range("2024-01-01", periods=26, freq="30min", tz="UTC")
    close = np.linspace(100, 110, len(index))
    frame = pd.DataFrame({"open": close - .1, "high": close + 1, "low": close - 1,
                          "close": close, "volume": np.arange(1, len(index) + 1)}, index=index)
    first = causal_asset_features(frame)
    extended = pd.concat([frame, pd.DataFrame({"open": [1.], "high": [2.], "low": [.5], "close": [1.5], "volume": [999.]},
                                                index=[index[-1] + pd.Timedelta(minutes=30)])])
    second = causal_asset_features(extended).loc[index]
    pd.testing.assert_frame_equal(first, second, check_freq=False)
    assert first.fast.iloc[18] is np.nan or pd.isna(first.fast.iloc[18])
    assert bool(first.fast.iloc[19])


def test_gap_resets_all_rolling_windows():
    index = pd.date_range("2024-01-01", periods=45, freq="30min", tz="UTC").delete(25)
    close = np.linspace(100, 110, len(index))
    frame = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                          "close": close, "volume": np.arange(1, len(index) + 1)}, index=index)
    features = causal_asset_features(frame)
    first_after_gap = index[25]
    assert pd.isna(features.loc[first_after_gap, "fast"])
    assert pd.isna(features.loc[first_after_gap, "rv_tr_atr"])


def test_slice_summary_excludes_censored_from_realized_tail_and_reports_mae():
    rows = pd.DataFrame({"censored": [False, False, True], "net_r": [11., -1., 20.],
                         "mae_r_exit_bar_window_approx": [-.5, -2., -3.]})
    result = summarize_slice(rows, label="all", metric="all")
    assert result["candidates"] == 3 and result["closed"] == 2 and result["censored"] == 1
    assert result["realized_ge_10r"] == pytest.approx(.5)
    assert result["mae_r_exit_bar_window_approx_median"] == pytest.approx(-1.25)
    assert pd.isna(result["matched_delta_net_r"])


def test_prefix_reader_stops_before_future_payload_is_parsed(tmp_path):
    path = tmp_path / "stream.csv.gz"
    path.write_bytes(gzip.compress(
        b"time,outcome\n2025-09-09T23:30:00+00:00,1.0\n2025-09-10T00:00:00+00:00,FORBIDDEN_FUTURE\n"
    ))
    rows = list(_prefix_rows(path, cutoff_field="time", cutoff=pd.Timestamp("2025-09-10T00:00:00Z")))
    assert len(rows) == 1
    assert rows[0][1] == ["2025-09-09T23:30:00+00:00", "1.0"]


def test_prefix_reader_does_not_decode_poisoned_boundary_payload(tmp_path):
    path = tmp_path / "stream.csv.gz"
    path.write_bytes(gzip.compress(
        b"time,outcome\n2025-09-09T23:30:00+00:00,1.0\n2025-09-10T00:00:00+00:00,\xff\n"
    ))
    rows = list(_prefix_rows(path, cutoff_field="time", cutoff=pd.Timestamp("2025-09-10T00:00:00Z")))
    assert rows == [(["time", "outcome"], ["2025-09-09T23:30:00+00:00", "1.0"])]


def test_development_prefix_digest_ignores_boundary_and_future_rows(tmp_path):
    path = tmp_path / "bars.csv.gz"
    prefix = (
        b"time,open,high,low,close,volume\n"
        b"2025-09-09T23:30:00+00:00,1,2,0,1.5,10\n"
    )
    first_future = b"2025-09-10T00:00:00+00:00,2,3,1,2.5,11\n"
    second_future = b"2025-09-10T00:00:00+00:00,999,999,999,999,999\n"

    def load_digest(payload: bytes) -> tuple[pd.DataFrame, str]:
        path.write_bytes(gzip.compress(payload))
        digest = hashlib.sha256()
        return _load_bars(str(path), prefix_digest=digest), digest.hexdigest()

    first_bars, first_digest = load_digest(prefix + first_future)
    second_bars, second_digest = load_digest(prefix + second_future)
    pd.testing.assert_frame_equal(first_bars, second_bars)
    assert first_digest == second_digest == hashlib.sha256(prefix).hexdigest()


def test_variant_block_reader_keeps_later_development_block_without_decoding_future_payload(tmp_path):
    path = tmp_path / "signals.csv.gz"
    path.write_bytes(gzip.compress(
        b"signal_confirm_time,variant,payload\n"
        b"2025-09-09T23:30:00+00:00,A,kept-a\n"
        b"2025-09-10T00:00:00+00:00,A,\xff\n"
        b"2025-09-09T23:30:00+00:00,B,kept-b\n"
    ))
    rows = list(_variant_block_rows(
        path, monotonic_field="signal_confirm_time", bounded_fields=("signal_confirm_time",),
        cutoff=pd.Timestamp("2025-09-10T00:00:00Z"),
    ))
    assert [values for _, values in rows] == [
        ["2025-09-09T23:30:00+00:00", "A", "kept-a"],
        ["2025-09-09T23:30:00+00:00", "B", "kept-b"],
    ]


@pytest.mark.parametrize(
    "payload, expected",
    [
        (
            b"signal_confirm_time,variant,payload\n"
            b"2025-09-09T23:30:00+00:00,A,x\n"
            b"2025-09-09T23:30:00+00:00,B,x\n"
            b"2025-09-09T23:30:00+00:00,A,x\n",
            "variant block reappears",
        ),
        (
            b"signal_confirm_time,variant,payload\n"
            b"2025-09-09T23:30:00+00:00,A,x\n"
            b"2025-09-09T23:00:00+00:00,A,x\n",
            "non-monotonic signal_confirm_time within variant A",
        ),
    ],
)
def test_variant_block_reader_rejects_reappearance_and_intra_block_reversal(tmp_path, payload, expected):
    path = tmp_path / "signals.csv.gz"
    path.write_bytes(gzip.compress(payload))
    with pytest.raises(ValueError, match=expected):
        list(_variant_block_rows(
            path, monotonic_field="signal_confirm_time", bounded_fields=("signal_confirm_time",),
            cutoff=pd.Timestamp("2025-09-10T00:00:00Z"),
        ))


def test_variant_block_trade_reader_skips_cross_cutoff_outcome_poison_and_keeps_later_variant(tmp_path, monkeypatch):
    root = tmp_path / "results/replay_two_year_20260912_v3/streams/stream-a"
    root.mkdir(parents=True)
    header = [
        "signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price", "initial_stop",
        "initial_risk", "initial_risk_frac", "protection", "mfe_r", "trail_armed", "exit_i", "exit_time",
        "exit_price", "exit_reason", "gross_return", "net_return", "gross_r", "net_r", "censored",
        "exit_time_precision", "account_equity_before", "account_equity_after", "variant", "venue", "symbol",
        "asset", "timeframe_min", "segment", "source_sha256",
    ]

    def row(*, variant: str, entry: str, exit_time: str, mfe_r: bytes, net_r: bytes) -> bytes:
        values = [
            "1", entry, "2", entry, "1", "100", "98", "2", ".02", "none", mfe_r, "False", "3", exit_time,
            "101", "end", ".01", ".009", ".5", net_r, "False", "bar", "1000", "1009", variant,
            "binance", "BTCUSDT", "BTC", "60", "development", "source",
        ]
        return b",".join(value if isinstance(value, bytes) else value.encode() for value in values) + b"\n"

    (root / "trades.csv.gz").write_bytes(gzip.compress(
        ",".join(header).encode() + b"\n"
        + row(
            variant="v1_common_execution_long", entry="2025-09-09T23:00:00+00:00",
            exit_time="2025-09-10T00:00:00+00:00", mfe_r=b"\xff", net_r=b"\xff",
        )
        + row(
            variant="v7_bb_long", entry="2025-09-09T22:00:00+00:00",
            exit_time="2025-09-09T23:00:00+00:00", mfe_r=b"3", net_r=b"1.5",
        )
    ))
    monkeypatch.setattr(study, "COMPARE_EXP", tmp_path)
    catalog = pd.DataFrame([{"venue": "binance", "symbol": "BTCUSDT", "base_asset": "BTC"}])
    trades = _base_deduplicated_trades(catalog)
    assert trades[["variant", "net_r"]].to_dict("records") == [{"variant": "v7_bb_long", "net_r": 1.5}]


def test_preflight_asof_clock_matches_final_panel_contract():
    clock = development_asof_clock()
    assert clock.iloc[0] == study.DEVELOPMENT_START
    assert clock.iloc[-1] == study.DEVELOPMENT_END - pd.Timedelta(minutes=30)
    assert len(clock) == int((study.DEVELOPMENT_END - study.DEVELOPMENT_START) / pd.Timedelta(minutes=30))


def test_density_uses_strict_hour_and_includes_current_signal():
    clock = pd.Series(pd.to_datetime(["2024-01-01T01:00:00Z"]))
    events = [(pd.Timestamp("2024-01-01T00:00:00Z"), "OLD"),
              (pd.Timestamp("2024-01-01T00:30:00Z"), "A"),
              (pd.Timestamp("2024-01-01T01:00:00Z"), "A"),
              (pd.Timestamp("2024-01-01T01:00:00Z"), "B")]
    assert launch_density_from_events(events, clock).tolist() == [2]


def test_complete_bucket_drops_partial_60_and_retains_complete_240():
    index = pd.date_range("2024-01-01", periods=9, freq="30min", tz="UTC").delete(2)
    bars = pd.DataFrame({"open": 1., "high": 2., "low": .5, "close": 1.5, "volume": 1.}, index=index)
    hourly = complete_aggregate_30m(bars, 60)
    four_hour = complete_aggregate_30m(bars, 240)
    assert pd.Timestamp("2024-01-01T02:00:00Z") in hourly.index
    assert pd.Timestamp("2024-01-01T00:00:00Z") not in four_hour.index


def test_context_joins_to_signal_bar_close_next_open_not_bar_open():
    signal_open = pd.Timestamp("2024-01-01T00:00:00Z")
    trades = pd.DataFrame({"entry_time": [signal_open + pd.Timedelta(hours=1)], "censored": [False]})
    panel = pd.DataFrame({"bar_open": [signal_open], "asof": [signal_open + pd.Timedelta(hours=1)],
                          "joint_breadth": [.4]})
    assert attach_context(trades, panel).joint_breadth.tolist() == [.4]
