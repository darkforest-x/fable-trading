"""Build an outcome-blinded Owner review pack from the audited causal dataset.

The pack samples 25 rows from each split x hidden outcome-kind x trade-direction
stratum (200 total). Public task IDs and filenames reveal none of those fields.
The private truth ledger preserves event lineage and inverse-probability weights
so a later audit can report stratum-weighted error rates without pretending the
balanced sample is a simple random sample.

No market data or future path is opened. Images are copied byte-for-byte from
the already audited causal dataset, and no generated label is training eligible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "datasets/ma_launch_5m_outcome_causal_v2"
DEFAULT_AUDIT = (
    ROOT
    / "experiments/active/exp-5m-ma-launch-outcome-causal-v2/results/causality_audit.json"
)
DEFAULT_DST = ROOT / "datasets/ma_launch_5m_shape_blind_review_v1"
DEFAULT_RECEIPT = (
    ROOT
    / "experiments/active/exp-5m-ma-launch-outcome-causal-v2/results/shape_blind_review_build_receipt.json"
)
SEED = 20260831
PER_STRATUM = 25


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def stratum_key(row: dict[str, object]) -> tuple[str, str, str]:
    """Return split x hidden outcome kind x direction for review allocation."""
    return (
        str(row["split"]),
        str(row["sample_kind"]),
        str(row["trade_direction"]),
    )


def select_stratified(
    rows: list[dict[str, object]],
    *,
    per_stratum: int = PER_STRATUM,
    seed: int = SEED,
) -> list[dict[str, object]]:
    """Select a deterministic balanced sample and attach estimation weights."""
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[stratum_key(row)].append(row)
    expected = {
        (split, kind, direction)
        for split in ("train", "val")
        for kind in ("positive", "negative")
        for direction in ("LONG", "SHORT")
    }
    if set(grouped) != expected:
        raise ValueError(f"review strata drifted: expected {sorted(expected)}, got {sorted(grouped)}")

    rng = np.random.default_rng(seed)
    selected: list[dict[str, object]] = []
    for key in sorted(expected):
        population = sorted(grouped[key], key=lambda row: str(row["event_id"]))
        if len(population) < per_stratum:
            raise ValueError(
                f"stratum {key} has {len(population)} rows, fewer than requested {per_stratum}"
            )
        indices = sorted(
            int(value)
            for value in rng.choice(len(population), size=per_stratum, replace=False)
        )
        for index in indices:
            chosen = dict(population[index])
            chosen["review_stratum"] = "|".join(key)
            chosen["stratum_population"] = len(population)
            chosen["stratum_selected"] = per_stratum
            chosen["selection_probability"] = per_stratum / len(population)
            chosen["estimation_weight"] = len(population) / per_stratum
            selected.append(chosen)
    order = rng.permutation(len(selected))
    return [selected[int(index)] for index in order]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--dst", type=Path, default=DEFAULT_DST)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--per-stratum", type=int, default=PER_STRATUM)
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    audit_path = args.audit.resolve()
    destination = args.dst.resolve()
    receipt_path = args.receipt.resolve()
    if destination.exists():
        raise SystemExit(f"refusing to overwrite existing review pack: {destination}")
    audit = json.loads(audit_path.read_text())
    if audit.get("passed") is not True or audit.get("source_to_pixel_failures") != 0:
        raise SystemExit("source dataset has not passed the full source-to-pixel audit")

    rows = [
        json.loads(line)
        for line in (dataset / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    selected = select_stratified(rows, per_stratum=args.per_stratum)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.building-", dir=destination.parent))
    public_dir = stage / "public"
    images_dir = public_dir / "images"
    labels_dir = public_dir / "labels"
    admin_dir = stage / "admin"
    images_dir.mkdir(parents=True)
    labels_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)

    public_rows: list[dict[str, object]] = []
    truth_rows: list[dict[str, object]] = []
    tasks: list[dict[str, object]] = []
    try:
        for number, row in enumerate(selected, 1):
            review_id = f"R{number:04d}"
            source_image = dataset / str(row["image_path"])
            image_path = images_dir / f"{review_id}.png"
            label_path = labels_dir / f"{review_id}.json"
            shutil.copy2(source_image, image_path)
            label_path.write_text("", encoding="utf-8")
            source_hash = str(row["image_sha256"])
            copied_hash = sha256_file(image_path)
            if copied_hash != source_hash:
                raise ValueError(f"byte copy mismatch for {review_id}")

            public_rows.append(
                {
                    "review_id": review_id,
                    "image_path": f"public/images/{review_id}.png",
                    "label_path": f"public/labels/{review_id}.json",
                    "timeframe": "5m",
                    "outcome_blinded": True,
                    "future_context_visible": False,
                    "required_verdicts": ["KEEP_LONG", "KEEP_SHORT", "REMOVE", "UNCERTAIN"],
                    "training_eligible": False,
                    "production_eligible": False,
                    "image_sha256": copied_hash,
                }
            )
            truth_rows.append(
                {
                    "review_id": review_id,
                    "event_id": row["event_id"],
                    "dataset_sample_id": row["dataset_sample_id"],
                    "source_path": row["source_path"],
                    "source_image_path": row["image_path"],
                    "source_image_sha256": source_hash,
                    "split": row["split"],
                    "hidden_outcome_kind": row["sample_kind"],
                    "hidden_barrier_outcome": row["barrier_outcome"],
                    "trade_direction": row["trade_direction"],
                    "decision_at": row["decision_at"],
                    "review_stratum": row["review_stratum"],
                    "stratum_population": row["stratum_population"],
                    "stratum_selected": row["stratum_selected"],
                    "selection_probability": row["selection_probability"],
                    "estimation_weight": row["estimation_weight"],
                }
            )
            tasks.append(
                {
                    "data": {
                        "image": (
                            "/data/local-files/?d=ma_launch_5m_shape_blind_review_v1/"
                            f"public/images/{review_id}.png"
                        ),
                        "review_id": review_id,
                        "protocol": "5m_causal_shape_blind_v1",
                    },
                    "predictions": [],
                }
            )

        public_manifest = public_dir / "manifest.jsonl"
        truth_manifest = admin_dir / "truth.jsonl"
        public_manifest.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in public_rows),
            encoding="utf-8",
        )
        truth_manifest.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in truth_rows),
            encoding="utf-8",
        )
        _write_json(public_dir / "label_studio_tasks.json", tasks)
        (public_dir / "label_config.xml").write_text(
            "<View>\n"
            "  <Header value=\"Judge only what is visible at the right edge; "
            "no outcome is shown.\"/>\n"
            "  <Image name=\"image\" value=\"$image\"/>\n"
            "  <Choices name=\"verdict\" toName=\"image\" choice=\"single\" "
            "required=\"true\">\n"
            "    <Choice value=\"KEEP_LONG\"/><Choice value=\"KEEP_SHORT\"/>\n"
            "    <Choice value=\"REMOVE\"/><Choice value=\"UNCERTAIN\"/>\n"
            "  </Choices>\n"
            "  <RectangleLabels name=\"box\" toName=\"image\">\n"
            "    <Label value=\"dense_cluster\"/>\n"
            "  </RectangleLabels>\n"
            "</View>\n",
            encoding="utf-8",
        )
        (public_dir / "README.md").write_text(
            "# 5m causal shape blind review v1\n\n"
            "Review each image without looking in `admin/`. Choose exactly one verdict:\n"
            "`KEEP_LONG`, `KEEP_SHORT`, `REMOVE`, or `UNCERTAIN`. For KEEP, draw one\n"
            "tight `dense_cluster` box around the visible core. Do not infer or label a\n"
            "future TP/SL result; no future bar is present. REMOVE and UNCERTAIN never\n"
            "become negatives automatically. This pack is an audit surface, not Gold.\n",
            encoding="utf-8",
        )

        stratum_counts = Counter(str(row["review_stratum"]) for row in selected)
        image_set_digest = hashlib.sha256(
            "".join(
                f"{row['review_id']}:{row['image_sha256']}\n" for row in public_rows
            ).encode("utf-8")
        ).hexdigest()
        receipt = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator_commit": git_head(),
            "generator_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
            "generator_sha256": sha256_file(Path(__file__).resolve()),
            "source_dataset": dataset.relative_to(ROOT).as_posix(),
            "source_manifest_sha256": sha256_file(dataset / "manifest.jsonl"),
            "source_audit": audit_path.relative_to(ROOT).as_posix(),
            "source_audit_sha256": sha256_file(audit_path),
            "review_pack": destination.relative_to(ROOT).as_posix(),
            "seed": SEED,
            "per_stratum": args.per_stratum,
            "rows": len(public_rows),
            "strata": dict(sorted(stratum_counts.items())),
            "public_manifest_sha256": sha256_file(public_manifest),
            "admin_truth_sha256": sha256_file(truth_manifest),
            "image_set_sha256": image_set_digest,
            "byte_copy_failures": 0,
            "outcome_blinded_in_public_surface": True,
            "holdout_rows_read": 0,
            "training_eligible": False,
            "production_eligible": False,
        }
        _write_json(stage / "build_receipt.json", receipt)
        stage.rename(destination)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination / "build_receipt.json", receipt_path)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
