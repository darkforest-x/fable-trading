"""Build the strict Owner-directed 15-minute MA-launch shortlist Review50 v5.

The frozen pre-holdout Review50 is audited in source order.  Explicit Owner
rejects are kept as no-box rows; explicit per-image shifts are applied exactly;
unmentioned rows pass only when a full contact-sheet review shows a compact MA
interaction immediately before a fresh directional release.  A pass remains a
proposal pending Owner review, never a Gold label.

Clean model-input crops contain 10-12 pre-core bars, the 4-5 bar proposed core,
and only 0-2 post-core bars.  Five later bars are rendered into a physically
separate review-only directory.  Geometry uses ``high``, ``low`` and
``sma/ema 20/60/120`` inside the proposed core.  No holdout OHLCV, YOLO labels,
training, model state, forward state, deployment state or order path is used.
"""

from __future__ import annotations

import html
import os
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix, sha256_file
from yoyo.datasets.ma_launch_owner_recrop_review import (
    FUTURE_REVIEW_BARS,
    HOLDOUT_START,
    ROOT,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    OwnerRecropReviewError,
    assert_contiguous,
    core_box,
    describe,
    draw_box,
    encode_png,
    read_json,
    read_jsonl,
    relative_final_path,
    stable_context,
    verify_builder_committed,
    write_json,
    write_jsonl,
)
from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.layers.l1_detection.render import render_chart


EXPERIMENT_ID = "exp-15m-ma-launch-owner-strict-review50-v5"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"


class OwnerStrictReviewError(OwnerRecropReviewError):
    """Raised when v5 shortlist identity, status or geometry drifts."""


def resolve_strict_decision(
    source_row: Mapping[str, Any],
    overrides: Mapping[str, Mapping[str, Any]],
    strict_pass_orders: set[int],
    strict_reject_reasons: Mapping[int, str],
) -> dict[str, Any]:
    """Return the frozen v5 decision for one Review50 source row."""

    order = int(source_row["source_order"] if "source_order" in source_row else 0)
    sample_id = str(source_row["sample_id"])
    override = overrides.get(sample_id)
    if override is not None and str(override["action"]) == "reject":
        return {
            "status": "OWNER_REJECT",
            "core_start_offset": None,
            "core_end_offset": None,
            "core_bars": 0,
            "reason": str(override["reason"]),
            "owner_semantic_verdict": True,
            "sample_owner_geometry_confirmed": False,
        }
    if order not in strict_pass_orders:
        if order not in strict_reject_reasons:
            raise OwnerStrictReviewError(f"strict reject reason missing for order {order}")
        return {
            "status": "CODEX_STRICT_REJECT",
            "core_start_offset": None,
            "core_end_offset": None,
            "core_bars": 0,
            "reason": str(strict_reject_reasons[order]),
            "owner_semantic_verdict": False,
            "sample_owner_geometry_confirmed": False,
        }
    if override is not None:
        action = str(override["action"])
        if action not in {"rebox", "keep_reference"}:
            raise OwnerStrictReviewError(f"unsupported active override for {sample_id}: {action}")
        start, end = int(override["core_start_offset"]), int(override["core_end_offset"])
        status = "OWNER_DIRECTED_REBOX_PROPOSAL" if action == "rebox" else "OWNER_REFERENCE_RECROP"
        reason = str(override["reason"])
        owner_semantic_verdict = action == "keep_reference"
    else:
        span = source_row["variants"]["L5_min24"]["span"]
        start, end = int(span["start_offset"]), int(span["end_offset"])
        status = "CODEX_STRICT_PASS_PENDING_OWNER"
        reason = "Contact-sheet pass: compact MA interaction precedes a fresh same-direction release; pending Owner review."
        owner_semantic_verdict = False
    core_bars = end - start + 1
    if core_bars not in {4, 5, 6, 7} or end > -1:
        raise OwnerStrictReviewError(f"invalid active core for {sample_id}: {start}..{end}")
    return {
        "status": status,
        "core_start_offset": start,
        "core_end_offset": end,
        "core_bars": core_bars,
        "reason": reason,
        "owner_semantic_verdict": owner_semantic_verdict,
        "sample_owner_geometry_confirmed": False,
    }


def validate_strict_preregistration(
    prereg: Mapping[str, Any], source_rows: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, dict[str, Any]], set[int], dict[int, str]]:
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise OwnerStrictReviewError("experiment ID drift")
    if len(source_rows) != 50 or len({str(row["sample_id"]) for row in source_rows}) != 50:
        raise OwnerStrictReviewError("source review is not 50 unique rows")
    for order, row in enumerate(source_rows, 1):
        row["source_order"] = order
    overrides = {str(row["sample_id"]): dict(row) for row in prereg["owner_decisions"]}
    if len(overrides) != len(prereg["owner_decisions"]):
        raise OwnerStrictReviewError("duplicate Owner decision")
    strict_pass_orders = {int(value) for value in prereg["strict_contact_sheet_review"]["pass_orders"]}
    strict_reject_reasons = {
        int(key): str(value)
        for key, value in prereg["strict_contact_sheet_review"]["reject_reasons_by_order"].items()
    }
    if strict_pass_orders & set(strict_reject_reasons):
        raise OwnerStrictReviewError("strict pass/reject overlap")
    if strict_pass_orders | set(strict_reject_reasons) | {3, 8, 20, 21, 22, 34} != set(range(1, 51)):
        raise OwnerStrictReviewError("strict contact-sheet coverage is incomplete")
    decisions = [
        resolve_strict_decision(row, overrides, strict_pass_orders, strict_reject_reasons)
        for row in source_rows
    ]
    expected = {
        "OWNER_REJECT": 6,
        "CODEX_STRICT_REJECT": 24,
        "OWNER_DIRECTED_REBOX_PROPOSAL": 5,
        "OWNER_REFERENCE_RECROP": 3,
        "CODEX_STRICT_PASS_PENDING_OWNER": 12,
    }
    if dict(Counter(str(row["status"]) for row in decisions)) != expected:
        raise OwnerStrictReviewError("strict status distribution drift")
    return overrides, strict_pass_orders, strict_reject_reasons


def render_html(rows: Sequence[Mapping[str, Any]], manifest_sha: str) -> str:
    cards: list[str] = []
    for row in rows:
        order = int(row["source_order"])
        title = f"{order:02d}/50 · {html.escape(str(row['symbol']))} · {html.escape(str(row['direction']))}"
        reason = html.escape(str(row["reason"]))
        if not bool(row["has_box_proposal"]):
            label = "Owner 剔除" if row["status"] == "OWNER_REJECT" else "严格门剔除"
            cards.append(
                f"<article class='reject'><h2>{title}</h2><span class='badge reject-b'>{label} · 无框</span>"
                f"<p>{reason}</p><img loading='lazy' src='{html.escape(str(row['review_src']))}'></article>"
            )
            continue
        meta = (
            f"core t{row['core_start_offset']}…t{row['core_end_offset']} · {row['core_bars']}根 · "
            f"模型右端 core+{row['post_core_visible_bars']} · 总窗 {row['model_window_bars']}根"
        )
        cards.append(
            f"<article><h2>{title}</h2><span class='badge'>{html.escape(str(row['status']))}</span>"
            f"<p>{html.escape(meta)}<br>{reason}</p><div class='pair'>"
            f"<figure><figcaption>拟模型输入叠框（clean PNG 无框另存）</figcaption><img loading='lazy' src='{html.escape(str(row['review_src']))}'></figure>"
            f"<figure><figcaption>未来 +5，仅人工审核，无 labels</figcaption><img loading='lazy' src='{html.escape(str(row['future_src']))}'></figure>"
            "</div></article>"
        )
    return """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>15m 严格 shortlist Review50 v5</title><style>
body{margin:0;background:#eef2f5;color:#18222c;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header,main{max-width:1500px;margin:auto;padding:18px}header{background:#fff7df;border-bottom:1px solid #d9c179}.note{line-height:1.65}.summary{margin-top:10px;padding:10px;background:#fff;border-left:4px solid #e33}article{background:#fff;border-radius:12px;padding:14px;margin-bottom:18px;box-shadow:0 2px 10px #1b304018}article.reject{border:1px solid #aab2b9}h1{margin:0 0 8px}h2{font-size:18px;margin:0 0 6px}.badge{display:inline-block;background:#fff0cd;color:#6b4b00;border-radius:999px;padding:3px 9px;font-size:12px}.reject-b{background:#eceff1;color:#4e5962}p{color:#596572;line-height:1.55;font-size:13px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}figure{margin:0}figcaption{font-size:12px;color:#5c6670;margin-bottom:5px}img{display:block;width:100%;height:auto;border:1px solid #d5dde4}@media(max-width:900px){.pair{grid-template-columns:1fr}}
</style></head><body><header><h1>15m 严格 shortlist Review50 v5</h1><div class='note'><b>宁缺毋滥：</b>50 张中仅保留 20 张有框提案，6 张按 Owner 明确意见无框剔除，另 24 张因平行均线、框内已启动、价格脱离均线或缺少新鲜释放而无框剔除。20 张仍只是待 Owner 复审提案，不是训练标签。</div><div class='summary'>每张有框图恰好 1 个红框；模型窗右侧只留核心后 0–2 根 K；未来 +5 根物理隔离。manifest SHA: """ + manifest_sha + "</div></header><main>" + "".join(cards) + "</main></body></html>"


def build(prereg_path: Path = DEFAULT_PREREG, output_dir: Path | None = None) -> dict[str, Any]:
    prereg_path = prereg_path.resolve()
    prereg = read_json(prereg_path)
    source_manifest = ROOT / str(prereg["source"]["review_manifest"])
    if sha256_file(source_manifest) != str(prereg["source"]["review_manifest_sha256"]):
        raise OwnerStrictReviewError("frozen source manifest hash drift")
    source_rows = read_jsonl(source_manifest)
    overrides, strict_pass_orders, strict_reject_reasons = validate_strict_preregistration(
        prereg, source_rows
    )
    builder_commit = verify_builder_committed(
        [Path(__file__), ROOT / "scripts" / "build_15m_ma_launch_owner_strict_review50.py", prereg_path]
    )
    final_dir = output_dir.resolve() if output_dir else prereg_path.parent / "results"
    building = final_dir.with_name(f"{final_dir.name}.building")
    if final_dir.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite owner-strict review: {final_dir}")
    public_images = building / "public" / "images"
    rejected_images = building / "public" / "rejected"
    model_inputs = building / "model_inputs_clean"
    future_reviews = building / "future_review_only"
    for directory in (public_images, rejected_images, model_inputs, future_reviews):
        directory.mkdir(parents=True)

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for order, row in enumerate(source_rows, 1):
        grouped[str(row["source_path"])].append((order, row))
    output_rows: list[dict[str, Any]] = []
    source_audits: list[dict[str, Any]] = []
    for source_path, items in sorted(grouped.items()):
        frame, audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("holdout OHLCV materialized")
        source_audits.append(audit)
        enriched = add_six_mas(frame)
        for order, source_row in items:
            source_row["source_order"] = order
            decision = resolve_strict_decision(
                source_row, overrides, strict_pass_orders, strict_reject_reasons
            )
            sample_id = str(source_row["sample_id"])
            has_box = int(decision["core_bars"]) > 0
            common: dict[str, Any] = {
                "source_order": order,
                "sample_id": sample_id,
                "symbol": str(source_row["symbol"]),
                "direction": str(source_row["direction"]),
                "split": str(source_row["split"]),
                "anchor_time": str(source_row["anchor_time"]),
                "source_path": str(source_row["source_path"]),
                **decision,
                "has_box_proposal": has_box,
                "training_eligible": False,
                "production_eligible": False,
                "yolo_label_path": None,
            }
            if not has_box:
                source_clean = ROOT / str(source_row["image_path"])
                if sha256_file(source_clean) != str(source_row["image_sha256"]):
                    raise OwnerStrictReviewError(f"no-box clean image SHA drift: {sample_id}")
                filename = f"{order:02d}_{source_row['symbol']}_{source_row['direction']}_{sample_id}_{decision['status']}.png"
                target = rejected_images / filename
                shutil.copyfile(source_clean, target)
                common.update(
                    {
                        "review_image_path": relative_final_path(target, building, final_dir),
                        "review_image_sha256": sha256_file(target),
                        "review_src": f"rejected/{filename}",
                        "model_input_path": None,
                        "model_input_sha256": None,
                        "future_review_path": None,
                        "future_review_sha256": None,
                        "future_src": None,
                        "model_window_bars": 0,
                        "pre_core_context_bars": 0,
                        "post_core_visible_bars": 0,
                        "core_to_model_right_gap_bars": None,
                        "box": None,
                    }
                )
                output_rows.append(common)
                continue

            anchor_i = int(source_row["source_anchor_i"])
            core_start_i = anchor_i + int(decision["core_start_offset"])
            core_end_i = anchor_i + int(decision["core_end_offset"])
            pre_bars, post_bars = stable_context(sample_id)
            model_start_i = core_start_i - pre_bars
            model_end_i = core_end_i + post_bars
            future_end_i = core_end_i + FUTURE_REVIEW_BARS
            model_window = enriched.iloc[model_start_i : model_end_i + 1].reset_index(drop=True)
            future_window = enriched.iloc[model_start_i : future_end_i + 1].reset_index(drop=True)
            assert_contiguous(model_window, sample_id=sample_id, kind="model")
            assert_contiguous(future_window, sample_id=sample_id, kind="future-review")
            if pd.Timestamp(future_window["open_time"].iloc[-1]) >= HOLDOUT_START:
                raise OwnerStrictReviewError(f"future review touches holdout: {sample_id}")
            core_start_local = core_start_i - model_start_i
            core_end_local = core_end_i - model_start_i
            clean, transform = render_chart(model_window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, out_path=None)
            box = core_box(transform, model_window, start_local=core_start_local, end_local=core_end_local)
            base_name = f"{order:02d}_{source_row['symbol']}_{source_row['direction']}_{sample_id}"
            model_path = model_inputs / f"{base_name}_MODEL_CLEAN.png"
            model_path.write_bytes(encode_png(clean))
            overlay = clean.copy()
            draw_box(overlay, box)
            overlay_path = public_images / f"{base_name}_MODEL_REVIEW.png"
            overlay_path.write_bytes(encode_png(overlay))
            future, future_transform = render_chart(
                future_window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, out_path=None
            )
            future_box = core_box(
                future_transform,
                future_window,
                start_local=core_start_i - model_start_i,
                end_local=core_end_i - model_start_i,
            )
            draw_box(future, future_box)
            future_path = future_reviews / f"{base_name}_FUTURE_PLUS5_REVIEW_ONLY.png"
            future_path.write_bytes(encode_png(future))
            common.update(
                {
                    "core_start_source_i": core_start_i,
                    "core_end_source_i": core_end_i,
                    "core_start_local": core_start_local,
                    "core_end_local": core_end_local,
                    "model_window_start_i": model_start_i,
                    "model_window_end_i": model_end_i,
                    "model_window_bars": len(model_window),
                    "pre_core_context_bars": pre_bars,
                    "post_core_visible_bars": post_bars,
                    "core_to_model_right_gap_bars": model_end_i - core_end_i,
                    "model_input_path": relative_final_path(model_path, building, final_dir),
                    "model_input_sha256": sha256_file(model_path),
                    "review_image_path": relative_final_path(overlay_path, building, final_dir),
                    "review_image_sha256": sha256_file(overlay_path),
                    "review_src": f"images/{overlay_path.name}",
                    "future_review_path": relative_final_path(future_path, building, final_dir),
                    "future_review_sha256": sha256_file(future_path),
                    "future_src": f"../future_review_only/{future_path.name}",
                    "future_review_bars_after_core": FUTURE_REVIEW_BARS,
                    "future_review_has_label_file": False,
                    "box": box,
                    "future_review_box": future_box,
                }
            )
            output_rows.append(common)

    output_rows.sort(key=lambda row: int(row["source_order"]))
    manifest_path = building / "review_manifest.jsonl"
    write_jsonl(manifest_path, output_rows)
    manifest_sha = sha256_file(manifest_path)
    index_path = building / "public" / "index.html"
    index_path.write_text(render_html(output_rows, manifest_sha), encoding="utf-8")
    active = [row for row in output_rows if row["has_box_proposal"]]
    no_box = [row for row in output_rows if not row["has_box_proposal"]]
    label_files = list(building.rglob("*.txt"))
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "n_source_rows": 50,
        "n_review_box_proposals": len(active),
        "n_no_box_rows": len(no_box),
        "n_owner_reject_no_box": sum(row["status"] == "OWNER_REJECT" for row in no_box),
        "n_codex_strict_reject_no_box": sum(row["status"] == "CODEX_STRICT_REJECT" for row in no_box),
        "status_counts": dict(Counter(str(row["status"]) for row in output_rows)),
        "boxes_per_active_image": 1,
        "boxes_per_no_box_image": 0,
        "core_bars_distribution": dict(Counter(int(row["core_bars"]) for row in active)),
        "model_window_bars": describe([float(row["model_window_bars"]) for row in active]),
        "core_to_model_right_gap_bars": describe(
            [float(row["core_to_model_right_gap_bars"]) for row in active]
        ),
        "all_model_right_gaps_tip_tip1_tip2": all(
            0 <= int(row["core_to_model_right_gap_bars"]) <= 2 for row in active
        ),
        "core_containment_pass": sum(bool(row["box"]["contains_core_wicks_and_six_mas"]) for row in active),
        "source_width_px": describe([float(row["box"]["source_width_px"]) for row in active]),
        "source_height_px": describe([float(row["box"]["source_height_px"]) for row in active]),
        "holdout_ohlcv_rows_materialized": sum(
            int(audit["holdout_ohlcv_rows_materialized"]) for audit in source_audits
        ),
        "future_review_directory_physically_separate": True,
        "future_review_label_files": len(label_files),
        "yolo_labels_written": 0,
        "training_started": False,
        "active_or_frozen_modified": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    if len(active) != 20 or len(no_box) != 30:
        raise OwnerStrictReviewError("strict shortlist size drift")
    if summary["core_containment_pass"] != 20 or not summary["all_model_right_gaps_tip_tip1_tip2"]:
        raise OwnerStrictReviewError(f"strict geometry QA failed: {summary}")
    if summary["holdout_ohlcv_rows_materialized"] != 0 or label_files:
        raise OwnerStrictReviewError(f"strict safety QA failed: {summary}")
    write_json(building / "summary.json", summary)
    write_jsonl(building / "source_audit.jsonl", source_audits)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "preregistration_sha256": sha256_file(prereg_path),
        "source_manifest_sha256": sha256_file(source_manifest),
        "review_manifest_sha256": manifest_sha,
        "review_html_sha256": sha256_file(index_path),
        "n_source_rows": 50,
        "n_review_box_proposals": 20,
        "n_no_box_rows": 30,
        "holdout_ohlcv_rows_materialized": 0,
        "yolo_labels_written": 0,
        "training_started": False,
        "active_or_frozen_modified": False,
    }
    write_json(building / "build_receipt.json", receipt)
    os.replace(building, final_dir)
    return receipt
