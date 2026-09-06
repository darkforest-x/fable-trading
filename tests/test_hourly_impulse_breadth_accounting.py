"""Synthetic V21 own-breadth clocks, participation and cached accounting."""
import ast
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_breadth_accounting as a


def episode(ids, times, values, parents=None):
    t = pd.to_datetime(times, utc=True, format="mixed")
    frame = pd.DataFrame(dict(event_id=ids, signal_time=t-pd.Timedelta(hours=1),
        decision_time=t, direction=1, signal_close=100., fold="2024H1",
        mother_signal_time=t-pd.Timedelta(hours=1), mother_decision_time=t,
        mother_deadline=t+pd.Timedelta(hours=72), terminal_time=t,
        occupied_until=t+pd.Timedelta(hours=2), entry_time=t,
        exit_time=t+pd.Timedelta(hours=2), status="request_emitted",
        episode_status="transition_colour_exit", episode_net_return=values,
        observed=True, executed=True, completed_trade=True,
        old_opaque_diagnostic=["keep:"+x for x in ids]))
    if parents is not None:
        frame["parent_event_id"] = parents
    return frame


def context_for(frame, population, states=None):
    result = frame[["event_id", "signal_time", "decision_time", "direction", "signal_close", "fold"]].copy()
    if "parent_event_id" in frame:
        result["parent_event_id"] = frame.parent_event_id
    result["population"] = population
    result["breadth_gate_state"] = states or ["accepted"]*len(frame)
    result["breadth_known"] = result.breadth_gate_state.ne("unknown")
    result["breadth_score"] = [direction*.5 if state == "accepted" else -direction*.5 if state == "abstain" else np.nan
        for direction, state in zip(frame.direction, result.breadth_gate_state)]
    result["breadth_cutoff"] = frame.signal_time
    result["breadth_available_at"] = frame.signal_time.where(result.breadth_known)
    result["breadth_reason"] = np.where(result.breadth_known, "known", "missing_panel")
    for symbol in ("ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"):
        result["breadth_"+symbol+"_score"] = result.breadth_score
    return result


def fixture(case_states=None, control_states=None):
    cases = episode(["c0", "c1", "c2"], ["2024-01-01", "2024-01-01T01:00Z", "2024-01-05"], [-.01, .02, .005])
    controls = episode(["r0", "r1", "r2"], ["2024-01-02", "2024-01-02T01:00Z", "2024-01-02T02:00Z"],
        [-.005, .01, .015], ["c0"]*3)
    context = pd.concat([context_for(cases, "case", case_states), context_for(controls, "control", control_states)], ignore_index=True)
    return cases, controls, context


def run(case_states=None, control_states=None):
    return a.evaluate_cached(*fixture(case_states, control_states))


def test_all_accepted_old_columns_exact_and_inputs_unmodified():
    cases, controls, context = fixture()
    cases.index, controls.index = [8, 3, 99], [7, 44, 2]
    originals = [frame.copy(deep=True) for frame in (cases, controls, context)]
    tables, summary = a.evaluate_cached(cases, controls, context.iloc[::-1])
    for population, old in (("case", cases), ("control", controls)):
        for arm in ("baseline", "candidate"):
            pd.testing.assert_frame_equal(old, tables[f"{arm}_{population}_episodes"][old.columns], check_exact=True)
    for old, current in zip(originals, (cases, controls, context)):
        pd.testing.assert_frame_equal(old, current, check_exact=True)
    assert summary["population"] == dict(cases=3, controls=3, matched_cases=1, unmatched_cases=2)
    assert summary["effects"]["case_delta"]["total_pairs"] == 3
    assert summary["effects"]["excess_delta"]["n"] == 1
    assert summary["effects"]["excess_delta"]["unknown_pairs"] == 2
    assert tables["case_delta"].difference.eq(0).all()
    assert tables["baseline_matched"].assigned_controls.tolist() == [3, 0, 0]
    for table in tables.values():
        assert not any(column.startswith("structure_") for column in table)
    for population in ("case", "control"):
        candidate = tables[f"candidate_{population}_episodes"].set_index("event_id")
        own = context.set_index("event_id").loc[candidate.index]
        for column in [c for c in own if c.startswith("breadth_")]:
            pd.testing.assert_series_equal(candidate[column], own[column], check_dtype=False, check_exact=True)


def test_abstain_unknown_fees_and_distinct_mean_denominators():
    tables, summary = run(["accepted", "abstain", "unknown"])
    candidate = tables["candidate_case_episodes"]
    assert candidate.episode_net_return.iloc[:2].tolist() == [-.01, 0.]
    assert np.isnan(candidate.episode_net_return.iloc[2])
    assert candidate.policy_fee_fraction.iloc[:2].tolist() == [.002, 0.]
    assert np.isnan(candidate.policy_fee_fraction.iloc[2])
    assert candidate.observed.tolist() == [True, True, False]
    assert candidate.executed.tolist() == [True, False, False]
    assert candidate.entry_time.iloc[1:].isna().all() and candidate.exit_time.iloc[1:].isna().all()
    assert candidate.episode_status.tolist() == ["transition_colour_exit", "breadth_abstain", "breadth_unknown"]
    assert candidate.occupied_until.iloc[1] == candidate.mother_decision_time.iloc[1]
    assert candidate.occupied_until.iloc[2] == candidate.mother_deadline.iloc[2]
    metrics = summary["arms"]["candidate"]["case"]["independent"]["all"]
    assert metrics["opportunities"] == 3 and metrics["known_opportunities"] == 2 and metrics["unknown_opportunities"] == 1
    assert metrics["completed_trades"] == 1 and metrics["mean_net_bp"] == -100
    assert metrics["mean_gross_bp"] == -80 and metrics["all_opportunity_mean_net_bp"] == -50
    assert metrics["opportunity_mean_denominator"] == 2
    assert summary["gate_counts"]["case"] == dict(accepted=1, abstain=1, unknown=1)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("value", [0., -0.0])
def test_known_zero_is_abstain_not_unknown_or_trade(direction, value):
    cases, controls, _ = fixture()
    cases.direction = controls.direction = direction
    context = pd.concat([context_for(cases, "case", ["abstain"]*3), context_for(controls, "control", ["abstain"]*3)], ignore_index=True)
    context.breadth_score = value
    tables, summary = a.evaluate_cached(cases, controls, context)
    assert tables["candidate_case_episodes"].episode_net_return.eq(0).all()
    metrics = summary["arms"]["candidate"]["case"]["independent"]["all"]
    assert metrics["completed_trades"] == 0 and metrics["known_opportunities"] == 3
    assert np.isnan(metrics["mean_net_bp"]) and np.isnan(metrics["profit_factor"])
    assert np.isnan(metrics["win_rate"])


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("value", [1., 1e-14])
def test_directional_gate_has_no_added_positive_threshold(direction, value):
    cases, controls, _ = fixture()
    cases.direction = controls.direction = direction
    context = pd.concat([context_for(cases, "case"), context_for(controls, "control")], ignore_index=True)
    context.breadth_score = direction*value
    tables, _ = a.evaluate_cached(cases, controls, context)
    assert tables["candidate_case_episodes"].executed.all()


def test_single_position_recomputed_abstention_releases_unknown_reserves():
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


def test_controls_own_gate_and_fixed_denominator_I_equals_D_minus_control_D():
    tables, summary = run(["abstain", "accepted", "accepted"], ["accepted", "abstain", "accepted"])
    assert tables["candidate_control_episodes"].executed.tolist() == [True, False, True]
    old, new = (tables[arm+"_matched"].set_index("event_id") for arm in ("baseline", "candidate"))
    assert old.loc["c0", "control_mean_return"] == pytest.approx((-.005+.01+.015)/3)
    assert new.loc["c0", "control_mean_return"] == pytest.approx((-.005+0+.015)/3)
    d, c, i = [tables[key].set_index("event_id").difference for key in ("case_delta", "matched_control_delta", "excess_delta")]
    np.testing.assert_allclose(i, d-c, equal_nan=True)
    assert i.iloc[1:].isna().all()
    assert summary["effects"]["control_delta"]["total_pairs"] == 3
    tables, _ = run(control_states=["accepted", "unknown", "accepted"])
    assert tables["candidate_matched"].control_mean_return.isna().all()
    assert len(tables["candidate_matched"]) == 3


def test_rejected_opportunity_cost_reports_avoided_losers_and_missed_winners():
    tables, _ = run(["abstain", "abstain", "accepted"])
    rows = tables["case_mechanics"].set_index("event_id")
    assert rows.loc["c0", "avoided_net_loser"] and rows.loc["c0", "avoided_loss_event_bp"] == 100
    assert rows.loc["c1", "missed_net_winner"] and rows.loc["c1", "missed_winner_event_bp"] == 200
    group = tables["mechanism_groups"].query("population == 'case' and breadth_gate_state == 'abstain'").iloc[0]
    assert group.known_pairs == 2 and group.mean_delta_bp == pytest.approx(-50)
    assert group.avoided_loss_event_bp == 100 and group.missed_winner_event_bp == 200


def test_accepted_unknown_remainder_stays_unknown_despite_realised_leg():
    cases, controls, context = fixture(["accepted", "abstain", "accepted"])
    cases.loc[0, ["observed", "completed_trade"]] = False
    cases.loc[0, "episode_net_return"] = np.nan
    cases.loc[0, "occupied_until"] = cases.loc[0, "mother_deadline"]
    cases["realised_partial_gross_return"] = [.01, 0., 0.]
    tables, _ = a.evaluate_cached(cases, controls, context)
    row = tables["candidate_case_episodes"].iloc[0]
    assert not row.observed and np.isnan(row.episode_net_return) and np.isnan(row.episode_gross_return)
    assert row.realised_partial_gross_return == .01
    context = pd.concat([context_for(cases, "case", ["abstain"]*3), context_for(controls, "control")], ignore_index=True)
    tables, _ = a.evaluate_cached(cases, controls, context)
    assert tables["candidate_case_episodes"].iloc[0].episode_net_return == 0
    assert not tables["case_mechanics"].iloc[0].known_pair


@pytest.mark.parametrize("mutation", ["signal", "decision", "direction", "population", "foreign", "duplicate", "missing",
    "gate", "known_false", "known_string", "known_int", "score_zero", "score_string", "score_bool", "score_inf", "score_high",
    "score_low", "score_nan", "available_future", "available_early", "available_missing", "cutoff_future", "cutoff_missing",
    "close", "null_id", "old_breadth", "old_structure", "context_structure", "parent", "fold"])
def test_mutated_or_stacked_own_context_rejected(mutation):
    cases, controls, context = fixture()
    if mutation == "signal": context.loc[3, "signal_time"] += pd.Timedelta(hours=1)
    elif mutation == "decision": context.loc[3, "decision_time"] += pd.Timedelta(hours=1)
    elif mutation == "direction": context.loc[3, "direction"] = -1
    elif mutation == "population": context.loc[3, "population"] = "case"
    elif mutation == "foreign": context.loc[3, "event_id"] = "other"
    elif mutation == "duplicate": context.loc[3, "event_id"] = "r1"
    elif mutation == "missing": context = context.iloc[:-1]
    elif mutation == "gate": context.loc[3, "breadth_gate_state"] = "abstain"
    elif mutation.startswith("known_"):
        context.breadth_known = context.breadth_known.astype(object)
        context.loc[3, "breadth_known"] = {"known_false": False, "known_string": "True", "known_int": 1}[mutation]
    elif mutation.startswith("score_"):
        context.breadth_score = context.breadth_score.astype(object)
        context.loc[3, "breadth_score"] = dict(score_zero=0., score_string=".5", score_bool=True,
            score_inf=np.inf, score_high=1.0001, score_low=-1.0001, score_nan=np.nan)[mutation]
    elif mutation == "available_future": context.loc[3, "breadth_available_at"] += pd.Timedelta(hours=1)
    elif mutation == "available_early": context.loc[3, "breadth_available_at"] -= pd.Timedelta(nanoseconds=1)
    elif mutation == "available_missing": context.loc[3, "breadth_available_at"] = pd.NaT
    elif mutation == "cutoff_future": context.loc[3, "breadth_cutoff"] += pd.Timedelta(nanoseconds=1)
    elif mutation == "cutoff_missing": context.loc[3, "breadth_cutoff"] = pd.NaT
    elif mutation == "close": context.loc[3, "signal_close"] = 101.
    elif mutation == "null_id": context.loc[3, "event_id"] = None
    elif mutation == "old_breadth": cases["breadth_score"] = 1.
    elif mutation == "old_structure": controls["structure_gate_state"] = "accepted"
    elif mutation == "context_structure": context["structure_state"] = 1
    elif mutation == "parent": context.loc[3, "parent_event_id"] = "c1"
    elif mutation == "fold": context.loc[3, "fold"] = "2024H2"
    with pytest.raises((ValueError, AssertionError)):
        a.evaluate_cached(cases, controls, context)


@pytest.mark.parametrize("mutation", ["zero_score", "known_available", "missing_cutoff", "future_cutoff"])
def test_unknown_never_gets_zero_score_or_fabricated_availability(mutation):
    cases, controls, context = fixture(["unknown", "accepted", "accepted"])
    if mutation == "zero_score": context.loc[0, "breadth_score"] = 0.
    elif mutation == "known_available": context.loc[0, "breadth_available_at"] = context.loc[0, "signal_time"]
    elif mutation == "missing_cutoff": context.loc[0, "breadth_cutoff"] = pd.NaT
    else: context.loc[0, "breadth_cutoff"] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        a.evaluate_cached(cases, controls, context)


@pytest.mark.parametrize("reverse", [False, True])
def test_shared_available_clock_string_roundtrip_and_one_ns_corruption(reverse):
    cases, controls, context = fixture()
    available = controls.decision_time-pd.Timedelta(minutes=5)
    csv_values = pd.read_csv(StringIO(pd.DataFrame({"known_5m_available": available}).to_csv(index=False)))
    controls["known_5m_available"] = available if reverse else csv_values.known_5m_available
    own_values = csv_values.known_5m_available if reverse else available
    context["known_5m_available"] = context.event_id.map(dict(zip(controls.event_id, own_values)))
    original = controls.copy(deep=True)
    tables, _ = a.evaluate_cached(cases, controls, context)
    pd.testing.assert_frame_equal(original, tables["candidate_control_episodes"][original.columns], check_exact=True)
    context.loc[3, "known_5m_available"] = pd.Timestamp(context.loc[3, "known_5m_available"])+pd.Timedelta(nanoseconds=1)
    with pytest.raises(AssertionError):
        a.evaluate_cached(cases, controls, context)


def test_timezone_conversion_unknown_object_bool_and_all_empty_fold_rows():
    cases, controls, context = fixture(["unknown", "abstain", "accepted"])
    for column in ("breadth_available_at", "breadth_cutoff", "signal_time"):
        context[column] = context[column].dt.tz_convert("Asia/Shanghai")
    context.breadth_known = context.breadth_known.astype(object)
    tables, _ = a.evaluate_cached(cases, controls, context)
    assert len(tables["metrics"]) == 40
    empty = tables["metrics"].query("fold == '2023H1'")
    assert len(empty) == 8 and empty.opportunities.eq(0).all()
    assert empty.all_opportunity_mean_net_bp.isna().all()
    context.signal_time = context.signal_time.dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone"):
        a.evaluate_cached(cases, controls, context)


def test_zero_control_groups_keep_unmatched_unknown_not_zero_excess():
    cases, controls, context = fixture()
    tables, summary = a.evaluate_cached(cases, controls.iloc[:0], context.loc[context.population.eq("case")])
    assert tables["candidate_matched"].assigned_controls.eq(0).all()
    assert tables["candidate_matched"].excess.isna().all()
    assert summary["population"]["unmatched_cases"] == len(cases)
    assert summary["effects"]["control_delta"]["total_pairs"] == 0


def test_returns_do_not_determine_gate_or_serial_mask():
    cases, controls, context = fixture(["abstain", "accepted", "accepted"])
    original, _ = a.evaluate_cached(cases, controls, context)
    cases.episode_net_return = [1000., -1000., 0.]
    changed, _ = a.evaluate_cached(cases, controls, context)
    pd.testing.assert_series_equal(original["candidate_case_episodes"].breadth_gate_state,
        changed["candidate_case_episodes"].breadth_gate_state)
    pd.testing.assert_series_equal(original["candidate_case_serial"].portfolio_selected,
        changed["candidate_case_serial"].portfolio_selected)


@pytest.mark.parametrize("mutation", ["two", "orphan", "duplicate_time", "bad_unknown", "false_complete", "infinity", "gross_cost", "deadline", "parent_side"])
def test_inherited_matching_clock_or_economics_corruption_rejected(mutation):
    cases, controls, context = fixture()
    if mutation == "two": controls = controls.iloc[:2]; context = context.loc[context.event_id.ne("r2")]
    elif mutation == "orphan": controls.parent_event_id = "outside"
    elif mutation == "duplicate_time":
        cols = [x for x in controls if x.endswith("_time") or x in ("occupied_until", "mother_deadline")]
        controls.loc[1, cols] = controls.loc[0, cols].to_numpy()
    elif mutation == "bad_unknown": cases.loc[0, "observed"] = False
    elif mutation == "false_complete": cases.loc[0, "executed"] = False
    elif mutation == "infinity": cases.loc[0, "episode_net_return"] = np.inf
    elif mutation == "gross_cost": cases["gross_return"] = cases.episode_net_return+.001
    elif mutation == "deadline": cases.loc[0, "mother_deadline"] += pd.Timedelta(hours=1)
    elif mutation == "parent_side": controls.direction = -1
    with pytest.raises((ValueError, AssertionError)):
        a.evaluate_cached(cases, controls, context)


def test_no_io_no_exit_replay_and_no_fake_structure_adapter():
    tree = ast.parse(Path(a.__file__).read_text())
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Attribute, ast.Name))}
    assert not calls & {"open", "read_csv", "read_text", "read_bytes", "write_text", "to_csv", "simulate_events", "Study"}
    # Generic pure helpers are permitted; the old structure entrypoint is not.
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"evaluate_cached", "_check_context", "_candidate", "_mechanics"}
        for n in ast.walk(tree))
