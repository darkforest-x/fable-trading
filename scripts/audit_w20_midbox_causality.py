#!/usr/bin/env python3
"""P0 causal / split audit for the w20 mid-box dataset (read-only).

Written for the 2026-08-07 "local signal V2" handover spec, which requires every
production sample to satisfy ``visible_end_bar <= decision_bar`` and forbids
random splits.  The spec's vocabulary maps onto the fields that
``scripts/build_w20_midbox_dataset.py`` already writes into ``w20_manifest.json``:

    spec anchor_bar     -> manifest ``mid_global``
    spec confirm_delay  -> manifest ``half``          (box is symmetric: mid +- half)
    spec decision_bar   -> ``mid_global + half`` == ``small_bars[1]``
    spec visible_end_bar-> ``win_start + win_len - 1``
    spec box_end_bar    -> ``small_bars[1]``

So ``future_bars = visible_end_bar - decision_bar`` is the single number that
decides whether a sample is Stage A (pattern pretrain, future allowed) or
Stage B (causal, future must be 0).  Everything else here is bookkeeping the
spec's section 12.1 asks for: split integrity, holdout containment, label
bounds, and manifest <-> file conservation.

Read-only: touches manifests, label .txt files and directory listings only.
No training, no model load, no holdout evaluation.

Usage:
  .venv/bin/python scripts/audit_w20_midbox_causality.py
  .venv/bin/python scripts/audit_w20_midbox_causality.py --out analysis/output/p0_w20_causal_audit.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DATASET = PROJECT / "datasets" / "dense_owner_w20_midbox"
# Iron rule 1: holdout starts here and must not appear in any training image.
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
# Stage A position buckets (spec 5.2 gives target shares but no edges; these are ours).
POS_BUCKETS = (("left_mid", 0.0, 0.35), ("mid", 0.35, 0.55),
               ("mid_right", 0.55, 0.75), ("right", 0.75, 1.0001))


# --------------------------------------------------------------------------
# pure functions -- unit tested in tests/test_w20_midbox_causality.py
# --------------------------------------------------------------------------
def decision_bar(anchor_bar: int, confirm_delay: int) -> int:
    """Earliest bar at which the signal may be emitted (spec 3.1)."""
    return int(anchor_bar) + int(confirm_delay)


def visible_end_bar(win_start: int, win_len: int) -> int:
    """Last real bar drawn in the window."""
    return int(win_start) + int(win_len) - 1


def future_bars(win_start: int, win_len: int, anchor_bar: int, confirm_delay: int) -> int:
    """Bars rendered strictly after the decision bar. 0 == causal, >0 == Stage A."""
    return visible_end_bar(win_start, win_len) - decision_bar(anchor_bar, confirm_delay)


def is_causal(win_start: int, win_len: int, anchor_bar: int, confirm_delay: int) -> bool:
    """Spec 12.1 invariant: visible_end_bar <= decision_bar."""
    return future_bars(win_start, win_len, anchor_bar, confirm_delay) <= 0


def box_inside_decision(box_end_bar: int, anchor_bar: int, confirm_delay: int) -> bool:
    """Spec 12.1 invariant: box_end_bar <= decision_bar."""
    return int(box_end_bar) <= decision_bar(anchor_bar, confirm_delay)


def position_bucket(frac: float) -> str:
    for name, lo, hi in POS_BUCKETS:
        if lo <= frac < hi:
            return name
    return "out_of_range"


def quantiles(values: list[float], probs=(0.0, 0.25, 0.5, 0.75, 1.0)) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    out: dict[str, float] = {}
    for p in probs:
        i = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
        out[f"p{int(p * 100)}"] = float(ordered[i])
    out["mean"] = float(sum(ordered) / len(ordered))
    return out


def label_rows(text: str) -> list[tuple[float, float, float, float]]:
    rows: list[tuple[float, float, float, float]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            rows.append(tuple(float(v) for v in parts[1:5]))
    return rows


def label_out_of_bounds(box: tuple[float, float, float, float]) -> bool:
    """YOLO xywh must stay inside the image (spec 16.1: 0 labels out of bounds)."""
    xc, yc, w, h = box
    if not (0.0 < w <= 1.0 and 0.0 < h <= 1.0):
        return True
    return xc - w / 2 < -1e-6 or xc + w / 2 > 1 + 1e-6 or yc - h / 2 < -1e-6 or yc + h / 2 > 1 + 1e-6


# --------------------------------------------------------------------------
# audit sections
# --------------------------------------------------------------------------
def audit_causality(pos_rows: list[dict]) -> dict:
    """Future-visibility profile. confirm_delay is taken from each row's `half`."""
    fut = [future_bars(r["win_start"], r["win_len"], r["mid_global"], r["half"]) for r in pos_rows]
    left = [r["small_bars"][0] - r["win_start"] for r in pos_rows]
    n = len(fut) or 1
    box_ok = sum(1 for r in pos_rows
                 if box_inside_decision(r["small_bars"][1], r["mid_global"], r["half"]))
    return {
        "n_positive": len(pos_rows),
        "future_bars": quantiles([float(v) for v in fut]),
        "n_causal": sum(1 for v in fut if v <= 0),
        "n_future_gt0": sum(1 for v in fut if v > 0),
        "frac_future_gt0": round(sum(1 for v in fut if v > 0) / n, 6),
        "frac_future_ge5": round(sum(1 for v in fut if v >= 5) / n, 6),
        "left_context_bars": quantiles([float(v) for v in left]),
        "box_end_le_decision": box_ok == len(pos_rows),
        "confirm_delay_hist": dict(sorted(Counter(r["half"] for r in pos_rows).items())),
        "verdict": "stage_a_only" if sum(1 for v in fut if v > 0) else "causal",
    }


def audit_position(pos_rows: list[dict]) -> dict:
    fracs = [float(r["box_pos_frac"]) for r in pos_rows]
    hist = Counter(position_bucket(f) for f in fracs)
    n = len(fracs) or 1
    return {
        "box_pos_frac": quantiles(fracs, probs=(0.0, 0.1, 0.5, 0.9, 1.0)),
        "bucket_share": {k: round(v / n, 4) for k, v in sorted(hist.items())},
        "win_len_hist": dict(sorted(Counter(r["win_len"] for r in pos_rows).items())),
    }


def audit_split(pos_rows: list[dict], neg_rows: list[dict]) -> dict:
    """Spec 7: no event across splits, and does the split separate time at all?"""
    by_split_sym = {s: {r["symbol"] for r in pos_rows if r["split"] == s} for s in ("train", "val")}
    stems = Counter(r["stem"] for r in pos_rows)
    cross = {stem for stem in stems
             if len({r["split"] for r in pos_rows if r["stem"] == stem}) > 1}
    times = pd.to_datetime([r["end_time"] for r in pos_rows], utc=True, errors="coerce")
    frame = pd.DataFrame({"t": times, "split": [r["split"] for r in pos_rows]}).dropna()
    ranges = {s: {"n": int((frame["split"] == s).sum()),
                  "min": str(frame.loc[frame["split"] == s, "t"].min()),
                  "max": str(frame.loc[frame["split"] == s, "t"].max())}
              for s in ("train", "val")}
    tr, va = frame.loc[frame["split"] == "train", "t"], frame.loc[frame["split"] == "val", "t"]
    overlap_days = 0.0
    if len(tr) and len(va):
        lo, hi = max(tr.min(), va.min()), min(tr.max(), va.max())
        overlap_days = max(0.0, (hi - lo).total_seconds() / 86400)
    return {
        "strategy_in_code": "sha1(symbol) % VAL_MOD  (src/detection/owner_eval.py:split_of)",
        "is_time_split": False,
        "n_events_crossing_split": len(cross),
        "symbol_overlap_train_val": len(by_split_sym["train"] & by_split_sym["val"]),
        "n_symbols": {s: len(v) for s, v in by_split_sym.items()},
        "crops_per_event_max": max(stems.values()) if stems else 0,
        "time_range": ranges,
        "train_val_time_overlap_days": round(overlap_days, 1),
        "negatives_have_timestamps": bool(neg_rows and "end_time" in neg_rows[0]),
        "purge_embargo_bars": 0,
    }


def audit_holdout(pos_rows: list[dict]) -> dict:
    """Iron rule 1: no training image may be drawn from >= 2026-05-04."""
    times = pd.to_datetime([r["end_time"] for r in pos_rows], utc=True, errors="coerce")
    frame = pd.DataFrame({"t": times, "split": [r["split"] for r in pos_rows],
                          "stem": [r["stem"] for r in pos_rows]}).dropna()
    hit = frame[frame["t"] >= HOLDOUT_START]
    return {
        "boundary": str(HOLDOUT_START),
        "n_positive_in_holdout": int(len(hit)),
        "frac_positive_in_holdout": round(len(hit) / max(len(frame), 1), 6),
        "by_split": {k: int(v) for k, v in hit["split"].value_counts().items()},
        "max_end_time": str(frame["t"].max()),
        "examples": [str(s) for s in hit["stem"].head(5)],
        "clean": len(hit) == 0,
    }


def audit_conservation(dataset: Path, pos_rows: list[dict], neg_rows: list[dict]) -> dict:
    """Spec 12.1: every image has a label, and every file traces to a manifest row."""
    counts, unmanifested, missing_pairs, bad_labels = {}, {}, [], []
    manifest_stems = {r["out_stem"] for r in pos_rows} | {r["stem"] for r in neg_rows}
    for split in ("train", "val"):
        img_dir, lbl_dir = dataset / "images" / split, dataset / "labels" / split
        imgs = {p.stem for p in img_dir.glob("*.png")} if img_dir.is_dir() else set()
        lbls = {p.stem for p in lbl_dir.glob("*.txt")} if lbl_dir.is_dir() else set()
        counts[split] = {"images": len(imgs), "labels": len(lbls)}
        missing_pairs.extend(sorted(imgs ^ lbls)[:5])
        orphans = sorted(imgs - manifest_stems)
        unmanifested[split] = {"n": len(orphans), "examples": orphans[:3]}
        for stem in sorted(lbls):
            for box in label_rows((lbl_dir / f"{stem}.txt").read_text()):
                if label_out_of_bounds(box):
                    bad_labels.append(f"{split}/{stem}")
                    break
    total_files = sum(c["images"] for c in counts.values())
    return {
        "counts": counts,
        "n_manifest_rows": len(manifest_stems),
        "n_image_files": total_files,
        "n_unmanifested_images": sum(v["n"] for v in unmanifested.values()),
        "unmanifested": unmanifested,
        "n_image_label_mismatch": len(missing_pairs),
        "n_labels_out_of_bounds": len(bad_labels),
        "labels_out_of_bounds_examples": bad_labels[:5],
        "conserved": sum(v["n"] for v in unmanifested.values()) == 0 and not missing_pairs,
    }


def run_audit(dataset: Path) -> dict:
    pos_rows = json.loads((dataset / "w20_manifest.json").read_text())
    neg_path = dataset / "w20_neg_manifest.json"
    neg_rows = json.loads(neg_path.read_text()) if neg_path.exists() else []
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "causality": audit_causality(pos_rows),
        "position": audit_position(pos_rows),
        "split": audit_split(pos_rows, neg_rows),
        "holdout": audit_holdout(pos_rows),
        "conservation": audit_conservation(dataset, pos_rows, neg_rows),
    }
    result["gates"] = {
        "causal_dataset (visible_end <= decision)": result["causality"]["verdict"] == "causal",
        "box_end <= decision": result["causality"]["box_end_le_decision"],
        "no_event_crosses_split": result["split"]["n_events_crossing_split"] == 0,
        "time_based_split": result["split"]["is_time_split"],
        "no_holdout_in_training": result["holdout"]["clean"],
        "labels_in_bounds": result["conservation"]["n_labels_out_of_bounds"] == 0,
        "manifest_conserved": result["conservation"]["conserved"],
    }
    result["p0_pass"] = all(result["gates"].values())
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--out", default=str(PROJECT / "analysis" / "output" / "p0_w20_causal_audit.json"))
    args = ap.parse_args()

    result = run_audit(Path(args.dataset))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    print(json.dumps(result["gates"], ensure_ascii=False, indent=2))
    print(f"p0_pass = {result['p0_pass']}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
