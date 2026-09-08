"""Synthetic event/settlement checks; no market reads, sampling or execution."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.altcoin_cost_diagnostics import (
    NOTICE, PROXY_LABEL, diagnose_events, run, sha, summarize_cases,
)


def event(*, side=1, exit_price=110., entry="2024-01-01T01:00:00Z",
          exit="2024-01-01T09:00:00Z", timing="open", period=3600, signal_i=3, **extra):
    gross = side*(exit_price/100-1)*10000
    return dict(valid=True, side=side, entry_price=100., exit_price=exit_price,
                gross_bp=gross, net_bp=gross-20, entry_time=entry, exit_time=exit,
                exit_timing=timing, period_seconds=period, symbol="TEST", minutes=period//60,
                fold="recent_test", cohort="high_vol", arm="base", parameter="base",
                domain="all", exit_rule="md", signal_i=signal_i, portfolio_selected=True,
                control_indexes="4 5", control_count=2, **extra)


def source():
    index = pd.date_range("2024-01-01", periods=3, freq="8h", tz="UTC")
    funding = pd.DataFrame(dict(funding_time=index.as_unit("ns").asi8//1_000_000,
                                realized_rate=[.001, .001, .001],
                                rate_status=["actual_available"]*3,
                                inst_id=["TEST-USDT-SWAP"]*3))
    opens = pd.Series([100., 110., 120.], index=index)
    return funding, opens


def check(row=None, funding=None, opens=None):
    default_funding, default_opens = source()
    return diagnose_events(pd.DataFrame([event() if row is None else row]),
                           default_funding if funding is None else funding,
                           default_opens if opens is None else opens).iloc[0]


def test_fixed_costs_and_turnover_proxy_do_not_change_paths_or_baseline():
    original = pd.DataFrame([event()])
    result = diagnose_events(original)
    r = result.iloc[0]
    assert r.break_even_cost_bp == pytest.approx(1000)
    assert r.net_20bp == pytest.approx(980)
    assert r.net_40bp == pytest.approx(960)
    assert r.net_60bp == pytest.approx(940)
    assert r.notional_fee_bp == pytest.approx(21)
    assert r.net_notional_fee_bp == pytest.approx(979)
    assert r.net_40bp-r.net_60bp == pytest.approx(20)
    pd.testing.assert_frame_equal(result[original.columns], original, check_dtype=False)
    assert pd.isna(r.funding_bp_low) and r.funding_status == "source_missing"


@pytest.mark.parametrize("side,exit_price,expected", [(1, 110., -11.), (-1, 90., 11.)])
def test_actual_positive_funding_long_pays_short_receives_entry_notional_units(side, exit_price, expected):
    r = check(event(side=side, exit_price=exit_price))
    assert r.funding_bp_low == pytest.approx(expected)
    assert r.funding_bp_high == pytest.approx(expected)
    assert r.funding_certain_count == 1 and r.funding_uncertain_count == 0
    assert r.net_20bp_funding_low == pytest.approx(r.net_bp+expected)
    assert r.funding_price_basis == PROXY_LABEL and "NOT_settlement_mark" in PROXY_LABEL
    assert r.funding_status == "observed_schedule_only"
    assert not r.funding_coverage_complete


def test_negative_rate_reverses_sign_and_funding_rows_can_be_unsorted():
    f, p = source()
    f["realized_rate"] = -.001
    r = check(funding=f.iloc[::-1], opens=p)
    assert r.funding_bp_low == pytest.approx(11)


@pytest.mark.parametrize("entry,expected", [
    ("2024-01-01T08:00:00Z", (-11., 0.)),
    ("2024-01-01T08:00:20Z", (-11., 0.)),
    ("2024-01-01T08:01:00Z", (-11., 0.)),
    ("2024-01-01T08:01:00.001Z", (0., 0.)),
])
def test_entry_within_one_minute_assessment_window_is_not_assumed_exempt(entry, expected):
    r = check(event(entry=entry))
    assert (r.funding_bp_low, r.funding_bp_high) == pytest.approx(expected)


@pytest.mark.parametrize("side,expected", [(1, (-11., 0.)), (-1, (0., 11.))])
@pytest.mark.parametrize("period,exit_time", [(3600, "2024-01-01T09:00:00Z"), (14400, "2024-01-01T12:00:00Z")])
def test_intrabar_exit_uses_whole_possible_interval_not_recorded_close(side, expected, period, exit_time):
    r = check(event(side=side, exit_price=110., timing="intrabar_unknown", period=period, exit=exit_time))
    assert (r.funding_bp_low, r.funding_bp_high) == pytest.approx(expected)
    assert r.funding_certain_count == 0 and r.funding_uncertain_count == 1


@pytest.mark.parametrize("exit_time,expected", [
    ("2024-01-01T07:59:59Z", (0., 0.)),
    ("2024-01-01T08:00:00Z", (-11., 0.)),
    ("2024-01-01T08:00:20Z", (-11., 0.)),
    ("2024-01-01T08:01:00Z", (-11., 0.)),
    ("2024-01-01T08:01:00.001Z", (-11., -11.)),
])
def test_known_exit_boundary_is_conservative(exit_time, expected):
    r = check(event(exit=exit_time))
    assert (r.funding_bp_low, r.funding_bp_high) == pytest.approx(expected)


def test_missing_actual_never_inherits_predicted_or_zero():
    f, p = source()
    f.loc[1, "realized_rate"] = np.nan
    f.loc[1, "rate_status"] = "predicted_only"
    f["predicted_rate"] = .012
    r = check(funding=f, opens=p)
    assert pd.isna(r.funding_bp_low) and pd.isna(r.net_20bp_funding_high)
    assert r.funding_missing_rate_count == 1 and r.funding_status == "missing_rate_or_proxy"


def test_exact_proxy_match_required_no_nearest_or_forward_fill():
    f, p = source()
    p.index = p.index-pd.Timedelta(seconds=1)
    r = check(funding=f, opens=p)
    assert r.funding_missing_proxy_count == 1 and pd.isna(r.funding_bp_low)


@pytest.mark.parametrize("change", [dict(entry="2023-12-31T23:59:59Z"), dict(exit="2024-01-01T17:00:00Z")])
def test_event_outside_observed_source_span_is_missing(change):
    r = check(event(**change))
    assert r.funding_status == "outside_observed_span" and pd.isna(r.funding_bp_low)


def test_span_is_never_promoted_to_complete_schedule_even_when_conditional_sum_is_zero():
    f, p = source()
    r = check(funding=f.drop(1), opens=p)
    assert r.funding_bp_low == r.funding_bp_high == 0
    assert r.funding_status == "observed_schedule_only" and not r.funding_coverage_complete
    assert "no complete funding coverage" in NOTICE


def test_empty_funding_and_missing_proxy_stay_missing():
    f, _ = source()
    r = diagnose_events(pd.DataFrame([event()]), f.iloc[:0]).iloc[0]
    assert pd.isna(r.funding_bp_low) and r.funding_status == "source_missing"
    r = diagnose_events(pd.DataFrame([event()]), f).iloc[0]
    assert pd.isna(r.funding_bp_low) and r.funding_missing_proxy_count == 1


def test_ns_ms_us_proxy_indexes_are_identical():
    f, p = source()
    baseline = diagnose_events(pd.DataFrame([event()]), f, p)
    for unit in ("ms", "us"):
        q = p.copy()
        q.index = q.index.as_unit(unit)
        pd.testing.assert_frame_equal(baseline, diagnose_events(pd.DataFrame([event()]), f, q))


@pytest.mark.parametrize("mutation,match", [
    ({"side": 0}, "finite"), ({"entry_price": 0}, "finite"),
    ({"gross_bp": 2000}, "disagrees"), ({"net_bp": 1}, "20bp"),
    ({"entry_time": "2024-01-01 01:00:00"}, "UTC"),
    ({"exit_timing": "unknown"}, "holding interval"),
    ({"exit_time": "2023-12-01T00:00:00Z"}, "holding interval"),
])
def test_contradictory_economics_and_clocks_fail(mutation, match):
    row = event()
    row.update(mutation)
    with pytest.raises(ValueError, match=match):
        check(row)


def test_invalid_candidates_remain_explicit_without_fake_costs():
    row = event()
    row.update(valid=False, entry_price=np.nan, exit_price=np.nan, gross_bp=np.nan, net_bp=np.nan)
    r = check(row)
    assert r.funding_status == "invalid_event"
    assert pd.isna(r.net_40bp) and pd.isna(r.funding_bp_low)


def test_funding_metadata_end_exclusive_and_duplicate_rejection():
    f, p = source()
    with pytest.raises(ValueError, match="frozen"):
        diagnose_events(pd.DataFrame([event()]), f, p, source_end="2024-01-01T16:00:00Z")
    with pytest.raises(ValueError, match="duplicate funding"):
        diagnose_events(pd.DataFrame([event()]), pd.concat([f, f.iloc[:1]]), p)


def paired_rows(*, missing_second=False):
    f, p = source()
    cases = diagnose_events(pd.DataFrame([event()]), f, p)
    controls = [event(exit_price=105., signal_i=4), event(exit_price=95., signal_i=5,
                exit="2024-01-01T17:00:00Z" if missing_second else "2024-01-01T09:00:00Z")]
    controls = diagnose_events(pd.DataFrame(controls), f, p)
    return cases, controls


def test_original_matches_event_equal_and_fee_excess_consistent():
    cases, controls = paired_rows()
    paired, summary = summarize_cases(cases, controls)
    r = summary.loc[summary.scope.eq("all_events")].iloc[0]
    assert r.case_count == r.matched_case_count == r.funding_common_case_count == 1
    assert r.control_link_count == r.funding_common_control_link_count == 2
    for bp in (20, 40, 60):
        assert r[f"matched_excess_mean_net_{bp}bp"] == pytest.approx(1000)
    assert r.matched_control_mean_net_40bp == pytest.approx(-40)
    assert r.funding_common_case_mean_low == pytest.approx(969)
    assert r.funding_common_control_mean_low == pytest.approx(-31)
    assert paired.control_indexes.iloc[0] == "4 5"
    assert set(summary.scope) == {"all_events", "portfolio_selected"}


def test_funding_common_support_requires_every_original_control_and_reports_both_coverages():
    _, summary = summarize_cases(*paired_rows(missing_second=True))
    r = summary.iloc[0]
    assert r.funding_case_observed_count == 1 and r.funding_control_observed_link_count == 1
    assert r.funding_common_case_count == 0 and r.control_link_count == 2
    assert pd.isna(r.funding_common_case_mean_low) and pd.isna(r.funding_common_control_mean_low)
    assert r.matched_excess_mean_net_60bp == pytest.approx(1000)


def test_mapping_missing_or_duplicate_controls_rejected_no_resampling():
    cases, controls = paired_rows()
    with pytest.raises(ValueError, match="control_count"):
        summarize_cases(cases, controls.iloc[:1])
    with pytest.raises(ValueError, match="duplicate original control identity"):
        summarize_cases(cases, pd.concat([controls, controls.iloc[:1]]))


def test_zero_original_matches_stays_unmatched_instead_of_new_sampling():
    cases, _ = paired_rows()
    cases["control_indexes"], cases["control_count"] = "", 0
    _, summary = summarize_cases(cases, pd.DataFrame())
    assert summary.matched_case_count.eq(0).all()
    assert summary.matched_excess_mean_net_40bp.isna().all()


def test_runner_original_schema_without_parameter_domain_uses_frozen_arm_mapping():
    cases, controls = paired_rows()
    cases = cases.drop(columns=["parameter", "domain"])
    paired, _ = summarize_cases(cases, controls)
    assert paired.matched_control_count.iloc[0] == 2
    cases["arm"] = "undocumented_arm"
    with pytest.raises(ValueError, match="unknown frozen arm"):
        summarize_cases(cases, controls)


def fixture_files(tmp_path, *, with_funding=True):
    results = tmp_path/"audit"
    (results/"TEST_60").mkdir(parents=True)
    rows = pd.DataFrame([event()])
    rows.to_csv(results/"TEST_60/events.csv.gz", index=False)
    pd.DataFrame([event(signal_i=4), event(signal_i=5)]).to_csv(results/"TEST_60/controls.csv.gz", index=False)
    index = pd.date_range("2024-01-01", periods=96, freq="15min", tz="UTC")
    csv_path = tmp_path/"history.csv"
    pd.DataFrame(dict(ts=index.as_unit("ns").asi8//1_000_000, open=110.)).to_csv(csv_path, index=False)
    history = tmp_path/"history.json"
    history.write_text(json.dumps(dict(end_exclusive="2024-01-02T00:00:00Z", symbols=[dict(
        symbol="TEST", status="complete", output_path=str(csv_path), output_sha256=sha(csv_path))])))
    derivatives = tmp_path/"derivatives"
    if with_funding:
        (derivatives/"normalized").mkdir(parents=True)
        derivatives.joinpath("manifest.json").write_text(json.dumps(dict(identity=dict(
            period="4H", start_ms=index[0].value//1_000_000, end_ms=(index[-1]+pd.Timedelta(minutes=15)).value//1_000_000))))
        source()[0].to_csv(derivatives/"normalized/TEST-USDT-SWAP_4H_funding.csv", index=False)
    return results, history, derivatives, tmp_path/"diagnostics", csv_path


@pytest.mark.parametrize("with_funding", [True, False])
def test_synthetic_cli_inputs_outputs_provenance_and_missing_sources(tmp_path, with_funding):
    args = fixture_files(tmp_path, with_funding=with_funding)
    receipt = run(*args[:4])
    assert receipt["input_sha256"][str(args[1])] == sha(args[1])
    assert receipt["funding_price_basis"] == PROXY_LABEL
    assert receipt["case_count"] == 1 and receipt["control_count"] == 2
    assert not receipt["candidate_reselection"] and not receipt["portfolio_recomputed"]
    assert not receipt["funding_coverage_complete"]
    events = pd.read_csv(args[3]/"cost_events.csv.gz")
    assert events.funding_status.iloc[0] == ("observed_schedule_only" if with_funding else "source_missing")
    assert len(pd.read_csv(args[3]/"summary.csv")) == 2
    with pytest.raises(ValueError, match="already exists"):
        run(*args[:4])


def test_history_hash_mismatch_rejected_before_output(tmp_path):
    args = fixture_files(tmp_path)
    args[4].write_text(args[4].read_text()+"\n")
    with pytest.raises(ValueError, match="SHA mismatch"):
        run(*args[:4])
    assert not args[3].exists()


def test_15m_inputs_not_read_or_scored_in_cost_diagnostics(tmp_path):
    args = fixture_files(tmp_path)
    path = args[0]/"TEST_15"
    path.mkdir()
    (path/"events.csv.gz").write_bytes(b"not-a-valid-gzip-file")
    receipt = run(*args[:4])
    assert receipt["skipped_files"] == [str(path/"events.csv.gz")]
    assert receipt["case_count"] == 1
