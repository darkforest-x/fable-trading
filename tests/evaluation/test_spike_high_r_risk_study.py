"""Identity and event coverage checks for the High-R research runner."""
import json

import pandas as pd
import pytest

from yoyo.evaluation.spike_high_r_risk_study import completed, digest, tag
from yoyo.evaluation.spike_high_r_risk_report import block_test, periods
from yoyo.evaluation import spike_high_r_risk_study as study


def test_receipt_rejects_modified_outputs(tmp_path):
    p = tmp_path / "trades.csv"
    p.write_text("original")
    r = dict(status="complete", key=tmp_path.name, run_identity="test", files={p.name: digest(p)})
    (tmp_path / "completion.json").write_text(json.dumps(r))
    assert completed(tmp_path, "test") == r
    p.write_text("changed")
    with pytest.raises(ValueError, match="hash changed"):
        completed(tmp_path, "test")


def test_receipt_rejects_foreign_identity(tmp_path):
    (tmp_path / "completion.json").write_text(json.dumps(dict(status="complete", key=tmp_path.name,
        run_identity="old", files={})))
    with pytest.raises(ValueError, match="identity"):
        completed(tmp_path, "new")


def test_tag_uses_signal_identity_not_execution_row_number():
    d = pd.DataFrame(dict(stream_key=["x", "x"], signal_i=[14, 14], side=[1, -1]))
    assert tag(d, "high_r_entry_v21").event_key.tolist() == ["x:14:1", "x:14:-1"]
    with pytest.raises(ValueError, match="duplicate"):
        tag(pd.concat([d, d]), "high_r_entry_v21")


def test_time_split_excludes_unresolved_and_crossing_from_earlier():
    d = pd.DataFrame({"entry_time": pd.to_datetime(["2025-09-09", "2025-09-09", "2025-09-10", "2025-09-09"], utc=True),
                      "exit_time": pd.to_datetime(["2025-09-09", "2025-09-10", "2025-09-11", None], utc=True)})
    p = dict(periods(d))
    assert p["earlier"].index.tolist() == [0]
    assert p["later"].index.tolist() == [2]
    assert p["crossing"].index.tolist() == [1, 3]


def test_month_signflip_known_exact_null():
    assert block_test([1, 1, 1])["p"] == .125
    assert block_test([0, 0, 0])["p"] == 1.
    assert block_test([-1, -1, -1])["p"] == 1.


def test_statistics_receipt_is_pinned_not_self_authenticating(tmp_path, monkeypatch):
    cfg = json.loads((study.EXP / "config.json").read_text())
    monkeypatch.setattr(study, "STATS", tmp_path)
    monkeypatch.setattr(study, "EXP", tmp_path)
    cfg["statistics_receipt_sha256"] = "0"*64
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    (tmp_path / "statistics_receipt.json").write_text(json.dumps({"source_receipts": [], "files": {}}))
    with pytest.raises(ValueError, match="identity changed"):
        study.verified_sources()


def test_imported_execution_constants_cannot_drift_from_plan(monkeypatch):
    cfg = json.loads((study.EXP / "config.json").read_text())
    study.validate_contract(cfg)
    monkeypatch.setattr(study.base, "ENTRY_COST", .002)
    with pytest.raises(ValueError, match="cost differs"):
        study.validate_contract(cfg)


def test_auc_direction_and_ties():
    from yoyo.evaluation.spike_high_r_risk_report import auc_score
    assert auc_score([False, True], [0., 1.]) == 1.
    assert auc_score([False, True], [1., 0.]) == 0.
    assert auc_score([False, True], [1., 1.]) == .5
    assert auc_score([True, True], [0., 1.]) is None


def test_rate_bootstrap_preserves_counts_and_missing_arm_months():
    from yoyo.evaluation.spike_high_r_risk_report import rate_contrast
    d = pd.DataFrame(dict(arm=["baseline"]*4+["high_r_entry_v21"]*2,
        censored=[False]*6, entry_time=pd.to_datetime(["2025-01-01"]*3+["2025-02-01"]+["2025-01-01"]*2, utc=True),
        net_r=[-1.,-1.,11.,-1.,11.,12.]))
    got = rate_contrast(d)
    assert got["win_rate_delta"] == pytest.approx(.75)
    assert got["gt10_rate_delta"] == pytest.approx(.75)
    assert got["shared_months"] == 1
    assert got["valid_bootstrap_draws"] < 2000


def test_control_bin_evidence_rejects_mismatch():
    from types import SimpleNamespace
    import numpy as np
    stamp = pd.Timestamp("2025-01-01", tz="UTC")
    prepared = SimpleNamespace(frame=pd.DataFrame(index=[stamp]), atr=np.array([1.]), close=np.array([100.]))
    c = pd.DataFrame(dict(control_signal_time=[stamp], side=[1], vol_bin=[1], month=["2025-01"]))
    study.enrich_control_bins(c, prepared)
    assert c.control_vol_bin.iloc[0] == 1
    c.loc[0,"vol_bin"] = 2
    with pytest.raises(ValueError, match="provenance"):
        study.enrich_control_bins(c, prepared)


def test_higher_rates_do_not_pass_when_economics_remain_negative():
    from yoyo.evaluation.spike_high_r_risk_report import evaluate_gate
    rows = pd.DataFrame(dict(dimension=["all"]*2, period=["later"]*2,
        arm=["baseline", "high_r_entry_v21"], win_rate=[.3,.4], gt10_rate=[.01,.02],
        mean_net_bp=[-20.,-1.], paired_excess_r=[0.,.1], random_p=[1.,.001], closed=[100,50]))
    rates = pd.DataFrame(dict(period=["later"], win_rate_ci_low=[.01], gt10_rate_ci_low=[.001]))
    got = evaluate_gate(rows, rates)
    assert got["status"] == "rejected"
    assert got["failed_checks"] == ["mean_net_bp_positive"]


def test_empty_treatment_later_period_is_explicit_rejection():
    from yoyo.evaluation.spike_high_r_risk_report import evaluate_gate
    rows = pd.DataFrame(dict(dimension=["all"], period=["later"], arm=["baseline"], closed=[100]))
    got = evaluate_gate(rows, pd.DataFrame())
    assert got["status"] == "rejected"
    assert got["failed_checks"] == ["later_closed_samples_in_both_arms"]


def test_monthly_cutoff_excludes_current_and_future_feature_values():
    from yoyo.evaluation.spike_high_r_risk_calibration import build_thresholds
    history = pd.DataFrame(dict(available_at=pd.date_range("2025-01-01", periods=120, freq="12h", tz="UTC"), timeframe_min=[60]*120, reference_risk_fraction=[.01]*120))
    before = build_thresholds(history, ["2025-04"])
    future = pd.DataFrame(dict(available_at=pd.to_datetime(["2025-04-01", "2025-05-01"], utc=True), timeframe_min=[60,60], reference_risk_fraction=[.000001,100.]))
    after = build_thresholds(pd.concat([history,future]), ["2025-04"])
    pd.testing.assert_frame_equal(before, after)
    row = before.loc[before.timeframe_min.eq(60)].iloc[0]
    assert row.known and row.cutoff == pytest.approx(.01) and row.n_history == 120
    assert not before.loc[before.timeframe_min.eq(30)].iloc[0].known


def test_monthly_cutoff_requires_complete_source_window():
    from yoyo.evaluation.spike_high_r_risk_calibration import build_thresholds
    d = pd.DataFrame(dict(available_at=pd.date_range("2024-09-10", periods=120, freq="h", tz="UTC"), timeframe_min=[60]*120, reference_risk_fraction=[.01]*120))
    got = build_thresholds(d, ["2024-10"])
    assert not got.known.any()
