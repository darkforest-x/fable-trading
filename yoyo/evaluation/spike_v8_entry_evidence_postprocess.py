"""Pure post-processing for frozen V8 entry-evidence event rows.

This module reads only the completed ``event_evidence.csv.gz`` emitted by the
entry-evidence official scan.  It computes tail-loss strata and the explicit
C-opposition deletion denominator; it never opens a cache, OHLCV source,
signal ledger, replay, or pairing table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import pandas as pd


EXP = Path("experiments/active/exp-spike-v8-entry-evidence-20260914-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v8_entry_evidence_postprocess.py")
LABELS = ("bb_episode_directional_order9_run3", "bb_ma_tight_overlap_run3", "htf_opposed_completed")
REQUIRED = {"period", "scoring_closed", "net_r", *LABELS}


def sha256(path: Path) -> str:
    """Return the SHA-256 identity of a frozen input or generated table."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean(paths: tuple[Path, ...]) -> bool:
    """Require the postprocessor and test at the current main HEAD for official output."""
    root = Path.cwd().resolve()
    for path in paths:
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return False
        if subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], capture_output=True).returncode:
            return False
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)]).returncode:
            return False
    return True


def _metrics(part: pd.DataFrame) -> dict[str, float | int]:
    """Return outcome-only metrics without looking at any market-time source."""
    wins = part.net_r.gt(0)
    loss = -float(part.loc[part.net_r.lt(0), "net_r"].sum())
    return {
        "n": len(part),
        "win_rate": float(wins.mean()) if len(part) else math.nan,
        "mean_net_r": float(part.net_r.mean()) if len(part) else math.nan,
        "net_r": float(part.net_r.sum()),
        "profit_factor": float(part.loc[wins, "net_r"].sum()) / loss if loss else math.inf,
        "realized_10r": int(part.net_r.ge(10).sum()),
        "loss_p05_r": float(part.net_r.quantile(.05)) if len(part) else math.nan,
        "loss_p01_r": float(part.net_r.quantile(.01)) if len(part) else math.nan,
        "minimum_net_r": float(part.net_r.min()) if len(part) else math.nan,
    }


def build_tables(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build tail and C-deletion tables from saved rows only.

    ``all_not_explicitly_opposed`` means every scoring-closed event except C
    true. It deliberately retains C unknown rows and labels them separately;
    it therefore measures deletion of explicit opposition, never a claim that
    unknown higher-timeframe evidence passed C.
    """
    missing = REQUIRED - set(events.columns)
    if missing:
        raise ValueError("event-evidence input lacks: " + ", ".join(sorted(missing)))
    closed = events.loc[events.scoring_closed.fillna(False).astype(bool)].copy()
    tail_rows: list[dict[str, object]] = []
    deletion_rows: list[dict[str, object]] = []
    for period, all_part in closed.groupby("period", dropna=False):
        full_10r = int(all_part.net_r.ge(10).sum())
        base = {"period": period, "full_scoring_closed": len(all_part), "full_original_10r": full_10r}
        for label in LABELS:
            known = all_part.loc[all_part[label].notna()]
            coverage = base | {"label": label, "known_scoring_closed": len(known), "unknown_scoring_closed": len(all_part) - len(known)}
            for cohort, part in (
                ("target_true", known.loc[known[label].eq(True)]),
                ("known_complement_false", known.loc[known[label].eq(False)]),
                ("unknown", all_part.loc[all_part[label].isna()]),
            ):
                selected_10r = int(part.net_r.ge(10).sum())
                tail_rows.append(coverage | {"cohort": cohort, **_metrics(part), "full_original_10r_retention": selected_10r / full_10r if full_10r else math.nan})
        label = "htf_opposed_completed"
        known = all_part.loc[all_part[label].notna()]
        target = known.loc[known[label].eq(True)]
        cbase = base | {
            "known_scoring_closed": len(known),
            "unknown_scoring_closed": len(all_part) - len(known),
            "definition": "all_not_explicitly_opposed retains htf unknown rows; unknown is disclosed and is not a C pass",
        }
        for cohort, part in (
            ("explicitly_opposed_true", target),
            ("known_not_opposed_false", known.loc[known[label].eq(False)]),
            ("all_not_explicitly_opposed", all_part.loc[~all_part.index.isin(target.index)]),
            ("unknown_htf", all_part.loc[all_part[label].isna()]),
        ):
            selected_10r = int(part.net_r.ge(10).sum())
            deletion_rows.append(cbase | {"cohort": cohort, **_metrics(part), "full_original_10r_retention": selected_10r / full_10r if full_10r else math.nan})
    return pd.DataFrame(tail_rows), pd.DataFrame(deletion_rows)


def run(input_dir: Path, output_dir: Path, *, official: bool = False) -> None:
    """Write receipt-bound derived tables from one completed feature scan."""
    event_path = input_dir / "event_evidence.csv.gz"
    scan_path = input_dir / "scan_manifest.json"
    if not event_path.is_file() or not scan_path.is_file():
        raise FileNotFoundError("derived postprocess requires saved official event_evidence and scan_manifest")
    scan = json.loads(scan_path.read_text())
    if not scan.get("complete") or int(scan.get("events", -1)) != 113295 or int(scan.get("scoring_closed", -1)) != 94180:
        raise ValueError("input event scan is incomplete or outside frozen V8 scope")
    if official and not _clean((Path(__file__), CONFIG, TEST)):
        raise ValueError("official derived output requires committed clean postprocessor/config/test")
    events = pd.read_csv(event_path, low_memory=False)
    if len(events) != int(scan["events"]):
        raise ValueError("event-evidence row count changed")
    tail, deletion = build_tables(events)
    output_dir.mkdir(parents=True, exist_ok=False)
    tail_path = output_dir / "tail_loss_summary.csv"
    deletion_path = output_dir / "c_opposition_deletion_summary.csv"
    tail.to_csv(tail_path, index=False)
    deletion.to_csv(deletion_path, index=False)
    manifest = {
        "complete": True,
        "official": official,
        "input_event_evidence_sha256": sha256(event_path),
        "input_scan_manifest_sha256": sha256(scan_path),
        "source": {str(path): sha256(path) for path in (Path(__file__), CONFIG, TEST)},
        "method": "pure outcome aggregation from event_evidence; no OHLCV/cache/signal/replay/pair source read",
        "tables": {tail_path.name: sha256(tail_path), deletion_path.name: sha256(deletion_path)},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="full_v1 directory containing saved event_evidence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official", action="store_true")
    args = parser.parse_args()
    run(args.input, args.output, official=args.official)
