#!/usr/bin/env python3
"""Audit and preview the actual 10k-positive + 30k-negative YOLO dataset.

The audit reads only the frozen pre-holdout plans and materialized model inputs.
It verifies three negatives per positive, exact pairing geometry, per-source
negative interval isolation, seed/additive lineage and deterministic hard/easy
samples.  It never reads OHLCV, starts training, changes model state or touches
the repository holdout.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_owner_autofill10000_yolo_neg30000_v2"
DEFAULT_RESULTS = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2"
    / "results"
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generator_commit() -> str:
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise RuntimeError("negative dataset audit must run on main")
    relative = str(SCRIPT_PATH.relative_to(ROOT))
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise RuntimeError(f"audit generator is not committed: {dirty}")
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", relative],
        cwd=ROOT,
        text=True,
    ).strip()
    if len(commit) != 40:
        raise RuntimeError("could not resolve audit generator commit")
    return commit


def evenly_spaced(rows: Sequence[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            int(row["paired_positive_source_order"]),
            int(row["pair_slot"]),
            str(row["source_sample_id"]),
        ),
    )
    if len(ordered) < count:
        raise ValueError(f"sample category has only {len(ordered)} rows, needs {count}")
    return [ordered[int(index)] for index in np.linspace(0, len(ordered) - 1, count)]


def contact_sheet(
    dataset_path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: int = 5,
) -> np.ndarray:
    thumb_w, thumb_h, caption_h = 480, 278, 34
    canvas_rows = math.ceil(len(rows) / columns)
    canvas = np.full(
        (canvas_rows * (thumb_h + caption_h), columns * thumb_w, 3),
        245,
        dtype=np.uint8,
    )
    for index, row in enumerate(rows):
        image = cv2.imread(str(dataset_path / str(row["image_path"])), cv2.IMREAD_COLOR)
        if image is None or image.shape != (742, 1280, 3):
            raise ValueError(f"invalid sampled model input: {row['image_path']}")
        thumb = cv2.resize(image, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        grid_row, column = divmod(index, columns)
        x, y = column * thumb_w, grid_row * (thumb_h + caption_h)
        canvas[y : y + thumb_h, x : x + thumb_w] = thumb
        caption = (
            f"pair#{int(row['paired_positive_source_order']):05d} "
            f"slot{int(row['pair_slot'])} {row['symbol']} {row['negative_kind']}"
        )
        cv2.putText(
            canvas,
            caption[:62],
            (x + 8, y + thumb_h + 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )
    return canvas


def write_preview_html(
    path: Path,
    *,
    dataset_path: Path,
    sections: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
) -> None:
    blocks: list[str] = []
    for title, rows in sections:
        cards: list[str] = []
        for row in rows:
            source = os.path.relpath(dataset_path / str(row["image_path"]), path.parent)
            cards.append(
                "<article>"
                f'<img src="{html.escape(source)}" loading="lazy">'
                f"<h3>{html.escape(str(row['symbol']))}</h3>"
                f"<p>pair #{int(row['paired_positive_source_order']):05d} · "
                f"slot {int(row['pair_slot'])} · {html.escape(str(row['negative_kind']))} · "
                f"{html.escape(str(row['split']))}</p></article>"
            )
        blocks.append(f"<h2>{html.escape(title)}</h2><section>{''.join(cards)}</section>")
    path.write_text(
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>3万负样本实际输入抽样</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#111;color:#eee;margin:0}header{position:sticky;top:0;background:#181818;padding:16px 22px;z-index:2}h2{margin:24px 18px 8px}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px;padding:10px 18px 24px}article{background:#202020;border:1px solid #444;border-radius:10px;padding:10px}img{display:block;width:100%;height:auto}h3{margin:9px 0 2px;font-size:15px}p{margin:0;color:#bbb;font-size:13px}</style></head><body>
<header><h1>3万负样本：模型实际无框 PNG 抽样</h1><p>前 50 张来自 v1 保留槽；后 50 张来自本轮新增 2 万槽。每组 hard/easy 各 25 张，均为数据集实际文件，负标签为空。</p></header>"""
        + "".join(blocks)
        + "</body></html>\n",
        encoding="utf-8",
    )


def audit(*, dataset_path: Path, results_path: Path) -> dict[str, Any]:
    commit = generator_commit()
    manifest_path = dataset_path / "manifest.jsonl"
    positive_plan_path = results_path / "positive_plan.jsonl"
    negative_plan_path = results_path / "negative_plan.jsonl"
    plan_receipt_path = results_path / "plan_receipt.json"
    build_receipt_path = results_path / "dataset_build_receipt.json"
    for path in (
        manifest_path,
        positive_plan_path,
        negative_plan_path,
        plan_receipt_path,
        build_receipt_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    manifest = read_jsonl(manifest_path)
    positives = [row for row in manifest if row["sample_kind"] == "positive"]
    negatives = [row for row in manifest if row["sample_kind"] == "negative"]
    if (len(positives), len(negatives)) != (10000, 30000):
        raise ValueError("dataset class counts drift")
    positive_by_id = {str(row["source_sample_id"]): row for row in positives}
    if len(positive_by_id) != 10000:
        raise ValueError("positive identities are not unique")

    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in negatives:
        by_pair[str(row["paired_positive_sample_id"])].append(row)
    if set(by_pair) != set(positive_by_id):
        raise ValueError("negative pair IDs differ from positive IDs")

    pair_kind_distribution: Counter[str] = Counter()
    for positive_id, paired in by_pair.items():
        positive = positive_by_id[positive_id]
        if len(paired) != 3 or {int(row["pair_slot"]) for row in paired} != {1, 2, 3}:
            raise ValueError(f"pair-slot drift: {positive_id}")
        for row in paired:
            exact = (
                row["source_path"] == positive["source_path"]
                and row["symbol"] == positive["symbol"]
                and row["paired_direction"] == positive["direction"]
                and int(row["paired_positive_source_order"])
                == int(positive["source_order"])
                and row["split"] == positive["split"]
                and row["time_block"] == positive["time_block"]
                and int(row["core_bars"]) == int(positive["core_bars"])
                and int(row["pre_core_context_bars"])
                == int(positive["pre_core_context_bars"])
                and int(row["post_core_context_bars"])
                == int(positive["post_core_context_bars"])
            )
            if not exact:
                raise ValueError(f"paired geometry drift: {row['source_sample_id']}")
        kinds = Counter(str(row["negative_kind"]) for row in paired)
        pair_kind_distribution[f"hard={kinds['hard']},easy={kinds['easy']}"] += 1

    by_source: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    separation = 2
    for row in negatives:
        by_source[str(row["source_path"])].append(
            (
                int(row["window_start_i"]) - separation,
                int(row["dependency_end_i"]) + separation,
                str(row["source_sample_id"]),
            )
        )
    for source_path, intervals in by_source.items():
        ordered = sorted(intervals)
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] <= previous[1]:
                raise ValueError(
                    f"negative dependency intervals overlap in {source_path}: "
                    f"{previous[2]} / {current[2]}"
                )

    seed = [row for row in negatives if int(row["pair_slot"]) == 1]
    added = [row for row in negatives if int(row["pair_slot"]) > 1]
    if (len(seed), len(added)) != (10000, 20000):
        raise ValueError("seed/additive row counts drift")
    categories = {
        "seed_hard": [row for row in seed if row["negative_kind"] == "hard"],
        "seed_easy": [row for row in seed if row["negative_kind"] == "easy"],
        "added_hard": [row for row in added if row["negative_kind"] == "hard"],
        "added_easy": [row for row in added if row["negative_kind"] == "easy"],
    }
    seed_sample = evenly_spaced(categories["seed_hard"], 25) + evenly_spaced(
        categories["seed_easy"], 25
    )
    added_sample = evenly_spaced(categories["added_hard"], 25) + evenly_spaced(
        categories["added_easy"], 25
    )
    preview_html = results_path / "actual_negative_inputs_seed50_added50.html"
    write_preview_html(
        preview_html,
        dataset_path=dataset_path,
        sections=(("v1 保留负样本 50 张", seed_sample), ("v2 新增负样本 50 张", added_sample)),
    )
    added_contact = results_path / "actual_added_negative_inputs_sample50.jpg"
    if not cv2.imwrite(
        str(added_contact),
        contact_sheet(dataset_path, added_sample),
        [cv2.IMWRITE_JPEG_QUALITY, 94],
    ):
        raise OSError(f"failed to write {added_contact}")

    build_receipt = read_json(build_receipt_path)
    plan_receipt = read_json(plan_receipt_path)
    receipt = {
        "schema_version": 1,
        "experiment_id": plan_receipt["experiment_id"],
        "generator_commit": commit,
        "dataset_manifest_sha256": sha256_file(manifest_path),
        "positive_plan_sha256": sha256_file(positive_plan_path),
        "negative_plan_sha256": sha256_file(negative_plan_path),
        "positive_rows": len(positives),
        "negative_rows": len(negatives),
        "seed_negative_rows": len(seed),
        "added_negative_rows": len(added),
        "negative_kinds": dict(Counter(row["negative_kind"] for row in negatives)),
        "seed_negative_kinds": dict(Counter(row["negative_kind"] for row in seed)),
        "added_negative_kinds": dict(Counter(row["negative_kind"] for row in added)),
        "pair_kind_distribution": dict(pair_kind_distribution),
        "pairs_with_slots_1_2_3": len(by_pair),
        "exact_same_source_symbol_halfyear_split_geometry_pairs": len(negatives),
        "sources_with_disjoint_negative_dependency_intervals": len(by_source),
        "strict_candidates_guarded": int(plan_receipt["strict_candidates_guarded"]),
        "holdout_ohlcv_rows_materialized": int(
            plan_receipt["holdout_ohlcv_rows_materialized"]
        ),
        "full_file_qa_passed": bool(build_receipt["full_qa"]["passed"]),
        "lineage_baseline_passed": bool(build_receipt["lineage_baseline"]["passed"]),
        "preview_html": str(preview_html.relative_to(ROOT)),
        "preview_html_sha256": sha256_file(preview_html),
        "added_negative_contact_sheet": str(added_contact.relative_to(ROOT)),
        "added_negative_contact_sheet_sha256": sha256_file(added_contact),
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
        "passed": True,
    }
    write_json(results_path / "negative_expansion_audit.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    result = audit(dataset_path=args.dataset.resolve(), results_path=args.results.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
