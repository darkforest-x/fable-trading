"""Causal boundary tests for the low-timeframe V8 research runner."""

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

import yoyo.evaluation.spike_v8_lowtf_study as study
from yoyo.evaluation.spike_v8_lowtf_study import _membership_mask


def test_previous_day_leaderboard_waits_for_first_new_day_bar_close() -> None:
    day = pd.Timestamp("2026-03-22T00:00:00Z")
    membership = {day: {"RIVERUSDT"}}
    signal_bar_opens = pd.DatetimeIndex(
        [
            "2026-03-21T23:55:00Z",  # closes at rank publication time: too early
            "2026-03-22T00:00:00Z",  # closes five minutes after publication: eligible
        ]
    )

    result = _membership_mask(signal_bar_opens, membership, "RIVERUSDT", 5)

    assert result.tolist() == [False, True]


def test_previous_day_leaderboard_rejects_nonmember() -> None:
    day = pd.Timestamp("2026-03-22T00:00:00Z")
    result = _membership_mask(
        pd.DatetimeIndex(["2026-03-22T00:00:00Z"]),
        {day: {"RIVERUSDT"}},
        "BTCUSDT",
        5,
    )

    assert result.tolist() == [False]


def test_fresh_run_without_holdout_receipt_fails_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh replay must reject before filesystem setup or any OHLCV path is reached."""
    out = tmp_path / "fresh-without-receipt"
    monkeypatch.setattr(sys, "argv", ["spike_v8_lowtf_study.py", "--out", str(out)])

    with pytest.raises(ValueError, match="pass both --allow-holdout and --holdout-exposure-number"):
        study.main()

    assert not out.exists()


def test_reuse_primary_is_mutually_exclusive_with_fresh_holdout_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hash-pinned replay cannot also request a fresh holdout read."""
    out = tmp_path / "reuse-with-authorization"
    source = tmp_path / "pinned-source"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "spike_v8_lowtf_study.py",
            "--out",
            str(out),
            "--reuse-primary",
            str(source),
            "--allow-holdout",
            "--holdout-exposure-number",
            "4",
        ],
    )

    with pytest.raises(ValueError, match="reuse-primary cannot be combined"):
        study.main()

    assert not out.exists()


def test_pinned_primary_hash_mismatch_rejects_before_leaderboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad pinned artifact must stop reuse before ranking can execute."""
    source = tmp_path / "pinned-source"
    source.mkdir()
    artifacts = (*study.PRIMARY_ARTIFACTS, "summary.csv", "manifest.json")
    for name in artifacts:
        (source / name).write_text(f"fixture:{name}\n")
    mismatched = study.PRIMARY_ARTIFACTS[0]
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in artifacts}
    hashes[mismatched] = "0" * 64
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "reusable_primary": {
                    "path": str(source),
                    "formal_holdout_exposure_number": 3,
                    "sha256": hashes,
                }
            }
        )
    )
    monkeypatch.setattr(study, "CONFIG", config)
    leaderboard_called = False

    def _leaderboard_must_not_run(*_args: object, **_kwargs: object) -> object:
        nonlocal leaderboard_called
        leaderboard_called = True
        raise AssertionError("leaderboard must not run after a pinned hash mismatch")

    monkeypatch.setattr(study, "run_leaderboard", _leaderboard_must_not_run)
    out = tmp_path / "reused-output"
    monkeypatch.setattr(
        sys,
        "argv",
        ["spike_v8_lowtf_study.py", "--out", str(out), "--reuse-primary", str(source)],
    )

    with pytest.raises(ValueError, match="reusable primary artifact mismatch"):
        study.main()

    assert leaderboard_called is False
    assert not any((out / name).exists() for name in study.PRIMARY_ARTIFACTS)
