"""Synthetic-only full-denominator cached structure-gate accounting checks."""
import ast
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_structure_accounting as a


def episode(ids, times, values, parents=None):
    t = pd.to_datetime(times, utc=True, format="mixed")
    frame = pd.DataFrame({"event_id": ids, "signal_time": t-pd.Timedelta(hours=1),
        "decision_time": t, "direction": 1, "signal_close": 100., "fold": "2024H1",
        "mother_signal_time": t-pd.Timedelta(hours=1), "mother_decision_time": t,
        "mother_deadline": t+pd.Timedelta(hours=72), "terminal_time": t,
        "occupied_until": t+pd.Timedelta(hours=2), "entry_time": t,
        "exit_time": t+pd.Timedelta(hours=2), "status": "request_emitted",
        "episode_status": "transition_colour_exit", "episode_net_return": values,
        "observed": True, "executed": True, "completed_trade": True,
        "old_opaque_diagnostic": ["kept:"+x for x in ids]})
    if parents is not None:
        frame["parent_event_id"] = parents
    return frame


def context_for(frame, population, states=None):
    result = frame[["event_id", "signal_time", "decision_time", "direction", "signal_close", "fold"]].copy()
    if "parent_event_id" in frame:
        result["parent_event_id"] = frame.parent_event_id
    result["population"] = population
    states = states or ["accepted"]*len(frame)
    result["structure_gate_state"] = states
    result["structure_known"] = result.structure_gate_state.ne("unknown")
    result["structure_state"] = pd.array([
        direction if state == "accepted" else -direction if state == "abstain" else pd.NA
        for direction, state in zip(frame.direction, states)], dtype="Int64")
    result["structure_available_at"] = frame.decision_time
    result["structure_signal_close"] = frame.signal_close
    result["structure_reason"] = np.where(result.structure_known, "known", "no_confirmed_break")
    return result


def fixture(case_states=None, control_states=None):
    cases = episode(["c0", "c1", "c2"], ["2024-01-01", "2024-01-01T01:00Z", "2024-01-05"], [-.01, .02, .005])
    controls = episode(["r0", "r1", "r2"], ["2024-01-02", "2024-01-02T01:00Z", "2024-01-02T02:00Z"],
        [-.005, .01, .015], ["c0"]*3)
    context = pd.concat([context_for(cases, "case", case_states), context_for(controls, "control", control_states)], ignore_index=True)
    return cases, controls, context


def run(case_states=None, control_states=None):
    return a.evaluate_cached(*fixture(case_states, control_states))


def test_all_accepted_old_fields_exact_inputs_untouched_and_all_denominators():
    cases, controls, context = fixture()
    cases.index = [8, 3, 99]
    controls.index = [7, 44, 2]
    originals = [x.copy(deep=True) for x in (cases, controls, context)]
    tables, summary = a.evaluate_cached(cases, controls, context.iloc[::-1])
    for population, original in (("case", cases), ("control", controls)):
        for arm in ("baseline", "candidate"):
            pd.testing.assert_frame_equal(original, tables[f"{arm}_{population}_episodes"][original.columns], check_exact=True)
    for original, unchanged in zip(originals, (cases, controls, context)):
        pd.testing.assert_frame_equal(original, unchanged, check_exact=True)
    assert summary["population"] == {"cases": 3, "controls": 3, "matched_cases": 1, "unmatched_cases": 2}
    assert summary["effects"]["case_delta"]["total_pairs"] == 3
    assert summary["effects"]["excess_delta"]["n"] == 1
    assert summary["effects"]["excess_delta"]["unknown_pairs"] == 2
    assert tables["case_delta"].difference.eq(0).all()
    assert tables["baseline_matched"].assigned_controls.tolist() == [3, 0, 0]


def test_abstention_unknown_and_completed_trade_denominators_and_fees():
    tables, summary = run(["accepted", "abstain", "unknown"])
    candidate = tables["candidate_case_episodes"]
    assert candidate.episode_net_return.iloc[:2].tolist() == [-.01, 0.]
    assert np.isnan(candidate.episode_net_return.iloc[2])
    assert candidate.policy_fee_fraction.iloc[:2].tolist() == [.002, 0.]
    assert np.isnan(candidate.policy_fee_fraction.iloc[2])
    assert candidate.observed.tolist() == [True, True, False]
    assert candidate.executed.tolist() == [True, False, False]
    assert candidate.entry_time.iloc[1:].isna().all() and candidate.exit_time.iloc[1:].isna().all()
    assert candidate.occupied_until.iloc[1] == candidate.mother_decision_time.iloc[1]
    assert candidate.occupied_until.iloc[2] == candidate.mother_deadline.iloc[2]
    m = summary["arms"]["candidate"]["case"]["independent"]["all"]
    assert m["opportunities"] == 3 and m["known_opportunities"] == 2 and m["unknown_opportunities"] == 1
    assert m["completed_trades"] == 1 and m["mean_net_bp"] == -100
    assert m["mean_gross_bp"] == -80 and m["all_opportunity_mean_net_bp"] == -50
    assert m["opportunity_mean_denominator"] == 2


def test_known_abstention_releases_slots_and_unknown_reserves_horizon():
    tables, _ = run(["abstain", "accepted", "accepted"], ["abstain", "accepted", "accepted"])
    assert tables["baseline_case_serial"].portfolio_selected.tolist() == [True, False, True]
    assert tables["candidate_case_serial"].portfolio_selected.tolist() == [True, True, True]
    assert tables["baseline_control_serial"].portfolio_selected.tolist() == [True, False, True]
    assert tables["candidate_control_serial"].portfolio_selected.tolist() == [True, True, False]
    delta = tables["serial_delta"].set_index("event_id")
    assert delta.loc["c1", "before"] == 0 and delta.loc["c1", "after"] == .02
    tables, _ = run(["unknown", "accepted", "accepted"])
    assert tables["candidate_case_serial"].portfolio_selected.tolist() == [True, False, True]
    assert np.isnan(tables["serial_delta"].set_index("event_id").loc["c0", "after"])
    assert tables["serial_delta"].set_index("event_id").loc["c1", "after"] == 0


def test_own_controls_gate_independently_and_I_equals_D_minus_same_triplet_D():
    tables, summary = run(["abstain", "accepted", "accepted"], ["accepted", "abstain", "accepted"])
    assert tables["candidate_control_episodes"].executed.tolist() == [True, False, True]
    old, new = (tables[arm+"_matched"].set_index("event_id") for arm in ("baseline", "candidate"))
    assert old.loc["c0", "control_mean_return"] == pytest.approx((-.005+.01+.015)/3)
    assert new.loc["c0", "control_mean_return"] == pytest.approx((-.005+0+.015)/3)
    d = tables["case_delta"].set_index("event_id").difference
    c = tables["matched_control_delta"].set_index("event_id").difference
    i = tables["excess_delta"].set_index("event_id").difference
    np.testing.assert_allclose(i, d-c, equal_nan=True)
    assert summary["effects"]["control_delta"]["total_pairs"] == 3
    assert i.iloc[1:].isna().all()
    tables, _ = run(control_states=["accepted", "unknown", "accepted"])
    assert tables["candidate_matched"].control_mean_return.isna().all()
    assert len(tables["candidate_matched"]) == 3


def test_retrospective_opportunity_cost_reports_both_losers_and_winners():
    tables, _ = run(["abstain", "abstain", "accepted"])
    rows = tables["case_mechanics"].set_index("event_id")
    assert rows.loc["c0", "avoided_net_loser"] and rows.loc["c0", "avoided_loss_event_bp"] == 100
    assert rows.loc["c1", "missed_net_winner"] and rows.loc["c1", "missed_winner_event_bp"] == 200
    group = tables["mechanism_groups"].query("population == 'case' and structure_gate_state == 'abstain'").iloc[0]
    assert group.known_pairs == 2 and group.mean_delta_bp == pytest.approx(-50)


def test_all_abstain_zero_is_not_win_or_completed_trade_and_empty_folds_are_explicit():
    tables, summary = run(["abstain"]*3, ["abstain"]*3)
    m = summary["arms"]["candidate"]["case"]["independent"]["all"]
    assert m["all_opportunity_mean_net_bp"] == 0 and m["completed_trades"] == 0
    assert np.isnan(m["mean_net_bp"]) and np.isnan(m["profit_factor"]) and np.isnan(m["win_rate"])
    assert len(tables["metrics"]) == 40
    empty = tables["metrics"].query("fold == '2023H1'")
    assert len(empty) == 8 and empty.opportunities.eq(0).all()
    assert empty.all_opportunity_mean_net_bp.isna().all()


def test_accepted_unknown_old_exit_does_not_become_a_known_realised_partial():
    cases, controls, context = fixture(["accepted", "abstain", "accepted"])
    cases.loc[0, ["observed", "completed_trade"]] = False
    cases.loc[0, "episode_net_return"] = np.nan
    cases.loc[0, "occupied_until"] = cases.loc[0, "mother_deadline"]
    cases["realised_partial_gross_return"] = [.01, 0., 0.]
    tables, _ = a.evaluate_cached(cases, controls, context)
    row = tables["candidate_case_episodes"].iloc[0]
    assert not row.observed and np.isnan(row.episode_net_return) and np.isnan(row.episode_gross_return)
    assert row.realised_partial_gross_return == .01
    context = pd.concat([context_for(cases, "case", ["abstain"]*3), context_for(controls, "control")])
    tables, _ = a.evaluate_cached(cases, controls, context)
    assert tables["candidate_case_episodes"].iloc[0].episode_net_return == 0
    assert not tables["case_mechanics"].iloc[0].known_pair


@pytest.mark.parametrize("mutation", ["signal", "decision", "direction", "population", "foreign", "duplicate", "missing",
    "gate", "known", "state", "future", "close", "null_id", "old_gate", "parent", "fold"])
def test_reject_mutated_or_transferred_own_context(mutation):
    cases, controls, context = fixture()
    if mutation == "signal": context.loc[3, "signal_time"] += pd.Timedelta(hours=1)
    elif mutation == "decision": context.loc[3, "decision_time"] += pd.Timedelta(hours=1)
    elif mutation == "direction": context.loc[3, "direction"] = -1
    elif mutation == "population": context.loc[3, "population"] = "case"
    elif mutation == "foreign": context.loc[3, "event_id"] = "other"
    elif mutation == "duplicate": context.loc[3, "event_id"] = "r1"
    elif mutation == "missing": context = context.iloc[:-1]
    elif mutation == "gate": context.loc[3, "structure_gate_state"] = "abstain"
    elif mutation == "known": context.loc[3, "structure_known"] = False
    elif mutation == "state": context.loc[3, "structure_state"] = 0
    elif mutation == "future": context.loc[3, "structure_available_at"] += pd.Timedelta(hours=1)
    elif mutation == "close": context.loc[3, "structure_signal_close"] = 101.
    elif mutation == "null_id": context.loc[3, "event_id"] = None
    elif mutation == "old_gate": cases["structure_gate_state"] = "accepted"
    elif mutation == "parent": context.loc[3, "parent_event_id"] = "c1"
    elif mutation == "fold": context.loc[3, "fold"] = "2024H2"
    with pytest.raises((ValueError, AssertionError)):
        a.evaluate_cached(cases, controls, context)


@pytest.mark.parametrize("mutation", ["two", "orphan", "duplicate_time", "bad_unknown", "false_complete", "infinity", "gross_cost", "deadline"])
def test_reject_matching_or_old_whole_episode_contract_corruption(mutation):
    cases, controls, context = fixture()
    if mutation == "two": controls = controls.iloc[:2]; context = context.loc[context.event_id.ne("r2")]
    elif mutation == "orphan": controls["parent_event_id"] = "outside"
    elif mutation == "duplicate_time":
        cols = [x for x in controls if x.endswith("_time") or x in ("occupied_until", "mother_deadline")]
        controls.loc[1, cols] = controls.loc[0, cols].to_numpy()
    elif mutation == "bad_unknown": cases.loc[0, "observed"] = False
    elif mutation == "false_complete": cases.loc[0, "executed"] = False
    elif mutation == "infinity": cases.loc[0, "episode_net_return"] = np.inf
    elif mutation == "gross_cost": cases["gross_return"] = cases.episode_net_return+.001
    elif mutation == "deadline": cases.loc[0, "mother_deadline"] += pd.Timedelta(hours=1)
    with pytest.raises((ValueError, AssertionError)):
        a.evaluate_cached(cases, controls, context)


def test_return_changes_do_not_select_the_gate_or_serial_mask():
    cases, controls, context = fixture(["abstain", "accepted", "accepted"])
    original, _ = a.evaluate_cached(cases, controls, context)
    cases["episode_net_return"] = [1000., -1000., 0.]
    changed, _ = a.evaluate_cached(cases, controls, context)
    pd.testing.assert_series_equal(original["candidate_case_episodes"].structure_gate_state,
        changed["candidate_case_episodes"].structure_gate_state)
    pd.testing.assert_series_equal(original["candidate_case_serial"].portfolio_selected,
        changed["candidate_case_serial"].portfolio_selected)


def test_one_ulp_source_serialization_allowed_but_accepted_values_unchanged():
    cases, controls, context = fixture()
    context.loc[0, "signal_close"] = np.nextafter(100., 101.)
    context.loc[0, "structure_signal_close"] = np.nextafter(100., 101.)
    tables, _ = a.evaluate_cached(cases, controls, context)
    pd.testing.assert_frame_equal(cases, tables["candidate_case_episodes"][cases.columns], check_exact=True)
    context.loc[0, "signal_close"] = 100.0001
    with pytest.raises(AssertionError):
        a.evaluate_cached(cases, controls, context)


def test_timezone_equivalence_is_valid_but_naive_own_clocks_are_not():
    cases, controls, context = fixture()
    context["signal_time"] = context.signal_time.dt.tz_convert("Asia/Shanghai")
    context["decision_time"] = context.decision_time.dt.tz_convert("Asia/Shanghai")
    context["structure_available_at"] = context.structure_available_at.dt.tz_convert("Asia/Shanghai")
    a.evaluate_cached(cases, controls, context)
    context["signal_time"] = context.signal_time.dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone"):
        a.evaluate_cached(cases, controls, context)


@pytest.mark.parametrize("reverse", [False, True])
def test_available_suffix_csv_string_timestamp_parity_still_rejects_one_ns(reverse):
    cases, controls, context = fixture()
    available = controls.decision_time-pd.Timedelta(minutes=5)
    # Reproduce the two historical readers entirely in memory: one leaves the
    # _available column as CSV strings, the other parses it as aware timestamps.
    csv_values = pd.read_csv(StringIO(pd.DataFrame({"known_5m_available": available}).to_csv(index=False)))
    controls["known_5m_available"] = available if reverse else csv_values.known_5m_available
    own_values = csv_values.known_5m_available if reverse else available
    context["known_5m_available"] = context.event_id.map(dict(zip(controls.event_id, own_values)))
    original = controls.copy(deep=True)
    tables, _ = a.evaluate_cached(cases, controls, context)
    pd.testing.assert_frame_equal(original, tables["candidate_control_episodes"][original.columns], check_exact=True)
    changed = context.copy(deep=True)
    changed.loc[3, "known_5m_available"] = pd.Timestamp(changed.loc[3, "known_5m_available"])+pd.Timedelta(nanoseconds=1)
    with pytest.raises(AssertionError):
        a.evaluate_cached(cases, controls, changed)


def test_mirrored_own_structure_directions_and_parent_direction_validation():
    cases, controls, _ = fixture()
    cases["direction"] = -1
    controls["direction"] = -1
    context = pd.concat([context_for(cases, "case", ["accepted", "abstain", "unknown"]),
        context_for(controls, "control", ["abstain", "accepted", "accepted"])], ignore_index=True)
    tables, _ = a.evaluate_cached(cases, controls, context)
    assert tables["candidate_case_episodes"].executed.tolist() == [True, False, False]
    assert tables["candidate_control_episodes"].executed.tolist() == [False, True, True]
    controls["direction"] = 1
    with pytest.raises(ValueError, match="parent"):
        a.evaluate_cached(cases, controls, context)


def test_zero_assigned_controls_does_not_invent_any_matched_return():
    cases, controls, context = fixture()
    tables, summary = a.evaluate_cached(cases, controls.iloc[:0], context.loc[context.population.eq("case")])
    assert tables["candidate_matched"].assigned_controls.eq(0).all()
    assert tables["candidate_matched"].excess.isna().all()
    assert summary["population"]["unmatched_cases"] == len(cases)
    assert summary["effects"]["control_delta"]["total_pairs"] == 0


def test_module_has_no_io_or_simulator_calls_or_v19_dependency():
    source = Path(a.__file__).read_text()
    tree = ast.parse(source)
    calls = [n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
        for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Attribute, ast.Name))]
    assert not set(calls) & {"open", "read_csv", "read_text", "read_bytes", "write_text", "to_csv", "simulate_events", "Study"}
    assert "failed_reduce" not in source
