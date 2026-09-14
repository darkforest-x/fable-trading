"""Read-only audit of the completed V8 admission-policy decisions.

This intentionally consumes only receipt-bound files from ``results/full_v3``.
It records the execution-time audit requested after the replay was frozen:
confirmation/open timestamp alignment, terminal no-next-open candidates, the
risk gate's rejected-candidate lifecycle, and the original USDC-excluded fixed
events' gross/net/cost-R accounting.  It never imports or invokes a replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


POLICIES = (
    "rv_gt50", "risk_gt30pct", "usdc_base", "stock_linked_all", "h00_utc",
    "sunday_utc", "joint_rv_gt50_tratr_gt10", "h00_asia_shanghai",
    "sunday_asia_shanghai",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rows_for_subset(frame: pd.DataFrame, subset: str) -> dict[str, object]:
    if subset == "closed":
        frame = frame.loc[~frame["censored"].astype(bool)]
    elif subset == "censored":
        frame = frame.loc[frame["censored"].astype(bool)]
    cost_r = frame["gross_r"] - frame["net_r"]
    expected_cost_r = 0.002 / frame["initial_risk_frac"]
    return {
        "subset": subset,
        "events": len(frame),
        "gross_return": frame["gross_return"].sum(),
        "net_return": frame["net_return"].sum(),
        "return_cost": (frame["gross_return"] - frame["net_return"]).sum(),
        "gross_r": frame["gross_r"].sum(),
        "net_r": frame["net_r"].sum(),
        "cost_r": cost_r.sum(),
        "expected_cost_r_from_0_002_over_initial_risk_frac": expected_cost_r.sum(),
        "cost_r_formula_abs_error_max": (cost_r - expected_cost_r).abs().max() if len(frame) else 0.0,
        "cost_r_formula_mismatch_events": int((cost_r - expected_cost_r).abs().gt(1e-8).sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results",
        type=Path,
        default=Path(__file__).parent / "results" / "full_v3",
    )
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "audit_v1")
    args = parser.parse_args()
    results, out = args.results, args.out
    streams = sorted(d for d in (results / "streams").iterdir() if (d / "completion.json").is_file())
    if len(streams) != 3531:
        raise RuntimeError(f"expected 3531 complete streams, found {len(streams)}")

    source_records: list[str] = []
    timing_rows: list[dict[str, object]] = []
    usdc_frames: list[pd.DataFrame] = []
    serial_baseline_net_r = serial_usdc_net_r = 0.0
    serial_baseline_gross_r = serial_usdc_gross_r = 0.0
    serial_baseline_rows = serial_usdc_rows = 0
    risk_rejected_attempted = risk_rejected_unattempted = risk_rejected_filled = 0

    for policy in POLICIES:
        candidates = with_next_open = mismatch = mismatch_filled = 0
        missing_next_open = terminal_unknown = 0
        for stream in streams:
            receipt_path = stream / "completion.json"
            receipt = json.loads(receipt_path.read_text())
            name = f"v8.{policy}.decisions.csv.gz"
            path, expected = stream / name, receipt["files"][name]
            actual = sha256(path)
            source_records.append(f"{stream.name}/{name}:{actual}")
            if actual != expected:
                raise RuntimeError(f"receipt hash mismatch: {path}")
            frame = pd.read_csv(path)
            entry = pd.to_datetime(frame["entry_time"], utc=True)
            expected_open = pd.to_datetime(frame["signal_bar_open"], utc=True) + pd.to_timedelta(frame["timeframe_min"], unit="min")
            valid = entry.notna()
            different = valid & entry.ne(expected_open)
            candidates += len(frame)
            with_next_open += int(valid.sum())
            mismatch += int(different.sum())
            mismatch_filled += int(frame.loc[different, "entry_filled"].fillna(False).astype(bool).sum())
            missing_next_open += int((~valid).sum())
            terminal_unknown += int((~valid & frame["gate_state"].eq("unknown_untested")).sum())
            if policy == "risk_gt30pct":
                rejected = frame.loc[frame["gate_state"].eq("rejected")]
                attempted = rejected["entry_attempted"].fillna(False).astype(bool)
                risk_rejected_attempted += int(attempted.sum())
                risk_rejected_unattempted += int((~attempted).sum())
                risk_rejected_filled += int(rejected.loc[attempted, "entry_filled"].fillna(False).astype(bool).sum())
        timing_rows.append({
            "section": "entry_time_alignment", "policy": policy, "candidates": candidates,
            "candidates_with_next_open": with_next_open, "entry_time_mismatch": mismatch,
            "mismatch_entry_filled": mismatch_filled, "missing_next_open": missing_next_open,
            "missing_next_open_unknown_untested": terminal_unknown,
        })

    # Read and receipt-validate the original fixed rows once, specifically for
    # the USDC policy's retained/rejected original-event comparison.
    for stream in streams:
        receipt = json.loads((stream / "completion.json").read_text())
        name = "v8.fixed_usdc_base.csv.gz"
        path, expected = stream / name, receipt["files"][name]
        actual = sha256(path)
        source_records.append(f"{stream.name}/{name}:{actual}")
        if actual != expected:
            raise RuntimeError(f"receipt hash mismatch: {path}")
        usdc_frames.append(pd.read_csv(path))
        for name, accum in (("v8.serial_baseline.csv.gz", "baseline"), ("v8.serial_usdc_base.csv.gz", "usdc")):
            path, expected = stream / name, receipt["files"][name]
            actual = sha256(path)
            source_records.append(f"{stream.name}/{name}:{actual}")
            if actual != expected:
                raise RuntimeError(f"receipt hash mismatch: {path}")
            serial = pd.read_csv(path)
            if accum == "baseline":
                serial_baseline_rows += len(serial)
                serial_baseline_net_r += serial["net_r"].sum()
                serial_baseline_gross_r += serial["gross_r"].sum()
            else:
                serial_usdc_rows += len(serial)
                serial_usdc_net_r += serial["net_r"].sum()
                serial_usdc_gross_r += serial["gross_r"].sum()

    risk_row = {
        "section": "risk_gate_rejected_lifecycle", "policy": "risk_gt30pct",
        "rejected_candidates": risk_rejected_attempted + risk_rejected_unattempted,
        "rejected_entry_attempted": risk_rejected_attempted,
        "rejected_entry_unattempted": risk_rejected_unattempted,
        "rejected_attempted_entry_filled": risk_rejected_filled,
    }
    timing = pd.DataFrame(timing_rows + [risk_row])
    nonempty_usdc_frames = [frame for frame in usdc_frames if not frame.empty]
    rejected_usdc = pd.concat(nonempty_usdc_frames, ignore_index=True).loc[lambda x: x["gate_rejected"].eq(True)].copy()
    costs = pd.DataFrame([rows_for_subset(rejected_usdc, s) for s in ("all", "closed", "censored")])
    excluded = costs.loc[costs["subset"].eq("all")].iloc[0]
    serial_delta_net_r = serial_usdc_net_r - serial_baseline_net_r
    serial_effect = pd.DataFrame([{
        "baseline_rows": serial_baseline_rows,
        "usdc_rows": serial_usdc_rows,
        "baseline_gross_r": serial_baseline_gross_r,
        "baseline_net_r": serial_baseline_net_r,
        "usdc_gross_r": serial_usdc_gross_r,
        "usdc_net_r": serial_usdc_net_r,
        "serial_net_r_improvement": serial_delta_net_r,
        "fixed_excluded_event_gross_r": excluded["gross_r"],
        "fixed_excluded_event_net_r": excluded["net_r"],
        "fixed_excluded_event_cost_r": excluded["cost_r"],
        "serial_improvement_plus_excluded_net_r": serial_delta_net_r + excluded["net_r"],
        "cost_r_share_of_serial_improvement": excluded["cost_r"] / serial_delta_net_r,
        "gross_r_share_of_serial_improvement": -excluded["gross_r"] / serial_delta_net_r,
    }])

    out.mkdir(parents=True, exist_ok=True)
    timing_path, cost_path, serial_path = out / "timing_and_risk.csv", out / "usdc_excluded_cost.csv", out / "usdc_serial_effect.csv"
    timing.to_csv(timing_path, index=False)
    costs.to_csv(cost_path, index=False)
    serial_effect.to_csv(serial_path, index=False)
    source_digest = hashlib.sha256("\n".join(sorted(source_records)).encode()).hexdigest()
    receipt = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only_source": str(results),
        "source_streams": len(streams),
        "source_files_sha256_verified": len(source_records),
        "source_file_records_sha256": source_digest,
        "source_file_hash_mismatches": 0,
        "inputs": ["all nine receipt-bound v8.*.decisions.csv.gz", "receipt-bound v8.fixed_usdc_base.csv.gz", "receipt-bound v8.serial_baseline.csv.gz and v8.serial_usdc_base.csv.gz"],
        "fixed_cost_assumption": 0.002,
        "outputs": {timing_path.name: sha256(timing_path), cost_path.name: sha256(cost_path), serial_path.name: sha256(serial_path)},
        "audit_builder_sha256": sha256(Path(__file__)),
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
