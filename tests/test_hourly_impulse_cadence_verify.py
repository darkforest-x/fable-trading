"""Synthetic ledgers and deliberately corrupted counterexamples; no price files."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_cadence_verify as verify


COUNTS = (3, 6, 6)
BASE, TREATMENT = verify.ARMS
FIVE, QUARTER = pd.Timedelta(minutes=5), pd.Timedelta(minutes=15)


def trade(event_id, entry, direction, cadence, gross=.01):
    entry = pd.Timestamp(entry)
    exit_time = entry+FIVE if cadence == 5 else entry.floor("15min")+QUARTER
    stop = 100.-direction*10.
    row = dict(event_id=event_id, decision_time=entry, entry_time=entry, entry_price=100.,
               direction=direction, initial_stop=stop, signal_atr=2., risk_pct=.1, risk_atr=5.,
               fold="2023H1", exit_time=exit_time, exit_price=100.*(1+direction*gross),
               gross_return=gross, net_return=gross-.002, net_r=(gross-.002)/.1,
               hold_minutes=(exit_time-entry).total_seconds()/60, closed=True, outcome="transition_colour_exit",
               transition_trigger_previous_open_time=entry-FIVE,
               transition_trigger_open_time=exit_time-FIVE, transition_trigger_available_at=exit_time,
               partial_fraction=0., exit_remaining_fraction=1., realised_partial_gross_return=0., funding_modelled=False,
               mg_entry_side=direction, mg_entry_state="aligned", mg_entry_bar_open=entry-FIVE,
               mg_entry_available_at=entry, mg_entry_reason="valid")
    if cadence == 15:
        row["transition_trigger_previous_available_at"] = entry
    return row


def refresh(arms):
    """Rebuild synthetic dependent tables so individual corruptions stay isolated."""
    for arm in verify.ARMS:
        a = arms[arm]
        cases, controls = a["case"].set_index("event_id"), a["control"]
        grouped = controls.groupby("parent_event_id").net_return.agg(["size", "mean"])
        pairs = cases[["decision_time", "fold", "net_return"]].rename(columns={"decision_time": "mother_decision_time", "net_return": "event_net_return"})
        pairs["assigned_controls"] = pairs.index.map(grouped["size"]).fillna(0).astype(int)
        pairs["control_mean_return"] = pairs.index.map(grouped["mean"])
        pairs["excess"] = pairs.event_net_return-pairs.control_mean_return
        a["matched"] = pairs.reset_index()
        emitted = a["serial"].status.eq("request_emitted")
        ids = a["serial"].loc[emitted, "entry_event_id"]
        a["serial"].loc[emitted, "episode_net_return"] = ids.map(cases.net_return)
        a["serial"].loc[emitted, "occupied_until"] = ids.map(cases.exit_time)
    deltas = {}
    for name, key, value, time in [
        ("case_delta", "case", "net_return", "decision_time"),
        ("excess_delta", "matched", "excess", "mother_decision_time"),
        ("serial_delta", "serial", "episode_net_return", "mother_decision_time"),
    ]:
        before, after = (arms[arm][key].set_index("event_id").sort_index() for arm in verify.ARMS)
        left, right = before[value], after[value]
        if name == "serial_delta":
            left = left.where(before.portfolio_selected, 0.)
            right = right.where(after.portfolio_selected, 0.)
        deltas[name] = pd.DataFrame({"mother_decision_time": before[time], "before": left,
                                    "after": right, "difference": right-left}).reset_index()
    return deltas


def fixture_tables():
    arms = {}
    case_times = ["2023-01-01T01:00:00Z", "2023-01-01T02:05:00Z", "2023-01-01T03:10:00Z"]
    for arm, cadence in zip(verify.ARMS, (5, 15)):
        cases = pd.DataFrame([trade("case"+str(i), time, 1 if i != 1 else -1, cadence,
                                    gross=(.01 if cadence == 5 else .02)+i*.001) for i, time in enumerate(case_times)])
        controls = []
        for i in range(6):
            parent = i//3
            row = trade("control"+str(i), pd.Timestamp("2023-01-02T01:00:00Z")+pd.Timedelta(hours=i),
                        1 if parent == 0 else -1, cadence, gross=.005 if cadence == 5 else .007)
            row["parent_event_id"] = "case"+str(parent)
            controls.append(row)
        serial = []
        for i in range(6):
            emitted = i < 3
            start = cases.loc[i, "entry_time"]-QUARTER if emitted else pd.Timestamp("2023-01-01T04:00:00Z")+pd.Timedelta(hours=i-3)
            serial.append(dict(event_id="zone"+str(i), zone_id="zone"+str(i), mother_decision_time=start,
                               fold="2023H1", status="request_emitted" if emitted else "expired_no_release",
                               entry_event_id="case"+str(i) if emitted else np.nan, episode_net_return=0.,
                               portfolio_selected=True, observed=True, terminal_time=start+QUARTER,
                               occupied_until=start+QUARTER))
        arms[arm] = {"case": cases, "control": pd.DataFrame(controls), "serial": pd.DataFrame(serial)}
    return arms, refresh(arms)


def run(arms, deltas):
    return verify.verify_tables(arms, deltas, expected_counts=COUNTS, expected_unmatched=1)


def change_exit(frame, row, *, time=None, price=None, outcome=None):
    if time is not None:
        frame.loc[row, "exit_time"] = time
    if price is not None:
        frame.loc[row, "exit_price"] = price
    if outcome is not None:
        frame.loc[row, "outcome"] = outcome
    r = frame.loc[row]
    gross = r.direction*(r.exit_price/r.entry_price-1)
    frame.loc[row, ["gross_return", "net_return", "net_r", "hold_minutes"]] = [
        gross, gross-.002, (gross-.002)/r.risk_pct, (r.exit_time-r.entry_time).total_seconds()/60]
    if r.outcome != "transition_colour_exit":
        for c in frame:
            if c.startswith("transition_trigger_"):
                frame.loc[row, c] = pd.NaT
    else:
        frame.loc[row, "transition_trigger_open_time"] = r.exit_time-FIVE
        frame.loc[row, "transition_trigger_available_at"] = r.exit_time


def test_valid_synthetic_three_phase_mirrored_ledgers_and_unknown_excess():
    arms, deltas = fixture_tables()
    report = run(arms, deltas)
    assert report["status"] == "passed"
    assert report["effects"]["case_delta"]["rows"] == 3
    assert report["effects"]["excess_delta"]["rows"] == 3
    assert report["effects"]["excess_delta"]["finite_pairs"] == 2
    assert report["effects"]["excess_delta"]["unknown_pairs"] == 1
    assert "not an independent raw" in report["scope"]
    assert json.loads(json.dumps(report, allow_nan=False))["status"] == "passed"


def test_default_support_is_frozen_not_inferred_from_available_rows():
    arms, deltas = fixture_tables()
    with pytest.raises(verify.VerificationError, match="population count"):
        verify.verify_tables(arms, deltas)


@pytest.mark.parametrize("column", ["gross_return", "net_return", "net_r", "risk_pct", "risk_atr", "hold_minutes"])
def test_each_economic_formula_is_independently_checked(column):
    arms, deltas = fixture_tables()
    arms[TREATMENT]["case"].loc[0, column] += .001
    with pytest.raises(verify.VerificationError, match="formula"):
        run(arms, deltas)


def test_consistently_recomputed_economics_cannot_hide_changed_stop():
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["case"]
    t.loc[2, "initial_stop"] = 89.
    t.loc[2, ["risk_pct", "risk_atr"]] = [.11, 5.5]
    t.loc[2, "net_r"] = t.loc[2, "net_return"]/.11
    with pytest.raises(verify.VerificationError, match="invariant changed"):
        run(arms, refresh(arms))


@pytest.mark.parametrize("problem", ["duplicate", "missing", "foreign"])
def test_case_identity_changes_fail(problem):
    arms, deltas = fixture_tables()
    if problem == "duplicate":
        arms[TREATMENT]["case"].loc[1, "event_id"] = "case0"
    elif problem == "missing":
        arms[TREATMENT]["case"] = arms[TREATMENT]["case"].iloc[:2]
    else:
        arms[TREATMENT]["case"].loc[1, "event_id"] = "foreign"
    with pytest.raises(verify.VerificationError):
        run(arms, deltas)


def test_unknown_trade_is_not_zero_or_a_successful_complete_run():
    arms, deltas = fixture_tables()
    t = arms[TREATMENT]["case"]
    t.loc[0, ["closed", "outcome", "net_return"]] = [False, "right_censored", np.nan]
    with pytest.raises(verify.VerificationError, match="incomplete outcomes"):
        run(arms, deltas)


@pytest.mark.parametrize("column", ["entry_price", "signal_atr", "net_return"])
def test_nonfinite_economics_fail(column):
    arms, deltas = fixture_tables()
    arms[TREATMENT]["control"].loc[0, column] = np.nan
    with pytest.raises(verify.VerificationError, match="nonfinite economics"):
        run(arms, deltas)


def test_native5_previous_bar_must_be_adjacent():
    arms, deltas = fixture_tables()
    arms[BASE]["case"].loc[0, "transition_trigger_previous_open_time"] -= FIVE
    with pytest.raises(verify.VerificationError, match="native5 adjacent"):
        run(arms, deltas)


def test_cadence_exit_cannot_be_a_delayed_nonquarter_fill():
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["case"]
    change_exit(t, 0, time=t.loc[0, "exit_time"]+FIVE)
    with pytest.raises(verify.VerificationError, match="quarter sample"):
        run(arms, refresh(arms))


def test_native_trigger_availability_cannot_be_faked_as_15m_bar():
    arms, deltas = fixture_tables()
    arms[TREATMENT]["case"].loc[0, "transition_trigger_open_time"] -= 2*FIVE
    with pytest.raises(verify.VerificationError, match="availability clock"):
        run(arms, deltas)


def test_previous_sample_availability_must_match_its_native5_bar_close():
    arms, deltas = fixture_tables()
    arms[TREATMENT]["case"].loc[0, "transition_trigger_previous_available_at"] += FIVE
    with pytest.raises(verify.VerificationError, match="previous sample availability"):
        run(arms, deltas)


@pytest.mark.parametrize("previous_offset,exit_offset", [(5, 30), (15, 45), (-15, 15)])
def test_invalid_sample_chain_is_rejected(previous_offset, exit_offset):
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["case"]
    entry = t.loc[0, "entry_time"]
    change_exit(t, 0, time=entry+pd.Timedelta(minutes=exit_offset))
    previous = entry+pd.Timedelta(minutes=previous_offset)
    t.loc[0, "transition_trigger_previous_available_at"] = previous
    t.loc[0, "transition_trigger_previous_open_time"] = previous-FIVE
    with pytest.raises(verify.VerificationError, match="quarter sample"):
        run(arms, refresh(arms))


def test_valid_later_adjacent_quarter_samples_pass():
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["case"]
    entry = t.loc[0, "entry_time"]
    change_exit(t, 0, time=entry+2*QUARTER)
    t.loc[0, "transition_trigger_previous_available_at"] = entry+QUARTER
    t.loc[0, "transition_trigger_previous_open_time"] = entry+QUARTER-FIVE
    assert run(arms, refresh(arms))["status"] == "passed"


def test_exit_at_entry_is_rejected_even_with_consistent_formulas():
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["case"]
    change_exit(t, 0, time=t.loc[0, "entry_time"])
    with pytest.raises(verify.VerificationError, match="holding clock"):
        run(arms, refresh(arms))


def test_one_nanosecond_exit_shift_fails_clock():
    arms, deltas = fixture_tables()
    arms[TREATMENT]["case"].loc[0, "exit_time"] += pd.Timedelta(nanoseconds=1)
    with pytest.raises(verify.VerificationError, match="raw5 grid"):
        run(arms, deltas)


def test_hard_stop_off_quarter_is_valid_and_keeps_fixed_price():
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["control"]
    change_exit(t, 0, time=t.loc[0, "entry_time"]+2*FIVE, price=t.loc[0, "initial_stop"], outcome="hard_stop")
    report = run(arms, refresh(arms))
    assert report["arms"][TREATMENT]["control"]["stops_off_quarter"] == 1


@pytest.mark.parametrize("row", [0, 1])
def test_gap_stop_mirrored_fill_beyond_fixed_stop_is_valid(row):
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["case"]
    price = t.loc[row, "initial_stop"]-t.loc[row, "direction"]
    change_exit(t, row, time=t.loc[row, "entry_time"]+FIVE, price=price, outcome="hard_stop_gap")
    assert run(arms, refresh(arms))["status"] == "passed"


@pytest.mark.parametrize("outcome", ["hard_stop", "hard_stop_gap", "transition_colour_exit"])
def test_incorrect_stop_or_colour_fill_is_rejected(outcome):
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["case"]
    price = 90. if outcome == "transition_colour_exit" else 91.
    change_exit(t, 0, price=price, outcome=outcome)
    with pytest.raises(verify.VerificationError, match="stop"):
        run(arms, refresh(arms))


def test_nonflip_cannot_carry_stale_trigger_fields():
    arms, _ = fixture_tables()
    t = arms[TREATMENT]["control"]
    change_exit(t, 0, price=t.loc[0, "initial_stop"], outcome="hard_stop")
    t.loc[0, "transition_trigger_open_time"] = t.loc[0, "exit_time"]-FIVE
    with pytest.raises(verify.VerificationError, match="stale trigger"):
        run(arms, refresh(arms))


def test_partial_exits_are_not_silently_validated_with_full_position_formula():
    arms, deltas = fixture_tables()
    arms[TREATMENT]["control"].loc[0, "partial_fraction"] = .5
    with pytest.raises(verify.VerificationError, match="partial_fraction"):
        run(arms, deltas)


def test_saved_control_mean_is_checked_against_all_three_control_returns():
    arms, deltas = fixture_tables()
    arms[TREATMENT]["matched"].loc[0, "control_mean_return"] += .001
    with pytest.raises(verify.VerificationError, match="independent control means"):
        run(arms, deltas)


def test_control_parent_or_triplet_composition_cannot_drift():
    arms, _ = fixture_tables()
    arms[TREATMENT]["control"].loc[0, "parent_event_id"] = "case1"
    with pytest.raises(verify.VerificationError, match="triplets"):
        run(arms, refresh(arms))


def test_matched_unassigned_control_is_unknown_not_zero():
    arms, deltas = fixture_tables()
    arms[TREATMENT]["matched"].loc[2, ["control_mean_return", "excess"]] = 0.
    with pytest.raises(verify.VerificationError, match="control means"):
        run(arms, deltas)


@pytest.mark.parametrize("column", ["before", "after", "difference"])
def test_i_preserves_unmatched_nan_in_every_saved_delta_column(column):
    arms, deltas = fixture_tables()
    deltas["excess_delta"].loc[2, column] = 0.
    with pytest.raises(verify.VerificationError, match="excess_delta"):
        run(arms, deltas)


@pytest.mark.parametrize("name", ["case_delta", "excess_delta", "serial_delta"])
def test_each_saved_delta_is_recomputed_not_trusted(name):
    arms, deltas = fixture_tables()
    deltas[name].loc[0, "difference"] += .001
    with pytest.raises(verify.VerificationError, match=name):
        run(arms, deltas)


def test_serial_all_original_zones_cannot_drop_known_nonentries():
    arms, deltas = fixture_tables()
    arms[TREATMENT]["serial"] = arms[TREATMENT]["serial"].iloc[:-1]
    with pytest.raises(verify.VerificationError, match="denominator"):
        run(arms, deltas)


@pytest.mark.parametrize("selected", [True, False])
def test_unknown_source_is_not_silently_zero_filled_or_dropped(selected):
    arms, deltas = fixture_tables()
    arms[TREATMENT]["serial"].loc[3, ["observed", "episode_net_return", "portfolio_selected"]] = [False, np.nan, selected]
    with pytest.raises(verify.VerificationError, match="unknown source outcome"):
        run(arms, deltas)


def test_serial_selection_is_independently_reconstructed_and_uses_fixed_denominator():
    arms, _ = fixture_tables()
    for arm in verify.ARMS:
        arms[arm]["serial"].loc[1, "mother_decision_time"] = pd.Timestamp("2023-01-01T01:10:00Z")
    with pytest.raises(verify.VerificationError, match="independent occupancy"):
        run(arms, refresh(arms))
    arms[TREATMENT]["serial"].loc[1, "portfolio_selected"] = False
    report = run(arms, refresh(arms))
    serial = report["arms"][TREATMENT]["serial"]
    assert serial["original_zones"] == 6 and serial["selected_zones"] == 5
    assert serial["selected_trades"] == 2 and serial["skipped_emitted"] == 1
    expected = arms[TREATMENT]["case"].loc[[0, 2], "net_return"].sum()/6*1e4
    assert serial["mean_net_bp_per_original_zone"] == pytest.approx(expected)


def test_loader_reads_only_explicit_saved_csvs_and_never_writes(tmp_path, monkeypatch):
    arms, deltas = fixture_tables()
    names = {"case": "case_trades.csv.gz", "control": "control_trades.csv.gz", "matched": "matched_request_outcomes.csv", "serial": "single_pending_zone_ledger.csv.gz"}
    for arm in verify.ARMS:
        (tmp_path/arm).mkdir()
        for key, name in names.items():
            arms[arm][key].to_csv(tmp_path/arm/name, index=False)
    for key, table in deltas.items():
        table.to_csv(tmp_path/(key+".csv"), index=False)
    files_before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    original = pd.read_csv
    called = []
    def tracked(path, *args, **kwargs):
        called.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(pd, "read_csv", tracked)
    result = verify.verify_results(tmp_path, expected_counts=COUNTS, expected_unmatched=1)
    assert result["status"] == "passed" and len(called) == 11
    assert set(called) == set(files_before)
    assert files_before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_cli_success_prints_json_only(monkeypatch, capsys, tmp_path):
    arms, deltas = fixture_tables()
    monkeypatch.setattr(verify, "verify_results", lambda path: run(arms, deltas))
    assert verify.main(["--results", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("error_type", [verify.VerificationError, TypeError])
def test_cli_failure_is_json_with_nonzero_exit_no_artifact(monkeypatch, capsys, tmp_path, error_type):
    def fail(path):
        raise error_type("deliberate counterexample")
    monkeypatch.setattr(verify, "verify_results", fail)
    assert verify.main(["--results", str(tmp_path)]) == 1
    message = json.loads(capsys.readouterr().out)
    assert message["status"] == "failed" and "counterexample" in message["error"]
    assert list(tmp_path.iterdir()) == []
