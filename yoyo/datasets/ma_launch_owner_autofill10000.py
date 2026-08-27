"""Build 10,000 strict pre-holdout 15m MA-launch shape examples.

The Owner accepted the prior fifty-example v7 delivery as a family.  This
module freezes those fifty profiles as nearest-neighbour references, scans the
hash-pinned existing OKX sources plus official pre-holdout monthly archives,
collapses alternate 4/5-bar boxes at the same endpoint, applies a one-hour
same-symbol/side event NMS, and renders 5,000 LONG plus 5,000 SHORT examples.

The five bars after each core are descriptive completed-history retrieval
inputs.  They are not a causal detector, live signal or training label.  This
builder writes no YOLO labels, model, ACTIVE pointer, forward state or order
state, and it materializes zero OHLCV rows at or after the repository holdout.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import (
    add_candidate_features,
    read_preholdout_prefix,
    sha256_file,
)
from yoyo.datasets.ma_launch_owner_autofill_review import (
    FEATURE_NAMES,
    Profile,
    frame_arrays,
    morphology_profile,
    passes_gate,
    profile_distance,
)
from yoyo.datasets.ma_launch_owner_recrop_review import (
    HOLDOUT_START,
    RED,
    ROOT,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    core_box,
    draw_box,
    encode_png,
    verify_builder_committed,
)
from yoyo.layers.l1_detection.render import render_chart


EXPERIMENT_ID = "exp-15m-ma-launch-owner-autofill10000-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUTPUT = DEFAULT_PREREG.parent / "results"


class OwnerAutofill10000Error(ValueError):
    """Raised when source, morphology, selection or render contracts drift."""


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _repo_path(value: object) -> Path:
    path = Path(str(value))
    path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise OwnerAutofill10000Error(f"path escapes repository: {value}") from exc
    return path


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _stable_id(*parts: object, length: int = 24) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def load_reference_profiles(
    prereg: Mapping[str, Any],
) -> tuple[list[Profile], list[dict[str, Any]]]:
    """Recompute the fifty accepted profiles from hash-pinned source geometry."""

    predecessor = prereg["predecessor"]
    predecessor_prereg = _repo_path(predecessor["preregistration_path"])
    reference_manifest = _repo_path(predecessor["reference_manifest_path"])
    if sha256_file(predecessor_prereg) != str(predecessor["preregistration_sha256"]):
        raise OwnerAutofill10000Error("predecessor preregistration SHA drift")
    if sha256_file(reference_manifest) != str(predecessor["reference_manifest_sha256"]):
        raise OwnerAutofill10000Error("accepted reference manifest SHA drift")
    rows = read_jsonl(reference_manifest)
    if len(rows) != int(predecessor["reference_rows"]):
        raise OwnerAutofill10000Error("accepted reference row count drift")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_path"])].append(row)

    profiles: list[Profile] = []
    audits: list[dict[str, Any]] = []
    for source_path, source_rows in sorted(grouped.items()):
        frame, audit = read_preholdout_prefix(
            _repo_path(source_path), end_exclusive=HOLDOUT_START
        )
        audit["source_path"] = source_path
        audits.append(audit)
        arrays = frame_arrays(add_candidate_features(frame))
        for row in source_rows:
            profile = morphology_profile(
                arrays,
                anchor_i=int(row["source_anchor_i"]),
                direction=str(row["direction"]),
                core_start_offset=int(row["core_start_offset"]),
                core_end_offset=int(row["core_end_offset"]),
            )
            if profile is None:
                raise OwnerAutofill10000Error(
                    f"accepted reference profile could not be recomputed: {row['sample_id']}"
                )
            profiles.append(profile)
    if len(profiles) != int(predecessor["reference_rows"]):
        raise OwnerAutofill10000Error("accepted reference profile count drift")
    if sum(int(row["holdout_ohlcv_rows_materialized"]) for row in audits) != 0:
        raise AssertionError("reference loading materialized holdout OHLCV")
    return profiles, audits


def load_source_specs(prereg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Resolve hash-pinned existing sources and completed archive outputs."""

    sources = prereg["sources"]
    existing_audit_path = _repo_path(sources["existing_source_audit_path"])
    if sha256_file(existing_audit_path) != str(sources["existing_source_audit_sha256"]):
        raise OwnerAutofill10000Error("existing source audit SHA drift")
    existing_rows = read_json(existing_audit_path)
    existing = [row for row in existing_rows if int(row["rows_materialized"]) > 0]
    if len(existing) != int(sources["existing_materialized_symbols"]):
        raise OwnerAutofill10000Error("existing materialized symbol count drift")
    if sum(int(row["rows_materialized"]) for row in existing) != int(
        sources["existing_preholdout_rows"]
    ):
        raise OwnerAutofill10000Error("existing pre-holdout row count drift")

    specs: list[dict[str, Any]] = []
    for row in existing:
        specs.append(
            {
                "kind": "existing",
                "symbol": str(row["symbol"]),
                "source_path": str(row["source_path"]),
                "expected_prefix_sha256": str(row["bounded_prefix_sha256"]),
                "expected_rows": int(row["rows_materialized"]),
            }
        )

    archive_contract = sources["official_archive"]
    archive_summary_path = _repo_path(archive_contract["summary_path"])
    if not archive_summary_path.exists():
        raise OwnerAutofill10000Error(
            f"official archive fetch summary is missing: {archive_summary_path}"
        )
    archive_summary = read_json(archive_summary_path)
    if int(archive_summary.get("holdout_ohlcv_rows_materialized", -1)) != 0:
        raise OwnerAutofill10000Error("archive summary does not prove zero holdout rows")
    for row in archive_summary["results"]:
        if row.get("status") != "complete":
            continue
        contract = row["contract"]
        if str(contract["max_exclusive"]) != str(
            pd.Timestamp(archive_contract["archive_max_exclusive"]).isoformat()
        ):
            raise OwnerAutofill10000Error("archive max-exclusive contract drift")
        path = _repo_path(row["output_path"])
        if sha256_file(path) != str(row["output_sha256"]):
            raise OwnerAutofill10000Error(f"archive output SHA drift: {path}")
        specs.append(
            {
                "kind": "official_archive_1m_aggregated_15m",
                "symbol": str(contract["symbol"]),
                "source_path": _relative(path),
                "expected_file_sha256": str(row["output_sha256"]),
                "expected_rows": int(row["rows"]),
            }
        )
    paths = [str(row["source_path"]) for row in specs]
    if len(paths) != len(set(paths)):
        raise OwnerAutofill10000Error("duplicate resolved source path")
    return sorted(specs, key=lambda row: (str(row["symbol"]), str(row["kind"])))


def scan_source(
    frame: pd.DataFrame,
    *,
    source_path: str,
    symbol: str,
    prereg: Mapping[str, Any],
    references: Sequence[Profile],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Scan every canonical core end using vectorized continuation prefilters."""

    gates = prereg["morphology_gate"]
    similarity = prereg["reference_family"]
    feature_scales = np.asarray(similarity["feature_scales"], dtype=float)
    enriched = add_candidate_features(frame)
    arrays = frame_arrays(enriched)
    segment = enriched["_segment_id"].to_numpy(dtype=int)
    atr = arrays["atr"]
    n = len(enriched)
    best: dict[tuple[str, int], dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    max_pre = max(int(value) for value in prereg["render"]["pre_core_context_bars"])
    max_post = max(int(value) for value in prereg["render"]["post_core_context_bars"])

    for core_len in (int(value) for value in gates["core_lengths"]):
        end = np.arange(core_len - 1 + max_pre, n - max(5, max_post), dtype=int)
        if not len(end):
            continue
        start = end - core_len + 1
        anchor = end + 2
        valid = (
            (segment[start - max_pre] == segment[end + max_post])
            & np.isfinite(atr[anchor])
            & (atr[anchor] > 0.0)
        )
        for direction, sign in (("LONG", 1.0), ("SHORT", -1.0)):
            post1 = sign * (arrays["close"][end + 1] - arrays["close"][end]) / atr[anchor]
            post2 = sign * (arrays["close"][end + 2] - arrays["close"][end]) / atr[anchor]
            post3 = sign * (arrays["close"][end + 3] - arrays["close"][end]) / atr[anchor]
            post5 = sign * (arrays["close"][end + 5] - arrays["close"][end]) / atr[anchor]
            coarse = (
                valid
                & (post1 >= float(gates["min_post1_progress_atr"]))
                & (post2 >= float(gates["min_post2_progress_atr"]))
                & (post3 >= float(gates["min_post3_progress_atr"]))
                & (post5 >= float(gates["min_post5_progress_atr"]))
            )
            counts[f"{direction.lower()}_coarse"] += int(coarse.sum())
            for end_i in end[coarse]:
                end_i = int(end_i)
                profile = morphology_profile(
                    arrays,
                    anchor_i=end_i + 2,
                    direction=direction,
                    core_start_offset=-core_len - 1,
                    core_end_offset=-2,
                )
                if profile is None or not passes_gate(profile, gates):
                    continue
                counts[f"{direction.lower()}_gate"] += 1
                distance = profile_distance(
                    profile,
                    references,
                    feature_scales=feature_scales,
                    feature_weight=float(similarity["feature_weight"]),
                    sequence_weight=float(similarity["sequence_weight"]),
                )
                if distance > float(similarity["max_distance"]):
                    continue
                counts[f"{direction.lower()}_distance"] += 1
                start_i = end_i - core_len + 1
                core_start_time = pd.Timestamp(enriched["open_time"].iloc[start_i]).isoformat()
                core_end_time = pd.Timestamp(enriched["open_time"].iloc[end_i]).isoformat()
                candidate = {
                    "event_id": _stable_id(
                        EXPERIMENT_ID, symbol, direction, core_start_time, core_end_time, length=20
                    ),
                    "symbol": symbol,
                    "direction": direction,
                    "source_path": source_path,
                    "source_core_start_i": start_i,
                    "source_core_end_i": end_i,
                    "source_comparison_anchor_i": end_i + 2,
                    "core_start_time": core_start_time,
                    "core_end_time": core_end_time,
                    "core_bars": core_len,
                    "core_start_offset": -core_len - 1,
                    "core_end_offset": -2,
                    "similarity_distance": float(distance),
                    "features": dict(
                        zip(FEATURE_NAMES, (float(value) for value in profile.features))
                    ),
                    "training_eligible": False,
                    "production_eligible": False,
                }
                key = (direction, end_i)
                prior = best.get(key)
                if prior is None or (
                    candidate["similarity_distance"], candidate["core_bars"]
                ) < (prior["similarity_distance"], prior["core_bars"]):
                    best[key] = candidate
    counts["unique_core_ends"] = len(best)
    return list(best.values()), dict(counts)


def event_nms(
    candidates: Sequence[Mapping[str, Any]], *, gap_bars: int
) -> list[dict[str, Any]]:
    """Collapse each fixed-width same-symbol/side cluster to its best endpoint."""

    if gap_bars <= 0:
        raise OwnerAutofill10000Error("event NMS gap must be positive")
    gap = pd.Timedelta(minutes=15 * gap_bars)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[(str(row["symbol"]), str(row["direction"]))].append(dict(row))
    output: list[dict[str, Any]] = []
    for _, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: pd.Timestamp(row["core_end_time"]))
        clusters: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for row in ordered:
            stamp = pd.Timestamp(row["core_end_time"])
            if current and stamp - pd.Timestamp(current[0]["core_end_time"]) >= gap:
                clusters.append(current)
                current = []
            current.append(row)
        if current:
            clusters.append(current)
        for cluster in clusters:
            output.append(
                min(
                    cluster,
                    key=lambda row: (
                        float(row["similarity_distance"]),
                        pd.Timestamp(row["core_end_time"]),
                        int(row["core_bars"]),
                    ),
                )
            )
    identities = [str(row["event_id"]) for row in output]
    if len(identities) != len(set(identities)):
        raise OwnerAutofill10000Error("event NMS emitted duplicate identities")
    return output


def select_balanced(
    candidates: Sequence[Mapping[str, Any]], selection: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Select balanced chronological quantiles under symbol/day/hour quotas."""

    target = int(selection["target_per_side"])
    bins_count = int(selection["time_bins_per_side"])
    target_per_bin = int(selection["target_per_time_bin"])
    if target != bins_count * target_per_bin:
        raise OwnerAutofill10000Error("selection time-bin arithmetic drift")
    max_symbol = int(selection["max_per_symbol_per_side"])
    max_day = int(selection["max_per_utc_day_per_side"])
    max_hour = int(selection["max_per_utc_hour_per_side"])
    selected: list[dict[str, Any]] = []

    for direction in ("LONG", "SHORT"):
        rows = [dict(row) for row in candidates if str(row["direction"]) == direction]
        if len(rows) < target:
            raise OwnerAutofill10000Error(
                f"strict {direction} pool has {len(rows)}, needs {target}"
            )
        unique_hours = sorted(
            {pd.Timestamp(row["core_end_time"]).floor("h") for row in rows}
        )
        if len(unique_hours) < target:
            raise OwnerAutofill10000Error(
                f"strict {direction} pool has only {len(unique_hours)} unique hours"
            )
        hour_bins = {
            hour: min(bins_count - 1, rank * bins_count // len(unique_hours))
            for rank, hour in enumerate(unique_hours)
        }
        buckets: list[list[dict[str, Any]]] = [[] for _ in range(bins_count)]
        for row in rows:
            stamp = pd.Timestamp(row["core_end_time"])
            bin_index = hour_bins[stamp.floor("h")]
            row["time_bin"] = bin_index
            buckets[bin_index].append(row)
        for bucket in buckets:
            bucket.sort(
                key=lambda row: (
                    float(row["similarity_distance"]),
                    str(row["event_id"]),
                )
            )

        positions = [0] * bins_count
        bin_counts = [0] * bins_count
        symbol_counts: Counter[str] = Counter()
        day_counts: Counter[str] = Counter()
        hour_counts: Counter[str] = Counter()
        side_selected: list[dict[str, Any]] = []
        while len(side_selected) < target:
            progress = False
            for bin_index, bucket in enumerate(buckets):
                if bin_counts[bin_index] >= target_per_bin:
                    continue
                while positions[bin_index] < len(bucket):
                    row = bucket[positions[bin_index]]
                    positions[bin_index] += 1
                    stamp = pd.Timestamp(row["core_end_time"])
                    symbol = str(row["symbol"])
                    day = stamp.strftime("%Y-%m-%d")
                    hour = stamp.floor("h").isoformat()
                    if (
                        symbol_counts[symbol] >= max_symbol
                        or day_counts[day] >= max_day
                        or hour_counts[hour] >= max_hour
                    ):
                        continue
                    side_selected.append(row)
                    bin_counts[bin_index] += 1
                    symbol_counts[symbol] += 1
                    day_counts[day] += 1
                    hour_counts[hour] += 1
                    progress = True
                    break
            if not progress:
                raise OwnerAutofill10000Error(
                    f"could not fill strict {direction} quotas; bins={bin_counts}"
                )
        if bin_counts != [target_per_bin] * bins_count:
            raise OwnerAutofill10000Error(f"{direction} time-bin selection drift")
        selected.extend(side_selected)

    if len(selected) != int(selection["total"]):
        raise OwnerAutofill10000Error("balanced selection total drift")
    return sorted(
        selected,
        key=lambda row: (
            pd.Timestamp(row["core_end_time"]),
            str(row["direction"]),
            str(row["event_id"]),
        ),
    )


def _context(sample_id: str, values: Sequence[int], salt: str) -> int:
    digest = hashlib.sha256(f"{EXPERIMENT_ID}|{salt}|{sample_id}".encode()).digest()
    return int(values[int.from_bytes(digest[:8], "big") % len(values)])


def _final_relative(path_in_building: Path, building: Path, final_dir: Path) -> str:
    return _relative(final_dir / path_in_building.relative_to(building))


def _page_html(rows: Sequence[Mapping[str, Any]], *, page: int, pages: int) -> str:
    cards = []
    for row in rows:
        image_name = Path(str(row["image_path"])).name
        cards.append(
            "<article><h2>"
            + f"{int(row['source_order']):05d} · {html.escape(str(row['symbol']))} · "
            + f"{html.escape(str(row['direction']))}</h2><p>"
            + f"{html.escape(str(row['core_end_time']))} · {int(row['core_bars'])}根 · "
            + f"distance {float(row['similarity_distance']):.3f}</p>"
            + f"<img loading='lazy' src='../images/{html.escape(image_name)}'>"
            + "</article>"
        )
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>严格形态 10,000 · {page}/{pages}</title><style>
body{{margin:0;background:#eef2f5;color:#18222c;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}header,main{{max-width:1500px;margin:auto;padding:18px}}header{{position:sticky;top:0;z-index:2;background:#fff7df;border-bottom:1px solid #d9c179}}main{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}article{{background:white;padding:12px;border-radius:12px;box-shadow:0 2px 10px #1b304018}}h1{{margin:0 0 8px}}h2{{font-size:17px;margin:0 0 5px}}p{{font-size:13px;color:#5c6872}}img{{display:block;width:100%;height:auto;border:1px solid #d5dde4}}a{{color:#075b9a}}@media(max-width:820px){{main{{grid-template-columns:1fr}}}}
</style></head><body><header><h1>15m 严格自动形态 · 第 {page}/{pages} 页</h1><p><a href='../index.html'>返回索引</a> · 本页 {len(rows)} 张；每张恰好一个 4–5 根红框。</p></header><main>{''.join(cards)}</main></body></html>"""


def _index_html(
    rows: Sequence[Mapping[str, Any]],
    *,
    page_size: int,
    manifest_sha: str,
    summary: Mapping[str, Any],
) -> str:
    pages = math.ceil(len(rows) / page_size)
    links = []
    for page in range(1, pages + 1):
        start = (page - 1) * page_size + 1
        end = min(page * page_size, len(rows))
        links.append(
            f"<a href='pages/page_{page:03d}.html'>第 {page:03d} 页<br><small>{start:05d}–{end:05d}</small></a>"
        )
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>15m 严格自动形态 10,000</title><style>
body{{margin:0;background:#eef2f5;color:#17222c;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}header,main{{max-width:1400px;margin:auto;padding:22px}}header{{background:#fff7df;border-bottom:1px solid #d9c179}}h1{{margin:0 0 10px}}.facts{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}}.fact{{background:white;border-radius:10px;padding:12px}}.pages{{display:grid;grid-template-columns:repeat(8,1fr);gap:9px}}.pages a{{background:white;padding:12px;text-align:center;border-radius:9px;color:#075b9a;text-decoration:none;box-shadow:0 1px 5px #20304018}}small{{color:#697681}}img{{max-width:100%;border:1px solid #ccd5dd}}@media(max-width:900px){{.facts{{grid-template-columns:1fr 1fr}}.pages{{grid-template-columns:repeat(3,1fr)}}}}
</style></head><body><header><h1>15m 严格自动形态 10,000 张</h1><p>5,000 LONG + 5,000 SHORT；每张 1280×742 PNG，恰好一个 4–5 根红框。内部自动淘汰和补位，不创建人工审核任务。本批仍是 P0 形态示例，不是训练标签。</p><div class='facts'><div class='fact'>严格池（NMS 后）<br><b>{int(summary['n_after_event_nms']):,}</b></div><div class='fact'>中位距离<br><b>{float(summary['similarity_distance_median']):.3f}</b></div><div class='fact'>币种数<br><b>{int(summary['unique_symbols'])}</b></div><div class='fact'>holdout OHLCV<br><b>0</b></div></div><p>manifest SHA: <code>{manifest_sha}</code></p></header><main><h2>全量 100 页</h2><div class='pages'>{''.join(links)}</div><h2>等距抽样 100 张总览</h2><img src='../contact_sheet_sample100.jpg'></main></body></html>"""


def _sample_contact_sheet(rows: Sequence[Mapping[str, Any]], final_dir: Path) -> np.ndarray:
    indices = np.linspace(0, len(rows) - 1, 100, dtype=int)
    tile_w, tile_h, columns = 256, 180, 10
    canvas = np.full((10 * tile_h, columns * tile_w, 3), 245, dtype=np.uint8)
    for slot, index in enumerate(indices):
        row = rows[int(index)]
        image_path = ROOT / str(row["image_path"])
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            # During .building the final path does not exist yet.
            image = cv2.imread(
                str(final_dir.with_name(f"{final_dir.name}.building") / "public" / "images" / image_path.name),
                cv2.IMREAD_COLOR,
            )
        if image is None:
            raise OwnerAutofill10000Error(f"contact sheet image is unreadable: {image_path}")
        preview = cv2.resize(image, (tile_w, tile_h - 24), interpolation=cv2.INTER_AREA)
        y, x = (slot // columns) * tile_h, (slot % columns) * tile_w
        canvas[y + 24 : y + tile_h, x : x + tile_w] = preview
        cv2.putText(
            canvas,
            f"{int(row['source_order']):05d} {row['symbol']} {row['direction']}",
            (x + 4, y + 17),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.34,
            (35, 42, 48),
            1,
            cv2.LINE_AA,
        )
    return canvas


def _sign_test_p_string(successes: int, total: int) -> str:
    """Return an exact two-sided sign-test p-value without SciPy."""

    from decimal import Decimal, localcontext

    if not 0 <= successes <= total or total <= 0:
        raise OwnerAutofill10000Error("invalid sign-test counts")
    tail_start = max(successes, total - successes)
    with localcontext() as context:
        context.prec = 50
        numerator = sum(Decimal(math.comb(total, value)) for value in range(tail_start, total + 1))
        p_value = min(Decimal(1), Decimal(2) * numerator / (Decimal(2) ** total))
        return f"{p_value:.8E}"


def _validate_prereg(prereg: Mapping[str, Any]) -> None:
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise OwnerAutofill10000Error("experiment ID drift")
    if pd.Timestamp(prereg["sources"]["holdout_start_exclusive"]) != HOLDOUT_START:
        raise OwnerAutofill10000Error("holdout boundary drift")
    if int(prereg["sources"]["holdout_ohlcv_rows_allowed"]) != 0:
        raise OwnerAutofill10000Error("holdout allowance must be zero")
    safety = prereg["safety"]
    forbidden_true = (
        "write_yolo_labels",
        "start_training",
        "manual_owner_review_workflow",
        "training_eligible",
        "production_eligible",
        "holdout_read",
        "active_or_frozen_change",
        "forward_or_order_state_change",
    )
    if any(safety.get(field) is not False for field in forbidden_true):
        raise OwnerAutofill10000Error("one or more safety switches are not false")
    render = prereg["render"]
    if int(render["width"]) != SOURCE_WIDTH or int(render["height"]) != SOURCE_HEIGHT:
        raise OwnerAutofill10000Error("render dimensions drift")
    if int(render["boxes_per_image"]) != 1 or list(render["box_core_bars"]) != [4, 5]:
        raise OwnerAutofill10000Error("render box contract drift")


def build(
    prereg_path: Path = DEFAULT_PREREG,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Scan, select, resumably render and publish the 10,000-example pack."""

    prereg_path = prereg_path.resolve()
    prereg = read_json(prereg_path)
    _validate_prereg(prereg)
    final_dir = output_dir.resolve() if output_dir else DEFAULT_OUTPUT
    building = final_dir.with_name(f"{final_dir.name}.building")
    if final_dir.exists():
        raise FileExistsError(f"refusing to overwrite completed output: {final_dir}")
    builder_commit = verify_builder_committed(
        [
            Path(__file__),
            ROOT / "scripts" / "build_15m_ma_launch_owner_autofill10000.py",
            ROOT / "src" / "data" / "fetch_okx.py",
            prereg_path,
        ]
    )
    prereg_sha = sha256_file(prereg_path)
    references, reference_audits = load_reference_profiles(prereg)
    source_specs = load_source_specs(prereg)

    state_path = building / "build_state.json"
    selection_path = building / "selection_manifest.jsonl"
    source_audit_path = building / "source_audit.jsonl"
    scan_receipt_path = building / "scan_receipt.json"
    if building.exists():
        if not all(path.exists() for path in (state_path, selection_path, source_audit_path, scan_receipt_path)):
            raise OwnerAutofill10000Error("incomplete building directory cannot be resumed")
        state = read_json(state_path)
        if state.get("preregistration_sha256") != prereg_sha:
            raise OwnerAutofill10000Error("resume preregistration SHA drift")
        selected = read_jsonl(selection_path)
        source_audits = read_jsonl(source_audit_path)
        scan_receipt = read_json(scan_receipt_path)
        print(f"resume scan selection: {len(selected)} rows", flush=True)
    else:
        all_candidates: list[dict[str, Any]] = []
        source_audits: list[dict[str, Any]] = []
        scan_counts: Counter[str] = Counter()
        total = len(source_specs)
        for index, spec in enumerate(source_specs, 1):
            source_path = _repo_path(spec["source_path"])
            frame, audit = read_preholdout_prefix(source_path, end_exclusive=HOLDOUT_START)
            audit.update(
                {
                    "source_path": str(spec["source_path"]),
                    "symbol": str(spec["symbol"]),
                    "kind": str(spec["kind"]),
                }
            )
            if int(audit["rows_materialized"]) != int(spec["expected_rows"]):
                raise OwnerAutofill10000Error(f"source row count drift: {source_path}")
            if spec["kind"] == "existing" and str(audit["bounded_prefix_sha256"]) != str(
                spec["expected_prefix_sha256"]
            ):
                raise OwnerAutofill10000Error(f"existing prefix SHA drift: {source_path}")
            rows, counts = scan_source(
                frame,
                source_path=str(spec["source_path"]),
                symbol=str(spec["symbol"]),
                prereg=prereg,
                references=references,
            )
            all_candidates.extend(rows)
            source_audits.append(audit)
            scan_counts.update(counts)
            scan_counts["source_rows_materialized"] += len(frame)
            scan_counts["holdout_ohlcv_rows_materialized"] += int(
                audit["holdout_ohlcv_rows_materialized"]
            )
            if index == 1 or index % 10 == 0 or index == total:
                print(
                    f"strict scan {index:03d}/{total} {spec['symbol']:<22} "
                    f"source_rows={len(frame):>7} strict_unique={len(all_candidates):>6}",
                    flush=True,
                )
        if scan_counts["holdout_ohlcv_rows_materialized"] != 0:
            raise AssertionError("strict scan materialized holdout OHLCV")
        nms_rows = event_nms(
            all_candidates,
            gap_bars=int(prereg["scan"]["same_symbol_direction_nms_bars"]),
        )
        selected = select_balanced(nms_rows, prereg["selection"])
        for source_order, row in enumerate(selected, 1):
            row["source_order"] = source_order
            row["sample_id"] = _stable_id(
                EXPERIMENT_ID,
                row["event_id"],
                row["core_bars"],
                length=24,
            )
        building.mkdir(parents=True)
        (building / "public" / "images").mkdir(parents=True)
        (building / "public" / "pages").mkdir(parents=True)
        scan_receipt = {
            "source_specs": len(source_specs),
            "existing_sources": sum(row["kind"] == "existing" for row in source_specs),
            "official_archive_sources": sum(
                row["kind"] == "official_archive_1m_aggregated_15m"
                for row in source_specs
            ),
            "source_rows_materialized": int(scan_counts["source_rows_materialized"]),
            "holdout_ohlcv_rows_materialized": 0,
            "profile_gate_counts": dict(scan_counts),
            "n_before_event_nms": len(all_candidates),
            "n_after_event_nms": len(nms_rows),
            "direction_before_event_nms": dict(
                Counter(str(row["direction"]) for row in all_candidates)
            ),
            "direction_after_event_nms": dict(
                Counter(str(row["direction"]) for row in nms_rows)
            ),
        }
        write_jsonl(selection_path, selected)
        write_jsonl(source_audit_path, source_audits)
        write_json(scan_receipt_path, scan_receipt)
        write_json(
            state_path,
            {
                "experiment_id": EXPERIMENT_ID,
                "preregistration_sha256": prereg_sha,
                "builder_commit": builder_commit,
                "phase": "selected",
            },
        )

    if len(selected) != int(prereg["selection"]["total"]):
        raise OwnerAutofill10000Error("resume selection row count drift")
    partial_manifest = building / "render_manifest.partial.jsonl"
    rendered_rows = read_jsonl(partial_manifest) if partial_manifest.exists() else []
    rendered_by_id = {str(row["sample_id"]): row for row in rendered_rows}
    if len(rendered_by_id) != len(rendered_rows):
        raise OwnerAutofill10000Error("partial render manifest has duplicate sample IDs")
    for row in rendered_rows:
        image_path = _repo_path(row["image_path"])
        building_image = building / "public" / "images" / image_path.name
        if not building_image.exists() or sha256_file(building_image) != str(row["image_sha256"]):
            raise OwnerAutofill10000Error(f"partial render image drift: {row['sample_id']}")

    grouped_selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        if str(row["sample_id"]) not in rendered_by_id:
            grouped_selected[str(row["source_path"])].append(row)
    image_dir = building / "public" / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    similarity = prereg["reference_family"]
    feature_scales = np.asarray(similarity["feature_scales"], dtype=float)
    with partial_manifest.open("a", encoding="utf-8") as partial_handle:
        rendered_count = len(rendered_rows)
        for source_number, (source_key, rows) in enumerate(sorted(grouped_selected.items()), 1):
            frame, audit = read_preholdout_prefix(
                _repo_path(source_key), end_exclusive=HOLDOUT_START
            )
            if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
                raise AssertionError("render reload materialized holdout OHLCV")
            enriched = add_candidate_features(frame)
            arrays = frame_arrays(enriched)
            for row in sorted(rows, key=lambda value: int(value["source_order"])):
                sample_id = str(row["sample_id"])
                core_start_i = int(row["source_core_start_i"])
                core_end_i = int(row["source_core_end_i"])
                pre_bars = _context(
                    sample_id,
                    [int(value) for value in prereg["render"]["pre_core_context_bars"]],
                    "pre",
                )
                post_bars = _context(
                    sample_id,
                    [int(value) for value in prereg["render"]["post_core_context_bars"]],
                    "post",
                )
                window_start_i = core_start_i - pre_bars
                window_end_i = core_end_i + post_bars
                window = enriched.iloc[window_start_i : window_end_i + 1].reset_index(drop=True)
                expected_window = pre_bars + int(row["core_bars"]) + post_bars
                if len(window) != expected_window:
                    raise OwnerAutofill10000Error(f"incomplete render window: {sample_id}")
                if pd.Timestamp(window["open_time"].iloc[-1]) >= HOLDOUT_START:
                    raise OwnerAutofill10000Error(f"render touches holdout: {sample_id}")
                clean, transform = render_chart(
                    window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, out_path=None
                )
                box = core_box(
                    transform,
                    window,
                    start_local=pre_bars,
                    end_local=pre_bars + int(row["core_bars"]) - 1,
                )
                overlay = clean.copy()
                draw_box(overlay, box)
                exact_red_pixels = int(np.all(overlay == np.asarray(RED), axis=2).sum())
                if exact_red_pixels < 100:
                    raise OwnerAutofill10000Error(f"red rectangle missing: {sample_id}")
                filename = (
                    f"{int(row['source_order']):05d}_{row['symbol']}_{row['direction']}_{sample_id}.png"
                )
                image_path = image_dir / filename
                temporary = image_path.with_suffix(".png.part")
                temporary.write_bytes(encode_png(overlay))
                os.replace(temporary, image_path)

                correct_profile = morphology_profile(
                    arrays,
                    anchor_i=int(row["source_comparison_anchor_i"]),
                    direction=str(row["direction"]),
                    core_start_offset=int(row["core_start_offset"]),
                    core_end_offset=int(row["core_end_offset"]),
                )
                wrong_direction = "SHORT" if str(row["direction"]) == "LONG" else "LONG"
                wrong_profile = morphology_profile(
                    arrays,
                    anchor_i=int(row["source_comparison_anchor_i"]),
                    direction=wrong_direction,
                    core_start_offset=int(row["core_start_offset"]),
                    core_end_offset=int(row["core_end_offset"]),
                )
                if correct_profile is None or wrong_profile is None:
                    raise OwnerAutofill10000Error(f"render profile missing: {sample_id}")
                correct_distance = profile_distance(
                    correct_profile,
                    references,
                    feature_scales=feature_scales,
                    feature_weight=float(similarity["feature_weight"]),
                    sequence_weight=float(similarity["sequence_weight"]),
                )
                wrong_distance = profile_distance(
                    wrong_profile,
                    references,
                    feature_scales=feature_scales,
                    feature_weight=float(similarity["feature_weight"]),
                    sequence_weight=float(similarity["sequence_weight"]),
                )
                if not math.isclose(
                    correct_distance,
                    float(row["similarity_distance"]),
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    raise OwnerAutofill10000Error(f"render distance drift: {sample_id}")
                output_row = {
                    **row,
                    "pre_core_context_bars": pre_bars,
                    "post_core_context_bars": post_bars,
                    "window_start_i": window_start_i,
                    "window_end_i": window_end_i,
                    "box": box,
                    "boxes_per_image": 1,
                    "image_path": _final_relative(image_path, building, final_dir),
                    "image_sha256": sha256_file(image_path),
                    "image_width": SOURCE_WIDTH,
                    "image_height": SOURCE_HEIGHT,
                    "exact_red_pixels": exact_red_pixels,
                    "opposite_direction_distance": float(wrong_distance),
                    "correct_direction_distance_better": bool(
                        correct_distance < wrong_distance
                    ),
                    "yolo_label_path": None,
                    "training_eligible": False,
                    "production_eligible": False,
                }
                partial_handle.write(
                    json.dumps(output_row, ensure_ascii=False, sort_keys=True) + "\n"
                )
                partial_handle.flush()
                rendered_by_id[sample_id] = output_row
                rendered_count += 1
                if rendered_count % 100 == 0:
                    os.fsync(partial_handle.fileno())
                    print(
                        f"render {rendered_count:05d}/{len(selected):05d}",
                        flush=True,
                    )
            if source_number % 20 == 0:
                print(
                    f"render sources {source_number}/{len(grouped_selected)}",
                    flush=True,
                )

    output_rows = sorted(rendered_by_id.values(), key=lambda row: int(row["source_order"]))
    if len(output_rows) != int(prereg["selection"]["total"]):
        raise OwnerAutofill10000Error(
            f"rendered {len(output_rows)}, expected {prereg['selection']['total']}"
        )
    if len({str(row["event_id"]) for row in output_rows}) != len(output_rows):
        raise OwnerAutofill10000Error("duplicate event IDs in rendered output")
    if len({str(row["image_sha256"]) for row in output_rows}) != len(output_rows):
        raise OwnerAutofill10000Error("duplicate image hashes in rendered output")
    if list(building.rglob("*.txt")):
        raise OwnerAutofill10000Error("YOLO label text unexpectedly exists")

    manifest_path = building / "review_manifest.jsonl"
    write_jsonl(manifest_path, output_rows)
    manifest_sha = sha256_file(manifest_path)
    correct_better = sum(bool(row["correct_direction_distance_better"]) for row in output_rows)
    times = [pd.Timestamp(row["core_end_time"]) for row in output_rows]
    distances = np.asarray([float(row["similarity_distance"]) for row in output_rows])
    symbols = Counter(str(row["symbol"]) for row in output_rows)
    days = Counter(stamp.strftime("%Y-%m-%d") for stamp in times)
    hours_by_side = Counter(
        (str(row["direction"]), pd.Timestamp(row["core_end_time"]).floor("h").isoformat())
        for row in output_rows
    )
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "n_rendered": len(output_rows),
        "direction_counts": dict(Counter(str(row["direction"]) for row in output_rows)),
        "core_bars_counts": dict(Counter(str(row["core_bars"]) for row in output_rows)),
        "boxes_per_image_min": min(int(row["boxes_per_image"]) for row in output_rows),
        "boxes_per_image_max": max(int(row["boxes_per_image"]) for row in output_rows),
        "unique_events": len({str(row["event_id"]) for row in output_rows}),
        "unique_images": len({str(row["image_sha256"]) for row in output_rows}),
        "unique_symbols": len(symbols),
        "max_per_symbol_across_sides": max(symbols.values()),
        "max_per_utc_day_across_sides": max(days.values()),
        "max_per_utc_hour_per_side": max(hours_by_side.values()),
        "first_core_end_time": min(times).isoformat(),
        "last_core_end_time": max(times).isoformat(),
        "similarity_distance_min": float(distances.min()),
        "similarity_distance_median": float(np.median(distances)),
        "similarity_distance_max": float(distances.max()),
        "n_before_event_nms": int(scan_receipt["n_before_event_nms"]),
        "n_after_event_nms": int(scan_receipt["n_after_event_nms"]),
        "direction_pool_after_event_nms": scan_receipt["direction_after_event_nms"],
        "source_rows_materialized": int(scan_receipt["source_rows_materialized"]),
        "official_archive_sources": int(scan_receipt["official_archive_sources"]),
        "holdout_ohlcv_rows_materialized": 0,
        "direction_null": {
            "correct_direction_distance_better": correct_better,
            "total": len(output_rows),
            "two_sided_exact_sign_test_p": _sign_test_p_string(
                correct_better, len(output_rows)
            ),
        },
        "manual_owner_review_workflow_created": False,
        "yolo_labels_written": 0,
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(building / "summary.json", summary)

    public = building / "public"
    pages_dir = public / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_size = int(prereg["render"]["html_page_size"])
    page_count = math.ceil(len(output_rows) / page_size)
    for page in range(1, page_count + 1):
        page_rows = output_rows[(page - 1) * page_size : page * page_size]
        (pages_dir / f"page_{page:03d}.html").write_text(
            _page_html(page_rows, page=page, pages=page_count),
            encoding="utf-8",
        )
    (public / "index.html").write_text(
        _index_html(
            output_rows,
            page_size=page_size,
            manifest_sha=manifest_sha,
            summary=summary,
        ),
        encoding="utf-8",
    )
    contact = _sample_contact_sheet(output_rows, final_dir)
    ok, encoded = cv2.imencode(".jpg", contact, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise OwnerAutofill10000Error("could not encode sample contact sheet")
    (building / "contact_sheet_sample100.jpg").write_bytes(encoded.tobytes())
    visual_qa = {
        "images_checked": len(output_rows),
        "all_dimensions_1280x742": all(
            int(row["image_width"]) == SOURCE_WIDTH
            and int(row["image_height"]) == SOURCE_HEIGHT
            for row in output_rows
        ),
        "all_exact_red_pixels_present": all(
            int(row["exact_red_pixels"]) >= 100 for row in output_rows
        ),
        "all_one_box": all(int(row["boxes_per_image"]) == 1 for row in output_rows),
        "all_core_4_or_5": all(int(row["core_bars"]) in {4, 5} for row in output_rows),
        "all_box_contains_core_wicks_and_six_mas": all(
            bool(row["box"]["contains_core_wicks_and_six_mas"]) for row in output_rows
        ),
        "yolo_label_files": 0,
        "passed": True,
    }
    write_json(building / "visual_qa_receipt.json", visual_qa)
    write_json(
        building / "build_receipt.json",
        {
            "experiment_id": EXPERIMENT_ID,
            "builder_commit": builder_commit,
            "preregistration_path": _relative(prereg_path),
            "preregistration_sha256": prereg_sha,
            "reference_profiles": len(references),
            "reference_holdout_ohlcv_rows_materialized": sum(
                int(row["holdout_ohlcv_rows_materialized"]) for row in reference_audits
            ),
            "manifest_sha256": manifest_sha,
            "summary": summary,
            "training_started": False,
            "active_or_frozen_changed": False,
            "forward_or_order_state_changed": False,
        },
    )
    write_json(
        state_path,
        {
            "experiment_id": EXPERIMENT_ID,
            "preregistration_sha256": prereg_sha,
            "builder_commit": builder_commit,
            "phase": "complete",
        },
    )
    os.replace(building, final_dir)
    return {
        **summary,
        "output_dir": _relative(final_dir),
        "html_path": _relative(final_dir / "public" / "index.html"),
        "manifest_sha256": manifest_sha,
    }
