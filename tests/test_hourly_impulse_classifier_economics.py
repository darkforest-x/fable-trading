"""Synthetic-only full-size V30 identity, policy, and inference contracts."""
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_classifier_economics as econ
from yoyo.evaluation.hourly_impulse_fixed_clock_research import paired_labels


def fixture_tables():
    """No saved outcomes: deterministic 251 mothers / 744 own-clock controls."""
    accepted = set(np.linspace(3, 250, 78, dtype=int))
    cases, controls, case_context, control_context = [], [], [], []
    index, control_index = 0, 0
    for fold, count in econ.base._FOLD_COUNTS.items():
        start = pd.Timestamp(econ.base.FOLD_BOUNDS[fold][0])
        for ordinal in range(count):
            decision = start + pd.DateOffset(months=ordinal % 6) + pd.Timedelta(days=2+ordinal//6, hours=3)
            event = "synthetic_%03d" % index
            direction = 1 if index % 2 else -1
            state = "unknown" if index < 3 else "accepted" if index in accepted else "abstain"
            context = dict(event_id=event, mother_id=event, fold=fold, direction=direction,
                decision_time=decision, signal_time=decision-pd.Timedelta(hours=1),
                mother_month=decision.strftime("%Y-%m"), request_kind="case",
                control_slot=None, matched_support=index >= 3, classifier_gate_state=state,
                classifier_available_at=decision, classifier_known=state != "unknown",
                classifier_direction=np.nan if state == "unknown" else direction if state == "accepted" else 0,
                classifier_reason="warmup" if state == "unknown" else "known")
            case_context.append(context)
            for hours in econ.base.HORIZONS_HOURS:
                net = .006 + (index % 7)*.0001 if state == "accepted" else -.004
                cases.append(dict({key: context[key] for key in ("event_id", "mother_id", "fold", "direction", "decision_time", "mother_month", "request_kind", "control_slot", "matched_support")},
                    horizon_hours=hours, endpoint_time=decision+pd.Timedelta(hours=hours),
                    role="primary" if hours == 4 else "descriptive", status="known", reason="known",
                    gross_markout=net+.002, cost_threshold_markout=net))
            if index >= 3:
                for slot in range(3):
                    own_decision = decision+pd.Timedelta(hours=1+slot)
                    control_state = "accepted" if control_index < 136 else "abstain"
                    own = dict(context, event_id="control_%03d" % control_index, mother_id=event,
                        signal_time=own_decision-pd.Timedelta(hours=1), decision_time=own_decision,
                        classifier_available_at=own_decision, request_kind="control", control_slot=slot,
                        classifier_gate_state=control_state, classifier_known=True,
                        classifier_direction=direction if control_state == "accepted" else 0,
                        classifier_reason="known")
                    control_context.append(own)
                    for hours in econ.base.HORIZONS_HOURS:
                        controls.append(dict({key: own[key] for key in ("event_id", "mother_id", "fold", "direction", "decision_time", "mother_month", "request_kind", "control_slot", "matched_support")},
                            horizon_hours=hours, endpoint_time=own_decision+pd.Timedelta(hours=hours),
                            role="primary" if hours == 4 else "descriptive", status="known", reason="known",
                            gross_markout=.001, cost_threshold_markout=-.001))
                    control_index += 1
            index += 1
    case_frame, control_frame = pd.DataFrame(cases), pd.DataFrame(controls)
    return [case_frame, control_frame, paired_labels(case_frame, control_frame),
            pd.DataFrame(case_context), pd.DataFrame(control_context)]


@pytest.fixture(scope="module")
def baseline():
    tables = fixture_tables()
    return tables, econ.analyze(*tables)


def replace_net(frame, event, hours, value):
    mask = frame.event_id.eq(event) & frame.horizon_hours.eq(hours)
    frame.loc[mask, "cost_threshold_markout"] = value
    frame.loc[mask, "gross_markout"] = value+.002


def unknown(frame, event, hours):
    mask = frame.event_id.eq(event) & frame.horizon_hours.eq(hours)
    frame.loc[mask, ["status", "reason"]] = ["unknown", "missing_bar"]
    frame.loc[mask, ["gross_markout", "cost_threshold_markout"]] = np.nan


def test_full_grids_exact_denominators_and_never_live_acceptance(baseline):
    _, (tables, result) = baseline
    assert len(tables["case_ledger"]) == 1004
    assert len(tables["control_ledger"]) == 2976
    assert len(tables["mother_ledger"]) == 1004
    assert len(tables["primary_monthly"]) == 96
    assert set(result["primary"]) == set(econ.PRIMARY_SERIES)
    assert result["primary"]["accepted_cost"]["n_total"] == 78
    assert result["primary"]["accepted_excess"]["n_known"] == 78
    assert result["primary"]["policy_excess"]["n_total"] == 251
    assert result["primary"]["policy_excess"]["n_known"] == 248
    assert result["primary"]["policy_delta"]["n_unknown"] == 3
    assert result["decision"]["exploratory_continue"] is True
    assert result["decision"]["economic_acceptance"] is False
    assert result["decision"]["production_eligible"] is False
    assert result["population"] is not econ.COUNTS
    assert result["population"]["case"] is not econ.COUNTS["case"]
    json.dumps(result, allow_nan=False)


def test_accepted_uses_full_original_control_triples_not_only_passing_controls(baseline):
    _, (tables, result) = baseline
    mothers = tables["mother_ledger"]
    accepted = mothers.loc[mothers.horizon_hours.eq(4) & mothers[econ.GATE].eq("accepted")]
    assert accepted.n_controls_assigned.sum() == 234
    assert accepted.control_gate_abstain.sum() > 0
    assert (accepted.accepted_excess-accepted.accepted_cost).to_numpy() == pytest.approx(np.full(78, .001))
    assert (accepted.policy_excess != accepted.accepted_excess).any()


def test_monthly_shared_rng_and_exact_plus_one_holm(baseline):
    _, (tables, result) = baseline
    rng = np.random.Generator(np.random.PCG64(20260907))
    indices = rng.integers(0, 24, size=(9999, 24), dtype=np.int64)
    signs = 2*rng.integers(0, 2, size=(9999, 24), dtype=np.int64)-1
    mothers = tables["mother_ledger"].query("horizon_hours == 4")
    raw = {}
    for name in econ.PRIMARY_SERIES:
        source = mothers.loc[mothers[econ.GATE].eq("accepted")] if name.startswith("accepted_") else mothers
        grouped = source.groupby("mother_month")[name]
        sums = grouped.sum().reindex(econ.base.MONTHS, fill_value=0).to_numpy()
        counts = grouped.count().reindex(econ.base.MONTHS, fill_value=0).to_numpy()
        ci = np.quantile(sums[indices].sum(axis=1)/counts[indices].sum(axis=1), [.025, .975], method="linear")
        p = (1+int(((signs*sums).sum(axis=1) >= sums.sum()).sum()))/10000
        actual = result["primary"][name]["monthly_cluster"]
        assert actual["ci95"] == pytest.approx(ci)
        assert actual["one_sided_p"] == p
        raw[name] = p
    wanted, previous = {}, 0.
    for rank, key in enumerate(sorted(raw, key=lambda key: (raw[key], key))):
        previous = max(previous, min(1., (4-rank)*raw[key]))
        wanted[key] = previous
    assert {name: result["primary"][name]["holm_p"] for name in raw} == wanted
    assert econ._holm(dict(a=.01, b=.03, c=.031, d=.9)) == dict(a=.04, b=.09, c=.09, d=.9)
    assert econ._holm(dict(a=.01, b=.03, c=None, d=None)) == dict(a=.04, b=.09, c=None, d=None)


@pytest.mark.parametrize("kind,state", [("case", "abstain"), ("case", "accepted"),
                                         ("control", "abstain"), ("control", "accepted")])
def test_unknown_future_labels_distinct_from_known_zero_policy(kind, state):
    inputs = fixture_tables()
    labels, context = (inputs[0], inputs[3]) if kind == "case" else (inputs[1], inputs[4])
    event = context.loc[context[econ.GATE].eq(state), "event_id"].iloc[0]
    unknown(labels, event, 4)
    inputs[2] = paired_labels(inputs[0], inputs[1])
    tables, result = econ.analyze(*inputs)
    ledger = tables[kind+"_ledger"]
    row = ledger.loc[ledger.event_id.eq(event) & ledger.horizon_hours.eq(4)].iloc[0]
    assert np.isnan(row.cost_threshold_markout)
    if state == "abstain":
        assert row.policy_cost_threshold_markout == 0
        assert row.policy_reason == "observed_abstention"
        assert row.policy_known
    else:
        assert np.isnan(row.policy_cost_threshold_markout)
        assert row.policy_reason == "unknown_accepted_label"
        assert not row.policy_known
    if kind == "control" and state == "abstain":
        pair = tables["mother_ledger"].loc[lambda frame: frame.event_id.eq(row.mother_id) & frame.horizon_hours.eq(4)].iloc[0]
        assert not pair.pair_complete
        assert pair.control_policy_complete
        assert pair.control_policy_known_count == 3
    if kind == "case" and state == "abstain":
        pair = tables["mother_ledger"].loc[lambda frame: frame.event_id.eq(event) & frame.horizon_hours.eq(4)].iloc[0]
        assert np.isnan(pair.policy_delta)


def test_unknown_gate_never_flat_even_when_label_known(baseline):
    _, (tables, _) = baseline
    cases = tables["case_ledger"].loc[lambda frame: frame[econ.GATE].eq("unknown")]
    assert len(cases) == 12
    assert cases.cost_threshold_markout.notna().all()
    assert cases.policy_cost_threshold_markout.isna().all()
    assert cases.policy_reason.eq("unknown_gate").all()


def test_primary_failure_cannot_be_rescued_by_other_horizons():
    inputs = fixture_tables()
    accepted = inputs[3].loc[inputs[3][econ.GATE].eq("accepted"), "event_id"]
    for event in accepted:
        replace_net(inputs[0], event, 4, -.01)
        replace_net(inputs[0], event, 24, 2.)
    inputs[2] = paired_labels(inputs[0], inputs[1])
    _, result = econ.analyze(*inputs)
    assert result["decision"]["exploratory_continue"] is False
    assert result["horizons"]["24"]["groups"]["case"]["accepted"]["net"]["mean"] == 2.
    assert "one_sided_p" not in json.dumps(result["horizons"])


def test_positive_pooled_result_still_requires_all_four_half_means():
    inputs = fixture_tables()
    ctx = inputs[3]
    accepted = ctx.loc[ctx[econ.GATE].eq("accepted") & ctx.fold.eq("2023H1"), "event_id"]
    for event in accepted:
        replace_net(inputs[0], event, 4, -.00001)
    inputs[2] = paired_labels(inputs[0], inputs[1])
    _, result = econ.analyze(*inputs)
    assert result["primary"]["accepted_cost"]["mean"] > 0
    assert not result["decision"]["four_half_accepted_means_positive"]
    assert not result["decision"]["exploratory_continue"]


def test_retained_rejected_contributions_and_all_outliers_kept():
    inputs = fixture_tables()
    event = inputs[3].loc[inputs[3][econ.GATE].eq("accepted"), "event_id"].iloc[0]
    replace_net(inputs[0], event, 4, 9.)
    inputs[2] = paired_labels(inputs[0], inputs[1])
    tables, result = econ.analyze(*inputs)
    groups = result["horizons"]["4"]["groups"]["case"]
    assert groups["accepted"]["net"]["maximum"] == 9.
    assert groups["accepted"]["net"]["iqr_outlier_count"] > 0
    assert groups["accepted"]["net"]["iqr_outliers_removed"] == 0
    assert groups["abstain"]["net"]["negative_sum"] == pytest.approx(-.004*170)
    assert sum(groups[state]["net"]["sum"] for state in econ.STATES) == pytest.approx(groups["all"]["net"]["sum"])
    assert groups["all"]["net"]["sum"] == pytest.approx(tables["case_ledger"].query("horizon_hours == 4").cost_threshold_markout.sum())


def test_order_invariance_no_mutation_extra_fields_not_used(baseline):
    inputs, (_, expected) = baseline
    copies = [frame.copy(deep=True) for frame in inputs]
    for index, frame in enumerate(copies):
        frame["unused_old_pnl"] = np.inf
        copies[index] = frame.sample(frac=1, random_state=index)
    _, actual = econ.analyze(*copies)
    assert actual == expected
    for original, copied in zip(inputs, copies):
        pd.testing.assert_frame_equal(original, copied.drop(columns="unused_old_pnl").sort_index())


@pytest.mark.parametrize("table,field,value", [
    (0, "direction", True), (0, "endpoint_time", "2023-01-03T05:00:00Z"),
    (0, "control_slot", 0), (0, "gross_markout", np.inf),
    (0, "cost_threshold_markout", .8), (0, "status", "missing"),
    (0, "matched_support", 1), (0, "horizon_hours", True),
    (1, "mother_id", "synthetic_004"), (1, "control_slot", 1),
    (1, "direction", 1), (1, "decision_time", "2023-04-03T05:00:00Z"),
    (1, "cost_threshold_markout", .1), (1, "reason", "missing_bar"),
    (2, "control_mean_gross_markout", 6.), (2, "n_controls_assigned", 3),
    (3, "classifier_known", 0), (3, "classifier_direction", 0),
    (3, "classifier_gate_state", "abstain"), (3, "classifier_reason", "known"),
    (3, "signal_time", "2023-01-03T00:00:00Z"),
    (3, "classifier_available_at", "2023-01-03T04:00:00Z"),
    (3, "decision_time", "2023-01-03T03:00:00"),
    (3, "decision_time", "2023-01-03T03:00:00.000000001Z"),
    (4, "mother_month", "2024-01"), (4, "matched_support", False),
    (4, "classifier_gate_state", "abstain"), (4, "classifier_direction", 0),
])
def test_invalid_identity_clocks_status_cost_and_pairs_rejected(table, field, value):
    inputs = fixture_tables()
    # Control0's true direction is +1; flip it for the explicit direction case.
    if table == 1 and field == "direction":
        value = -int(inputs[1].iloc[0].direction)
    inputs[table][field] = inputs[table][field].astype(object)
    inputs[table].at[0, field] = value
    with pytest.raises(ValueError):
        econ.analyze(*inputs)


@pytest.mark.parametrize("table", range(5))
def test_no_missing_duplicate_or_unrecognized_population_rows(table):
    inputs = fixture_tables()
    inputs[table] = inputs[table].iloc[1:]
    with pytest.raises(ValueError):
        econ.analyze(*inputs)
    inputs = fixture_tables()
    inputs[table] = pd.concat([inputs[table], inputs[table].iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError):
        econ.analyze(*inputs)


def test_control_aggregate_tamper_detected_even_when_cost_consistent():
    inputs = fixture_tables()
    replace_net(inputs[1], inputs[1].event_id.iloc[0], 4, .04)
    with pytest.raises(ValueError, match="individual-control reconstruction"):
        econ.analyze(*inputs)


def test_pair_control_id_tamper_detected():
    inputs = fixture_tables()
    index = inputs[2].index[inputs[2].matched_support][0]
    inputs[2].at[index, "control_event_ids"] = json.dumps(["wrong"]*3)
    with pytest.raises(ValueError, match="control IDs"):
        econ.analyze(*inputs)


def test_accepted_context_cannot_steal_an_unsupported_unknown_mother():
    inputs = fixture_tables()
    context = inputs[3]
    accepted_index = context.index[context[econ.GATE].eq("accepted")][0]
    for index, state in ((0, "accepted"), (accepted_index, "unknown")):
        context.at[index, econ.GATE] = state
        context.at[index, "classifier_known"] = state == "accepted"
        context.at[index, "classifier_reason"] = "known" if state == "accepted" else "warmup"
        context.at[index, "classifier_direction"] = context.at[index, "direction"] if state == "accepted" else np.nan
    with pytest.raises(ValueError, match="original control triples"):
        econ.analyze(*inputs)


def test_no_input_output_or_training_api_in_pure_module():
    source = inspect.getsource(econ)
    for forbidden in ("read_csv(", "to_csv(", "open(", "read_text(", "write_text(", "fit(", "sample("):
        assert forbidden not in source
    assert "9999" in source and "20260907" in source
