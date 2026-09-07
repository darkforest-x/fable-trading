"""Source-first V32 runner over saved 2023--2024 V24 labels / V31 contexts.

No raw-price, future-feature, holdout, parameter-search or execution path.
All pinned files are hashed before parsing; timestamp-only CSV preflight
precedes outcome materialization. The separately frozen economics do not
change V31's support-only decision. Whole-month inference is frozen
before reading subgroup outcomes. See the experiment PROJECT_PLAN.md.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_volume_wave_economics import analyze, PRIMARY_SERIES, GATE
from yoyo.evaluation.hourly_impulse_fixed_clock_analysis import diagnose

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path("experiments/active/exp-btcusdtp-1h-volume-wave-economics-preholdout-20260907-v32")
V24 = Path("experiments/active/exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24/results")
V31 = Path("experiments/active/exp-btcusdtp-1h-volume-wave-support-preholdout-20260907-v31")
CONFIG_SHA = "a4684fa36f16f86a468c951fc574e29c6f4feab4f9c28d7f7ee0b3097259ba5c"
OWN_SOURCES = [
    "yoyo/evaluation/hourly_impulse_volume_wave_economics.py",
    "yoyo/evaluation/hourly_impulse_volume_wave_economic_research.py",
    "tests/test_hourly_impulse_volume_wave_economics.py",
    "tests/test_hourly_impulse_volume_wave_economic_research.py",
    "yoyo/evaluation/hourly_impulse_structure_event_support.py",
    "scripts/audit_hourly_impulse_volume_wave_economics_v32.py",
    "tests/test_audit_hourly_impulse_volume_wave_economics_v32.py",
    str(EXPERIMENT / "config.json"), str(EXPERIMENT / "PROJECT_PLAN.md"),
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False,
                                   default=str) + "\n")


def now():
    return pd.Timestamp.now(tz="UTC").isoformat()


def committed_sources(root, paths):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sources = []
    for name in dict.fromkeys(paths):
        blob = subprocess.check_output(["git", "show", commit + ":" + name], cwd=root)
        sha = hashlib.sha256(blob).hexdigest()
        if sha != digest(root / name):
            raise ValueError("Commit source first: " + name)
        sources.append(dict(path=name, sha256=sha))
    return commit, sources


def verify_hashes(root, config):
    if digest(root / EXPERIMENT / "config.json") != CONFIG_SHA:
        raise ValueError("V32 frozen configuration changed")
    for name, sha in {**config["inputs"], **config["source_pins"]}.items():
        if digest(root / name) != sha:
            raise ValueError("Pinned input/source changed: " + name)


def preflight(frame):
    """Timestamp-only fixed-clock rows; explicit UTC, native-hour own decisions."""
    out = {}
    for field in ("decision_time", "endpoint_time"):
        raw = frame[field]
        if raw.isna().any() or raw.map(lambda x: not isinstance(x, str) or
                not (x.endswith("Z") or x.endswith("+00:00"))).any():
            raise ValueError("Explicit UTC timestamps required")
        t = pd.to_datetime(raw, utc=True, format="mixed", errors="raise")
        if not (t.ge("2023-01-01") & t.lt("2025-01-01")).all() or not t.eq(t.dt.floor("h")).all():
            raise ValueError("Label outside development/native-hour clock")
        out[field] = t
    h = pd.to_numeric(frame.horizon_hours, errors="raise")
    if not h.isin([1, 4, 12, 24]).all() or not out["endpoint_time"].eq(
            out["decision_time"] + pd.to_timedelta(h, unit="h")).all():
        raise ValueError("Fixed endpoint differs from own decision plus horizon")
    return {key: dict(first=str(t.min()), last=str(t.max())) for key, t in out.items()}


def provenance(root, config):
    """Verify historical receipts, without opening raw prices or new controls."""
    load = lambda path: json.loads((root / path).read_text())
    old, started, sampled = [load(V24 / name) for name in
                             ("summary.json", "started.json", "sampling_frozen.json")]
    if old["status"] != "labels_materialized_not_economic_acceptance" or old["holdout_consumed"] or old["executable_pnl"]:
        raise ValueError("V24 scope/status changed")
    if not pd.Timestamp(started["at"]) <= pd.Timestamp(sampled["at"]) <= pd.Timestamp(old["generated_at"]):
        raise ValueError("V24 source/sampling/label chronology changed")
    if not sampled["before_any_label"] or not sampled["before_any_raw_read"]:
        raise ValueError("V24 sampling was not frozen before labels")
    if old["sources"] != started["sources"] or sampled["sources"] != old["sources"]:
        raise ValueError("V24 source receipts disagree")
    source = old["source_receipt"]
    if source["holdout_price_rows"] != 0 or source["post2024_price_rows"] != 0 or pd.Timestamp(source["phase_price_last_open"]) >= pd.Timestamp("2025-01-01", tz="UTC"):
        raise ValueError("V24 phase boundary not proven")
    for name in ("case_labels.csv.gz", "control_labels.csv.gz", "paired_labels.csv.gz"):
        if old["output_hashes"][name] != config["inputs"][str(V24 / name)]:
            raise ValueError("Saved labels differ from original V24 receipt")
    for row in old["sources"]:
        blob = subprocess.check_output(["git", "show", old["builder_commit"] + ":" + row["path"]], cwd=root)
        if hashlib.sha256(blob).hexdigest() != row["sha256"]:
            raise ValueError("V24 historical source blob changed")
    commit_at = pd.Timestamp(subprocess.check_output(["git", "show", "-s", "--format=%cI", old["builder_commit"]], cwd=root, text=True).strip())
    if commit_at > pd.Timestamp(started["at"]):
        raise ValueError("V24 label builder not committed first")
    audit, support, frozen, wave_started = [load(V31 / path) for path in
        ("audit.json", "results/summary.json", "results/support_frozen.json", "results/started.json")]
    validate_support_receipts(audit, support, frozen, wave_started, config)
    for row in frozen["sources"]:
        blob = subprocess.check_output(["git", "show", frozen["builder_commit"] + ":" + row["path"]], cwd=root)
        if hashlib.sha256(blob).hexdigest() != row["sha256"]:
            raise ValueError("V31 historical source blob changed")
    wave_commit_at = pd.Timestamp(subprocess.check_output(
        ["git", "show", "-s", "--format=%cI", frozen["builder_commit"]], cwd=root, text=True).strip())
    if wave_commit_at > pd.Timestamp(wave_started["at"]):
        raise ValueError("V31 support builder not committed first")
    return dict(v24_builder=old["builder_commit"], v24_sources=len(old["sources"]),
                v31_builder=frozen["builder_commit"], v31_auditor=audit["auditor_commit"],
                historical_phase_verified=True, raw_prices_read=False, holdout_consumed=False,
                reused_development=True)


def validate_support_receipts(audit, support, frozen, started, config):
    """Pure metadata validation of the already frozen V31 support experiment."""
    if (audit.get("status") != "passed" or audit.get("scalar_replay") is not True
            or audit.get("contexts") != 995 or support.get("support_pass") is not True
            or support.get("status") != "support_pass_requires_separate_outcome_preregistration"):
        raise ValueError("V31 support/audit scope changed")
    for receipt, fields in (
        (audit, ("economic_outcomes_read", "raw5_read", "holdout_consumed")),
        (support, ("outcomes_read_or_computed", "economic_acceptance")),
        (frozen, ("outcomes_read_or_computed", "raw5_read", "holdout_consumed"))):
        if any(receipt.get(field) is not False for field in fields):
            raise ValueError("V31 was not outcome-blind support")
    if support["support_frozen_sha256"] != config["inputs"][str(V31 / "results/support_frozen.json")]:
        raise ValueError("V31 support freeze hash changed")
    if not (started["sources"] == frozen["sources"]
            and started["builder_commit"] == frozen["builder_commit"] == support["builder_commit"]):
        raise ValueError("V31 source receipts disagree")
    if any(audit["input_and_output_hashes"].get(row["path"]) != row["sha256"]
           for row in frozen["sources"]):
        raise ValueError("V31 audit does not cover frozen sources")
    if not (pd.Timestamp(started["at"]) <= pd.Timestamp(frozen["at"]) <=
            pd.Timestamp(support["generated_at"]) <= pd.Timestamp(audit["at"])):
        raise ValueError("V31 freeze/summary/audit chronology changed")
    if support.get("accepted_case_control_states") != dict(total=300, accepted=158, abstain=142, unknown=0, known=300):
        raise ValueError("Original accepted-case control support changed")
    for kind in ("case", "control"):
        expected = config["population"][kind + "_states"]
        if {s: support["population"][kind][s] for s in expected} != expected:
            raise ValueError("V31 frozen gate counts changed")
        symmetry = audit["direction_symmetry_control"][kind]
        if (symmetry["accepted"] != expected["accepted"] or
                symmetry["direction_reversed_accepted"] != expected["abstain"] or
                symmetry["flat"] != 0 or symmetry["known"] != expected["accepted"] + expected["abstain"]):
            raise ValueError("V31 scalar audit counts disagree")
        name = kind + "_context.csv.gz"
        path = str(V31 / "results" / name)
        sha = config["inputs"][path]
        if not (support["output_hashes"][name] == frozen["output_hashes"][name] ==
                audit["input_and_output_hashes"][path] == sha):
            raise ValueError("V31 context hash receipts disagree")


def run(root=ROOT):
    root = Path(root)
    if digest(root / EXPERIMENT / "config.json") != CONFIG_SHA:
        raise ValueError("V32 frozen configuration changed")
    config = json.loads((root / EXPERIMENT / "config.json").read_text())
    if np.__version__ != "2.0.2" or pd.__version__ != "2.3.3":
        raise ValueError("Numeric version contract differs")
    commit, sources = committed_sources(root, OWN_SOURCES + list(config["source_pins"]))
    directory = root / EXPERIMENT / "results"
    directory.mkdir(exist_ok=False)
    write_json(directory / "started.json", dict(at=now(), builder_commit=commit, sources=sources,
        inputs=config["inputs"], config_sha256=CONFIG_SHA, before_subgroup_outcome_read=True))
    try:
        verify_hashes(root, config)
        receipt = provenance(root, config)
        # Dependency/source preflight must happen before real outcome reads.
        helper = root / "scripts/diagnose_hourly_impulse_failed_confirm_v18.py"
        spec = importlib.util.spec_from_file_location("_v32_pinned_diagnostics", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        utility = module.load_pinned_utility()
        diagnose([0., .001, -.001, .002], ["synthetic-0", "synthetic-1", "synthetic-2", "synthetic-3"], utility.check_normality)
        clocks = {kind: preflight(pd.read_csv(root / V24 / (kind + "_labels.csv.gz"),
            usecols=["decision_time", "endpoint_time", "horizon_hours"])) for kind in ("case", "control")}
        write_json(directory / "preflight.json", dict(at=now(), clocks=clocks,
            before_outcome_materialization=True, provenance=receipt))
        labels = [pd.read_csv(root / V24 / (name + "_labels.csv.gz")) for name in ("case", "control", "paired")]
        contexts = [pd.read_csv(root / V31 / "results" / (kind + "_context.csv.gz")) for kind in ("case", "control")]
        tables, summary = analyze(*labels, *contexts)
        primary = tables["mother_ledger"].loc[lambda d: d.horizon_hours.eq(4)]
        diagnostics = {}
        for name in PRIMARY_SERIES:
            part = primary.loc[primary[GATE].eq("accepted")] if name.startswith("accepted_") else primary
            diagnostics[name] = diagnose(part[name], part.event_id, utility.check_normality)
        if digest(module.UTILITY) != module.UTILITY_SHA256:
            raise ValueError("Assumption utility changed during calculation")
        verify_hashes(root, config)
        if committed_sources(root, OWN_SOURCES + list(config["source_pins"])) != (commit, sources):
            raise ValueError("Sources changed during calculation")
        hashes = {}
        for name, table in tables.items():
            path = directory / (name + ".csv.gz")
            table.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
            hashes[path.name] = digest(path)
        write_json(directory / "diagnostics.json", dict(at=now(), series=diagnostics,
            utility_path=str(module.UTILITY), utility_sha256=module.UTILITY_SHA256,
            before_interpretation=True, test_selection_changed=False, outliers_removed=0))
        hashes["diagnostics.json"] = digest(directory / "diagnostics.json")
        summary.update(experiment_id=EXPERIMENT.name, generated_at=now(), builder_commit=commit,
            sources=sources, input_receipt=receipt, output_hashes=hashes, config_sha256=CONFIG_SHA,
            holdout_consumed=False, raw_prices_read=False, new_controls_sampled=False,
            parameter_search=False, v31_support_result="100 accepted; support pass only, separately evaluated here")
        write_json(directory / "summary.json", summary)
        return summary
    except Exception as error:
        write_json(directory / "failure.json", dict(at=now(), status="failed_not_evidence",
                   error_type=type(error).__name__, message=str(error)))
        raise


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = run()
    print(json.dumps({"primary": {k: {"n": v["n_known"], "mean_bp": v["mean"]*10000, "ci95_bp": [x*10000 for x in v["monthly_cluster"]["ci95"]], "holm_p": v["holm_p"]} for k,v in result["primary"].items()}, "decision": result["decision"]}, indent=2))
