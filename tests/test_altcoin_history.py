"""Synthetic contracts for freezing research history; no network or real data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.data import altcoin_history as history

END = "2026-01-01T02:00:00Z"


def bars(start=0, stop=8):
    times = pd.date_range("2026-01-01", periods=10, freq="15min", tz="UTC")[start:stop]
    return pd.DataFrame(dict(ts=times.asi8 // 1_000_000, open=100.0, high=102.0,
                             low=99.0, close=101.0, volume=10.0, open_time=times))


def write(path: Path, frame: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_merge_keeps_only_closed_rows_and_verifies_identical_overlap(tmp_path):
    old = write(tmp_path / "old.csv", bars(0, 6))
    # Out-of-cutoff malformed prices are not part of the frozen price input.
    recent_rows = bars(4, 10)
    recent_rows["close"] = recent_rows["close"].astype(object)
    recent_rows.loc[recent_rows.index[-1], "close"] = "not-a-price"
    recent = write(tmp_path / "recent.csv", recent_rows)
    before = {p: sha(p) for p in (old, recent)}
    frame, audit = history.merge_symbol("AAA", [old, recent], end=END)
    assert frame.ts.tolist() == bars().ts.tolist()
    assert audit["rows"] == 8 and audit["overlap_timestamps"] == 2
    assert audit["end_close"] == "2026-01-01T02:00:00+00:00"
    assert audit["sources"][1]["excluded_rows"] == 2
    assert all(row["confirmation"] == "legacy_csv_confirmation_not_retained" for row in audit["sources"])
    assert {p: sha(p) for p in (old, recent)} == before


@pytest.mark.parametrize("column", history.OHLCV)
def test_any_overlap_disagreement_rejects_instead_of_prefer_recent(tmp_path, column):
    old = write(tmp_path / "old.csv", bars())
    changed = bars(4, 8)
    changed.loc[0, column] += .01
    new = write(tmp_path / "new.csv", changed)
    for order in ([old, new], [new, old]):
        with pytest.raises(history.HistoryValidationError) as caught:
            history.merge_symbol("AAA", order, end=END)
        assert caught.value.audit["reason"] == "overlapping_quotes_disagree"
        assert caught.value.audit["conflict_timestamps"] == 1
        assert column in caught.value.audit["conflicts"][0]["columns"]


def test_internal_duplicate_conflict_is_not_silently_deduplicated(tmp_path):
    data = pd.concat([bars(), bars(0, 1)], ignore_index=True)
    data.loc[len(data)-1, "volume"] = 11
    path = write(tmp_path / "duplicate.csv", data)
    with pytest.raises(history.HistoryValidationError, match="overlapping_quotes_disagree"):
        history.merge_symbol("AAA", [path], end=END)


def test_input_order_and_exact_duplicates_do_not_create_fake_gaps(tmp_path):
    data = pd.concat([bars().iloc[::-1], bars(0, 2)], ignore_index=True)
    path = write(tmp_path / "unordered.csv", data)
    frame, audit = history.merge_symbol("AAA", [path], end=END)
    assert frame.ts.tolist() == bars().ts.tolist()
    assert audit["gap_count"] == 0 and audit["overlap_rows"] == 2


def test_tiny_relative_float_noise_only_is_accepted(tmp_path):
    a = write(tmp_path / "a.csv", bars())
    tiny = bars(); tiny.loc[0, "volume"] *= 1 + 2e-13
    b = write(tmp_path / "b.csv", tiny)
    result, audit = history.merge_symbol("AAA", [a, b], end=END)
    assert len(result) == 8 and audit["conflict_timestamps"] == 0


@pytest.mark.parametrize("problem", ["gap", "tail", "geometry", "nonfinite", "unaligned", "clock", "unconfirmed"])
def test_missing_or_untrustworthy_history_is_rejected(tmp_path, problem):
    data = bars()
    expected = "invalid_ohlcv"
    if problem == "gap":
        data = data.drop(index=3); expected = "missing_intervals"
    elif problem == "tail":
        data = data.iloc[:-1]; expected = "incomplete_requested_tail"
    elif problem == "geometry":
        data.loc[0, "high"] = 100
    elif problem == "nonfinite":
        data.loc[0, "volume"] = float("inf")
    elif problem == "unaligned":
        data.loc[0, "ts"] += 1; expected = "misaligned_timestamp"
    elif problem == "clock":
        data.loc[0, "open_time"] += pd.Timedelta(minutes=15); expected = "open_time_disagrees_with_ts"
    elif problem == "unconfirmed":
        data["confirm"] = "1"; data.loc[0, "confirm"] = "0"; expected = "unconfirmed_source_rows"
    path = write(tmp_path / "bad.csv", data)
    with pytest.raises(history.HistoryValidationError, match=expected) as caught:
        history.merge_symbol("AAA", [path], end=END)
    if problem == "gap":
        assert caught.value.audit["gap_count"] == 1
        assert caught.value.audit["missing_bars"] == 1
    if problem == "tail":
        assert caught.value.audit["tail_missing_bars"] == 1


@pytest.mark.parametrize("end", ["2026-01-01T02:00:00", "2026-01-01T10:00:00+08:00",
                               "2026-01-01T02:01:00Z", "2026-01-01T02:15:00Z"])
def test_cutoff_must_be_explicit_utc_and_already_closed(end):
    with pytest.raises(ValueError):
        history.utc_cutoff(end, now="2026-01-01T02:07:00Z")
    assert history.utc_cutoff(END, now="2026-01-01T02:07:00Z") == pd.Timestamp(END)


def universe_fixture(tmp_path):
    root = tmp_path / "repo"
    old = write(root / "data/kline_deep/okx_AAA_USDT_SWAP_15m_6.csv", bars(0, 6))
    write(root / "data/kline_fetched/okx_AAA_USDT_SWAP_15m_4.csv", bars(4, 8))
    for symbol in history.ILLUSTRATIONS:
        write(root / f"data/kline_fetched/okx_{symbol}_USDT_SWAP_15m_8.csv", bars())
    universe = root / "universe.json"
    universe.write_text(json.dumps({"sources": [{"symbol": "AAA", "path": str(old.relative_to(root)), "sha256": sha(old)}]}))
    return root, universe, old


def test_freeze_tags_illustrations_and_hashes_outputs_without_mutating_sources(tmp_path):
    root, universe, old = universe_fixture(tmp_path)
    original = sha(old)
    target = tmp_path / "frozen"
    result = history.prepare_history(universe, target, end=END, repo_root=root, expected_pool_size=1)
    assert result["status"] == "complete"
    assert result["pool_symbols"] == ["AAA"]
    assert result["illustration_symbols"] == ["SOPH", "USELESS"]
    assert result["complete_pool_symbols"] == 1 and result["complete_illustrations"] == 2
    assert result["files"].keys() == {"frozen_pool/AAA_USDT_SWAP_15m.csv", "owner_illustration/SOPH_USDT_SWAP_15m.csv", "owner_illustration/USELESS_USDT_SWAP_15m.csv"}
    for relative, digest in result["files"].items():
        assert sha(target / relative) == digest
    assert json.loads((target / "manifest.json").read_text()) == result
    assert sha(old) == original
    before = (target / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        history.prepare_history(universe, target, end=END, repo_root=root, expected_pool_size=1)
    assert (target / "manifest.json").read_bytes() == before


def test_recent_research_extension_fills_tail_but_conflict_never_emits_symbol(tmp_path):
    root, universe, old = universe_fixture(tmp_path)
    fetched = root / "data/kline_fetched/okx_AAA_USDT_SWAP_15m_4.csv"
    fetched.unlink()
    recent = tmp_path / "recent"
    write(recent / "okx_AAA_USDT_SWAP_15m_4.csv", bars(4, 8))
    good = history.prepare_history(universe, tmp_path / "good", end=END, recent_dir=recent,
                                   repo_root=root, expected_pool_size=1)
    assert good["status"] == "complete"
    changed = bars(4, 8); changed.loc[0, "volume"] = 30
    write(recent / "okx_AAA_USDT_SWAP_15m_4.csv", changed)
    bad_path = tmp_path / "bad"
    bad = history.prepare_history(universe, bad_path, end=END, recent_dir=recent,
                                  repo_root=root, expected_pool_size=1)
    assert bad["status"] == "rejected" and bad["complete_pool_symbols"] == 0
    assert bad["symbols"][0]["reason"] == "overlapping_quotes_disagree"
    assert not (bad_path / "frozen_pool/AAA_USDT_SWAP_15m.csv").exists()
    assert (bad_path / "manifest.json").exists()


def test_changed_frozen_source_hash_rejects_that_symbol(tmp_path):
    root, universe, old = universe_fixture(tmp_path)
    old.write_bytes(old.read_bytes() + b"\n")
    result = history.prepare_history(universe, tmp_path / "out", end=END, repo_root=root, expected_pool_size=1)
    assert result["symbols"][0]["reason"] == "source_hash_mismatch"
    assert result["complete_illustrations"] == 2


def test_canonical_output_and_wrong_universe_are_refused_before_write(tmp_path):
    root, universe, _ = universe_fixture(tmp_path)
    with pytest.raises(ValueError, match="canonical"):
        history.prepare_history(universe, root / "data/kline_fetched/new", end=END,
                                repo_root=root, expected_pool_size=1)
    with pytest.raises(ValueError, match="pool size"):
        history.prepare_history(universe, tmp_path / "out", end=END, repo_root=root)
    assert not (tmp_path / "out").exists()


def test_cli_refuses_uncommitted_builder_before_reading_sources_or_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(history.subprocess, "check_output", lambda args, **kwargs:
                        "a" * 40 if args[1] == "rev-parse" else b"different builder")
    monkeypatch.setattr(history, "prepare_history", lambda *args, **kwargs: pytest.fail("uncommitted build started"))
    result = history.main(["--universe", str(tmp_path / "unread.json"), "--out-dir", str(tmp_path / "out"), "--end", END])
    assert result == 1
    assert not (tmp_path / "out").exists()
