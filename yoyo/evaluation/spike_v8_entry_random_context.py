"""Describe frozen V8 entry-evidence labels against an existing random receipt.

This reduction reads only two frozen CSV artifacts: the causal V8
``event_evidence`` ledger and MA-cycle's pre-existing random-entry receipt.
It adds no candidates, does no replay, and deliberately does not make an
alpha, selection, or acceptance claim.  The source random receipt has no
control exit timestamp, so it is retained solely as descriptive validation
context and is never eligible for development selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


EXP = Path("experiments/active/exp-spike-v8-entry-evidence-20260914-v1")
EVENTS = EXP / "results/full_v1/event_evidence.csv.gz"
RANDOM_RECEIPT = Path(
    "experiments/active/exp-spike-v8-ma-cycle-20260913-v1/"
    "results/full_v1/existing_random_controls_reused.csv"
)
KEY = ["stream_key", "signal_bar_open", "side", "period"]
LABELS = (
    "bb_episode_directional_order9_run3",
    "bb_ma_tight_overlap_run3",
    "htf_opposed_completed",
)
REUSE_LIMITATION = (
    "existing random-control artifact lacks the control exit timestamp; "
    "retained for descriptive context only, never development selection"
)


def sha256(path: Path) -> str:
    """Return SHA-256 without loading a possibly large CSV into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bool(series: pd.Series, name: str) -> pd.Series:
    """Normalize a receipt boolean while refusing silently malformed values."""
    allowed = {True: True, False: False, "True": True, "False": False}
    invalid = series.notna() & ~series.isin(allowed)
    if invalid.any():
        raise ValueError(f"{name} contains non-boolean values")
    return series.map(allowed).astype("boolean")


def _require_columns(frame: pd.DataFrame, expected: set[str], name: str) -> None:
    missing = expected - set(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {', '.join(sorted(missing))}")


def _event_rows(path: Path) -> pd.DataFrame:
    """Read only event identity, fixed labels, and unchanged V8 outcomes."""
    columns = [*KEY, "scoring_closed", "net_r", *LABELS]
    events = pd.read_csv(path, usecols=columns, low_memory=False)
    _require_columns(events, set(columns), "event evidence")
    if events.duplicated(KEY).any():
        raise ValueError("event evidence identity is not unique")
    events["scoring_closed"] = _bool(events["scoring_closed"], "scoring_closed")
    for label in LABELS:
        events[label] = _bool(events[label], label)
    return events


def _random_rows(path: Path) -> pd.DataFrame:
    """Read the old random receipt and verify its documented limitation."""
    columns = [
        *KEY,
        "arm",
        "seed",
        "matched",
        "target_net_r",
        "control_net_r",
        "reuse_limitation",
    ]
    random = pd.read_csv(path, usecols=columns, low_memory=False)
    _require_columns(random, set(columns), "existing random receipt")
    if set(random["arm"].dropna().unique()) != {"v8"}:
        raise ValueError("existing random receipt must contain only frozen V8 rows")
    if random.duplicated([*KEY, "seed"]).any():
        raise ValueError("existing random receipt is not unique by event identity plus seed")
    if set(random["reuse_limitation"].dropna().unique()) != {REUSE_LIMITATION}:
        raise ValueError("existing random receipt limitation text changed")
    random["matched"] = _bool(random["matched"], "matched")
    return random


def _net_r_summary(events: pd.DataFrame, controls: pd.DataFrame) -> dict[str, object]:
    """Summarize one event cohort without counting target outcomes per seed."""
    return {
        "target_events_n": int(len(events)),
        "matched_control_receipts_n": int(len(controls)),
        "target_net_r_sum": float(events["net_r"].sum()),
        "target_net_r_mean": float(events["net_r"].mean()),
        "control_net_r_sum": float(controls["control_net_r"].sum()),
        "control_net_r_mean": float(controls["control_net_r"].mean()),
        "control_minus_target_net_r_mean_per_receipt": float(
            (controls["control_net_r"] - controls["net_r"]).mean()
        ),
    }


def build_tables(events: pd.DataFrame, random: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Attach the three causal labels and derive validation-only descriptions.

    The many-to-one join is intentional: a target V8 event can have several
    existing random seeds.  Target outcomes are first de-duplicated by ``KEY``
    so the target side is not inflated by the number of old receipts.
    """
    joined = random.merge(events, on=KEY, how="left", validate="many_to_one", indicator=True)
    missing = int(joined["_merge"].ne("both").sum())
    if missing:
        raise ValueError(f"{missing} random-receipt rows have no event-evidence identity")
    joined = joined.drop(columns="_merge")
    expected = joined["net_r"].to_numpy(float)
    recorded = joined["target_net_r"].to_numpy(float)
    if not np.isfinite(expected).all() or not np.isfinite(recorded).all():
        raise ValueError("target net R must be finite in both frozen receipts")
    if not np.allclose(expected, recorded, rtol=0.0, atol=1e-10):
        raise ValueError("existing random receipt target_net_r differs from event evidence")

    validation = joined.loc[
        joined["period"].eq("validation") & joined["matched"].eq(True) & joined["scoring_closed"].eq(True)
    ].copy()
    if validation.empty:
        raise ValueError("no matched scoring-closed validation random receipts")
    target = validation.drop_duplicates(KEY).copy()

    random_rows: list[dict[str, object]] = [
        {
            "context": "all_matched_validation_scoring_closed",
            "label": "all",
            "cohort": "all",
            **_net_r_summary(target, validation),
        }
    ]
    outcome_rows: list[dict[str, object]] = []
    validation_events = events.loc[
        events["period"].eq("validation") & events["scoring_closed"].eq(True)
    ].copy()
    for label in LABELS:
        known_target = target.loc[target[label].notna()]
        known_events = validation_events.loc[validation_events[label].notna()]
        for value, cohort in ((True, "target_true"), (False, "complement_false")):
            target_part = known_target.loc[known_target[label].eq(value)]
            controls_part = validation.loc[validation[label].eq(value)]
            random_rows.append(
                {
                    "context": "matched_random_validation_descriptive",
                    "label": label,
                    "cohort": cohort,
                    **_net_r_summary(target_part, controls_part),
                }
            )
            outcome_rows.append(
                {
                    "label": label,
                    "cohort": cohort,
                    "validation_scoring_closed_events_n": int(len(known_events.loc[known_events[label].eq(value)])),
                    "validation_scoring_closed_net_r_sum": float(known_events.loc[known_events[label].eq(value), "net_r"].sum()),
                    "validation_scoring_closed_net_r_mean": float(known_events.loc[known_events[label].eq(value), "net_r"].mean()),
                    "unknown_label_events_excluded": int(validation_events[label].isna().sum()),
                }
            )
    audit = {
        "event_rows": int(len(events)),
        "event_key_duplicates": 0,
        "random_rows": int(len(random)),
        "random_key_seed_duplicates": 0,
        "random_rows_without_event_label": missing,
        "matched_validation_scoring_closed_receipts": int(len(validation)),
        "matched_validation_scoring_closed_unique_targets": int(len(target)),
        "reuse_limitation": REUSE_LIMITATION,
        "control_exit_time_available": False,
        "development_selection_allowed": False,
        "purpose": "descriptive validation context only; not an alpha or acceptance test",
    }
    return pd.DataFrame(random_rows), pd.DataFrame(outcome_rows), audit


def run(output: Path) -> None:
    """Write an immutable, CSV-only descriptive reduction into an empty folder."""
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    events = _event_rows(EVENTS)
    random = _random_rows(RANDOM_RECEIPT)
    random_summary, label_outcomes, audit = build_tables(events, random)
    output.mkdir(parents=True, exist_ok=True)
    random_path = output / "validation_matched_random_context.csv"
    outcome_path = output / "validation_label_target_vs_complement.csv"
    audit_path = output / "join_audit.json"
    random_summary.to_csv(random_path, index=False)
    label_outcomes.to_csv(outcome_path, index=False)
    audit_path.write_text(json.dumps(audit, indent=2) + "\n")
    manifest = {
        "complete": True,
        "official": True,
        "research_only": True,
        "no_ohlcv_read": True,
        "no_random_controls_created": True,
        "no_development_selection": True,
        "source": str(Path(__file__)),
        "input_sha256": {str(EVENTS): sha256(EVENTS), str(RANDOM_RECEIPT): sha256(RANDOM_RECEIPT)},
        "labels": list(LABELS),
        "reuse_limitation": REUSE_LIMITATION,
        "outputs": {str(path.name): sha256(path) for path in (random_path, outcome_path, audit_path)},
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    receipt = {path.name: sha256(path) for path in output.iterdir() if path.is_file()}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output)
