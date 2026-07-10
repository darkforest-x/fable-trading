"""Champion/challenger shadow registry + comparison (no market IO)."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from src.judgment.forward_records import merge_forward_log, write_forward_log
from src.judgment.forward_scan import resolve_forward_exit
from src.judgment.shadow_compare import compare_shadow_books, format_comparison_text
from src.judgment.shadow_registry import (
    champion_book,
    get_shadow_book,
    list_shadow_books,
    registry_snapshot,
    resolve_runner,
    supported_books,
    unsupported_books,
)


def test_registry_names_and_roles_are_explicit() -> None:
    books = list_shadow_books()
    names = [b.name for b in books]
    assert names == [
        "tp5_sl2_long_swap",
        "h1_scaled_25_t3",
        "h8_30m_h48",
        "h10_short_tp5_sl2",
    ]
    assert champion_book().role == "champion"
    assert champion_book().name == "tp5_sl2_long_swap"
    assert {b.name for b in supported_books()} == {"tp5_sl2_long_swap", "h1_scaled_25_t3"}
    assert {b.name for b in unsupported_books()} == {"h8_30m_h48", "h10_short_tp5_sl2"}


def test_unsupported_challengers_are_not_approximated() -> None:
    h8 = get_shadow_book("h8_30m_h48")
    h10 = get_shadow_book("h10_short_tp5_sl2")
    assert h8.status == "unsupported"
    assert h10.status == "unsupported"
    assert "30m" in h8.unsupported_reason or "frozen" in h8.unsupported_reason.lower()
    assert "short" in h10.unsupported_reason.lower()
    with pytest.raises(ValueError, match="unsupported"):
        resolve_runner(h8)
    with pytest.raises(ValueError, match="unsupported"):
        resolve_runner(h10)


def test_registry_never_promotes_active() -> None:
    snap = registry_snapshot()
    assert snap["active_promotion"] == "disabled"
    for book in snap["books"]:
        assert book["promotes_active"] is False


def test_merge_idempotent_on_source_symbol_signal_time() -> None:
    row = {
        "source": "okx",
        "symbol": "BTC_USDT_SWAP",
        "signal_time": "2026-07-08 00:00:00+00:00",
        "detected_at": "first",
        "status": "open",
        "score": 0.5,
        "threshold": 0.4,
        "model_path": "models/frozen.txt",
        "dataset_sha256": "abc",
        "signal_i": 1,
        "entry_time": "2026-07-08 00:15:00+00:00",
        "entry_price": 100.0,
        "maker_filled": True,
        "outcome": "",
        "label": -1,
        "exit_offset": 0,
        "exit_time": "",
        "realized_ret": math.nan,
        "atr_pct": 0.01,
        "dense_run_len": 8,
    }
    existing = pd.DataFrame([row])
    closed = dict(row)
    closed["status"] = "closed"
    closed["outcome"] = "tp"
    closed["label"] = 1
    closed["detected_at"] = "second"
    closed["realized_ret"] = 0.05
    once = merge_forward_log(existing, [closed])  # type: ignore[list-item]
    twice = merge_forward_log(once.frame, [closed])  # type: ignore[list-item]
    assert once.new_signals == 0 and once.closed_updates == 1
    assert twice.new_signals == 0 and twice.closed_updates == 0
    assert len(twice.frame) == 1
    assert twice.frame.iloc[0]["detected_at"] == "first"


def test_exit_resolver_no_lookahead_past_available_bars() -> None:
    """Exit math may only use bars from entry through available horizon."""
    # signal at 0, entry at 1; only 2 path bars → incomplete 72h → open
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=3, freq="15min", tz="UTC"),
            "open": [99.0, 100.0, 100.0],
            "high": [100.0, 100.5, 100.5],
            "low": [98.0, 99.5, 99.5],
            "close": [100.0, 100.0, 100.0],
            "atr14": [1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01],
        }
    )
    outcome = resolve_forward_exit(frame, 0)
    assert outcome is not None
    assert outcome.status == "open"
    # Future bars beyond len(frame) must not invent a close.
    assert outcome.exit_time == ""
    assert math.isnan(outcome.realized_ret)


def test_compare_marks_unsupported_and_reads_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.judgment import shadow_compare as sc
    from src.judgment.shadow_registry import ShadowBook

    champ_log = tmp_path / "champ.csv"
    h1_log = tmp_path / "h1.csv"
    write_forward_log(
        champ_log,
        pd.DataFrame(
            [
                {
                    "source": "okx",
                    "symbol": "AAA_USDT_SWAP",
                    "signal_time": "2026-07-08 00:00:00+00:00",
                    "detected_at": "t",
                    "status": "closed",
                    "score": 0.5,
                    "threshold": 0.4,
                    "model_path": "m",
                    "dataset_sha256": "s",
                    "signal_i": 1,
                    "entry_time": "2026-07-08 00:15:00+00:00",
                    "entry_price": 100.0,
                    "maker_filled": True,
                    "outcome": "tp",
                    "label": 1,
                    "exit_offset": 1,
                    "exit_time": "2026-07-08 00:30:00+00:00",
                    "realized_ret": 0.01,
                    "atr_pct": 0.01,
                    "dense_run_len": 4,
                }
            ]
        ),
    )
    books = (
        ShadowBook(
            name="tp5_sl2_long_swap",
            role="champion",
            status="supported",
            log_path=champ_log,
            bar="15m",
            side="long",
            exit_family="tp5_sl2",
            entry_model="tp5_sl2_swap",
            description="test",
            unsupported_reason="",
            runner_key="mainline_tp5_sl2",
        ),
        ShadowBook(
            name="h1_scaled_25_t3",
            role="challenger",
            status="supported",
            log_path=h1_log,
            bar="15m",
            side="long",
            exit_family="scaled_25_t3",
            entry_model="tp5_sl2_swap",
            description="test",
            unsupported_reason="",
            runner_key="h1_scaled_shadow",
        ),
        ShadowBook(
            name="h8_30m_h48",
            role="challenger",
            status="unsupported",
            log_path=tmp_path / "h8.csv",
            bar="30m",
            side="long",
            exit_family="tp5_sl2",
            entry_model="none",
            description="test",
            unsupported_reason="no freeze",
            runner_key=None,
        ),
    )
    comparison = sc.compare_shadow_books(books)
    assert comparison["books"][0]["closed_rows"] == 1
    assert comparison["books"][0]["duplicate_keys"] == 0
    assert comparison["books"][1]["exists"] is False
    assert comparison["books"][2]["status"] == "unsupported"
    text = format_comparison_text(comparison)
    assert "unsupported" in text
    assert "tp5_sl2_long_swap" in text
    assert comparison["evidence_class"] == "prospective_forward_observation"
