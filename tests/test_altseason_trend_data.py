"""Synthetic cutoff, source identity, aggregation and daily-context contracts."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import altseason_trend_data as data


def _bars(start="2025-12-31T00:00:00Z", count=96):
    index = pd.date_range(start, periods=count, freq="15min")
    return pd.DataFrame(dict(ts=index.as_unit("ns").asi8 // 1_000_000,
                             open=100., high=103., low=98., close=102., volume=10.,
                             open_time=index, confirm="1"))


def _write(tmp_path, frame, symbol="AAA"):
    path = tmp_path / f"{symbol}.csv"
    frame.to_csv(path, index=False)
    return dict(symbol=symbol, cohort="frozen_pool", status="complete",
                output_path=str(path), output_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def _manifest(tmp_path, records, **overrides):
    value = dict(schema_version=1, end_exclusive="2026-01-01T00:00:00Z",
                 builder_path="yoyo/data/altcoin_history.py", builder_sha256="a" * 64,
                 builder_commit="b" * 40, symbols=records)
    value.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value))
    return path


def test_membership_preserves_pool_and_history_builder_receipt(tmp_path):
    record = _write(tmp_path, _bars())
    illustration = {**record, "symbol": "SOPH", "cohort": "owner_illustration"}
    path = _manifest(tmp_path, [record, illustration])
    loaded = data.load_universe(path, expected_count=1)
    assert [r["symbol"] for r in loaded] == ["AAA"]
    assert loaded[0]["source_receipt"]["history_builder_commit"] == "b" * 40
    assert data.freeze_metadata(loaded)["symbol_count"] == 1
    with pytest.raises(ValueError, match="membership count"):
        data.load_universe(path)


@pytest.mark.parametrize("problem", ["duplicate", "rejected", "illustration", "later_cutoff", "missing_builder"])
def test_universe_does_not_drop_failed_members_or_use_future_quality(tmp_path, problem):
    record = _write(tmp_path, _bars())
    records = [record]
    overrides = {}
    count = 1
    if problem == "duplicate":
        records.append(record); count = 2
    elif problem == "rejected":
        record["status"] = "rejected"
    elif problem == "illustration":
        record["symbol"] = "SOPH"
    elif problem == "later_cutoff":
        overrides["end_exclusive"] = "2026-09-01T00:00:00Z"
    else:
        overrides["builder_commit"] = None
    with pytest.raises(ValueError):
        data.load_universe(_manifest(tmp_path, records, **overrides), expected_count=count)


def test_cutoff_sentinel_never_enters_price_parser_and_future_request_is_clamped(tmp_path):
    frame = _bars(count=98)
    frame["close"] = frame.close.astype(object)
    frame.loc[96:, "close"] = "DO_NOT_PARSE_THIS_FUTURE_PRICE"
    record = _write(tmp_path, frame)
    bars = data.load_bars(record, "2026-08-01T00:00:00Z")
    assert len(bars) == 6
    assert bars.index[-1] == pd.Timestamp("2025-12-31T20:00:00Z")
    assert bars.close.eq(102.).all()
    receipt = bars.attrs["source_receipt"]
    assert receipt["effective_end"] == "2026-01-01T00:00:00+00:00"
    assert receipt["source_rows"] == 96
    assert receipt["holdout_price_rows_materialized"] == 0


def test_source_mutation_is_not_hidden_by_prefix_filter(tmp_path):
    record = _write(tmp_path, _bars())
    with open(record["output_path"], "a") as handle:
        handle.write("1767225600000,sentinel\n")
    with pytest.raises(ValueError, match="SHA256"):
        data.load_bars(record)


def test_complete_groups_aggregate_and_partial_edges_are_not_filled(tmp_path):
    frame = _bars(start="2025-12-30T01:00:00Z", count=40)
    frame["close"] = np.linspace(100., 102., len(frame))
    record = _write(tmp_path, frame)
    bars = data.load_bars(record)
    assert len(bars) == 1
    assert bars.index[0] == pd.Timestamp("2025-12-30T04:00:00Z")
    assert bars.iloc[0].to_dict() == pytest.approx(dict(open=100., high=103., low=98., close=frame.close.iloc[27], volume=160.))
    assert bars.attrs["source_receipt"]["partial_edge_rows_dropped"] == 24


@pytest.mark.parametrize("problem", ["gap", "duplicate", "reverse", "geometry", "negative_volume", "infinity", "alignment", "clock", "confirm"])
def test_invalid_retained_history_is_rejected_without_repair(tmp_path, problem):
    frame = _bars()
    if problem == "gap":
        frame = frame.drop(10)
    elif problem == "duplicate":
        frame = pd.concat([frame.iloc[:10], frame.iloc[[9]], frame.iloc[10:]], ignore_index=True)
    elif problem == "reverse":
        frame = frame.iloc[::-1]
    elif problem == "geometry":
        frame.loc[10, "high"] = 90.
    elif problem == "negative_volume":
        frame.loc[10, "volume"] = -1.
    elif problem == "infinity":
        frame.loc[10, "close"] = np.inf
    elif problem == "alignment":
        frame.loc[10, "ts"] += 1
    elif problem == "clock":
        frame.loc[10, "open_time"] = frame.open_time.iloc[9]
    else:
        frame.loc[10, "confirm"] = "0"
    with pytest.raises(ValueError):
        data.load_bars(_write(tmp_path, frame))


def _features(up=True, ready=True):
    index = pd.date_range("2025-01-01T16:00:00Z", periods=4, freq="4h")
    return pd.DataFrame({"ready": ready, "daily_above_ema50": up,
                         "daily_above_ema200": up,
                         "daily_source_close_time": (index + pd.Timedelta(hours=4)).floor("D")}, index=index)


def _market(up=6, count=10):
    return {"BTC": _features(), **{f"ALT{i}": _features(i < up) for i in range(count)}}


def test_regime_threshold_is_strict_and_major_illustration_coins_are_excluded():
    market = _market(up=5)
    market.update(ETH=_features(), SOPH=_features(), USELESS=_features())
    result = data.market_regime(market)
    assert result.eligible_count.eq(10).all()
    assert result.breadth.eq(.5).all()
    assert result.strong.eq(False).all()
    market["ALT5"]["daily_above_ema50"] = True
    assert data.market_regime(market).strong.eq(True).all()


@pytest.mark.parametrize("reason", ["too_few", "btc_not_ready", "btc_missing", "btc_nan", "alt_nan", "stale_source"])
def test_unknown_context_remains_unknown_not_weak(reason):
    market = _market()
    if reason == "too_few":
        del market["ALT9"]
    elif reason == "btc_not_ready":
        market["BTC"]["ready"] = False
    elif reason == "btc_missing":
        del market["BTC"]
    elif reason == "btc_nan":
        market["BTC"]["daily_above_ema200"] = pd.NA
    elif reason == "alt_nan":
        market["ALT9"]["daily_above_ema50"] = pd.NA
    else:
        market["BTC"]["daily_source_close_time"] -= pd.Timedelta(days=1)
    result = data.market_regime(market)
    assert result.strong.isna().all()
    assert str(result.strong.dtype) == "boolean"


def test_btc_below_ema_is_known_weak_when_breadth_is_known():
    market = _market()
    market["BTC"]["daily_above_ema200"] = False
    result = data.market_regime(market)
    assert result.strong.eq(False).all()
    assert result.btc_up.eq(False).all()


@pytest.mark.parametrize("problem", ["future_day", "incomplete_day", "missing_field"])
def test_unclosed_daily_context_is_rejected(problem):
    market = _market()
    if problem == "future_day":
        market["ALT0"]["daily_source_close_time"] += pd.Timedelta(days=1)
    elif problem == "incomplete_day":
        market["ALT0"]["daily_source_close_time"] -= pd.Timedelta(hours=1)
    else:
        del market["ALT0"]["daily_source_close_time"]
    with pytest.raises(ValueError, match="Daily context"):
        data.market_regime(market)


def test_market_prefix_ignores_later_membership_and_daily_values():
    full = _market()
    prefix = {symbol: frame.iloc[:2].copy() for symbol, frame in full.items()}
    result = data.market_regime(prefix)
    full["LATE"] = _features().iloc[2:]
    for frame in full.values():
        frame.loc[frame.index[2:], "daily_above_ema50"] = False
    pd.testing.assert_frame_equal(result, data.market_regime(full).iloc[:2])


def test_canonical_holdout_is_an_independent_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "HOLDOUT_START", pd.Timestamp("2025-12-31T12:00:00Z"))
    frame = _bars()
    frame["close"] = frame.close.astype(object)
    frame.loc[48:, "close"] = "HOLDOUT_PRICE_SENTINEL"
    loaded = data.load_bars(_write(tmp_path, frame))
    assert len(loaded) == 3
    assert loaded.attrs["source_receipt"]["effective_end"] == "2025-12-31T12:00:00+00:00"


def test_context_accepts_engine_closed_day_receipts_on_synthetic_history():
    from yoyo.evaluation.altseason_trend_engine import build_features

    index = pd.date_range("2023-01-01T00:00:00Z", periods=260 * 6, freq="4h")
    closes = np.linspace(100., 200., len(index))
    bars = pd.DataFrame(dict(open=closes, high=closes + 1, low=closes - 1,
                             close=closes, volume=10.), index=index)
    features = build_features(bars)
    market = {"BTC": features, **{f"ALT{i}": features for i in range(10)}}
    result = data.market_regime(market)
    assert result.strong.iloc[-1]
    assert result.strong.iloc[:255 * 6].isna().all()
