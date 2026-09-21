"""Matched controls must preserve event weights, failures and frozen lineage."""
from __future__ import annotations

from copy import deepcopy

import pytest

from yoyo.evaluation.ma_profit_control_metrics import ControlMetricError, compare_controls


def target(event_id, gross_r=3.):
    return {"event_id": event_id, "split": "val", "source_path": "price.csv",
            "canonical_asset": "BTC", "direction": "LONG", "bar_minutes": 1, "core_bars": 4,
            "profit": {"outcome": "TP" if gross_r == 3 else "SL", "retained": gross_r == 3,
                       "risk_price": 1., "entry_price": 100., "gross_r": gross_r, "net_r": gross_r-.2}}


def model(row, score):
    p = row["profit"]
    return {**row, "outcome": p["outcome"], "retained": p["retained"], "score": score,
            "quality_score": score, "same_direction_deployed": score >= .25,
            "gross_r": p["gross_r"], "net_r": p["net_r"], "gross_bp": p["gross_r"]*100,
            "net_bp": p["net_r"]*100}


def control(row, order, gross_r=3., *, unknown=False, offset=100):
    source = {key: row[key] for key in ("source_path", "canonical_asset", "direction", "bar_minutes", "core_bars", "split")}
    source.update({"event_id": f"{row['event_id']}_control_{order}", "matched_event_id": row["event_id"],
                   "source_core_start_i": offset+order, "source_core_end_i": offset+order+3,
                   "match_order": order, "control_identity": f"price.csv|LONG|{offset+order}|{offset+order+3}"})
    outcome = {**source, "profit": target("unused", gross_r)["profit"] if not unknown else {"outcome": "UNKNOWN"}}
    return source, outcome


def receipt(targets, selected):
    rows = []
    for row in targets:
        count = sum(control["matched_event_id"] == row["event_id"] for control in selected)
        rows.append({"matched_event_id": row["event_id"], "requested": 5, "selected": count,
                     "shortage": 5-count, "reason": "ok" if count == 5 else "insufficient_exact_candidates"})
    return {"desired_per_event": 5, "shortages": rows}


def compare(targets, pairs, scores=None):
    sources, labels = [pair[0] for pair in pairs], [pair[1] for pair in pairs]
    events = [model(row, (scores or {}).get(row["event_id"], .5)) for row in targets]
    return compare_controls(events, targets, sources, labels, receipt(targets, sources), split="val")


def test_per_event_average_does_not_overweight_events_with_five_controls():
    a, b = target("a"), target("b")
    pairs = [control(a, order, 3.) for order in range(1, 6)] + [control(b, 1, -1., offset=200)]
    summary = compare([a, b], pairs)["overall"]["all"]
    assert summary["paired_events"] == 2 and summary["selected_controls"] == 6
    assert summary["per_event_mean_control"]["net_bp"] == pytest.approx(80.)
    assert summary["candidate_minus_control"]["net_bp"] == pytest.approx(200.)
    assert summary["fewer_than_five_resolved"] == 1


def test_unresolved_controls_and_missing_top_event_do_not_cause_replacement():
    a, b = target("a"), target("b", -1.)
    result = compare([a, b], [control(a, 1, unknown=True), control(b, 1, -1., offset=200)], {"a": .9, "b": .2})
    top = result["overall"]["model_top10"]
    assert top["requested_events"] == 1 and top["paired_events"] == 0 and top["unmatched_events"] == 1
    assert top["candidate_on_all_requested"]["net_bp"] == pytest.approx(280.)
    assert top["control_outcomes"] == {"UNKNOWN": 1}
    assert top["candidate_minus_control"]["net_bp"] is None
    assert result["overall"]["all"]["paired_events"] == 1


def test_raw_control_reuse_is_disclosed_without_dropping_target_pairs():
    a, b = target("a"), target("b")
    result = compare([a, b], [control(a, 1), control(b, 1)])["overall"]["all"]
    assert result["selected_controls"] == 2 and result["unique_raw_controls"] == 1
    assert result["reused_raw_controls"] == 1 and result["max_raw_control_reuse"] == 2
    assert result["paired_events"] == 2


def test_same_raw_control_cannot_have_different_outcomes_for_different_targets():
    a, b = target("a"), target("b")
    with pytest.raises(ControlMetricError, match="inconsistent resolver outcomes"):
        compare([a, b], [control(a, 1, 3.), control(b, 1, -1.)])


def test_missing_control_or_score_and_mixed_splits_are_rejected():
    a = target("a"); selected, labelled = control(a, 1)
    r = receipt([a], [selected])
    with pytest.raises(ControlMetricError, match="sets differ"):
        compare_controls([model(a, .5)], [a], [selected], [], r, split="val")
    with pytest.raises(ControlMetricError, match="exact target split"):
        compare_controls([], [a], [selected], [labelled], r, split="val")
    with pytest.raises(ControlMetricError, match="exact target split"):
        compare_controls([model(a, .5) | {"split": "test"}], [a], [selected], [labelled], r, split="val")


def test_label_lineage_matching_counts_and_fee_drift_are_rejected():
    a = target("a"); selected, labelled = control(a, 1)
    r = receipt([a], [selected]); args = ([model(a, .5)], [a], [selected])
    with pytest.raises(ControlMetricError, match="lineage changed"):
        compare_controls(*args, [labelled | {"source_core_end_i": 999}], r, split="val")
    bad_r = deepcopy(r); bad_r["shortages"][0]["selected"] = 2
    with pytest.raises(ControlMetricError, match="count/order"):
        compare_controls(*args, [labelled], bad_r, split="val")
    bad_label = deepcopy(labelled); bad_label["profit"]["net_r"] = 3.
    with pytest.raises(ControlMetricError, match="round-trip cost"):
        compare_controls(*args, [bad_label], r, split="val")


def test_constant_ranking_missing_baseline_and_economic_drift_are_explicit():
    a = target("a"); selected, labelled = control(a, 1)
    r = receipt([a], [selected]); event = model(a, .5) | {"quality_score": None}
    result = compare_controls([event], [a], [selected], [labelled], r, split="val")
    assert result["overall"]["model_top10_selection"] == "constant_score_arbitrary_id_tiebreak"
    assert result["overall"]["quality_top10"] == {"status": "not_computable_missing_score"}
    with pytest.raises(ControlMetricError, match="economic drift"):
        compare_controls([event | {"net_bp": 300.}], [a], [selected], [labelled], r, split="val")
