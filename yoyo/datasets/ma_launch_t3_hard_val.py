"""Build a pre-holdout hard-negative validation sidecar for MA-launch YOLO.

The source columns are ``open/high/low/close`` plus causal SMA/EMA 20/60/120.
ATR14 and the six-MA bandwidth at pseudo-``t`` use no later row than pseudo-
``t`` (the bandwidth intentionally uses ``t-1`` exactly as the frozen base
builder does).  The negative label may inspect pseudo-``t..t+11`` only to prove
that no completed launch occurred.  Model pixels remain a 14--22 bar window
ending at pseudo-``t..t+2`` according to the matched 3--5 confirmation count.

This module never mutates the frozen base dataset.  Every new window stays in
the original chronological validation split, avoids every candidate guard and
every existing negative window, and is disjoint from every other new window.
An exact 1:1 ratio is a soft target: a missing sample is recorded rather than
weakening exclusions or crossing symbols.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import (
    read_preholdout_prefix,
    sha256_file,
    utc,
)
from yoyo.datasets.ma_launch_t3_training import (
    ROOT,
    _interval_is_contiguous,
    _negative_pool,
    load_candidate_union,
    load_preregistration,
    mark_positive_guards,
    negative_feature_arrays,
    split_for_interval,
)
from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.layers.l1_detection.render import render_chart


EXPERIMENT_ID = "exp-15m-ma-launch-t3-hardval-v1"
DEFAULT_EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
DEFAULT_PREREG = DEFAULT_EXPERIMENT / "preregistration.json"
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_t3_hardval_v1"
DEFAULT_RESULTS = DEFAULT_EXPERIMENT / "results"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class HardValError(ValueError):
    """Fail-closed hard-validation construction error."""


@dataclass(frozen=True)
class HardValPlan:
    """One hard no-launch validation window paired to a val positive geometry."""

    sample_id: str
    template_positive_sample_id: str
    symbol: str
    source_path: str
    split: str
    pseudo_t_i: int
    pseudo_t_time: str
    window_len: int
    confirmation_bars: int
    window_start_i: int
    window_end_i: int
    label_future_end_i: int
    bandwidth_pct: float
    close_abs_atr: float
    two_sided_favorable_abs_atr: float


def _repo_path(value: object) -> Path:
    path = (ROOT / str(value)).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise HardValError(f"path escapes repository: {value}") from exc
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


def load_contract(path: Path = DEFAULT_PREREG) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the sidecar contract and its hash-pinned frozen base contract."""

    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("experiment_id") != EXPERIMENT_ID:
        raise HardValError("unexpected experiment_id")
    safety = contract["safety"]
    if any(value is not False for value in safety.values()):
        raise HardValError("every safety/eligibility switch must remain false")
    if int(contract["sources"]["holdout_ohlcv_rows_allowed"]) != 0:
        raise HardValError("holdout OHLCV allowance must be zero")
    source = contract["sources"]["base_preregistration"]
    base_path = _repo_path(source["path"])
    if sha256_file(base_path) != str(source["sha256"]):
        raise HardValError("base preregistration hash drifted")
    base = load_preregistration(base_path)
    if utc(base["sources"]["holdout_start"]) != utc(contract["sources"]["holdout_start"]):
        raise HardValError("holdout boundary differs from frozen base")
    frozen = contract["hard_validation_contract"]
    original = base["negative_sampling"]
    if float(frozen["six_ma_bandwidth_pct_max"]) != float(
        original["hard_definition"]["six_ma_bandwidth_pct_max"]
    ):
        raise HardValError("hard bandwidth threshold differs from frozen base")
    no_launch = original["completed_no_launch_condition"]
    if float(frozen["pseudo_t_close_abs_atr_max_over_12_bars"]) != float(
        no_launch["pseudo_t_close_abs_atr_max_over_12_bars"]
    ):
        raise HardValError("close no-launch threshold differs from frozen base")
    if float(frozen["pseudo_t_two_sided_favorable_abs_atr_max_over_12_bars"]) != float(
        no_launch["pseudo_t_two_sided_favorable_abs_atr_max_over_12_bars"]
    ):
        raise HardValError("favorable no-launch threshold differs from frozen base")
    return contract, base


def load_base_manifest(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Load and hash-check the frozen 36,812-row base manifest."""

    source = contract["sources"]["base_dataset"]
    path = _repo_path(source["manifest"])
    if sha256_file(path) != str(source["manifest_sha256"]):
        raise HardValError("base dataset manifest hash drifted")
    rows = _read_jsonl(path)
    if len(rows) != int(source["manifest_rows"]):
        raise HardValError("base dataset manifest row count drifted")
    counts = Counter(str(row["sample_kind"]) for row in rows)
    val_positive = sum(
        row["sample_kind"] == "positive_weak" and row["split"] == "val" for row in rows
    )
    negatives = counts["negative_easy"] + counts["negative_hard"]
    if val_positive != int(source["val_positive_rows"]):
        raise HardValError("base val-positive count drifted")
    if negatives != int(source["existing_negative_rows"]):
        raise HardValError("base negative count drifted")
    return rows


def _template_geometry(row: Mapping[str, Any]) -> tuple[int, int]:
    geometry = row["geometry"]
    return int(geometry["window_len"]), int(geometry["confirmation_bars"])


def select_source_hard_val(
    enriched: pd.DataFrame,
    *,
    source_path: str,
    symbol: str,
    source_candidates: Sequence[Mapping[str, Any]],
    templates: Sequence[Mapping[str, Any]],
    existing_negative_intervals: Sequence[tuple[int, int]],
    contract: Mapping[str, Any],
    base: Mapping[str, Any],
) -> tuple[list[HardValPlan], list[dict[str, Any]]]:
    """Select a deterministic maximum-effort 1:1 hard-val set for one source."""

    features = negative_feature_arrays(enriched, base)
    times = pd.to_datetime(enriched["open_time"], utc=True)
    segments = enriched["_segment_id"].to_numpy(dtype=int)
    occupied = np.zeros(len(enriched), dtype=bool)
    mark_positive_guards(occupied, source_candidates, base)
    for start, end in existing_negative_intervals:
        if not 0 <= int(start) <= int(end) < len(occupied):
            raise HardValError(f"base negative interval is out of bounds: {source_path}")
        occupied[int(start) : int(end) + 1] = True

    pool = list(
        map(
            int,
            _negative_pool(
                features,
                kind="hard",
                seed_parts=(
                    base["protocol"],
                    contract["protocol"],
                    source_path,
                    "val",
                    "hard",
                ),
            ),
        )
    )
    available = [True] * len(pool)
    # Longer windows are more constrained.  Stable sample_id is the tie-breaker.
    ordered = sorted(
        templates,
        key=lambda row: (-_template_geometry(row)[0], str(row["sample_id"])),
    )
    selected: list[HardValPlan] = []
    missing: list[dict[str, Any]] = []
    split = base["split"]
    holdout = utc(contract["sources"]["holdout_start"])
    for template in ordered:
        window_len, confirmation = _template_geometry(template)
        found: HardValPlan | None = None
        for pool_index, pseudo_t in enumerate(pool):
            if not available[pool_index]:
                continue
            window_end = pseudo_t + confirmation - 3
            window_start = window_end - window_len + 1
            label_end = pseudo_t + 11
            if not _interval_is_contiguous(segments, window_start, label_end):
                continue
            if occupied[window_start : window_end + 1].any():
                continue
            assigned = split_for_interval(
                times.iloc[window_start],
                times.iloc[label_end],
                cutoff=split["cutoff"],
                purge_bars=int(split["purge_bars"]),
                bar_minutes=int(base["sources"]["bar_minutes"]),
            )
            if assigned != "val" or times.iloc[label_end] >= holdout:
                continue
            sample_id = hashlib.sha256(
                (
                    f"{contract['protocol']}|{source_path}|{template['sample_id']}|"
                    f"{pseudo_t}|{window_len}|{confirmation}"
                ).encode("utf-8")
            ).hexdigest()[:24]
            found = HardValPlan(
                sample_id=sample_id,
                template_positive_sample_id=str(template["sample_id"]),
                symbol=symbol,
                source_path=source_path,
                split="val",
                pseudo_t_i=pseudo_t,
                pseudo_t_time=times.iloc[pseudo_t].isoformat(),
                window_len=window_len,
                confirmation_bars=confirmation,
                window_start_i=window_start,
                window_end_i=window_end,
                label_future_end_i=label_end,
                bandwidth_pct=float(features["bandwidth_pct"][pseudo_t]),
                close_abs_atr=float(features["close_abs_atr"][pseudo_t]),
                two_sided_favorable_abs_atr=float(
                    features["two_sided_favorable_abs_atr"][pseudo_t]
                ),
            )
            available[pool_index] = False
            occupied[window_start : window_end + 1] = True
            break
        if found is None:
            missing.append(
                {
                    "template_positive_sample_id": str(template["sample_id"]),
                    "symbol": symbol,
                    "source_path": source_path,
                    "window_len": window_len,
                    "confirmation_bars": confirmation,
                    "hard_anchor_pool_rows": len(pool),
                    "reason": "safe_same_source_capacity_exhausted",
                }
            )
        else:
            selected.append(found)
    return selected, missing


def _encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise OSError("OpenCV failed to encode hard-val PNG")
    return encoded.tobytes()


def _write_sample(dataset: Path, plan: HardValPlan, image: np.ndarray) -> dict[str, Any]:
    stem = f"neg_hv_{plan.sample_id}"
    image_rel = Path("images/val") / f"{stem}.png"
    label_rel = Path("labels/val") / f"{stem}.txt"
    image_bytes = _encode_png(image)
    (dataset / image_rel).write_bytes(image_bytes)
    (dataset / label_rel).write_bytes(b"")
    row = asdict(plan)
    row.update(
        {
            "sample_kind": "negative_hard_val",
            "negative_kind": "hard",
            "class_id": None,
            "class_name": None,
            "image_path": image_rel.as_posix(),
            "label_path": label_rel.as_posix(),
            "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "label_sha256": EMPTY_SHA256,
            "input_latest_offset_from_pseudo_t": plan.confirmation_bars - 3,
            "negative_label_future_latest_offset_from_pseudo_t": 11,
            "training_eligible": False,
            "production_eligible": False,
        }
    )
    return row


def _contact_sheet(rows: Sequence[Mapping[str, Any]], dataset: Path, output: Path) -> None:
    selected = sorted(
        rows,
        key=lambda row: hashlib.sha256(str(row["sample_id"]).encode()).hexdigest(),
    )[:20]
    width, title_height, columns = 420, 32, 4
    cells: list[np.ndarray] = []
    for row in selected:
        image = cv2.imread(str(dataset / str(row["image_path"])), cv2.IMREAD_COLOR)
        if image is None:
            raise HardValError("hard-val preview image decode failed")
        scale = width / image.shape[1]
        resized = cv2.resize(
            image,
            (width, int(round(image.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        cell = np.full((title_height + resized.shape[0], width, 3), 255, dtype=np.uint8)
        label = (
            f"{row['symbol']} W{row['window_len']} C{row['confirmation_bars']} "
            f"bw={row['bandwidth_pct']:.3f}%"
        )
        cv2.putText(
            cell,
            label,
            (6, 21),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (35, 45, 55),
            1,
            cv2.LINE_AA,
        )
        cell[title_height:] = resized
        cells.append(cell)
    cell_height = max(cell.shape[0] for cell in cells)
    canvas = np.full(
        (math.ceil(len(cells) / columns) * cell_height, columns * width, 3),
        245,
        dtype=np.uint8,
    )
    for index, cell in enumerate(cells):
        row_i, col_i = divmod(index, columns)
        y, x = row_i * cell_height, col_i * width
        canvas[y : y + cell.shape[0], x : x + cell.shape[1]] = cell
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_encode_png(canvas))


GALLERY_STYLE = """
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#17212b}
body{margin:0;background:#f3f5f7}header{position:sticky;top:0;background:#fffffff2;padding:14px 20px;border-bottom:1px solid #d9e0e7;z-index:2}
h1{font-size:20px;margin:0 0 6px}p{margin:5px 0;color:#586777}nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}a{color:#0969da;text-decoration:none}
main{max-width:1800px;margin:auto;padding:16px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.card{background:white;border:1px solid #dde4eb;border-radius:10px;padding:10px}.card h2{font-size:14px;margin:0 0 7px}.card img{display:block;width:100%;height:auto}.meta{font-size:12px;line-height:1.45}.warn{color:#9a5b00}@media(max-width:1100px){.grid{grid-template-columns:1fr}header{position:static}}
"""


def _write_gallery(
    rows: Sequence[Mapping[str, Any]], *, dataset: Path, output_dir: Path, page_size: int
) -> list[Path]:
    gallery = output_dir / "gallery"
    gallery.mkdir(parents=True, exist_ok=True)
    page_count = math.ceil(len(rows) / page_size)
    pages: list[Path] = []
    links = " ".join(
        f'<a href="page_{number:03d}.html">{number}</a>'
        for number in range(1, page_count + 1)
    )
    for page_index in range(page_count):
        path = gallery / f"page_{page_index + 1:03d}.html"
        cards: list[str] = []
        for row in rows[page_index * page_size : (page_index + 1) * page_size]:
            source = Path(
                os.path.relpath(
                    (dataset / str(row["image_path"])).resolve(),
                    start=path.parent.resolve(),
                )
            ).as_posix()
            cards.append(
                f"""<article class="card"><h2>{html.escape(str(row['symbol']))} · {html.escape(str(row['pseudo_t_time']))}</h2>
<img loading="lazy" src="{html.escape(source)}" alt="exact hard-negative model input">
<p class="meta">W{int(row['window_len'])} · confirm {int(row['confirmation_bars'])} · input latest t+{int(row['input_latest_offset_from_pseudo_t'])}<br>
MA bandwidth {float(row['bandwidth_pct']):.4f}% · 12-bar close |move| {float(row['close_abs_atr']):.3f} ATR · max two-sided {float(row['two_sided_favorable_abs_atr']):.3f} ATR<br>
empty YOLO label · no box · sample {html.escape(str(row['sample_id']))}</p></article>"""
            )
        path.write_text(
            f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>15m hard-val {page_index + 1}/{page_count}</title><style>{GALLERY_STYLE}</style></head>
<body><header><h1>15m hard-negative val · {page_index + 1}/{page_count}</h1><p>每张都是模型实际输入 PNG；负样本没有框。未来 t..t+11 只用于 no-launch 标签判定，不在像素中。</p><nav>{links}</nav></header><main><section class="grid">{''.join(cards)}</section></main></body></html>""",
            encoding="utf-8",
        )
        pages.append(path)
    index = gallery / "index.html"
    index.write_text(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>15m hard-negative val</title><style>{GALLERY_STYLE}</style></head><body><main><section class="card"><h1>15m hard-negative val</h1><p>{len(rows):,} 张安全 hard-val，旧数据集未修改。所有图片均为 1280×742、W14–22、同一渲染器、空标签且无框。</p><p class="warn">这是 pre-holdout 诊断集，不是 Gold，不得用于调阈值、生产或 promote。</p><nav>{links}</nav></section></main></body></html>""",
        encoding="utf-8",
    )
    return [index, *pages]


def build_hard_val(
    prereg_path: Path = DEFAULT_PREREG,
    *,
    dataset_path: Path = DEFAULT_DATASET,
    results_path: Path = DEFAULT_RESULTS,
    page_size: int = 100,
) -> dict[str, Any]:
    """Materialize the immutable hard-negative validation sidecar."""

    if page_size <= 0:
        raise ValueError("page_size must be positive")
    contract, base = load_contract(prereg_path)
    manifest = load_base_manifest(contract)
    candidates = load_candidate_union(base)
    dataset = dataset_path.resolve()
    building = dataset.with_name(dataset.name + ".building")
    if dataset.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite hard-val dataset: {dataset}")
    if results_path.exists():
        raise FileExistsError(f"refusing to overwrite hard-val results: {results_path}")
    (building / "images/val").mkdir(parents=True)
    (building / "labels/val").mkdir(parents=True)

    by_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_templates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_old_negatives: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in candidates:
        by_candidates[str(row["source_path"])].append(dict(row))
    for row in manifest:
        source = str(row["source_path"])
        if row["sample_kind"] == "positive_weak" and row["split"] == "val":
            by_templates[source].append(row)
        elif str(row["sample_kind"]).startswith("negative_"):
            by_old_negatives[source].append(
                (int(row["window_start_i"]), int(row["window_end_i"]))
            )

    plans: list[HardValPlan] = []
    missing: list[dict[str, Any]] = []
    source_audits: list[dict[str, Any]] = []
    sources = sorted(by_templates)
    for source_number, source_path in enumerate(sources, 1):
        frame, audit = read_preholdout_prefix(
            _repo_path(source_path),
            end_exclusive=utc(contract["sources"]["holdout_start"]),
        )
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise HardValError("holdout OHLCV row materialized")
        audit.update({"source_path": source_path, "symbol": by_templates[source_path][0]["symbol"]})
        source_audits.append(audit)
        selected, absent = select_source_hard_val(
            add_six_mas(frame),
            source_path=source_path,
            symbol=str(by_templates[source_path][0]["symbol"]),
            source_candidates=by_candidates[source_path],
            templates=by_templates[source_path],
            existing_negative_intervals=by_old_negatives[source_path],
            contract=contract,
            base=base,
        )
        plans.extend(selected)
        missing.extend(absent)
        if source_number == 1 or source_number % 25 == 0 or source_number == len(sources):
            print(
                f"[hard-val] source {source_number}/{len(sources)} "
                f"selected={len(plans)} missing={len(missing)}",
                flush=True,
            )

    target = int(contract["sources"]["base_dataset"]["val_positive_rows"])
    minimum = int(contract["hard_validation_contract"]["minimum_acceptable_rows"])
    if len(plans) < minimum or len(plans) + len(missing) != target:
        raise HardValError(
            f"hard-val capacity outside preregistered bounds: {len(plans)} + "
            f"{len(missing)} != {target}, minimum={minimum}"
        )

    rows: list[dict[str, Any]] = []
    by_source_plan: dict[str, list[HardValPlan]] = defaultdict(list)
    for plan in plans:
        by_source_plan[plan.source_path].append(plan)
    for source_number, source_path in enumerate(sorted(by_source_plan), 1):
        frame, audit = read_preholdout_prefix(
            _repo_path(source_path),
            end_exclusive=utc(contract["sources"]["holdout_start"]),
        )
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise HardValError("holdout OHLCV row materialized during render")
        enriched = add_six_mas(frame)
        for plan in by_source_plan[source_path]:
            window = enriched.iloc[plan.window_start_i : plan.window_end_i + 1].reset_index(drop=True)
            image, _ = render_chart(window, out_path=None)
            rows.append(_write_sample(building, plan, image))
        if source_number == 1 or source_number % 25 == 0 or source_number == len(by_source_plan):
            print(
                f"[hard-val] render source {source_number}/{len(by_source_plan)} rows={len(rows)}",
                flush=True,
            )

    rows.sort(key=lambda row: str(row["sample_id"]))
    _write_jsonl(building / "manifest.jsonl", rows)
    _write_jsonl(building / "missing_capacity.jsonl", missing)
    _write_jsonl(building / "source_audit.jsonl", source_audits)
    (building / "data.yaml").write_text(
        f"path: {dataset}\nval: images/val\nnames:\n  0: dense_long\n  1: dense_short\n",
        encoding="utf-8",
    )
    dataset_summary = {
        "experiment_id": EXPERIMENT_ID,
        "protocol": contract["protocol"],
        "target_rows": target,
        "materialized_rows": len(rows),
        "missing_rows": len(missing),
        "missing_by_symbol": dict(sorted(Counter(row["symbol"] for row in missing).items())),
        "window_lengths": dict(
            sorted(Counter(str(row["window_len"]) for row in rows).items())
        ),
        "confirmation_bars": dict(
            sorted(Counter(str(row["confirmation_bars"]) for row in rows).items())
        ),
        "holdout_ohlcv_rows_materialized": 0,
        "base_dataset_files_changed": 0,
        "models_trained": 0,
        "training_eligible": False,
        "production_eligible": False,
    }
    _write_json(building / "build_summary.json", dataset_summary)
    building.rename(dataset)

    results_path.mkdir(parents=True)
    preview = results_path / "hard_val_contact_sheet.png"
    _contact_sheet(rows, dataset, preview)
    gallery = _write_gallery(rows, dataset=dataset, output_dir=results_path, page_size=page_size)
    receipt = {
        **dataset_summary,
        "dataset_path": _repo_relative(dataset),
        "manifest_path": _repo_relative(dataset / "manifest.jsonl"),
        "manifest_sha256": sha256_file(dataset / "manifest.jsonl"),
        "missing_capacity_path": _repo_relative(dataset / "missing_capacity.jsonl"),
        "missing_capacity_sha256": sha256_file(dataset / "missing_capacity.jsonl"),
        "source_audit_sha256": sha256_file(dataset / "source_audit.jsonl"),
        "data_yaml_sha256": sha256_file(dataset / "data.yaml"),
        "preview_path": _repo_relative(preview),
        "preview_sha256": sha256_file(preview),
        "gallery_index": _repo_relative(gallery[0]),
        "gallery_pages": len(gallery) - 1,
        "future_rows_rendered_into_model_input": 0,
        "boxes_or_markers_rendered_into_model_input": 0,
        "passed": True,
    }
    _write_json(results_path / "build_receipt.json", receipt)
    return receipt

