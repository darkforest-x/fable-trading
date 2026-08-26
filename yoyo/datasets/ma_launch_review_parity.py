"""Build a causal/review parity gallery for the 15m MA-launch weak dataset.

The left panel reuses the exact canonical YOLO training PNG and label.  Its
visible OHLC window is therefore the per-sample 14--22 bar interval recorded in
``datasets/ma_launch_t3_10000_v1/manifest.jsonl`` and ends between selection
bar ``t`` and ``t+2``.  The right panel is a physically separate 48-bar human
review image spanning ``t-30..t+17``.  Future bars never enter the causal image
or its YOLO label.

The original 1,000-candidate gallery marked selection bar ``t``.  This module
re-renders only those review-only images with a blue ``t-3`` marker and an
orange dashed ``t`` marker.  The already-correct 9,000-candidate review images
are hash-verified and reused.  Historical artifacts are never overwritten.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np

from yoyo.datasets.fifteen_minute_launch_candidates import (
    CandidateSpec,
    read_preholdout_prefix,
    render_review_chart,
    utc,
)


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-15m-ma-launch-t3-review-parity-v2"


class ReviewParityError(ValueError):
    """Fail-closed review-parity build error."""


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of one file without loading it all into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_path(value: object) -> Path:
    path = (ROOT / str(value)).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ReviewParityError(f"path escapes repository: {value}") from exc
    return path


def _repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def load_contract(path: Path) -> dict[str, Any]:
    """Load and validate the immutable source and safety contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise ReviewParityError("unexpected experiment_id")
    if int(payload["view_contract"]["review_marker_offset_bars"]) != -3:
        raise ReviewParityError("review marker must be t-3")
    if payload["view_contract"].get("causal_panel_source") != "canonical_training_png":
        raise ReviewParityError("causal panel must reuse canonical training pixels")
    if payload["view_contract"].get("future_panel_training_eligible") is not False:
        raise ReviewParityError("future review panel must remain training-ineligible")
    safety = payload["safety"]
    forbidden = (
        "holdout_read",
        "training_dataset_change",
        "model_training",
        "active_or_frozen_change",
        "promote",
        "deployment",
        "forward_or_order_state_change",
    )
    if any(safety.get(key) is not False for key in forbidden):
        raise ReviewParityError("one or more safety switches drifted from false")
    return payload


def _load_pinned_rows(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = _repo_path(contract["path"])
    if sha256_file(path) != str(contract["sha256"]):
        raise ReviewParityError(f"manifest hash drifted: {path}")
    rows = _read_jsonl(path)
    if len(rows) != int(contract["rows"]):
        raise ReviewParityError(f"manifest row count drifted: {path}")
    return rows


def _load_candidate_union(
    prereg: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any]]]:
    rows: list[dict[str, Any]] = []
    contracts: dict[str, Mapping[str, Any]] = {}
    for contract in prereg["sources"]["candidate_manifests"]:
        pool = str(contract["pool"])
        if pool in contracts:
            raise ReviewParityError(f"duplicate pool contract: {pool}")
        contracts[pool] = contract
        part = _load_pinned_rows(contract)
        for row in part:
            item = dict(row)
            item["candidate_pool"] = pool
            rows.append(item)
    if len(rows) != int(prereg["sources"]["candidate_rows"]):
        raise ReviewParityError("candidate union count drifted")
    event_ids = [str(row["event_id"]) for row in rows]
    identities = [
        (str(row["symbol"]), str(row["direction"]), utc(row["anchor_time"]).isoformat())
        for row in rows
    ]
    if len(event_ids) != len(set(event_ids)):
        raise ReviewParityError("candidate event_id is not unique")
    if len(identities) != len(set(identities)):
        raise ReviewParityError("candidate symbol/side/time identity is not unique")
    return rows, contracts


def parse_yolo_label(text: str) -> tuple[int, float, float, float, float]:
    """Parse exactly one normalized YOLO detection label."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ReviewParityError("positive label must contain exactly one box")
    fields = lines[0].split()
    if len(fields) != 5:
        raise ReviewParityError("YOLO label must have five fields")
    class_id = int(fields[0])
    values = tuple(float(value) for value in fields[1:])
    if class_id not in (0, 1):
        raise ReviewParityError(f"unexpected class id: {class_id}")
    cx, cy, width, height = values
    if not all(math.isfinite(value) for value in values):
        raise ReviewParityError("YOLO label contains a non-finite coordinate")
    if not (0.0 < width <= 1.0 and 0.0 < height <= 1.0):
        raise ReviewParityError("YOLO label width/height is invalid")
    if cx - width / 2 < 0 or cx + width / 2 > 1:
        raise ReviewParityError("YOLO label crosses the horizontal image boundary")
    if cy - height / 2 < 0 or cy + height / 2 > 1:
        raise ReviewParityError("YOLO label crosses the vertical image boundary")
    return class_id, cx, cy, width, height


def css_box(box: Sequence[float]) -> str:
    """Return a percentage CSS rectangle for one normalized YOLO box."""

    if len(box) != 4:
        raise ReviewParityError("CSS box requires cx/cy/width/height")
    cx, cy, width, height = (float(value) for value in box)
    return (
        f"left:{100 * (cx - width / 2):.6f}%;"
        f"top:{100 * (cy - height / 2):.6f}%;"
        f"width:{100 * width:.6f}%;"
        f"height:{100 * height:.6f}%;"
    )


def _load_training_positives(prereg: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    contract = prereg["sources"]["training_manifest"]
    rows = _load_pinned_rows(contract)
    positives = {
        str(row["event_id"]): dict(row)
        for row in rows
        if row.get("sample_kind") == "positive_weak"
    }
    if len(positives) != int(contract["positive_rows"]):
        raise ReviewParityError("training positive count drifted")
    if any(int(row["core_end_i"]) != int(row["source_anchor_i"]) - 3 for row in positives.values()):
        raise ReviewParityError("one or more training cores do not end at t-3")
    return positives


def _rerender_pool_t3(
    rows: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    *,
    output_dir: Path,
    holdout_start: object,
) -> dict[str, dict[str, Any]]:
    """Re-render one candidate pool with a review-only blue t-3 marker."""

    prereg_path = _repo_path(contract["preregistration_path"])
    if sha256_file(prereg_path) != str(contract["preregistration_sha256"]):
        raise ReviewParityError("candidate preregistration hash drifted")
    original = json.loads(prereg_path.read_text(encoding="utf-8"))
    spec = replace(CandidateSpec.from_preregistration(original), review_marker_offset_bars=-3)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_path"])].append(row)
    rendered: dict[str, dict[str, Any]] = {}
    for source_number, (source_path, source_rows) in enumerate(sorted(grouped.items()), 1):
        frame, audit = read_preholdout_prefix(
            _repo_path(source_path), end_exclusive=utc(holdout_start)
        )
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise ReviewParityError("holdout row materialized during review render")
        for row in source_rows:
            old_name = Path(str(row["review_path"])).name
            target = output_dir / old_name
            receipt = render_review_chart(frame, row, spec=spec, output=target)
            if int(receipt["review_marker_offset_bars"]) != -3:
                raise ReviewParityError("re-rendered marker is not t-3")
            rendered[str(row["event_id"])] = receipt
        if source_number == 1 or source_number % 25 == 0 or source_number == len(grouped):
            print(
                f"[review-parity] rerender source {source_number}/{len(grouped)} "
                f"rows={len(source_rows)}"
            )
    if len(rendered) != len(rows):
        raise ReviewParityError("re-rendered pool count drifted")
    return rendered


def _html_src(target: Path, html_path: Path) -> str:
    return Path(os.path.relpath(target.resolve(), start=html_path.parent.resolve())).as_posix()


def _card_html(row: Mapping[str, Any], page_path: Path) -> str:
    side = html.escape(str(row["direction"]))
    title = (
        f"{side} #{int(row['rank'])} · {html.escape(str(row['symbol']))} · "
        f"{html.escape(str(row['anchor_time']))}"
    )
    review_src = html.escape(_html_src(_repo_path(row["review_v2_path"]), page_path))
    if row["causal_status"] == "training_positive":
        causal_src = html.escape(_html_src(_repo_path(row["causal_image_path"]), page_path))
        box_style = css_box(row["yolo_box"])
        box_class = "long" if int(row["class_id"]) == 0 else "short"
        causal = f"""
          <figure>
            <div class="chart-frame">
              <img loading="lazy" src="{causal_src}" alt="exact model input">
              <span class="bbox {box_class}" style="{box_style}"></span>
            </div>
            <figcaption>模型实际输入（原 PNG 未改） · W{int(row['window_len'])} · core {int(row['core_len'])} 根 · confirm {int(row['confirmation_bars'])} 根</figcaption>
          </figure>
        """
    else:
        causal = """
          <figure class="missing">
            <div>时间切分 purge：没有进入训练集，也没有伪造训练图片。</div>
            <figcaption>仅保留候选审核视图</figcaption>
          </figure>
        """
    return f"""
      <article class="card" data-side="{side}" data-pool="{html.escape(str(row['candidate_pool']))}">
        <h2>{title}</h2>
        <p class="meta">event {html.escape(str(row['event_id']))} · pool {html.escape(str(row['candidate_pool']))} · 蓝线 t-3 / 橙线 t</p>
        <div class="panels">
          {causal}
          <figure>
            <img loading="lazy" src="{review_src}" alt="review-only completed path">
            <figcaption>人工复核专用 48 根：t-30..t+17；右侧未来不进入模型输入或标签框</figcaption>
          </figure>
        </div>
      </article>
    """


PAGE_STYLE = """
  :root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  body { margin: 0; background: #f3f5f7; color: #17212b; }
  header { position: sticky; top: 0; z-index: 5; background: rgba(255,255,255,.96); border-bottom: 1px solid #d9e0e7; padding: 14px 22px; }
  header h1 { margin: 0 0 6px; font-size: 20px; }
  header p { margin: 0; color: #536170; font-size: 14px; }
  nav { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 8px; }
  nav a { color: #0969da; text-decoration: none; }
  main { max-width: 1800px; margin: 0 auto; padding: 18px; }
  .card { background: white; border: 1px solid #dfe5eb; border-radius: 12px; margin-bottom: 18px; padding: 14px; box-shadow: 0 2px 8px rgba(31,42,55,.05); }
  .card h2 { margin: 0; font-size: 18px; }
  .meta { margin: 5px 0 12px; color: #687686; font-size: 13px; }
  .panels { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }
  figure { margin: 0; min-width: 0; }
  figure > img, .chart-frame > img { display: block; width: 100%; height: auto; background: white; }
  figcaption { margin-top: 6px; color: #566575; font-size: 13px; line-height: 1.45; }
  .chart-frame { position: relative; width: 100%; line-height: 0; background: white; }
  .bbox { position: absolute; display: block; box-sizing: border-box; border: 3px solid; pointer-events: none; }
  .bbox.long { border-color: #14944f; }
  .bbox.short { border-color: #e03c31; }
  .missing { min-height: 240px; display: grid; place-content: center; text-align: center; background: #f7f8fa; border: 1px dashed #aab5c0; color: #697888; }
  @media (max-width: 1000px) { .panels { grid-template-columns: 1fr; } header { position: static; } }
"""


def _write_gallery(
    rows: Sequence[Mapping[str, Any]], *, output_dir: Path, page_size: int
) -> list[Path]:
    pages: list[Path] = []
    page_count = math.ceil(len(rows) / page_size)
    for page_index in range(page_count):
        page_path = output_dir / f"page_{page_index + 1:03d}.html"
        page_rows = rows[page_index * page_size : (page_index + 1) * page_size]
        links = " ".join(
            f'<a href="page_{number:03d}.html">{number}</a>'
            for number in range(1, page_count + 1)
        )
        body = "\n".join(_card_html(row, page_path) for row in page_rows)
        page_path.write_text(
            f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>15m t-3 因果/审核双视图 · {page_index + 1}/{page_count}</title><style>{PAGE_STYLE}</style></head>
<body><header><h1>15m t-3 因果/审核双视图 · 第 {page_index + 1}/{page_count} 页</h1>
<p>左：模型逐像素输入与标签框；右：物理隔离的未来复核图。两者不再冒充同一坐标尺度。</p><nav>{links}</nav></header><main>{body}</main></body></html>""",
            encoding="utf-8",
        )
        pages.append(page_path)
    index_path = output_dir / "index.html"
    page_links = "\n".join(
        f'<li><a href="{path.name}">第 {index + 1} 页</a></li>'
        for index, path in enumerate(pages)
    )
    index_path.write_text(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>15m t-3 因果/审核双视图</title><style>{PAGE_STYLE} .summary{{background:white;padding:22px;border-radius:12px}} li{{margin:8px 0}}</style></head>
<body><main><section class="summary"><h1>15m t-3 因果/审核双视图</h1>
<p>10,000 个候选全部统一显示蓝线 t-3；9,938 个训练正例左图直接引用 canonical PNG，CSS 只叠加同名 YOLO 标签框；62 个 purge 候选不伪造训练图。</p>
<p>右图含 t 后未来，仅供人工确认完成形态，永不作为模型输入。</p><ol>{page_links}</ol></section></main></body></html>""",
        encoding="utf-8",
    )
    return [index_path, *pages]


def _draw_box(image: Any, box: Sequence[float], class_id: int) -> Any:
    out = image.copy()
    height, width = out.shape[:2]
    cx, cy, bw, bh = (float(value) for value in box)
    x0, x1 = round((cx - bw / 2) * width), round((cx + bw / 2) * width)
    y0, y1 = round((cy - bh / 2) * height), round((cy + bh / 2) * height)
    color = (30, 150, 30) if class_id == 0 else (30, 30, 210)
    cv2.rectangle(out, (x0, y0), (x1, y1), color, 4, cv2.LINE_AA)
    return out


def _resize_width(image: Any, width: int) -> Any:
    scale = width / image.shape[1]
    return cv2.resize(
        image,
        (width, int(round(image.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _select_overview(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    targets = {
        "candidate1000": (1, 250, 500),
        "candidate9000": (1, 2250, 4500),
    }
    selected: list[Mapping[str, Any]] = []
    for pool, ranks in targets.items():
        for side in ("LONG", "SHORT"):
            eligible = [
                row
                for row in rows
                if row["candidate_pool"] == pool
                and row["direction"] == side
                and row["causal_status"] == "training_positive"
            ]
            for target in ranks:
                selected.append(min(eligible, key=lambda row: abs(int(row["rank"]) - target)))
    return selected


def _build_overview(rows: Sequence[Mapping[str, Any]], output: Path) -> list[str]:
    selected = _select_overview(rows)
    cell_width = 400
    title_height = 34
    cells: list[Any] = []
    event_ids: list[str] = []
    for row in selected:
        causal = cv2.imread(str(_repo_path(row["causal_image_path"])), cv2.IMREAD_COLOR)
        review = cv2.imread(str(_repo_path(row["review_v2_path"])), cv2.IMREAD_COLOR)
        if causal is None or review is None:
            raise ReviewParityError("overview image decode failed")
        causal = _resize_width(_draw_box(causal, row["yolo_box"], int(row["class_id"])), cell_width)
        review = _resize_width(review, cell_width)
        panel_height = title_height + causal.shape[0] + review.shape[0]
        cell = np.full((panel_height, cell_width, 3), 255, dtype=np.uint8)
        label = f"{row['candidate_pool']} {row['direction']} #{int(row['rank'])} {row['symbol']}"
        cv2.putText(cell, label, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (32, 42, 52), 1, cv2.LINE_AA)
        cell[title_height : title_height + causal.shape[0]] = causal
        cell[title_height + causal.shape[0] :] = review
        cells.append(cell)
        event_ids.append(str(row["event_id"]))
    columns = 4
    rows_count = math.ceil(len(cells) / columns)
    cell_height = max(cell.shape[0] for cell in cells)
    canvas = np.full(
        (rows_count * cell_height, columns * cell_width, 3), 245, dtype=np.uint8
    )
    for index, cell in enumerate(cells):
        row_i, col_i = divmod(index, columns)
        y, x = row_i * cell_height, col_i * cell_width
        canvas[y : y + cell.shape[0], x : x + cell.shape[1]] = cell
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise OSError(f"failed to write overview: {output}")
    return event_ids


def build_review_parity(
    prereg_path: Path,
    *,
    output_dir: Path,
    page_size: int = 250,
) -> dict[str, Any]:
    """Materialize the v2 non-destructive review parity artifact."""

    prereg = load_contract(prereg_path)
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite review-parity output: {output_dir}")
    output_dir.mkdir(parents=True)
    candidates, contracts = _load_candidate_union(prereg)
    positives = _load_training_positives(prereg)
    event_ids = {str(row["event_id"]) for row in candidates}
    if not set(positives).issubset(event_ids):
        raise ReviewParityError("training manifest contains an event outside candidate union")

    first_pool = str(prereg["view_contract"]["rerender_pool"])
    first_rows = [row for row in candidates if row["candidate_pool"] == first_pool]
    rerendered = _rerender_pool_t3(
        first_rows,
        contracts[first_pool],
        output_dir=output_dir / "review_charts_t3_first1000",
        holdout_start=prereg["sources"]["holdout_start"],
    )

    joined: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    dataset_root = _repo_path(prereg["sources"]["training_manifest"]["dataset_root"])
    for candidate in candidates:
        event_id = str(candidate["event_id"])
        row = dict(candidate)
        if row["candidate_pool"] == first_pool:
            receipt = rerendered[event_id]
            review_path = _repo_path(receipt["review_path"])
            row["review_v2_path"] = _repo_relative(review_path)
            row["review_v2_sha256"] = str(receipt["review_sha256"])
            row["review_v2_origin"] = "rerendered_t3"
            row["review_marker_offset_bars"] = -3
            row["review_marker_time"] = str(receipt["review_marker_time"])
            row["review_marker_source_i"] = int(receipt["review_marker_source_i"])
        else:
            if int(row.get("review_marker_offset_bars", 999)) != -3:
                raise ReviewParityError("reused review image is not marked at t-3")
            review_path = _repo_path(row["review_path"])
            if sha256_file(review_path) != str(row["review_sha256"]):
                raise ReviewParityError("reused review image hash drifted")
            row["review_v2_path"] = _repo_relative(review_path)
            row["review_v2_sha256"] = str(row["review_sha256"])
            row["review_v2_origin"] = "reused_already_t3"
        row["review_marker_is_training_label"] = False
        if int(row["review_marker_source_i"]) != int(row["source_anchor_i"]) - 3:
            raise ReviewParityError("review marker source index is not t-3")
        if utc(row["review_marker_time"]) != utc(row["anchor_time"]) - timedelta(minutes=45):
            raise ReviewParityError("review marker timestamp is not 45 minutes before t")

        positive = positives.get(event_id)
        if positive is None:
            row.update(
                {
                    "causal_status": "purged_no_training_image",
                    "causal_image_path": None,
                    "causal_image_sha256": None,
                    "label_path": None,
                    "label_sha256": None,
                    "yolo_box": None,
                    "class_id": None,
                    "window_len": None,
                    "core_len": None,
                    "confirmation_bars": None,
                }
            )
            counts["purged"] += 1
        else:
            image_path = dataset_root / str(positive["image_path"])
            label_path = dataset_root / str(positive["label_path"])
            if sha256_file(image_path) != str(positive["image_sha256"]):
                raise ReviewParityError("canonical training image hash drifted")
            if sha256_file(label_path) != str(positive["label_sha256"]):
                raise ReviewParityError("canonical training label hash drifted")
            class_id, cx, cy, width, height = parse_yolo_label(
                label_path.read_text(encoding="utf-8")
            )
            if class_id != int(positive["class_id"]):
                raise ReviewParityError("label class differs from training manifest")
            row.update(
                {
                    "causal_status": "training_positive",
                    "causal_image_path": _repo_relative(image_path),
                    "causal_image_sha256": str(positive["image_sha256"]),
                    "label_path": _repo_relative(label_path),
                    "label_sha256": str(positive["label_sha256"]),
                    "yolo_box": [cx, cy, width, height],
                    "class_id": class_id,
                    "window_len": int(positive["geometry"]["window_len"]),
                    "core_len": int(positive["geometry"]["core_len"]),
                    "confirmation_bars": int(positive["geometry"]["confirmation_bars"]),
                    "causal_visible_start_time": str(positive["render_start_time"]),
                    "causal_visible_end_time": str(positive["render_end_time"]),
                    "causal_latest_offset_from_t": int(positive["input_latest_offset_from_t"]),
                }
            )
            counts["training_positive"] += 1
            counts[f"training_positive/{row['direction'].lower()}"] += 1
        counts[f"pool/{row['candidate_pool']}"] += 1
        joined.append(row)

    expected = prereg["expected_counts"]
    if counts["training_positive"] != int(expected["training_positive"]):
        raise ReviewParityError("joined training-positive count drifted")
    if counts["purged"] != int(expected["purged"]):
        raise ReviewParityError("joined purge count drifted")
    if len(joined) != int(expected["candidate_rows"]):
        raise ReviewParityError("joined candidate count drifted")

    manifest_path = output_dir / "parity_manifest.jsonl"
    _write_jsonl(manifest_path, joined)
    pages = _write_gallery(joined, output_dir=output_dir, page_size=page_size)
    overview_path = output_dir / "comparison_overview.png"
    overview_events = _build_overview(joined, overview_path)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "candidate_rows": len(joined),
        "candidate_pools": {
            key.removeprefix("pool/"): value
            for key, value in sorted(counts.items())
            if key.startswith("pool/")
        },
        "training_positive_rows": counts["training_positive"],
        "training_positive_long": counts["training_positive/long"],
        "training_positive_short": counts["training_positive/short"],
        "purged_without_training_image": counts["purged"],
        "review_marker_t_minus_3_rows": sum(
            int(row["review_marker_source_i"]) == int(row["source_anchor_i"]) - 3
            for row in joined
        ),
        "first1000_rerendered_t_minus_3": len(rerendered),
        "later9000_reused_t_minus_3": len(joined) - len(rerendered),
        "canonical_training_pixels_reused_without_reencode": counts["training_positive"],
        "future_review_images_used_as_training_input": 0,
        "future_review_images_used_as_training_labels": 0,
        "holdout_ohlcv_rows_materialized": 0,
        "training_dataset_files_changed": 0,
        "models_trained": 0,
        "manifest_path": _repo_relative(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "gallery_index": _repo_relative(pages[0]),
        "gallery_pages": len(pages) - 1,
        "overview_path": _repo_relative(overview_path),
        "overview_sha256": sha256_file(overview_path),
        "overview_event_ids": overview_events,
        "training_eligible": False,
        "production_eligible": False,
        "passed": True,
    }
    receipt_path = output_dir / "verification_receipt.json"
    _write_json(receipt_path, receipt)
    return receipt
