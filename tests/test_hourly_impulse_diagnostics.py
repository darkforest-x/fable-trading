"""Synthetic ledger checks; these tests never access real experiment outcomes."""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_diagnostics import (
    build_diagnostics, classify_trades, diagnose_frame, fixed_bin_table,
    paired_exit_comparison,
)


def ledger():
    # Precedence examples: hard SL beats fee/giveback/early; fee beats giveback;
    # giveback beats early. Zero-net fee flip remains a flat rather than loss.
    specifications = [
        ("stop", "hard_stop_gap", -.10, -.102, 2.0, 10, True),
        ("cost", "colour_exit", .001, -.001, 1.5, 15, True),
        ("giveback", "colour_exit", -.01, -.012, 1.2, 15, True),
        ("early", "colour_exit", -.01, -.012, .1, 30, True),
        ("other", "time_exit", -.01, -.012, 0.0, 60, True),
        ("winner", "colour_exit", .04, .038, 4.0, 120, True),
        ("flat", "colour_exit", .002, 0.0, .3, 15, True),
        ("censored", "right_censored", np.nan, np.nan, 3.0, 60, False),
        ("reject", "entry_missing", np.nan, np.nan, 0.0, np.nan, False),
    ]
    rows = []
    for i, (event_id, outcome, gross, net, mfe, hold, closed) in enumerate(specifications):
        rows.append({
            "event_id": event_id, "entry_time": pd.Timestamp("2024-01-01T00:00:00Z") + pd.Timedelta(days=i),
            "direction": 1 if i % 2 else -1, "fold": "A" if i < 5 else "B",
            "entry_price": 100.0, "initial_stop": 90.0 if i % 2 else 110.0,
            "outcome": outcome, "gross_return": gross, "net_return": net,
            "max_favourable_r": mfe, "max_adverse_r": -.1, "hold_minutes": hold,
            "risk_pct": .01, "closed": closed, "body_ratio": .7, "range_atr": 1.5,
            "extension_atr": .3, "cross_count24": 2.0, "efficiency24": .3,
            "volume_ratio": 1.0, "ma_slope_atr": .02,
        })
    return pd.DataFrame(rows)


def test_primary_taxonomy_is_exclusive_and_precedence_is_explicit():
    result = classify_trades(ledger()).set_index("event_id")
    assert result.loc[["stop", "cost", "giveback", "early", "other"], "primary_loss_reason"].tolist() == [
        "hard_stop", "cost_flip", "giveback", "early_reversal", "other_loss",
    ]
    assert result.loc["flat", "primary_loss_reason"] == "flat"
    assert result.loc["flat", "fees_flip"]
    assert not result.loc["flat", "net_loser"]
    assert result.loc["other", "never_positive"]
    assert result.loc["stop", "giveback"]
    assert "not a proven causal mechanism" in result.loc["cost", "diagnostic_explanation"]


def test_metrics_exclude_marks_rejections_and_nonfinite_closed_returns():
    source = ledger()
    malformed = source.iloc[[5]].assign(event_id="bad", net_return=np.inf)
    source = pd.concat([source, malformed], ignore_index=True)
    _, summary, tables = diagnose_frame(source)
    assert summary["closed_finite_events"] == 7
    assert summary["opened_censored"] == 1
    assert summary["rejected_entries"] == 1
    assert summary["closed_nonfinite"] == 1
    assert len(tables["losing_trades"]) == 5
    assert summary["metrics"]["n"] == 7
    assert summary["fee_flips_including_flat"] == 2


def test_bins_have_fixed_boundaries_and_each_closed_event_counted_once():
    classified = classify_trades(ledger())
    classified.loc[0, "body_ratio"] = .65
    classified.loc[1, "body_ratio"] = np.nan
    bins = fixed_bin_table(classified)
    body = bins.loc[bins.feature.eq("body_ratio")]
    assert body.n.sum() == 7
    assert body.loc[body.bin.eq("[0.65, 0.8)"), "n"].iloc[0] == 6
    assert body.loc[body.bin.eq("missing"), "n"].iloc[0] == 1
    assert set(bins.feature) == {
        "body_ratio", "range_atr", "risk_pct", "fee_to_risk", "extension_atr",
        "cross_count24", "efficiency24", "volume_ratio", "ma_slope_atr",
    }
    assert classified.fee_to_risk.dropna().eq(.2).all()


def test_group_counts_reconcile_with_eligible_and_losing_ledgers():
    _, summary, tables = diagnose_frame(ledger())
    for name in ("outcome", "direction", "fold", "outcome_direction_fold", "daily", "monthly"):
        assert tables[name].n.sum() == summary["closed_finite_events"]
    assert tables["loss_taxonomy"].n.sum() == len(tables["losing_trades"])
    assert len(tables["monthly"]) == 1
    assert tables["monthly"].sum_event_net_bp.iloc[0] == pytest.approx(ledger().net_return.sum() * 1e4)


def test_paired_exit_delta_uses_only_same_event_closed_in_both():
    a = ledger()
    b = a.copy()
    b.loc[0, ["net_return", "gross_return"]] += .01
    b.loc[1, ["net_return", "gross_return"]] += .002
    b.loc[2, ["closed", "net_return", "gross_return"]] = [False, np.nan, np.nan]
    summary, events = paired_exit_comparison({"15m_two": classify_trades(b), "15m_first": classify_trades(a)})
    row = summary.iloc[0]
    assert row.reference == "15m_first"
    assert row.candidate == "15m_two"
    assert row.n_same_event_closed == 6
    assert row.n_reference_closed == 7 and row.n_candidate_closed == 6
    assert row.n_improved == 2
    assert row.n_loss_to_profit == 1
    assert row.mean_delta_net_bp == pytest.approx(20.0)
    assert "giveback" not in set(events.event_id)


def test_pair_contract_mismatch_is_not_silently_interpreted_as_an_exit_effect():
    a = ledger()
    b = a.copy()
    b.loc[0, "entry_price"] = 101.0
    with pytest.raises(ValueError, match="entry contract"):
        paired_exit_comparison({"15m_first": classify_trades(a), "5m_native40": classify_trades(b)})


def test_top_winners_denominator_is_explicit_even_when_total_net_negative():
    _, summary, tables = diagnose_frame(ledger())
    assert summary["metrics"]["sum_event_net_bp"] < 0
    assert summary["top1_share_of_positive_profits"] == 1.0
    assert summary["top2_share_of_total_net"] is None
    assert tables["top_winners"].share_of_total_event_net.isna().all()


def test_empty_and_string_boolean_ledgers_are_supported():
    frame = ledger()
    frame["closed"] = frame.closed.astype(str)
    assert classify_trades(frame).diagnostic_eligible.sum() == 7
    classified, summary, _ = diagnose_frame(frame.iloc[:0])
    assert len(classified) == 0
    assert summary["metrics"]["n"] == 0


def test_build_reads_only_named_synthetic_ledgers_and_writes_repeatable_outputs(tmp_path):
    source, output = tmp_path / "results", tmp_path / "diagnostics"
    source.mkdir()
    frame = ledger()
    frame.to_csv(source / "development_baseline_trades.csv.gz", index=False)
    frame.to_csv(source / "development_candidate_trades.csv.gz", index=False)
    frame.to_csv(source / "development_exit_15m_first_trades.csv.gz", index=False)
    frame.to_csv(source / "development_exit_15m_two_trades.csv.gz", index=False)
    (source / "raw_prices.csv").write_text("this must never be read\n")
    before = hashlib.sha256((source / "development_baseline_trades.csv.gz").read_bytes()).hexdigest()
    result = build_diagnostics(source, output)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir()}
    repeated = build_diagnostics(source, output)
    assert result == repeated
    assert hashes == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir()}
    assert len(result["source_manifest"]) == 4
    assert result["exit_arm_count"] == 2
    assert before == hashlib.sha256((source / "development_baseline_trades.csv.gz").read_bytes()).hexdigest()
    assert json.loads((output / "summary.json").read_text())["datasets"]["development_baseline"]["closed_finite_events"] == 7
    assert (output / "development_baseline_losing_trades.csv.gz").exists()


def test_duplicate_events_raise_instead_of_multiplying_pair_counts():
    frame = pd.concat([ledger(), ledger().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="unique"):
        classify_trades(frame)


def test_diagnostic_output_cannot_overwrite_source_result_directory(tmp_path):
    with pytest.raises(ValueError, match="separate"):
        build_diagnostics(tmp_path, tmp_path)
