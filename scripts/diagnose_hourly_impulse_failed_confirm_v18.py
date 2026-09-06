"""Saved V18 distribution diagnostics; no raw prices, filtering or new inference.

The pinned statistical-analysis skill utility is actually imported and its
check_normality(values, name=..., plot=False) is called. System python3 has
its plotting-import dependencies; the repository .venv may not. Import failure
is an explicit failure, not an automatic SciPy fallback or dependency install.
Normality never selects another inferential test. All finite values, including
IQR outliers and zero changes, remain in descriptive statistics. Missing pairs
stay missing; infinities are rejected, not silently omitted as unknown.

Quantiles use NumPy2 linear interpolation; SD uses ddof=1. Shapiro-Wilk is a
distribution description, not evidence of IID trades or a normality proof:
https://numpy.org/doc/2.0/reference/generated/numpy.quantile.html
https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.stats.shapiro.html
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd
import scipy


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18"
EXPERIMENT_RELATIVE = "experiments/active/"+EXPERIMENT_ID
UTILITY = Path("/Users/zhangzc/.codex/skills/statistical-analysis/scripts/assumption_checks.py")
UTILITY_SHA256 = "3fabf359ab0128bbfc498dadb53a4782431cde473c0a6681ea7298a605757018"
QUANTILES = (0., .05, .25, .5, .75, .95, 1.)
COLUMNS = ("before", "after", "difference")


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def load_pinned_utility(path=UTILITY):
    """No fallback, dependency stub or install; verify actual utility source."""
    path = Path(path).resolve()
    if digest(path.read_bytes()) != UTILITY_SHA256:
        raise ValueError("Assumption utility source hash changed")
    spec = importlib.util.spec_from_file_location("_v18_pinned_assumption_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if digest(path.read_bytes()) != UTILITY_SHA256:
        raise ValueError("Assumption utility changed during import")
    if not callable(getattr(module, "check_normality", None)):
        raise ValueError("Pinned utility omitted check_normality")
    return module


def diagnose_frame(frame, *, normality_check, expected_n=251):
    """Pure supplied-frame diagnostics; callback sees untrimmed finite bp data.

    Uses event_id, mother_decision_time, before, after, difference only. The
    original case population is retained; each variable states its own finite
    n and missing count. No normality choice changes the calculation path.
    """
    required = {"event_id", "mother_decision_time", *COLUMNS}
    if (isinstance(expected_n, (bool, np.bool_)) or not isinstance(expected_n, (int, np.integer))
            or expected_n < 1 or len(frame) != expected_n):
        raise ValueError("Every original case is required")
    if frame.columns.duplicated().any() or not required.issubset(frame.columns):
        raise ValueError("Missing or duplicate case-delta columns")
    ids = frame.event_id
    if ids.isna().any() or ids.duplicated().any() or ids.map(lambda x: not isinstance(x, str) or not x.strip()).any():
        raise ValueError("Unique nonempty original event IDs required")
    times = frame.mother_decision_time
    if times.map(lambda x: isinstance(x, (int, float, bool, np.number))).any():
        raise ValueError("Case time must be an explicit timestamp")
    times = pd.to_datetime(times, utc=True, errors="raise", format="mixed")
    if times.isna().any() or times.duplicated().any() or not (times.ge("2023-01-01") & times.lt("2025-01-01")).all():
        raise ValueError("Unique development2023--2024 case times required")
    arrays = {}
    for name in COLUMNS:
        if frame[name].map(lambda x: isinstance(x, (bool, np.bool_))).any():
            raise ValueError("Boolean is not an observed return")
        values = pd.to_numeric(frame[name], errors="raise").to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any():
            raise ValueError("Infinite outcomes are not missing values")
        arrays[name] = values
    with np.errstate(over="ignore", invalid="ignore"):
        difference = arrays["after"]-arrays["before"]
    if np.isinf(difference).any() or not np.allclose(arrays["difference"], difference, rtol=0, atol=1e-12, equal_nan=True):
        raise ValueError("Paired difference must remain after-before including missing pairs")
    info = {}
    for name in COLUMNS:
        finite = np.isfinite(arrays[name])
        with np.errstate(over="ignore", invalid="ignore"):
            x = arrays[name][finite]*1e4
        if not np.isfinite(x).all():
            raise ValueError("Nonfinite basis-point arithmetic")
        n = len(x)
        q = np.quantile(x, QUANTILES, method="linear") if n else None
        iqr = float(q[4]-q[2]) if n else None
        lower = float(q[2]-1.5*iqr) if n else None
        upper = float(q[4]+1.5*iqr) if n else None
        mask = ((x < lower) | (x > upper)) if n else np.zeros(0, dtype=bool)
        normality = {"status": "insufficient_observations", "test": "Shapiro-Wilk", "n": n,
                     "statistic": None, "p_value": None, "is_normal": None, "warnings": [],
                     "diagnostic_only": True, "utility_recommendation_applied": False}
        if n >= 3:
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always")
                observed = normality_check(x.copy(), name="V18 "+name+" (bp)", plot=False)
            if observed.get("n") != n or observed.get("test") != "Shapiro-Wilk":
                raise ValueError("Utility result does not describe the supplied sample")
            statistic, pvalue = float(observed["statistic"]), float(observed["p_value"])
            if not np.isfinite([statistic, pvalue]).all() or not 0 <= pvalue <= 1 or not 0 <= statistic <= 1:
                raise ValueError("Invalid normality result")
            constant = bool(np.all(x == x[0]))
            normality.update(status="degenerate_constant" if constant else "computed",
                statistic=statistic, p_value=pvalue, is_normal=None if constant else bool(observed["is_normal"]),
                warnings=[str(item.message) for item in emitted])
        info[name] = {"total": len(frame), "n": n, "missing": len(frame)-n,
            "mean_bp": float(np.mean(x)) if n else None, "sd_bp": float(np.std(x, ddof=1)) if n > 1 else None,
            "sd_ddof": 1, "quantile_levels": list(QUANTILES), "quantile_method": "linear",
            "quantiles_bp": q.tolist() if n else [None]*len(QUANTILES),
            "iqr_bp": iqr, "iqr_lower_bp": lower, "iqr_upper_bp": upper,
            "iqr_outliers": int(mask.sum()), "outlier_event_ids": ids[finite].to_numpy()[mask].tolist(),
            "outliers_removed": 0, "outliers_retained": int(mask.sum()), "zero_iqr": iqr == 0 if n else None,
            "normality": normality}
    # Reject numerical overflow in descriptive aggregates rather than emitting
    # an invalid JSON number or disguising arithmetic failure as missingness.
    json.dumps(info, allow_nan=False)
    return info


def run(root=ROOT):
    """Read only fixed saved V18 inputs and exclusively create one diagnostic."""
    root = Path(root).resolve()
    directory = root/EXPERIMENT_RELATIVE
    output = directory/"distribution_diagnostics.json"
    if output.exists() or output.is_symlink():
        raise ValueError("Preserve prior distribution diagnostics")
    if directory.resolve() != directory:
        raise ValueError("Experiment directory changed identity")
    paths = {name: directory/"results"/name for name in ("summary.json", "case_delta.csv")}
    if any(path.resolve() != path for path in paths.values()):
        raise ValueError("Saved evidence symlink changed identity")
    if (directory/"results/failure.json").exists():
        raise ValueError("Failed research is not diagnostic evidence")
    def reject_constant(value):
        raise ValueError("Nonfinite summary JSON: "+value)
    payload = paths["summary.json"].read_bytes()
    summary = json.loads(payload, parse_constant=reject_constant)
    if summary.get("experiment_id") != EXPERIMENT_ID or summary.get("status") != "diagnostic_only_no_candidate_acceptance":
        raise ValueError("Wrong experiment/status")
    if any(summary.get(flag) is not False for flag in ("holdout_consumed", "audit_prices_loaded", "training_eligible", "production_eligible")):
        raise ValueError("Unexpected evidence eligibility or price scope")
    data = paths["case_delta.csv"].read_bytes()
    sha = digest(data)
    if sha != summary.get("output_hashes", {}).get("case_delta.csv"):
        raise ValueError("Saved case_delta source pin mismatch")
    frame = pd.read_csv(io.BytesIO(data), dtype={"event_id": str})
    utility = load_pinned_utility()
    distributions = diagnose_frame(frame, normality_check=utility.check_normality)
    effect = summary["effects"]["case_delta"]
    if any(effect.get(field) != value or isinstance(effect.get(field), bool) for field, value in
           (("total_pairs", 251), ("n", distributions["difference"]["n"]), ("unknown_pairs", distributions["difference"]["missing"]))):
        raise ValueError("Summary paired denominator mismatch")
    observed_mean = distributions["difference"]["mean_bp"]
    if (observed_mean is None and effect.get("mean_bp") is not None) or (observed_mean is not None and
            (not isinstance(effect.get("mean_bp"), (int, float)) or isinstance(effect.get("mean_bp"), bool)
             or not np.isclose(observed_mean, effect["mean_bp"], rtol=1e-10, atol=1e-8))):
        raise ValueError("Summary paired mean mismatch")
    result = {"experiment_id": EXPERIMENT_ID, "at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": sha, "summary_sha256": digest(payload),
        "utility": {"path": str(UTILITY), "sha256": UTILITY_SHA256, "function": "check_normality", "plot": False,
                    "actually_imported": True, "fallback_used": False},
        "environment": {"executable": sys.executable, "python": sys.version.split()[0],
                        "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__},
        "distributions": distributions, "raw_prices_read": False, "inference_method_changed": False,
        "inferential_p_recomputed": False, "outliers_removed": 0,
        "warning": "Descriptive normality only: temporal dependence and repeated development reuse remain. No inference switching or tail deletion. Zero-IQR outliers are not invalid data; constant-sample normality is not interpretable."}
    encoded = json.dumps(result, indent=2, allow_nan=False)+"\n"
    with output.open("x") as handle:
        handle.write(encoded)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(run(args.root), allow_nan=False))


if __name__ == "__main__":
    main()
