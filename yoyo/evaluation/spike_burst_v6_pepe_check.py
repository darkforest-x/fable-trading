"""Small frozen PEPE 1H V5→V6 functional check; it never computes returns.

The check consumes one already-authenticated local feature artifact only after
the V6 source and plan are committed. It compares actual structural signal bars
inside one known-positive 24-hour slice and writes retention/movement receipts,
not performance claims or a two-year replay.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_dataset import load_feature
from yoyo.evaluation.spike_burst_early_warning import detect as legacy_detect
from yoyo.evaluation.spike_burst_progressive import progressive_fields
from yoyo.evaluation.spike_burst_v5_structure import detect as detect_v5
from yoyo.evaluation.spike_burst_v6_structure import detect as detect_v6


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-spike-v6-volume-price-20260911-v1"
SOURCE = ROOT / "experiments/active/exp-spike-burst-validation-20260910-v1/results/features/75d10ad8568817c52dd030fd1e6a.pkl.gz"
SOURCE_SHA = "031d3e57bb6081002e3ce4484e999b90ca7759322e6e78da8d9d61173248d07a"
WINDOW_START = pd.Timestamp("2026-08-19T12:00Z")
WINDOW_END = pd.Timestamp("2026-08-20T12:00Z")

OWNED = (
    "yoyo/evaluation/pine/spike_burst_v6.pine",
    "yoyo/evaluation/spike_burst_v6_structure.py",
    "yoyo/evaluation/spike_burst_v6_pepe_check.py",
    "tests/test_spike_burst_v6_structure.py",
    "experiments/active/exp-spike-v6-volume-price-20260911-v1/PROJECT_PLAN.md",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_committed() -> dict[str, str]:
    pins = {}
    for relative in OWNED:
        committed = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT)
        path = ROOT / relative
        if committed != path.read_bytes():
            raise ValueError("Commit exact V6 implementation and plan before PEPE replay: " + relative)
        pins[relative] = sha(path)
    return pins


def _supplied(frame: pd.DataFrame) -> pd.DataFrame:
    legacy = legacy_detect(frame)
    progress = progressive_fields(frame)
    out = frame[["open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ready"]].copy()
    out["legacy_confirmed"] = legacy.confirmed.astype(bool)
    out["legacy_parent_high"] = legacy.frozen_parent_high
    out["legacy_parent_low"] = [
        float(legacy.prog_prior_low.iloc[int(parent)]) if np.isfinite(parent) else np.nan
        for parent in legacy.parent_i
    ]
    out["advance3"] = progress.prog_advance
    out["volume_ratio3"] = progress.prog_volume_ratio
    out["data_gap"] = False
    out["confirmed"] = True
    return out


def main() -> None:
    pins = _require_committed()
    frame = load_feature(SOURCE, SOURCE_SHA, 60, pd.Timestamp("2026-09-09T00:00Z"))
    supplied = _supplied(frame)
    v5, v6 = detect_v5(supplied), detect_v6(supplied)
    reviewed = pd.DataFrame(index=supplied.index)
    reviewed["v5"] = v5.confirmed
    reviewed["v6"] = v6.confirmed
    reviewed["v6_evidence"] = v6.evidence
    reviewed["v6_evidence_i"] = v6.evidence_i
    reviewed["v6_reason"] = v6.why_pending
    reviewed["close"] = supplied.close
    window = reviewed.loc[(reviewed.index >= WINDOW_START) & (reviewed.index < WINDOW_END)].copy()
    retained = window.index[window.v5 & window.v6].astype(str).tolist()
    v5_only = window.index[window.v5 & ~window.v6].astype(str).tolist()
    v6_only = window.index[~window.v5 & window.v6].astype(str).tolist()
    # The owner-selected point is a known V5 completion at Aug20 00:00 BJT.
    # V6 may legitimately defer or reject it; record that state instead of
    # manufacturing a favorable retention assertion.
    known = pd.Timestamp("2026-08-19T16:00Z")
    known_v5 = bool(window.loc[known, "v5"])
    known_v6 = bool(window.loc[known, "v6"])
    if not known_v5:
        raise AssertionError("Known PEPE V5 completion is absent from frozen feature replay")
    prefix = supplied.loc[:known]
    pd.testing.assert_frame_equal(detect_v6(prefix), v6.loc[prefix.index])
    output = EXPERIMENT / "results"
    output.mkdir(parents=True, exist_ok=True)
    trace = output / "pepe_1h_v5_v6_functional_trace.csv"
    window.to_csv(trace, index_label="bar_open_utc")
    receipt = {
        "schema": "spike-v6-pepe-1h-functional-v1",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_path": str(SOURCE.relative_to(ROOT)), "source_sha256": SOURCE_SHA,
        "source_pins": pins, "window": [str(WINDOW_START), str(WINDOW_END)],
        "v5_count": int(window.v5.sum()), "v6_count": int(window.v6.sum()),
        "retained_v5_v6": retained, "v5_only_explicitly_rejected": v5_only,
        "v6_only": v6_only, "known_pepe_v5": known_v5, "known_pepe_v6": known_v6,
        "prefix_causal": True,
        "trace_sha256": sha(trace), "economic_evaluation": False,
        "holdout_consumption": "authorized functional software check; no outcome scoring",
        "limitations": "One known positive and a software state oracle; no native Pine parity, profit, recall, or broad history claim.",
    }
    (output / "pepe_1h_v5_v6_functional_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in ("v5_count", "v6_count", "retained_v5_v6", "v5_only_explicitly_rejected", "v6_only", "known_pepe_v5", "known_pepe_v6", "prefix_causal")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
