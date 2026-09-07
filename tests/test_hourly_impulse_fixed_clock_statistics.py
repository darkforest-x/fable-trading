"""Synthetic-only checks for V24 fixed-clock analysis and preregistered gates."""
import ast
import copy
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_fixed_clock_statistics as stats


def fixture_tables():
    """251 synthetic identities, exact frozen fold counts and 248 supports."""
    cases, pairs = [], []
    index = 0
    for fold, count in stats._FOLD_COUNTS.items():
        start = pd.Timestamp(stats.FOLD_BOUNDS[fold][0])
        for ordinal in range(count):
            stamp = start + pd.DateOffset(months=ordinal % 6) + pd.Timedelta(days=2+ordinal//6, hours=3)
            event = "synthetic_%03d" % index
            supported = index >= 3
            for hours in stats.HORIZONS_HOURS:
                net = .004 + (index % 7)*.0001
                gross = net + .002
                cn = .001
                cg = cn + .002
                identity = dict(event_id=event, mother_id=event, fold=fold, direction=1 if index % 2 else -1,
                                decision_time=stamp, mother_month=stamp.strftime("%Y-%m"), horizon_hours=hours,
                                role="primary" if hours == 4 else "descriptive", matched_support=supported)
                cases.append(dict(identity, request_kind="case", status="known", reason="known",
                                  gross_markout=gross, cost_threshold_markout=net))
                pairs.append(dict(identity, case_status="known", case_reason="known", case_known=True,
                                  n_controls_expected=3, n_controls_assigned=3 if supported else 0,
                                  n_controls_known=3 if supported else 0, pair_complete=supported,
                                  pair_reason="known" if supported else "unmatched_support",
                                  case_gross_markout=gross, case_cost_threshold_markout=net,
                                  control_mean_gross_markout=cg if supported else np.nan,
                                  control_mean_cost_threshold_markout=cn if supported else np.nan,
                                  gross_excess_markout=gross-cg if supported else np.nan,
                                  cost_threshold_excess_markout=net-cn if supported else np.nan))
            index += 1
    return pd.DataFrame(cases), pd.DataFrame(pairs)


def run(cases, pairs):
    return stats.analyze_fixed_clock_statistics(cases, pairs, stats.FOLD_BOUNDS)


def unknown_case(cases, pairs, mask):
    cases.loc[mask, ["status", "reason"]] = ["unknown", "missing_bar"]
    cases.loc[mask, ["gross_markout", "cost_threshold_markout"]] = np.nan
    pairs.loc[mask, ["case_status", "case_reason", "case_known", "pair_complete"]] = ["unknown", "missing_bar", False, False]
    pairs.loc[mask & pairs.matched_support, "pair_reason"] = "unknown_case_label"
    pairs.loc[mask & ~pairs.matched_support, "pair_reason"] = "unmatched_support"
    pairs.loc[mask, ["case_gross_markout", "case_cost_threshold_markout", "gross_excess_markout", "cost_threshold_excess_markout"]] = np.nan


def set_case_net(cases, pairs, mask, net):
    cases.loc[mask, "cost_threshold_markout"] = net
    cases.loc[mask, "gross_markout"] = cases.loc[mask, "cost_threshold_markout"] + .002
    pairs.loc[mask, "case_cost_threshold_markout"] = cases.loc[mask, "cost_threshold_markout"]
    pairs.loc[mask, "case_gross_markout"] = cases.loc[mask, "gross_markout"]
    complete = mask & pairs.pair_complete
    pairs.loc[complete, "gross_excess_markout"] = pairs.loc[complete, "case_gross_markout"]-pairs.loc[complete, "control_mean_gross_markout"]
    pairs.loc[complete, "cost_threshold_excess_markout"] = pairs.loc[complete, "case_cost_threshold_markout"]-pairs.loc[complete, "control_mean_cost_threshold_markout"]


def test_positive_fixture_has_explicit_denominators_and_never_profitability():
    cases, pairs = fixture_tables()
    result = run(cases, pairs)
    assert result["decision"]["exploratory_continue"] is True
    assert result["decision"]["profitability_accepted"] is False
    assert result["decision"]["production_eligible"] is False
    for hours, row in result["horizons"].items():
        assert row["all_case"]["n_total"] == 251
        assert row["all_case"]["n_known"] == 251
        assert row["paired_excess"]["n_total"] == 251
        assert row["paired_excess"]["n_known"] == 248
        assert row["paired_excess"]["n_unknown"] == 3
        assert row["paired_case"]["n_known"] == 248
        assert sum(item["n_known"] for item in row["all_case"]["monthly_cluster"]["months"]) == 251
        if hours != "4":
            assert "one_sided_p" not in row["all_case"]["monthly_cluster"]
            assert "ci95" not in row["paired_excess"]["monthly_cluster"]
    json.dumps(result, allow_nan=False)


def test_shared_seed_arrays_exact_weighting_and_plus_one():
    cases, pairs = fixture_tables()
    result = run(cases, pairs)
    rng = np.random.Generator(np.random.PCG64(20260907))
    indices = rng.integers(0, 24, size=(9999, 24), dtype=np.int64)
    signs = 2*rng.integers(0, 2, size=(9999, 24), dtype=np.int64)-1
    for name, frame, column in (("all_case", cases, "cost_threshold_markout"), ("paired_excess", pairs, "cost_threshold_excess_markout")):
        frame = frame[frame.horizon_hours == 4]
        grouped = frame.groupby("mother_month")[column]
        sums = grouped.sum().reindex(stats.MONTHS, fill_value=0).to_numpy()
        counts = grouped.count().reindex(stats.MONTHS, fill_value=0).to_numpy()
        means = sums[indices].sum(axis=1)/counts[indices].sum(axis=1)
        expected_ci = np.quantile(means, [.025, .975], method="linear")
        actual = result["horizons"]["4"][name]["monthly_cluster"]
        assert actual["ci95"] == pytest.approx(expected_ci)
        assert actual["one_sided_p"] == (1+np.count_nonzero((signs*sums).sum(axis=1) >= sums.sum()))/10000
        assert result["horizons"]["4"][name]["mean"] == pytest.approx(frame[column].mean())


def test_unknowns_remain_unknown_and_no_all_clock_complete_case_filter():
    cases, pairs = fixture_tables()
    mask = (cases.horizon_hours == 24) & (cases.index < 100)
    unknown_case(cases, pairs, mask)
    result = run(cases, pairs)
    assert result["horizons"]["24"]["all_case"]["n_known"] == 226
    assert result["horizons"]["4"]["all_case"]["n_known"] == 251
    assert result["decision"]["exploratory_continue"] is True


def test_primary_known_gate_is_226_not_outcome_shrunk_denominator():
    cases, pairs = fixture_tables()
    mask = (cases.horizon_hours == 4) & (cases.index >= 12) & (cases.index < 116)
    unknown_case(cases, pairs, mask)
    result = run(cases, pairs)
    assert result["horizons"]["4"]["all_case"]["n_known"] == 225
    assert result["decision"]["status"] == "inconclusive_coverage"
    assert result["decision"]["exploratory_continue"] is False


def test_unknown_control_invalidates_whole_triplet_without_case_deletion():
    cases, pairs = fixture_tables()
    row = pairs.index[(pairs.event_id == "synthetic_010") & (pairs.horizon_hours == 4)][0]
    pairs.loc[row, ["n_controls_known", "pair_complete", "pair_reason"]] = [2, False, "unknown_control_label"]
    pairs.loc[row, ["control_mean_gross_markout", "control_mean_cost_threshold_markout", "gross_excess_markout", "cost_threshold_excess_markout"]] = np.nan
    result = run(cases, pairs)
    assert result["horizons"]["4"]["paired_excess"]["n_known"] == 247
    assert result["horizons"]["4"]["all_case"]["n_known"] == 251


def test_all_zero_excess_has_p_one_and_is_not_support():
    cases, pairs = fixture_tables()
    mask = pairs.pair_complete
    pairs.loc[mask, "control_mean_gross_markout"] = pairs.loc[mask, "case_gross_markout"]
    pairs.loc[mask, "control_mean_cost_threshold_markout"] = pairs.loc[mask, "case_cost_threshold_markout"]
    pairs.loc[mask, ["gross_excess_markout", "cost_threshold_excess_markout"]] = 0.
    result = run(cases, pairs)
    assert result["horizons"]["4"]["paired_excess"]["monthly_cluster"]["one_sided_p"] == 1.
    assert result["horizons"]["4"]["paired_excess"]["monthly_cluster"]["ci95"] == [0., 0.]
    assert not result["decision"]["exploratory_continue"]


def test_negative_fold_fails_continue_even_when_pooled_primary_positive():
    cases, pairs = fixture_tables()
    set_case_net(cases, pairs, cases.fold == "2023H1", -.0001)
    result = run(cases, pairs)
    assert result["horizons"]["4"]["all_case"]["mean"] > 0
    assert not result["decision"]["four_fold_case_means_positive"]
    assert not result["decision"]["exploratory_continue"]


def test_profitable_descriptive_clocks_cannot_replace_failed_4h():
    cases, pairs = fixture_tables()
    set_case_net(cases, pairs, cases.horizon_hours == 4, -.002)
    set_case_net(cases, pairs, cases.horizon_hours != 4, 2.)
    result = run(cases, pairs)
    assert not result["decision"]["exploratory_continue"]
    assert result["horizons"]["24"]["all_case"]["mean"] == 2.
    assert "one_sided_p" not in result["horizons"]["24"]["all_case"]["monthly_cluster"]


def test_case_positive_without_background_excess_is_not_support():
    cases, pairs = fixture_tables()
    supported = pairs.matched_support
    pairs.loc[supported, "control_mean_cost_threshold_markout"] = .1
    pairs.loc[supported, "control_mean_gross_markout"] = .102
    pairs.loc[supported, "gross_excess_markout"] = pairs.loc[supported, "case_gross_markout"]-.102
    pairs.loc[supported, "cost_threshold_excess_markout"] = pairs.loc[supported, "case_cost_threshold_markout"]-.1
    result = run(cases, pairs)
    assert result["horizons"]["4"]["all_case"]["mean"] > 0
    assert result["horizons"]["4"]["paired_excess"]["mean"] < 0
    assert not result["decision"]["exploratory_continue"]


def test_all_tails_preserved_and_descriptives_use_sample_sd():
    cases, pairs = fixture_tables()
    set_case_net(cases, pairs, cases.event_id == "synthetic_250", 8.)
    result = run(cases, pairs)
    actual = result["horizons"]["4"]["all_case"]
    values = cases.loc[cases.horizon_hours == 4, "cost_threshold_markout"]
    assert actual["maximum"] == 8.
    assert actual["mean"] == pytest.approx(values.mean())
    assert actual["sd"] == pytest.approx(values.std(ddof=1))
    assert actual["iqr"] == pytest.approx(values.quantile(.75)-values.quantile(.25))


def test_row_order_irrelevant_inputs_unchanged_and_extra_old_outcomes_ignored():
    cases, pairs = fixture_tables()
    before_cases, before_pairs = cases.copy(deep=True), pairs.copy(deep=True)
    first = run(cases, pairs)
    cases["old_exit"] = "synthetic_unread"
    pairs["old_mfe"] = np.inf
    second = run(cases.sample(frac=1, random_state=7), pairs.sample(frac=1, random_state=9))
    assert first == second
    pd.testing.assert_frame_equal(cases.drop(columns="old_exit"), before_cases)
    pd.testing.assert_frame_equal(pairs.drop(columns="old_mfe"), before_pairs)


@pytest.mark.parametrize("table,column,value", [
    ("case", "role", "descriptive"), ("case", "horizon_hours", 5),
    ("case", "horizon_hours", True), ("case", "direction", True),
    ("case", "mother_month", "2024-01"), ("case", "fold", "2024H1"),
    ("case", "decision_time", "2023-01-03 03:00:00"),
    ("case", "decision_time", "2023-01-03T03:01:00Z"),
    ("case", "decision_time", 1), ("case", "request_kind", "control"),
    ("case", "matched_support", 1), ("case", "gross_markout", np.inf),
    ("case", "cost_threshold_markout", .01), ("case", "reason", "missing_bar"),
    ("pair", "case_known", False), ("pair", "case_status", "unknown"),
    ("pair", "case_cost_threshold_markout", .8), ("pair", "n_controls_expected", 2),
    ("pair", "n_controls_assigned", 2), ("pair", "n_controls_known", 2),
    ("pair", "pair_complete", False), ("pair", "gross_excess_markout", .4),
    ("pair", "pair_reason", "unknown_control_label"),
    ("pair", "cost_threshold_excess_markout", .4),
    ("pair", "control_mean_cost_threshold_markout", .4),
])
def test_contract_tampering_is_rejected(table, column, value):
    cases, pairs = fixture_tables()
    frame = cases if table == "case" else pairs
    row = frame.index[(frame.event_id == "synthetic_010") & (frame.horizon_hours == 4)][0]
    frame[column] = frame[column].astype(object)
    frame.at[row, column] = value
    with pytest.raises((ValueError, TypeError)):
        run(cases, pairs)


@pytest.mark.parametrize("column", ["gross_markout", "cost_threshold_markout"])
def test_unknown_case_zero_fill_rejected(column):
    cases, pairs = fixture_tables()
    mask = (cases.event_id == "synthetic_010") & (cases.horizon_hours == 4)
    unknown_case(cases, pairs, mask)
    cases.loc[mask, column] = 0.
    with pytest.raises(ValueError, match="zero-filled"):
        run(cases, pairs)


@pytest.mark.parametrize("column", ["gross_excess_markout", "cost_threshold_excess_markout", "control_mean_gross_markout", "control_mean_cost_threshold_markout"])
def test_unmatched_control_or_excess_zero_fill_rejected(column):
    cases, pairs = fixture_tables()
    pairs.loc[(pairs.event_id == "synthetic_000") & (pairs.horizon_hours == 4), column] = 0.
    with pytest.raises(ValueError, match="zero-filled"):
        run(cases, pairs)


@pytest.mark.parametrize("table", ["case", "pair"])
def test_duplicate_event_clock_rejected(table):
    cases, pairs = fixture_tables()
    if table == "case":
        cases = pd.concat([cases, cases.iloc[[0]]], ignore_index=True)
    else:
        pairs = pd.concat([pairs, pairs.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate event/horizon"):
        run(cases, pairs)


def test_dropped_unknown_mothers_rejected():
    cases, pairs = fixture_tables()
    with pytest.raises(ValueError, match="251"):
        run(cases[cases.matched_support], pairs[pairs.matched_support])


def test_fold_boundary_contract_cannot_be_extended():
    cases, pairs = fixture_tables()
    bounds = copy.deepcopy(stats.FOLD_BOUNDS)
    bounds["2024H2"] = (bounds["2024H2"][0], "2025-01-02T00:00:00Z")
    with pytest.raises(ValueError, match="cannot change"):
        stats.analyze_fixed_clock_statistics(cases, pairs, bounds)


def test_cross_horizon_support_identity_cannot_change():
    cases, pairs = fixture_tables()
    cases.loc[(cases.event_id == "synthetic_010") & (cases.horizon_hours == 4), "matched_support"] = False
    with pytest.raises(ValueError, match="changes across horizons"):
        run(cases, pairs)


def test_nullable_unknown_is_allowed_without_imputation():
    cases, pairs = fixture_tables()
    mask = (cases.event_id == "synthetic_010") & (cases.horizon_hours == 4)
    unknown_case(cases, pairs, mask)
    for frame, columns in ((cases, ["gross_markout", "cost_threshold_markout"]), (pairs, ["case_gross_markout", "case_cost_threshold_markout", "gross_excess_markout", "cost_threshold_excess_markout"])):
        for column in columns:
            frame[column] = frame[column].astype("Float64")
    assert run(cases, pairs)["horizons"]["4"]["all_case"]["n_unknown"] == 1


def test_no_known_primary_is_honest_and_json_safe():
    cases, pairs = fixture_tables()
    unknown_case(cases, pairs, cases.horizon_hours == 4)
    result = run(cases, pairs)
    assert result["decision"]["status"] == "inconclusive_coverage"
    assert result["horizons"]["4"]["all_case"]["monthly_cluster"]["ci95"] is None
    json.dumps(result, allow_nan=False)


def test_zero_known_bootstrap_draws_are_not_redrawn_or_dropped():
    values = [np.nan]*251
    values[0] = .01
    months = ["2023-01"]*251
    rng = np.random.Generator(np.random.PCG64(20260907))
    indices = rng.integers(0, 24, size=(9999, 24), dtype=np.int64)
    signs = 2*rng.integers(0, 2, size=(9999, 24), dtype=np.int64)-1
    result = stats._monthly(values, months, indices, signs)
    assert result["bootstrap_zero_count_draws"] > 0
    assert result["ci95"] is None
    assert result["inference_status"] == "bootstrap_contains_zero_known_count"


@pytest.mark.parametrize("values", [[.01]*24, [1e16, 1., -1e16]+[0.]*21, [.1, .2, .3]+[0.]*21])
def test_signflip_counts_ties_using_the_same_reduction(values):
    indices = np.tile(np.arange(24, dtype=np.int64), (9999, 1))
    signs = -np.ones((9999, 24), dtype=np.int64)
    signs[0] = 1
    sums = np.asarray(values)
    expected = (1+np.count_nonzero((signs*sums).sum(axis=1) >= sums.sum()))/10000
    result = stats._monthly(values, list(stats.MONTHS), indices, signs)
    assert result["one_sided_p"] == expected


@pytest.mark.parametrize("replacement", ["known", "unknown_case_label", "unknown_control_label", "anything"])
def test_unmatched_reason_must_not_claim_a_different_failure(replacement):
    cases, pairs = fixture_tables()
    pairs.loc[(pairs.event_id == "synthetic_000") & (pairs.horizon_hours == 4), "pair_reason"] = replacement
    with pytest.raises(ValueError, match="pair reason"):
        run(cases, pairs)


def test_module_has_no_io_strategy_imports_or_dynamic_evaluation():
    tree = ast.parse(inspect.getsource(stats))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(name and name.startswith("yoyo") for name in imports)
    forbidden = {"open", "eval", "exec", "read_csv", "read_parquet", "read_text", "to_csv", "to_json"}
    called = {getattr(node.func, "id", getattr(node.func, "attr", None)) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not forbidden & called
