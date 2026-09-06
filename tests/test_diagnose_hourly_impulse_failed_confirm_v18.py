"""Synthetic saved-shape diagnostics only; never load real experiment output."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("diagnose_v18", ROOT/"scripts/diagnose_hourly_impulse_failed_confirm_v18.py")
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


def frame(n=251):
    before = np.linspace(-.01, .01, n)
    difference = np.linspace(-.003, .005, n)
    return pd.DataFrame({"event_id": ["synthetic"+str(i) for i in range(n)],
        "mother_decision_time": pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC"),
        "before": before, "after": before+difference, "difference": difference})


def checker(values, *, name, plot):
    assert plot is False and name.startswith("V18 ")
    return {"n": len(values), "test": "Shapiro-Wilk", "statistic": .9, "p_value": .01, "is_normal": False}


def evidence(root, *, mutate=None, table=None):
    table = frame() if table is None else table
    experiment = root/d.EXPERIMENT_RELATIVE
    directory = experiment/"results"
    directory.mkdir(parents=True)
    payload = table.to_csv(index=False).encode()
    (directory/"case_delta.csv").write_bytes(payload)
    summary = {"experiment_id": d.EXPERIMENT_ID, "status": "diagnostic_only_no_candidate_acceptance",
        "holdout_consumed": False, "audit_prices_loaded": False, "training_eligible": False, "production_eligible": False,
        "output_hashes": {"case_delta.csv": hashlib.sha256(payload).hexdigest()},
        "effects": {"case_delta": {"total_pairs": len(table), "n": int(table.difference.notna().sum()),
            "unknown_pairs": int(table.difference.isna().sum()), "mean_bp": float(table.difference.mean()*1e4)}}}
    if mutate: mutate(summary)
    (directory/"summary.json").write_text(json.dumps(summary, allow_nan=False))
    return experiment


def test_pure_descriptives_keep_missing_outliers_and_call_all_three_untrimmed_samples():
    table = frame(6)
    table.loc[4, ["before", "after", "difference"]] = np.nan
    table.loc[5, ["before", "after", "difference"]] = [1., 2., 1.]
    before = table.copy(deep=True)
    calls = []
    def observed(values, *, name, plot):
        calls.append((values.copy(), name, plot))
        return checker(values, name=name, plot=plot)
    result = d.diagnose_frame(table, normality_check=observed, expected_n=6)
    pd.testing.assert_frame_equal(table, before)
    assert len(calls) == 3
    for column, (values, name, plot) in zip(d.COLUMNS, calls):
        x = table[column].dropna().to_numpy()*1e4
        np.testing.assert_array_equal(x, values)
        info = result[column]
        assert info["n"] == 5 and info["missing"] == 1 and info["total"] == 6
        assert info["sd_bp"] == pytest.approx(np.std(x, ddof=1))
        assert info["mean_bp"] == pytest.approx(np.mean(x))
        assert info["quantiles_bp"] == np.quantile(x, d.QUANTILES).tolist()
        assert info["outliers_removed"] == 0 and info["outliers_retained"] == 1
        assert info["outlier_event_ids"] == ["synthetic5"]
        assert info["normality"]["diagnostic_only"] and not info["normality"]["utility_recommendation_applied"]
    json.dumps(result, allow_nan=False)


def test_zero_iqr_nonzero_differences_are_retained_not_marked_bad_data():
    table = frame()
    table["difference"] = 0.
    table.loc[0, "difference"] = 1.
    table["after"] = table.before+table.difference
    result = d.diagnose_frame(table, normality_check=checker)["difference"]
    assert result["zero_iqr"] and result["iqr_outliers"] == 1
    assert result["n"] == 251 and result["outliers_removed"] == 0
    assert result["mean_bp"] == pytest.approx(10000/251)


def test_all_missing_and_small_n_do_not_invent_normality_or_sample_sd():
    table = frame(3)
    table.loc[:, list(d.COLUMNS)] = np.nan
    def must_not_call(*args, **kwargs): raise AssertionError("Insufficient sample")
    result = d.diagnose_frame(table, normality_check=must_not_call, expected_n=3)
    for info in result.values():
        assert info["n"] == 0 and info["missing"] == 3
        assert info["mean_bp"] is info["sd_bp"] is None
        assert info["normality"]["status"] == "insufficient_observations"
    table.loc[0, list(d.COLUMNS)] = [.01, .02, .01]
    result = d.diagnose_frame(table, normality_check=must_not_call, expected_n=3)
    assert result["before"]["n"] == 1 and result["before"]["sd_bp"] is None


@pytest.mark.parametrize("fault", ["short", "duplicate", "empty_id", "null_id", "nan_diff_zero", "difference", "infinity", "bool", "numeric_time", "future_time", "duplicate_columns"])
def test_bad_identity_pairing_or_numeric_input_is_rejected(fault):
    table = frame()
    if fault == "short": table = table.iloc[:-1]
    elif fault == "duplicate": table.loc[0, "event_id"] = table.loc[1, "event_id"]
    elif fault == "empty_id": table.loc[0, "event_id"] = " "
    elif fault == "null_id": table.loc[0, "event_id"] = None
    elif fault == "nan_diff_zero": table.loc[0, "after"] = np.nan; table.loc[0, "difference"] = 0
    elif fault == "difference": table.loc[0, "difference"] = 50
    elif fault == "infinity": table.loc[0, "before"] = np.inf
    elif fault == "bool": table["before"] = table.before.astype(object); table.loc[0, "before"] = True
    elif fault == "numeric_time": table["mother_decision_time"] = 1
    elif fault == "future_time": table.loc[0, "mother_decision_time"] = pd.Timestamp("2025-01-01", tz="UTC")
    else: table = pd.concat([table, table[["before"]]], axis=1)
    with pytest.raises(ValueError): d.diagnose_frame(table, normality_check=checker)


def test_actual_pinned_utility_import_and_plot_false_call_on_synthetic_data():
    # Run this suite with system python3, which has the real seaborn import.
    if not d.UTILITY.is_file():
        pytest.skip("Real optional utility integration requires the fixed local skill file; CI need not contain it")
    pytest.importorskip("seaborn", reason="Real optional skill utility integration requires seaborn; system python3 exercises it")
    utility = d.load_pinned_utility()
    result = d.diagnose_frame(frame(), normality_check=utility.check_normality)
    assert all(info["normality"]["status"] == "computed" for info in result.values())
    constant = frame(4)
    constant.loc[:, list(d.COLUMNS)] = [0., 0., 0.]
    info = d.diagnose_frame(constant, normality_check=utility.check_normality, expected_n=4)["difference"]
    assert info["normality"]["status"] == "degenerate_constant"
    assert info["normality"]["is_normal"] is None and info["normality"]["warnings"]


def test_hash_drift_fails_before_loading_any_utility_code(tmp_path):
    path = tmp_path/"assumption_checks.py"
    path.write_text("raise AssertionError('MUST NOT EXECUTE')\n")
    with pytest.raises(ValueError, match="utility source hash"): d.load_pinned_utility(path)


def test_synthetic_saved_run_records_actual_utility_environment_and_refuses_overwrite(tmp_path):
    if not d.UTILITY.is_file():
        pytest.skip("Real optional utility integration requires the fixed local skill file; CI need not contain it")
    pytest.importorskip("seaborn", reason="Real optional skill utility integration requires seaborn; system python3 exercises it")
    experiment = evidence(tmp_path)
    result = d.run(tmp_path)
    path = experiment/"distribution_diagnostics.json"
    assert json.loads(path.read_text()) == result
    assert result["utility"]["sha256"] == d.UTILITY_SHA256
    assert result["utility"]["actually_imported"] and not result["utility"]["fallback_used"]
    assert result["environment"]["numpy"] == "2.0.2"
    assert not result["raw_prices_read"] and not result["inferential_p_recomputed"] and not result["inference_method_changed"]
    assert "fallback" not in result and "seaborn" not in json.dumps(result)
    saved = path.read_bytes()
    with pytest.raises(ValueError, match="Preserve"): d.run(tmp_path)
    assert path.read_bytes() == saved


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(experiment_id="wrong"), lambda s: s.update(production_eligible=True),
    lambda s: s["output_hashes"].update({"case_delta.csv": "a"*64}),
    lambda s: s["effects"]["case_delta"].update(total_pairs=250),
    lambda s: s["effects"]["case_delta"].update(unknown_pairs=1),
    lambda s: s["effects"]["case_delta"].update(mean_bp=999),
])
def test_rehashed_summary_cannot_change_scope_or_denominator(tmp_path, monkeypatch, mutation):
    # Exercise input/output failure guards in every interpreter. Real utility
    # import/call is tested separately, never faked through sys.modules.
    monkeypatch.setattr(d, "load_pinned_utility", lambda: SimpleNamespace(check_normality=checker))
    experiment = evidence(tmp_path, mutate=mutation)
    with pytest.raises(ValueError): d.run(tmp_path)
    assert not (experiment/"distribution_diagnostics.json").exists()


@pytest.mark.parametrize("fault", ["changed_csv", "failure_receipt", "symlink", "unavailable_utility"])
def test_saved_input_failure_never_writes_partial_diagnostic(tmp_path, monkeypatch, fault):
    experiment = evidence(tmp_path)
    path = experiment/"results/case_delta.csv"
    if fault == "changed_csv": path.write_bytes(path.read_bytes()+b"\n")
    elif fault == "failure_receipt": (experiment/"results/failure.json").write_text('{}')
    elif fault == "symlink":
        moved = tmp_path/"redirect.csv"; path.rename(moved); path.symlink_to(moved)
    else:
        def unavailable(): raise ModuleNotFoundError("Synthetic unavailable dependency")
        monkeypatch.setattr(d, "load_pinned_utility", unavailable)
    with pytest.raises((ValueError, ModuleNotFoundError)): d.run(tmp_path)
    assert not (experiment/"distribution_diagnostics.json").exists()
