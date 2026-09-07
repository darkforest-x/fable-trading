"""Source-first V30 runner over saved 2023--2024 V24 labels / V29 contexts.

No raw-price, future-feature, holdout, parameter-search or execution path.
All pinned files are hashed before parsing; timestamp-only CSV preflight
precedes outcome materialization. The separately approved economics do not
change V29's support-inconclusive decision. Whole-month inference is frozen
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

from yoyo.evaluation.hourly_impulse_classifier_economics import analyze, PRIMARY_SERIES, GATE
from yoyo.evaluation.hourly_impulse_fixed_clock_analysis import diagnose

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path("experiments/active/exp-btcusdtp-1h-classifier-economics-preholdout-20260907-v30")
V24 = Path("experiments/active/exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24/results")
V29 = Path("experiments/active/exp-btcusdtp-1h-classifier-support-preholdout-20260907-v29")
CONFIG_SHA = "d770a84a1ed930d9929e03f97625137541cc5cc747928d927612fb59cbd45eaf"
OWN_SOURCES = [
    "yoyo/evaluation/hourly_impulse_classifier_economics.py",
    "yoyo/evaluation/hourly_impulse_classifier_economic_research.py",
    "tests/test_hourly_impulse_classifier_economics.py",
    "tests/test_hourly_impulse_classifier_economic_research.py",
    "yoyo/evaluation/hourly_impulse_structure_event_support.py",
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
        raise ValueError("V30 frozen configuration changed")
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
    audit, support = load(V29 / "audit.json"), load(V29 / "results/summary.json")
    if audit["status"] != "passed" or audit["summary_sha256"] != config["inputs"][str(V29 / "results/summary.json")]:
        raise ValueError("V29 audit does not cover saved support")
    for kind in ("case", "control"):
        if {s: audit["population"][kind][s] for s in ("accepted", "abstain", "unknown")} != config["population"][kind + "_states"]:
            raise ValueError("V29 frozen gate counts changed")
        name = kind + "_context.csv.gz"
        if support["output_hashes"][name] != config["inputs"][str(V29 / "results" / name)]:
            raise ValueError("V29 context hash receipt differs")
    return dict(v24_builder=old["builder_commit"], v24_sources=len(old["sources"]),
                v29_builder=audit["builder_commit"], v29_auditor=audit["auditor_commit"],
                historical_phase_verified=True, raw_prices_read=False, holdout_consumed=False,
                reused_development=True)


def run(root=ROOT):
    root = Path(root)
    if digest(root / EXPERIMENT / "config.json") != CONFIG_SHA:
        raise ValueError("V30 frozen configuration changed")
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
        spec = importlib.util.spec_from_file_location("_v30_pinned_diagnostics", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        utility = module.load_pinned_utility()
        diagnose([0., .001, -.001, .002], ["synthetic-0", "synthetic-1", "synthetic-2", "synthetic-3"], utility.check_normality)
        clocks = {kind: preflight(pd.read_csv(root / V24 / (kind + "_labels.csv.gz"),
            usecols=["decision_time", "endpoint_time", "horizon_hours"])) for kind in ("case", "control")}
        write_json(directory / "preflight.json", dict(at=now(), clocks=clocks,
            before_outcome_materialization=True, provenance=receipt))
        labels = [pd.read_csv(root / V24 / (name + "_labels.csv.gz")) for name in ("case", "control", "paired")]
        contexts = [pd.read_csv(root / V29 / "results" / (kind + "_context.csv.gz")) for kind in ("case", "control")]
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
            parameter_search=False, v29_support_result="78<80; unchanged support-inconclusive")
        write_json(directory / "summary.json", summary)
        return summary
    except Exception as error:
        write_json(directory / "failure.json", dict(at=now(), status="failed_not_evidence",
                   error_type=type(error).__name__, message=str(error)))
        raise


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = run()
    print(json.dumps({"primary": result["primary"], "decision": result["decision"]}, indent=2))
