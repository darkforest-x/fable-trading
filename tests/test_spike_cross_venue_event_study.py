import gzip
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import spike_cross_venue_event_study as study
from yoyo.evaluation.spike_cross_venue_event_study import attach_outcomes, collapse_events, prepare_signals


def _signals(rows):
    return pd.DataFrame(rows, columns=["v7", "v8", "side", "signal_bar_open", "period", "stream_key", "venue", "asset", "timeframe_min"])


def test_confirmation_is_causal_and_does_not_chain_across_two_bars():
    signals = prepare_signals(_signals([
        [True, True, 1, "2026-01-01T00:00Z", "validation", "a", "binance", "BTC", 60],
        [True, True, 1, "2026-01-01T01:00Z", "validation", "b", "okx", "BTC", 60],
        [True, True, 1, "2026-01-01T02:00Z", "validation", "c", "gate", "BTC", 60],
    ]), "v7")
    members, events = collapse_events(signals)
    assert len(members) == 3
    assert len(events) == 2
    assert events.source_confirmation_count.tolist() == [2, 1]
    assert events.multi_venue_confirmed.tolist() == [True, False]
    assert events.confirmation_delay_min.dropna().tolist() == [60.0]


def test_tolerance_boundary_is_inclusive_but_later_confirmation_is_new_event():
    signals = prepare_signals(_signals([
        [True, True, -1, "2026-01-01T00:00Z", "validation", "a", "binance", "ETH", 30],
        [True, True, -1, "2026-01-01T00:30Z", "validation", "b", "okx", "ETH", 30],
        [True, True, -1, "2026-01-01T01:00Z", "validation", "c", "gate", "ETH", 30],
    ]), "v7")
    _, events = collapse_events(signals, tolerance_bars=1)
    assert events.source_confirmation_count.tolist() == [2, 1]


def test_same_venue_rows_do_not_create_independent_venue_confirmation_or_disappear():
    signals = prepare_signals(_signals([
        [True, True, 1, "2026-01-01T00:00Z", "validation", "a1", "binance", "SOL", 60],
        [True, True, 1, "2026-01-01T00:30Z", "validation", "a2", "binance", "SOL", 60],
        [True, True, 1, "2026-01-01T01:00Z", "validation", "b", "okx", "SOL", 60],
    ]), "v7")
    members, events = collapse_events(signals)
    assert len(members) == len(signals)
    assert events.iloc[0].venue_count == 2
    assert events.iloc[0].multi_venue_confirmed
    assert members.venue_duplicate_within_event.sum() == 1


def test_side_and_timeframe_never_fold_together():
    signals = prepare_signals(_signals([
        [True, True, 1, "2026-01-01T00:00Z", "validation", "a", "binance", "X", 60],
        [True, True, -1, "2026-01-01T00:00Z", "validation", "b", "okx", "X", 60],
        [True, True, 1, "2026-01-01T00:00Z", "validation", "c", "gate", "X", 30],
    ]), "v7")
    _, events = collapse_events(signals)
    assert len(events) == 3


def test_event_folding_has_no_coverage_or_outcome_precondition():
    """A lone source admission remains an event even before a follower exists."""
    signals = prepare_signals(_signals([
        [True, False, 1, "2026-01-01T00:00Z", "validation", "a", "binance", "NEW", 60],
    ]), "v7")
    members, events = collapse_events(signals)
    assert len(members) == 1
    assert len(events) == 1
    assert not events.iloc[0].multi_venue_confirmed


def test_unknown_or_duplicate_trade_mapping_fails_closed():
    signals = prepare_signals(_signals([
        [True, True, 1, "2026-01-01T00:00Z", "validation", "a", "binance", "BTC", 60],
    ]), "v7")
    members, _ = collapse_events(signals)
    unknown = pd.DataFrame([{
        "stream_key": "other", "signal_bar_open": "2026-01-01T00:00Z", "side": 1, "arm": "v7", "trade_id": "x",
        "censored": False, "net_r": 1.0, "net_return": .01, "mfe_r": 1.0,
    }])
    with pytest.raises(ValueError, match="unknown source"):
        attach_outcomes(members, unknown, "v7")
    duplicated = pd.concat([unknown.assign(stream_key="a"), unknown.assign(stream_key="a")], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        attach_outcomes(members, duplicated, "v7")


def _write_gzip(path, content):
    path.write_bytes(gzip.compress(content.encode("utf-8"), mtime=0))


def _receipt_bound_source(tmp_path, monkeypatch):
    """Build the smallest complete source and preregister each parsed receipt."""
    exp = tmp_path / "experiment"
    exp.mkdir()
    monkeypatch.setattr(study, "EXP", exp)
    source = tmp_path / "source"
    streams = source / "streams"
    streams.mkdir(parents=True)
    signals = (
        "v7,v8,side,signal_bar_open,period,stream_key,venue,asset,timeframe_min\n"
        "True,False,1,2026-01-01T00:00:00Z,validation,one,binance,BTC,60\n"
    )
    trades = (
        "stream_key,signal_bar_open,side,arm,trade_id,censored,net_r,net_return,mfe_r\n"
        "one,2026-01-01T00:00:00Z,1,v7,trade-1,False,1.0,0.01,1.0\n"
    )
    controls = "stream_key,signal_bar_open,side,arm,matched,net_r_difference,net_return_difference\n"
    _write_gzip(streams / "one.signals.csv.gz", signals)
    _write_gzip(streams / "one.trades.csv.gz", trades)
    _write_gzip(streams / "one.controls.csv.gz", controls)
    source_manifest = {"complete": True, "streams": 1}
    manifest_bytes = json.dumps(source_manifest, sort_keys=True).encode("utf-8")
    (source / "manifest.json").write_bytes(manifest_bytes)
    receipt_paths = study.input_receipt_paths(streams, stream_limit=None)
    receipts = study.inventory_input_receipts(receipt_paths, source)
    input_manifest = {
        "schema_version": 1,
        "receipts": receipts,
        "receipt_count": len(receipts),
        "aggregate_sha256": study._receipt_aggregate(receipts),
    }
    input_manifest_bytes = (json.dumps(input_manifest, indent=2) + "\n").encode("utf-8")
    (exp / "input_receipts.json").write_bytes(input_manifest_bytes)
    config = {
        "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "expected_streams": 1,
        "expected_v7_signals": 1,
        "input_receipt_manifest": "input_receipts.json",
        "input_receipt_manifest_sha256": hashlib.sha256(input_manifest_bytes).hexdigest(),
        "input_receipt_aggregate_sha256": input_manifest["aggregate_sha256"],
        "expected_input_receipts": len(receipts),
        "event_tolerance_bars": 1,
    }
    (exp / "config.json").write_text(json.dumps(config))
    return source, streams, {"signals": signals, "trades": trades, "controls": controls}


@pytest.mark.parametrize("kind", ["signals", "trades", "controls"])
def test_run_rejects_a_changed_receipt_before_csv_parse_even_when_v7_total_is_unchanged(tmp_path, monkeypatch, kind):
    source, streams, contents = _receipt_bound_source(tmp_path, monkeypatch)
    first = tmp_path / "first"
    study.run(source, first)
    receipt = json.loads((first / "run_manifest.json").read_text())
    assert receipt["config_sha256"] == hashlib.sha256((study.EXP / "config.json").read_bytes()).hexdigest()
    assert receipt["code_sha256"] == hashlib.sha256(Path(study.__file__).read_bytes()).hexdigest()
    # The signals variant changes V8 only; the others are payload-only changes.
    # All retain the source stream count and V7 admission total.
    changed = contents[kind].replace("True,False", "True,True") if kind == "signals" else contents[kind] + "\n"
    _write_gzip(streams / f"one.{kind}.csv.gz", changed)

    def should_not_parse(*_args, **_kwargs):
        raise AssertionError("CSV parsing must not start after receipt verification fails")

    monkeypatch.setattr(study.pd, "read_csv", should_not_parse)
    with pytest.raises(ValueError, match="input receipt identity"):
        study.run(source, tmp_path / "changed")
