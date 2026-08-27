"""Mine a fully boxed 15-minute MA-launch example pack from frozen candidates.

The Owner did not request a box/no-box adjudication workflow.  This builder
therefore treats rejection as an internal retrieval step: rows that fail the
strict temporal gate disappear and are replaced from the 10,000-candidate
pre-holdout pool until 25 LONG and 25 SHORT examples remain.  Every delivered
PNG contains exactly one four- or five-bar box.

Similarity is computed on direction-normalized core-plus-five-bar sequences.
Inputs are OHLC plus SMA/EMA 20/60/120 and Pine-RMA ATR14.  The five bars after
a proposed core may extend beyond the source candidate anchor and are used only
to require an already-collected completed launch, never to claim a live signal.
This is a review/example artifact, not a causal detector, YOLO label set,
training set, model, promotion, forward event or trading action.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
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
from yoyo.datasets.ma_launch_owner_recrop_review import (
    HOLDOUT_START,
    ROOT,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    core_box,
    draw_box,
    encode_png,
    verify_builder_committed,
)
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS
from yoyo.layers.l1_detection.render import render_chart


EXPERIMENT_ID = "exp-15m-ma-launch-owner-autofill50-v7"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
FEATURE_NAMES = (
    "ma_envelope_atr",
    "ma_spread_end_atr",
    "candle_envelope_atr",
    "max_body_atr",
    "core_progress_atr",
    "post1_progress_atr",
    "post2_progress_atr",
    "post3_progress_atr",
    "post5_progress_atr",
    "aligned_ma_slope_atr",
    "ma_slope_std_atr",
    "minimum_close_to_ma_atr",
    "max_close_to_ma_envelope_atr",
    "max_body_to_ma_envelope_atr",
)


class OwnerAutofillError(ValueError):
    """Raised when strict retrieval, chronology or delivery geometry drifts."""


@dataclass(frozen=True)
class Profile:
    """Direction-normalized morphology for one proposed core and five later bars."""

    features: np.ndarray
    sequence: np.ndarray


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def frame_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    columns = ("open", "high", "low", "close", "atr", *SIX_MA_COLUMNS)
    return {column: frame[column].to_numpy(dtype=float) for column in columns}


def _resample(values: np.ndarray, size: int = 5) -> np.ndarray:
    if values.ndim != 1 or len(values) not in {4, 5}:
        raise OwnerAutofillError("core resampling requires four or five values")
    return np.interp(np.linspace(0.0, 1.0, size), np.linspace(0.0, 1.0, len(values)), values)


def morphology_profile(
    arrays: Mapping[str, np.ndarray],
    *,
    anchor_i: int,
    direction: str,
    core_start_offset: int,
    core_end_offset: int,
) -> Profile | None:
    """Return a normalized core+5 profile or ``None`` for invalid chronology."""

    start_i = anchor_i + core_start_offset
    end_i = anchor_i + core_end_offset
    core_len = end_i - start_i + 1
    if core_len not in {4, 5} or start_i < 0 or end_i + 5 >= len(arrays["close"]):
        return None
    atr = float(arrays["atr"][anchor_i])
    if not np.isfinite(atr) or atr <= 0.0:
        return None
    sign = 1.0 if direction == "LONG" else -1.0 if direction == "SHORT" else 0.0
    if sign == 0.0:
        raise OwnerAutofillError(f"unsupported direction: {direction}")
    stop = end_i + 6
    ma_core = np.stack(
        [arrays[column][start_i : end_i + 1] for column in SIX_MA_COLUMNS], axis=1
    )
    ma_all = np.stack([arrays[column][start_i:stop] for column in SIX_MA_COLUMNS], axis=1)
    values = np.concatenate(
        [
            arrays[column][start_i:stop]
            for column in ("open", "high", "low", "close")
        ]
        + [ma_all.ravel(), np.asarray([atr])]
    )
    if not np.isfinite(values).all():
        return None
    ma_origin = float(ma_core[0].mean())
    close = arrays["close"][start_i:stop]
    body = sign * (arrays["close"][start_i:stop] - arrays["open"][start_i:stop]) / atr
    close_path = sign * (close - ma_origin) / atr
    ma_center = sign * (ma_all.mean(axis=1) - ma_origin) / atr
    ma_spread = (ma_all.max(axis=1) - ma_all.min(axis=1)) / atr
    sequence = np.stack(
        [
            np.r_[_resample(channel[:core_len]), channel[core_len:]]
            for channel in (close_path, body, ma_center, ma_spread)
        ]
    )
    end_ma = ma_core[-1]
    slopes = (end_ma - ma_core[0]) / atr
    minimum_close_to_ma = float(
        np.abs(arrays["close"][start_i : end_i + 1, None] - ma_core).min() / atr
    )
    ma_low = ma_core.min(axis=1)
    ma_high = ma_core.max(axis=1)
    core_close = arrays["close"][start_i : end_i + 1]
    core_open = arrays["open"][start_i : end_i + 1]
    body_low = np.minimum(core_open, core_close)
    body_high = np.maximum(core_open, core_close)
    close_to_envelope = np.maximum(
        np.maximum(ma_low - core_close, core_close - ma_high), 0.0
    )
    body_to_envelope = np.maximum(
        np.maximum(ma_low - body_high, body_low - ma_high), 0.0
    )
    features = np.asarray(
        [
            (ma_core.max() - ma_core.min()) / atr,
            (end_ma.max() - end_ma.min()) / atr,
            (
                arrays["high"][start_i : end_i + 1].max()
                - arrays["low"][start_i : end_i + 1].min()
            )
            / atr,
            np.abs(
                arrays["close"][start_i : end_i + 1]
                - arrays["open"][start_i : end_i + 1]
            ).max()
            / atr,
            sign * (arrays["close"][end_i] - arrays["close"][start_i]) / atr,
            sign * (arrays["close"][end_i + 1] - arrays["close"][end_i]) / atr,
            sign * (arrays["close"][end_i + 2] - arrays["close"][end_i]) / atr,
            sign * (arrays["close"][end_i + 3] - arrays["close"][end_i]) / atr,
            sign * (arrays["close"][end_i + 5] - arrays["close"][end_i]) / atr,
            sign * float(slopes.mean()),
            float(slopes.std()),
            minimum_close_to_ma,
            float(close_to_envelope.max() / atr),
            float(body_to_envelope.max() / atr),
        ],
        dtype=float,
    )
    return Profile(features=features, sequence=sequence)


def passes_gate(profile: Profile, gates: Mapping[str, float]) -> bool:
    values = dict(zip(FEATURE_NAMES, profile.features))
    return bool(
        values["ma_envelope_atr"] <= gates["max_ma_envelope_atr"]
        and values["ma_spread_end_atr"] <= gates["max_ma_spread_end_atr"]
        and values["max_body_atr"] <= gates["max_core_body_atr"]
        and gates["min_core_progress_atr"]
        <= values["core_progress_atr"]
        <= gates["max_core_progress_atr"]
        and values["post1_progress_atr"] >= gates["min_post1_progress_atr"]
        and values["post2_progress_atr"] >= gates["min_post2_progress_atr"]
        and values["post3_progress_atr"] >= gates["min_post3_progress_atr"]
        and values["post5_progress_atr"] >= gates["min_post5_progress_atr"]
        and values["aligned_ma_slope_atr"] >= gates["min_aligned_ma_slope_atr"]
        and values["minimum_close_to_ma_atr"] <= gates["max_minimum_close_to_ma_atr"]
        and values["max_close_to_ma_envelope_atr"]
        <= gates["max_close_to_ma_envelope_atr"]
        and values["max_body_to_ma_envelope_atr"]
        <= gates["max_body_to_ma_envelope_atr"]
    )


def profile_distance(
    profile: Profile,
    references: Sequence[Profile],
    *,
    feature_scales: np.ndarray,
    feature_weight: float,
    sequence_weight: float,
) -> float:
    """Return nearest-reference direction-normalized morphology distance."""

    if not references or feature_scales.shape != profile.features.shape:
        raise OwnerAutofillError("invalid reference distance inputs")
    distances = []
    for reference in references:
        feature_distance = float(
            np.sqrt(np.mean(((profile.features - reference.features) / feature_scales) ** 2))
        )
        sequence_distance = float(np.sqrt(np.mean((profile.sequence - reference.sequence) ** 2)))
        distances.append(feature_weight * feature_distance + sequence_weight * sequence_distance)
    return min(distances)


def best_span(
    arrays: Mapping[str, np.ndarray],
    *,
    anchor_i: int,
    direction: str,
    core_lengths: Sequence[int],
    end_offsets: Sequence[int],
    gates: Mapping[str, float],
    references: Sequence[Profile],
    feature_scales: np.ndarray,
    feature_weight: float,
    sequence_weight: float,
) -> dict[str, Any] | None:
    choices: list[dict[str, Any]] = []
    for core_len in core_lengths:
        for end_offset in end_offsets:
            start_offset = int(end_offset) - int(core_len) + 1
            profile = morphology_profile(
                arrays,
                anchor_i=anchor_i,
                direction=direction,
                core_start_offset=start_offset,
                core_end_offset=int(end_offset),
            )
            if profile is None or not passes_gate(profile, gates):
                continue
            distance = profile_distance(
                profile,
                references,
                feature_scales=feature_scales,
                feature_weight=feature_weight,
                sequence_weight=sequence_weight,
            )
            choices.append(
                {
                    "core_start_offset": start_offset,
                    "core_end_offset": int(end_offset),
                    "core_bars": int(core_len),
                    "similarity_distance": distance,
                    "features": dict(
                        zip(FEATURE_NAMES, (float(value) for value in profile.features))
                    ),
                }
            )
    return min(choices, key=lambda row: (row["similarity_distance"], -row["core_end_offset"])) if choices else None


def select_diverse(
    candidates: Sequence[Mapping[str, Any]], selection: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Select 25 per side without turning clustered bursts into fake diversity."""

    target_per_side = int(selection["target_per_side"])
    bins_count = int(selection["time_bins_per_side"])
    per_bin = target_per_side // bins_count
    if target_per_side % bins_count:
        raise OwnerAutofillError("target_per_side must divide evenly across time bins")
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for direction in ("LONG", "SHORT"):
        group = [dict(row) for row in candidates if row["direction"] == direction]
        group.sort(key=lambda row: (pd.Timestamp(row["anchor_time"]), row["event_id"]))
        if len(group) < target_per_side:
            raise OwnerAutofillError(f"insufficient strict {direction} candidates")
        unique_hours = sorted({pd.Timestamp(row["anchor_time"]).floor("h") for row in group})
        if len(unique_hours) < target_per_side:
            raise OwnerAutofillError(f"insufficient independent {direction} event hours")
        hour_bin = {
            hour: min(bins_count - 1, (rank * bins_count) // len(unique_hours))
            for rank, hour in enumerate(unique_hours)
        }
        buckets: list[list[dict[str, Any]]] = [[] for _ in range(bins_count)]
        for row in group:
            bin_index = hour_bin[pd.Timestamp(row["anchor_time"]).floor("h")]
            row["time_bin"] = bin_index
            buckets[bin_index].append(row)
        for bin_index, bucket in enumerate(buckets):
            grouped[(direction, bin_index)] = bucket

    group_options: dict[tuple[str, int], list[tuple[float, tuple[dict[str, Any], ...]]]] = {}
    max_per_day = int(selection["max_per_utc_day"])
    for key, rows in grouped.items():
        options: list[tuple[float, tuple[dict[str, Any], ...]]] = []
        ordered = sorted(rows, key=lambda row: (row["similarity_distance"], row["event_id"]))
        for option in combinations(ordered, per_bin):
            symbols = [str(row["symbol"]) for row in option]
            stamps = [pd.Timestamp(row["anchor_time"]) for row in option]
            hours = [stamp.floor("h").isoformat() for stamp in stamps]
            days = [stamp.strftime("%Y-%m-%d") for stamp in stamps]
            if len(set(symbols)) != per_bin or len(set(hours)) != per_bin:
                continue
            if max(Counter(days).values()) > max_per_day:
                continue
            options.append((sum(float(row["similarity_distance"]) for row in option), option))
        options.sort(key=lambda item: (item[0], tuple(row["event_id"] for row in item[1])))
        if not options:
            raise OwnerAutofillError(f"no internally diverse combination for {key}")
        group_options[key] = options

    solution: list[dict[str, Any]] | None = None
    search_nodes = 0

    def solve(
        remaining: tuple[tuple[str, int], ...],
        chosen: list[dict[str, Any]],
        used_symbols: set[str],
        used_hours: set[str],
        day_counts: Counter[str],
    ) -> bool:
        nonlocal search_nodes, solution
        search_nodes += 1
        if search_nodes > 250_000:
            raise OwnerAutofillError("diversity search exceeded deterministic node budget")
        if not remaining:
            solution = list(chosen)
            return True
        compatible_by_group: list[
            tuple[tuple[str, int], list[tuple[float, tuple[dict[str, Any], ...]]]]
        ] = []
        for key in remaining:
            compatible = []
            for item in group_options[key]:
                option = item[1]
                option_symbols = {str(row["symbol"]) for row in option}
                option_stamps = [pd.Timestamp(row["anchor_time"]) for row in option]
                option_hours = {stamp.floor("h").isoformat() for stamp in option_stamps}
                option_days = Counter(stamp.strftime("%Y-%m-%d") for stamp in option_stamps)
                if option_symbols & used_symbols or option_hours & used_hours:
                    continue
                if any(day_counts[day] + count > max_per_day for day, count in option_days.items()):
                    continue
                compatible.append(item)
            if not compatible:
                return False
            compatible_by_group.append((key, compatible))
        key, compatible = min(
            compatible_by_group,
            key=lambda item: (len(item[1]), item[0][0], item[0][1]),
        )
        next_remaining = tuple(item for item in remaining if item != key)
        for _, option in compatible:
            option_rows = list(option)
            option_symbols = {str(row["symbol"]) for row in option_rows}
            option_stamps = [pd.Timestamp(row["anchor_time"]) for row in option_rows]
            option_hours = {stamp.floor("h").isoformat() for stamp in option_stamps}
            option_days = Counter(stamp.strftime("%Y-%m-%d") for stamp in option_stamps)
            next_day_counts = day_counts.copy()
            next_day_counts.update(option_days)
            if solve(
                next_remaining,
                chosen + option_rows,
                used_symbols | option_symbols,
                used_hours | option_hours,
                next_day_counts,
            ):
                return True
        return False

    keys = tuple(sorted(group_options, key=lambda key: (len(group_options[key]), key)))
    if not solve(keys, [], set(), set(), Counter()):
        raise OwnerAutofillError("could not solve strict cross-pack diversity constraints")
    assert solution is not None
    selected = solution
    used_symbols = {str(row["symbol"]) for row in selected}
    if len(selected) != 2 * target_per_side or len(used_symbols) != len(selected):
        raise OwnerAutofillError("strict selection is not 50 unique-symbol rows")
    return sorted(selected, key=lambda row: (row["direction"], row["time_bin"], row["anchor_time"]))


def _sample_id(row: Mapping[str, Any]) -> str:
    raw = (
        f"{EXPERIMENT_ID}|{row['symbol']}|{row['direction']}|{row['anchor_time']}|"
        f"{row['core_start_offset']}|{row['core_end_offset']}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _context(sample_id: str, values: Sequence[int], salt: str) -> int:
    digest = hashlib.sha256(f"{EXPERIMENT_ID}|{salt}|{sample_id}".encode()).digest()
    return int(values[int.from_bytes(digest[:8], "big") % len(values)])


def _relative(path: Path, building: Path, final_dir: Path) -> str:
    return str((final_dir / path.relative_to(building)).relative_to(ROOT))


def _render_html(rows: Sequence[Mapping[str, Any]], manifest_sha: str) -> str:
    cards = []
    for order, row in enumerate(rows, 1):
        cards.append(
            "<article><h2>"
            + f"{order:02d}/50 · {html.escape(str(row['symbol']))} · {row['direction']}"
            + "</h2><p>"
            + f"core t{row['core_start_offset']}..t{row['core_end_offset']} · "
            + f"{row['core_bars']}根 · distance {row['similarity_distance']:.3f}</p>"
            + f"<img loading='lazy' src='images/{Path(str(row['image_path'])).name}'>"
            + "</article>"
        )
    return """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>15m 严格自动补齐 50</title><style>
body{margin:0;background:#eef2f5;color:#18222c;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header,main{max-width:1500px;margin:auto;padding:18px}header{background:#fff7df;border-bottom:1px solid #d9c179}main{display:grid;grid-template-columns:1fr 1fr;gap:16px}article{background:white;padding:12px;border-radius:12px;box-shadow:0 2px 10px #1b304018}h1{margin:0 0 8px}h2{font-size:17px;margin:0 0 5px}p{font-size:13px;color:#5c6872}img{display:block;width:100%;height:auto;border:1px solid #d5dde4}@media(max-width:820px){main{grid-template-columns:1fr}}
</style></head><body><header><h1>15m 严格自动补齐 50</h1><p>内部淘汰后自动补位；交付 25 LONG + 25 SHORT，50 张均恰好一个 4–5 根红框。无需 Owner 做 KEEP/ADJUST/REJECT。本批只是形态示例，不是训练标签。<br>manifest SHA: """ + manifest_sha + "</p></header><main>" + "".join(cards) + "</main></body></html>"


def _contact_sheet(images: Sequence[np.ndarray], rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    tile_w, tile_h, columns = 320, 210, 5
    rows_count = (len(images) + columns - 1) // columns
    canvas = np.full((rows_count * tile_h, columns * tile_w, 3), 245, dtype=np.uint8)
    for index, (image, row) in enumerate(zip(images, rows)):
        preview = cv2.resize(image, (tile_w, tile_h - 28), interpolation=cv2.INTER_AREA)
        y, x = (index // columns) * tile_h, (index % columns) * tile_w
        canvas[y + 28 : y + tile_h, x : x + tile_w] = preview
        cv2.putText(
            canvas,
            f"{index + 1:02d} {row['symbol']} {row['direction']}",
            (x + 5, y + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (35, 42, 48),
            1,
            cv2.LINE_AA,
        )
    return canvas


def build(prereg_path: Path = DEFAULT_PREREG, output_dir: Path | None = None) -> dict[str, Any]:
    prereg_path = prereg_path.resolve()
    prereg = read_json(prereg_path)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise OwnerAutofillError("experiment ID drift")
    inputs = prereg["inputs"]
    candidate_rows: list[dict[str, Any]] = []
    for item in inputs["candidate_manifests"]:
        path = ROOT / str(item["path"])
        if sha256_file(path) != str(item["sha256"]):
            raise OwnerAutofillError(f"candidate manifest SHA drift: {path}")
        candidate_rows.extend(read_jsonl(path))
    exclude_path = ROOT / str(inputs["exclude_review50_manifest"]["path"])
    if sha256_file(exclude_path) != str(inputs["exclude_review50_manifest"]["sha256"]):
        raise OwnerAutofillError("Review50 exclusion manifest SHA drift")
    excluded_rows = read_jsonl(exclude_path)
    excluded_keys = {
        (str(row["source_path"]), int(row["source_anchor_i"]), str(row["direction"]))
        for row in excluded_rows
    }
    unique_candidates = {
        (str(row["source_path"]), int(row["source_anchor_i"]), str(row["direction"])): row
        for row in candidate_rows
    }
    if len(unique_candidates) != int(inputs["expected_unique_candidates"]):
        raise OwnerAutofillError("candidate identity count drift")
    builder_commit = verify_builder_committed(
        [Path(__file__), ROOT / "scripts" / "build_15m_ma_launch_owner_autofill50.py", prereg_path]
    )
    gates = prereg["morphology_gate"]
    similarity = prereg["similarity"]
    feature_scales = np.asarray(similarity["feature_scales"], dtype=float)
    reference_profiles: list[Profile] = []
    reference_audits = []
    for reference in prereg["owner_calibration_references"]:
        frame, audit = read_preholdout_prefix(
            ROOT / str(reference["source_path"]), end_exclusive=HOLDOUT_START
        )
        reference_audits.append(audit)
        profile = morphology_profile(
            frame_arrays(add_candidate_features(frame)),
            anchor_i=int(reference["source_anchor_i"]),
            direction=str(reference["direction"]),
            core_start_offset=int(reference["core_start_offset"]),
            core_end_offset=int(reference["core_end_offset"]),
        )
        if profile is None:
            raise OwnerAutofillError("reference profile could not be computed")
        reference_profiles.append(profile)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key, row in unique_candidates.items():
        if key not in excluded_keys:
            groups[str(row["source_path"])].append(dict(row))
    strict_candidates: list[dict[str, Any]] = []
    gate_pass_count = 0
    source_audits = []
    for source_path, rows in sorted(groups.items()):
        frame, audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
        source_audits.append(audit)
        arrays = frame_arrays(add_candidate_features(frame))
        for row in rows:
            span = best_span(
                arrays,
                anchor_i=int(row["source_anchor_i"]),
                direction=str(row["direction"]),
                core_lengths=tuple(int(value) for value in gates["core_lengths"]),
                end_offsets=tuple(int(value) for value in gates["core_end_offsets"]),
                gates=gates,
                references=reference_profiles,
                feature_scales=feature_scales,
                feature_weight=float(similarity["feature_weight"]),
                sequence_weight=float(similarity["sequence_weight"]),
            )
            if span is None:
                continue
            gate_pass_count += 1
            if float(span["similarity_distance"]) > float(similarity["max_distance"]):
                continue
            strict_candidates.append(
                {
                    **row,
                    **span,
                    "training_eligible": False,
                    "production_eligible": False,
                }
            )
    selected = select_diverse(strict_candidates, prereg["selection"])
    final_dir = output_dir.resolve() if output_dir else prereg_path.parent / "results"
    building = final_dir.with_name(f"{final_dir.name}.building")
    if final_dir.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite autofill review: {final_dir}")
    image_dir = building / "public" / "images"
    image_dir.mkdir(parents=True)
    selected_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        row["sample_id"] = _sample_id(row)
        selected_groups[str(row["source_path"])].append(row)
    output_rows: list[dict[str, Any]] = []
    contact_images: list[np.ndarray] = []
    render_audits = []
    for source_path, rows in sorted(selected_groups.items()):
        frame, audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
        render_audits.append(audit)
        enriched = add_candidate_features(frame)
        for row in rows:
            anchor_i = int(row["source_anchor_i"])
            core_start_i = anchor_i + int(row["core_start_offset"])
            core_end_i = anchor_i + int(row["core_end_offset"])
            sample_id = str(row["sample_id"])
            pre_bars = _context(sample_id, prereg["render"]["pre_core_context_bars"], "pre")
            post_bars = _context(sample_id, prereg["render"]["post_core_context_bars"], "post")
            window_start_i = core_start_i - pre_bars
            window_end_i = core_end_i + post_bars
            window = enriched.iloc[window_start_i : window_end_i + 1].reset_index(drop=True)
            if len(window) != pre_bars + int(row["core_bars"]) + post_bars:
                raise OwnerAutofillError(f"incomplete render window: {sample_id}")
            if pd.Timestamp(window["open_time"].iloc[-1]) >= HOLDOUT_START:
                raise OwnerAutofillError(f"render touches holdout: {sample_id}")
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
            filename = f"{len(output_rows) + 1:02d}_{row['symbol']}_{row['direction']}_{sample_id}.png"
            image_path = image_dir / filename
            image_path.write_bytes(encode_png(overlay))
            contact_images.append(overlay)
            output_rows.append(
                {
                    "source_order": len(output_rows) + 1,
                    "sample_id": sample_id,
                    "event_id": str(row["event_id"]),
                    "symbol": str(row["symbol"]),
                    "direction": str(row["direction"]),
                    "anchor_time": str(row["anchor_time"]),
                    "source_path": source_path,
                    "source_anchor_i": anchor_i,
                    "core_start_offset": int(row["core_start_offset"]),
                    "core_end_offset": int(row["core_end_offset"]),
                    "core_bars": int(row["core_bars"]),
                    "pre_core_context_bars": pre_bars,
                    "post_core_context_bars": post_bars,
                    "window_start_i": window_start_i,
                    "window_end_i": window_end_i,
                    "similarity_distance": float(row["similarity_distance"]),
                    "time_bin": int(row["time_bin"]),
                    "features": row["features"],
                    "box": box,
                    "image_path": _relative(image_path, building, final_dir),
                    "image_sha256": sha256_file(image_path),
                    "boxes_per_image": 1,
                    "training_eligible": False,
                    "production_eligible": False,
                    "yolo_label_path": None,
                }
            )
    if len(output_rows) != 50:
        raise OwnerAutofillError("rendered row count is not 50")
    manifest_path = building / "review_manifest.jsonl"
    write_jsonl(manifest_path, output_rows)
    manifest_sha = sha256_file(manifest_path)
    index_path = building / "public" / "index.html"
    index_path.write_text(_render_html(output_rows, manifest_sha), encoding="utf-8")
    sheet_path = building / "contact_sheet_50.png"
    sheet_path.write_bytes(encode_png(_contact_sheet(contact_images, output_rows)))
    days = [pd.Timestamp(row["anchor_time"]).strftime("%Y-%m-%d") for row in output_rows]
    hours = [pd.Timestamp(row["anchor_time"]).floor("h").isoformat() for row in output_rows]
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "n_source_unique_candidates": len(unique_candidates),
        "n_internal_gate_pass_before_distance": gate_pass_count,
        "n_internal_gate_and_distance_pass": len(strict_candidates),
        "n_delivered": 50,
        "direction_counts": dict(Counter(row["direction"] for row in output_rows)),
        "core_bars_counts": dict(Counter(str(row["core_bars"]) for row in output_rows)),
        "unique_symbols": len({row["symbol"] for row in output_rows}),
        "max_per_utc_day": max(Counter(days).values()),
        "max_per_utc_hour": max(Counter(hours).values()),
        "boxes_per_image_min": 1,
        "boxes_per_image_max": 1,
        "similarity_distance_min": min(row["similarity_distance"] for row in output_rows),
        "similarity_distance_median": float(np.median([row["similarity_distance"] for row in output_rows])),
        "similarity_distance_max": max(row["similarity_distance"] for row in output_rows),
        "holdout_ohlcv_rows_materialized": sum(int(a["holdout_ohlcv_rows_materialized"]) for a in source_audits + reference_audits + render_audits),
        "yolo_labels_written": 0,
        "training_started": False,
        "manual_owner_review_workflow_created": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    if summary["direction_counts"] != {"LONG": 25, "SHORT": 25}:
        raise OwnerAutofillError("direction balance drift")
    if summary["unique_symbols"] != 50 or summary["max_per_utc_day"] > 2 or summary["max_per_utc_hour"] > 1:
        raise OwnerAutofillError("selection diversity drift")
    if summary["holdout_ohlcv_rows_materialized"] != 0:
        raise OwnerAutofillError("holdout rows materialized")
    write_json(building / "summary.json", summary)
    write_jsonl(building / "source_audit.jsonl", source_audits + reference_audits + render_audits)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "preregistration_sha256": sha256_file(prereg_path),
        "review_manifest_sha256": manifest_sha,
        "review_html_sha256": sha256_file(index_path),
        "contact_sheet_sha256": sha256_file(sheet_path),
        "n_delivered": 50,
        "boxes_per_image": 1,
        "holdout_ohlcv_rows_materialized": 0,
        "yolo_labels_written": 0,
        "training_started": False,
    }
    write_json(building / "build_receipt.json", receipt)
    os.replace(building, final_dir)
    return receipt
