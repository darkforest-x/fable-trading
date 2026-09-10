"""Synthetic native-amount source authentication and UTC history boundaries."""
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
import pytest

from yoyo.data import spike_burst_history as history


HEAD = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"


def make_zip(symbol="ABCUSDT", start="2026-04-30T22:00:00Z", offsets=range(8),
             quote="201.25", volume="2", header=HEAD, fractional_close=False):
    first = pd.Timestamp(start)
    month = first.strftime("%Y-%m")
    lines = [header] if header else []
    for i in offsets:
        ms = first.value // 1000000 + i * 900000
        close = str(ms + 899999) + (".1" if fractional_close else "")
        lines.append(f"{ms},100,102,99,101,{volume},{close},{quote},3,1,100.2,0")
    csv = ("\n".join(lines) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(symbol + "-15m-" + month + ".csv", csv)
    payload = buffer.getvalue()
    return payload, history.digest(payload), history.digest(csv)


def parse(payload_info, symbol="ABCUSDT", month="2026-04"):
    return history.parse_native_month(payload_info[0], symbol, month, payload_info[1], payload_info[2])


def hourly(start="2026-05-01T00:00:00Z", count=3):
    idx = pd.date_range(start, periods=count, freq="h", tz="UTC")
    frame = pd.DataFrame(dict(open=100., high=102., low=99., close=101.,
                              volume=8., quote_volume=805.), index=idx)
    return history.validate_frame(frame, 60)


def save_month(root, payload_info, symbol="ABCUSDT", month="2026-04"):
    path = root / "downloads" / symbol / (symbol + "-15m-" + month + ".zip")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload_info[0])
    _, audit = parse(payload_info, symbol, month)
    row = dict(audit, status="complete", zip_path=str(path))
    return dict(symbol=symbol, month_audits=[row])


def save_recent(root, frame, symbol="ABCUSDT"):
    path = root / "data/normalized/binance" / (symbol + "_1h.csv.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out["ts"] = frame.index.as_unit("ns").asi8 // 1000000
    out["confirmed"] = 1
    out.to_csv(path, index=False)
    return path, dict(sha256=history.digest(path.read_bytes()), rows=len(frame),
                      native_volume_unit="base_asset", quote_volume_unit="USDT")


def test_quotes_are_native_and_summed_not_estimated():
    frame, _ = parse(make_zip())
    assert frame.quote_volume.iloc[0] == 201.25
    assert frame.quote_volume.iloc[0] != frame.close.iloc[0] * frame.volume.iloc[0]
    bars = history.resample_complete(frame, 60)
    assert list(bars.columns) == history.COLS
    assert len(bars) == 2
    assert bars.quote_volume.tolist() == [805., 805.]
    assert bars.volume.tolist() == [8., 8.]
    assert bars.attrs["minutes"] == 60
    assert str(bars.index.tz) == "UTC"


@pytest.mark.parametrize("quote,volume", [("nan", "2"), ("-1", "2"), ("0", "2"), ("1", "0")])
def test_bad_native_amount_rejected(quote, volume):
    with pytest.raises(ValueError):
        parse(make_zip(quote=quote, volume=volume))


def test_zero_volume_and_unheaded_zip_are_valid():
    bars, _ = parse(make_zip(quote="0", volume="0", header=None))
    assert bars.quote_volume.sum() == 0


def test_header_order_and_fractional_clock_rejected():
    with pytest.raises(ValueError, match="header order"):
        parse(make_zip(header=HEAD.replace("quote_volume,count", "count,quote_volume")))
    with pytest.raises(ValueError, match="clock"):
        parse(make_zip(fractional_close=True))


def test_both_zip_and_csv_identity_required():
    source = make_zip()
    with pytest.raises(ValueError, match="CSV SHA"):
        parse((source[0], source[1], "0" * 64))
    with pytest.raises(RuntimeError, match="checksum"):
        parse((source[0], "0" * 64, source[2]))


def test_complete_utc_group_only_and_gap_split():
    frame, _ = parse(make_zip(start="2026-04-30T20:00:00Z", offsets=[0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11]))
    bars = history.resample_complete(frame, 60)
    assert bars.index.hour.tolist() == [20, 22]
    assert len(history.contiguous_segments(bars)) == 2
    assert history.resample_complete(frame, 240).empty


def test_partial_first_group_is_not_relabelled_full_hour():
    frame, _ = parse(make_zip(start="2026-04-30T20:15:00Z", offsets=range(7)))
    bars = history.resample_complete(frame, 60)
    assert len(bars) == 1 and bars.index[0].hour == 21


def test_native_duplicate_and_unaligned_clock_rejected():
    frame = hourly()
    with pytest.raises(ValueError, match="Duplicate"):
        history.contiguous_segments(pd.concat([frame, frame]))
    frame.index += pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="Misaligned"):
        history.validate_frame(frame, 60)


def test_seam_overlap_checks_all_native_columns():
    old = hourly(start="2026-04-30T22:00:00Z", count=3)
    new = hourly(count=3)
    merged, audit = history.merge_hourly(old, new, old.index[0], pd.Timestamp("2026-05-01T03:00Z"))
    assert len(merged) == 5 and audit["overlap_hours_checked"] == 1 and audit["gap_count"] == 0
    bad = new.copy()
    bad.iloc[0, bad.columns.get_loc("quote_volume")] += 1
    with pytest.raises(ValueError, match="overlap mismatch"):
        history.merge_hourly(old, bad, old.index[0], pd.Timestamp("2026-05-01T03:00Z"))


def test_exclusive_end_complete_close_and_gap_never_filled():
    old = hourly(start="2026-04-30T20:00:00Z", count=2)
    new = hourly(count=3)
    merged, audit = history.merge_hourly(old, new, old.index[0], pd.Timestamp("2026-05-01T02:00Z"))
    assert len(merged) == 4 and merged.index[-1] == pd.Timestamp("2026-05-01T01:00Z")
    assert audit["missing_hours_between_rows"] == 2
    assert len(history.contiguous_segments(merged)) == 2


def test_recent_source_hash_confirmation_and_amount_validation(tmp_path):
    path, source = save_recent(tmp_path, hourly())
    out = history.load_recent(path, source, pd.Timestamp("2026-05-01T00:00Z"), pd.Timestamp("2026-05-01T02:00Z"))
    assert len(out) == 2 and out.quote_volume.sum() == 1610
    with pytest.raises(ValueError, match="SHA"):
        history.load_recent(path, dict(source, sha256="0" * 64), out.index[0], out.index[-1])
    raw = pd.read_csv(path)
    raw.loc[0, "confirmed"] = 0
    raw.to_csv(path, index=False)
    source["sha256"] = history.digest(path.read_bytes())
    with pytest.raises(ValueError, match="unconfirmed"):
        history.load_recent(path, source, out.index[0], out.index[-1])


def test_load_month_verifies_metadata_and_clips_complete_bar(tmp_path):
    record = save_month(tmp_path, make_zip())
    out, audit = history.load_months(tmp_path, record, pd.Timestamp("2026-04-30T22:15Z"), pd.Timestamp("2026-04-30T23:15Z"))
    assert len(out) == 4 and len(audit["sources"]) == 1
    record["month_audits"][0]["rows"] += 1
    with pytest.raises(ValueError, match="metadata mismatch"):
        history.load_months(tmp_path, record, pd.Timestamp("2026-04-30T22:15Z"), pd.Timestamp("2026-05-01T00:00Z"))


def test_materialize_only_synthetic_continuous_frames_and_record_corruption(tmp_path):
    archive, recent, output = tmp_path / "old", tmp_path / "recent", tmp_path / "output"
    record = save_month(archive, make_zip())
    _, source = save_recent(recent, hourly())
    job = dict(symbol="ABCUSDT", venue="binance", asset="ABC", major=False, archive_record=record,
               recent_source=source, exclude_reason="", tick="0.001", tick_origin="PRICE_FILTER", tick_snapshot="recent_catalog")
    start, end = pd.Timestamp("2026-04-30T22:00Z"), pd.Timestamp("2026-05-01T03:00Z")
    result = history.materialize_market(job, output, archive, recent, start, end)
    assert result["status"] == "complete" and result["boundary_adjacent"]
    assert len(result["segments"]) == 1 and result["rows"] == 5
    segment = result["segments"][0]
    frame = pd.read_pickle(segment["source_features_path"])
    assert list(frame.columns) == history.COLS and frame.attrs["minutes"] == 60
    assert history.digest(Path(segment["source_features_path"]).read_bytes()) == segment["source_features_sha256"]
    record["month_audits"][0]["csv_sha256"] = "0" * 64
    result = history.materialize_market(job, tmp_path / "bad", archive, recent, start, end)
    assert result["status"] == "rejected" and result["segments"] == []


def test_empty_excluded_and_missing_are_explicit(tmp_path):
    start, end = pd.Timestamp("2023-05-01T00:00Z"), pd.Timestamp("2026-09-09T00:00Z")
    job = dict(symbol="MISSINGUSDT", venue="binance", asset="MISSING", major=False, archive_record=None,
               recent_source=None, exclude_reason="", tick="0.001", tick_origin="PRICE_FILTER", tick_snapshot="recent_catalog")
    result = history.materialize_market(job, tmp_path, tmp_path, tmp_path, start, end)
    assert result["status"] == "empty" and not result["segments"]
    job["exclude_reason"] = "stablecoin"
    assert history.materialize_market(job, tmp_path, tmp_path, tmp_path, start, end)["status"] == "excluded"
    old = dict(symbol="MISSINGUSDT", month_audits=[dict(month="2025-12", status="missing", reason="checksum_404")])
    out, audit = history.load_months(tmp_path, old, start, end)
    assert out.empty and audit["missing_months"] == [dict(month="2025-12", reason="checksum_404")]


def test_naive_reversed_or_duplicate_aggregation_rejected():
    with pytest.raises(ValueError, match="UTC-aware"):
        history.utc("2023-09-09")
    with pytest.raises(ValueError, match="multiple"):
        history.resample_complete(hourly(), 15, source_minutes=60)


def test_recent_missing_quote_is_hard_rejection(tmp_path):
    path, source = save_recent(tmp_path, hourly())
    raw = pd.read_csv(path).drop(columns=["quote_volume"])
    raw.to_csv(path, index=False)
    source["sha256"] = history.digest(path.read_bytes())
    with pytest.raises(ValueError, match="amount schema missing"):
        history.load_recent(path, source, pd.Timestamp("2026-05-01T00:00Z"), pd.Timestamp("2026-05-01T03:00Z"))


def test_full_synthetic_build_mixed_complete_empty_excluded(tmp_path, monkeypatch):
    archive, recent = tmp_path / "old", tmp_path / "recent"
    record = save_month(archive, make_zip())
    _, source = save_recent(recent, hourly())
    common = dict(venue="binance", asset="ABC", major=False, archive_record=None,
                  recent_source=None, exclude_reason="", tick="0.001", tick_origin="PRICE_FILTER", tick_snapshot="recent_catalog")
    jobs = [dict(common, symbol="ABCUSDT", archive_record=record, recent_source=source),
            dict(common, symbol="EMPTYUSDT"), dict(common, symbol="USD1USDT", exclude_reason="stablecoin")]
    # Only synthetic source records are permitted into this test's build.
    monkeypatch.setattr(history, "committed_sources", lambda: ("synthetic-commit", []))
    monkeypatch.setattr(history, "load_inputs", lambda *args: (jobs, []))
    output = tmp_path / "result"
    result = history.build(output, "2026-04-30T22:00Z", "2026-05-01T03:00Z", 2, archive, recent)
    assert result["status"] == "complete"
    assert result["status_counts"] == dict(complete=1, empty=1, excluded=1, rejected=0)
    assert len(result["segments"]) == 1 and len(result["markets"]) == 3
    disk = json.loads((output / "history_manifest.json").read_text())
    assert disk["segments"] == result["segments"]
    with pytest.raises(ValueError, match="directory must be empty"):
        history.build(output, "2026-04-30T22:00Z", "2026-05-01T03:00Z", 2, archive, recent)


def test_artifacts_blocked_when_builder_is_not_committed(tmp_path, monkeypatch):
    def mismatch():
        raise ValueError("Builder must match committed HEAD")
    monkeypatch.setattr(history, "committed_sources", mismatch)
    with pytest.raises(ValueError, match="committed HEAD"):
        history.build(tmp_path / "result", "2023-05-01T00:00Z", "2026-09-09T00:00Z")
    assert not (tmp_path / "result").exists()
