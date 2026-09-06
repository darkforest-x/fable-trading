"""Saved-only V19 descriptives using pinned reusable V18 diagnostics.

Read all251 paired observations for D and I, retaining97 unmatched I values
as unknown. Quantiles, sample SD and Shapiro diagnostics do not choose another
test or delete extremes. No raw market I/O, fitted model or fee repricing.
The source utility is actually imported; no dependency installation/fallback.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-1h-failed-reduce-preholdout-20260906-v19"
E = ROOT/"experiments/active"/EXPERIMENT_ID
HELPER_SHA = "5bdb0ad504ade6ba42b3fcf602587f794ecbac4a1f08f3e9211167cd9c4e6e44"


def main():
    relative = Path(__file__).resolve().relative_to(ROOT).as_posix()
    if subprocess.check_output(["git", "show", "HEAD:"+relative], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError("Commit diagnostics before execution")
    path = Path(__file__).with_name("diagnose_hourly_impulse_failed_confirm_v18.py")
    if hashlib.sha256(path.read_bytes()).hexdigest() != HELPER_SHA:
        raise ValueError("Pinned descriptive helper changed")
    spec = importlib.util.spec_from_file_location("v19_saved_diagnostics_helper", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    output = E/"distribution_diagnostics.json"
    if output.exists() or (E/"results/failure.json").exists():
        raise ValueError("Preserve prior diagnostics; failed research is not evidence")
    summary = json.loads((E/"results/summary.json").read_text())
    if summary["experiment_id"] != EXPERIMENT_ID or summary["status"] != "diagnostic_only_no_candidate_acceptance":
        raise ValueError("Wrong experiment/status")
    if any(summary[k] is not False for k in ("holdout_consumed", "audit_prices_loaded", "production_eligible", "training_eligible")):
        raise ValueError("Unjustified price scope or eligibility")
    utility = helper.load_pinned_utility()
    groups = {}
    for name in ("case_delta", "excess_delta"):
        path = E/"results"/(name+".csv")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != summary["output_hashes"][path.name]:
            raise ValueError("Saved paired evidence changed")
        frame = pd.read_csv(path, dtype={"event_id": str})
        result = helper.diagnose_frame(frame, normality_check=lambda x, **kw:
            utility.check_normality(x, **dict(kw, name=kw["name"].replace("V18", "V19 "+name))))
        effect, delta = summary["effects"][name], result["difference"]
        if effect["total_pairs"] != 251 or effect["n"] != delta["n"] or effect["unknown_pairs"] != delta["missing"]:
            raise ValueError("Paired denominator changed")
        if not np.isclose(effect["mean_bp"], delta["mean_bp"], rtol=1e-10, atol=1e-8):
            raise ValueError("Summary mean differs from saved observations")
        groups[name] = {"input_sha256": sha, "distributions": result}
    info = {"experiment_id": EXPERIMENT_ID, "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "helper_sha256": HELPER_SHA, "utility_sha256": helper.UTILITY_SHA256,
        "versions": {"numpy": np.__version__, "pandas": pd.__version__, "scipy": helper.scipy.__version__},
        "groups": groups, "raw_prices_read": False, "primary_inference_changed": False,
        "outliers_removed": 0, "iid_claimed": False}
    with output.open("x") as handle:
        handle.write(json.dumps(info, indent=2, allow_nan=False)+"\n")
    print(json.dumps(info, allow_nan=False))


if __name__ == "__main__":
    main()
