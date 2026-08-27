"""Build the Owner-approved 15m MA-launch weak-label YOLO dataset.

Positive pixels use only each accepted manifest window.  The exact accepted
box is written to a separate YOLO label and is never burned into model input.
For lineage, drawing that box back onto the clean re-render must reproduce the
delivered boxed PNG byte-for-byte.

One or more negative images are paired to each positive by source file, symbol,
calendar half-year, split and exact window geometry.  A negative label may
inspect prices through pseudo core ``+5`` to prove no completed launch; model
pixels contain only the matched render window.  Every strict accepted-family
candidate is guarded before sampling.  A pinned predecessor plan may seed an
additive expansion, but every seed is revalidated against the same guards and
feature masks before reuse.  No source row at or after the repository holdout
is materialized, and this module never trains, promotes, deploys or changes
live state.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
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
from yoyo.datasets.ma_launch_owner_autofill10000 import scan_source
from yoyo.datasets.ma_launch_owner_autofill_review import frame_arrays
from yoyo.datasets.ma_launch_owner_autofill10000 import (
    load_reference_profiles,
)
from yoyo.datasets.ma_launch_owner_recrop_review import (
    HOLDOUT_START,
    RED,
    ROOT,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    draw_box,
    encode_png,
)
from yoyo.layers.l1_detection.render import render_chart


EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-dataset10000-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_RESULTS = DEFAULT_PREREG.parent / "results"
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_owner_autofill10000_yolo_v1"
MODULE_PATH = Path(__file__).resolve()
SCRIPT_PATH = ROOT / "scripts" / "build_15m_ma_launch_owner_yolo_dataset10000.py"
CLASS_IDS = {"LONG": 0, "SHORT": 1}
EXACT_OVERLAY_RED = np.asarray(RED, dtype=np.uint8)


class OwnerYoloDatasetError(ValueError):
    """Raised when lineage, chronology, sampling or render contracts drift."""


@dataclass(frozen=True)
class PositivePlan:
    """One exact accepted positive window and box."""

    sample_id: str
    event_id: str
    source_order: int
    symbol: str
    direction: str
    source_path: str
    split: str
    negative_kind: str
    core_bars: int
    core_start_i: int
    core_end_i: int
    pre_core_context_bars: int
    post_core_context_bars: int
    window_start_i: int
    window_end_i: int
    dependency_end_i: int
    core_start_time: str
    core_end_time: str
    window_start_time: str
    window_end_time: str
    dependency_end_time: str
    time_block: str
    box: dict[str, Any]
    accepted_image_path: str
    accepted_image_sha256: str


@dataclass(frozen=True)
class NegativePlan:
    """One paired clean empty-label background."""

    sample_id: str
    paired_positive_sample_id: str
    paired_positive_source_order: int
    pair_slot: int
    paired_direction: str
    symbol: str
    source_path: str
    split: str
    negative_kind: str
    core_bars: int
    core_end_i: int
    pre_core_context_bars: int
    post_core_context_bars: int
    window_start_i: int
    window_end_i: int
    dependency_end_i: int
    core_end_time: str
    window_start_time: str
    window_end_time: str
    dependency_end_time: str
    time_block: str
    ma_envelope_atr: float
    ma_spread_end_atr: float
    max_body_atr: float
    candle_envelope_atr: float
    minimum_close_to_ma_atr: float
    abs_close_progress_atr_core_plus_2: float
    abs_close_progress_atr_core_plus_3: float
    abs_close_progress_atr_core_plus_5: float
    two_sided_excursion_atr_core_plus_1_to_5: float


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


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _repo_path(value: object) -> Path:
    path = Path(str(value))
    path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise OwnerYoloDatasetError(f"path escapes repository: {value}") from exc
    return path


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def stable_int(*parts: object) -> int:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:16], 16)


def stable_id(*parts: object, length: int = 24) -> str:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def calendar_halfyear(value: object) -> str:
    stamp = utc(value)
    return f"{stamp.year:04d}H{1 if stamp.month <= 6 else 2}"


def interval_split(
    start: object,
    end: object,
    *,
    cutoff: object,
    purge_bars: int,
    bar_minutes: int,
) -> str:
    """Split a complete dependency interval, with crossed rows excluded."""

    start_ts, end_ts, cutoff_ts = utc(start), utc(end), utc(cutoff)
    if end_ts < start_ts:
        raise OwnerYoloDatasetError("dependency interval ends before it starts")
    purge = pd.Timedelta(minutes=int(purge_bars) * int(bar_minutes))
    if end_ts <= cutoff_ts - purge:
        return "train"
    if start_ts >= cutoff_ts + purge:
        return "val"
    return "excluded"


def verify_builder_committed(paths: Sequence[Path]) -> str:
    """Require main and committed builder inputs before artifacts are written."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise OwnerYoloDatasetError("dataset builder must run on main")
    relative = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise OwnerYoloDatasetError(f"builder inputs are not committed:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", relative[0]],
        cwd=ROOT,
        text=True,
    ).strip()
    if len(commit) != 40:
        raise OwnerYoloDatasetError("could not resolve builder commit")
    return commit


def load_preregistration(path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    payload = read_json(path)
    experiment_id = str(payload.get("experiment_id", ""))
    if not experiment_id or path.resolve().parent.name != experiment_id:
        raise OwnerYoloDatasetError("experiment_id must match preregistration directory")
    auth = payload["owner_authorization"]
    if auth.get("positive_label_materialization_authorized") is not True:
        raise OwnerYoloDatasetError("positive label materialization is not authorized")
    if auth.get("automatic_negative_materialization_authorized") is not True:
        raise OwnerYoloDatasetError("negative materialization is not authorized")
    if auth.get("training_run_authorized") is not False:
        raise OwnerYoloDatasetError("this builder must not authorize a training run")
    safety = payload["safety"]
    for field in (
        "holdout_read",
        "training_started",
        "training_eligible",
        "production_eligible",
        "active_or_frozen_change",
        "promote",
        "deployment",
        "forward_state_change",
        "order_state_change",
        "remote_write",
    ):
        if safety.get(field) is not False:
            raise OwnerYoloDatasetError(f"safety switch must remain false: {field}")
    if int(payload["sources"]["holdout_ohlcv_rows_allowed"]) != 0:
        raise OwnerYoloDatasetError("holdout row allowance must be zero")
    negative_cfg = payload["negative_sampling"]
    rows = int(payload["positive_source"]["rows"])
    per_positive = int(negative_cfg["negative_per_positive"])
    if per_positive < 1:
        raise OwnerYoloDatasetError("negative_per_positive must be positive")
    if int(negative_cfg["target"]) != rows * per_positive:
        raise OwnerYoloDatasetError("positive/negative target drift")
    target_kinds = negative_cfg.get("target_kinds_per_positive")
    if target_kinds is not None:
        normalized = [str(value) for value in target_kinds]
        if len(normalized) != per_positive or set(normalized) - {"hard", "easy"}:
            raise OwnerYoloDatasetError("invalid target_kinds_per_positive")
        preferred = Counter(normalized)
        expected = Counter(
            hard=int(negative_cfg["preferred_hard_total"]) // rows,
            easy=int(negative_cfg["preferred_easy_total"]) // rows,
        )
        if (
            int(negative_cfg["preferred_hard_total"]) % rows
            or int(negative_cfg["preferred_easy_total"]) % rows
            or preferred != expected
        ):
            raise OwnerYoloDatasetError("preferred negative totals drift from per-pair target")
    return payload


def _verify_pinned(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256_file(path) != expected:
        raise OwnerYoloDatasetError(f"{label} SHA drift: {path}")


def load_positive_rows(prereg: Mapping[str, Any]) -> list[dict[str, Any]]:
    source = prereg["positive_source"]
    prereg_path = _repo_path(source["preregistration_path"])
    manifest_path = _repo_path(source["manifest_path"])
    receipt_path = _repo_path(source["build_receipt_path"])
    _verify_pinned(prereg_path, str(source["preregistration_sha256"]), "positive prereg")
    _verify_pinned(manifest_path, str(source["manifest_sha256"]), "positive manifest")
    _verify_pinned(receipt_path, str(source["build_receipt_sha256"]), "positive receipt")
    rows = read_jsonl(manifest_path)
    counts = Counter(str(row.get("direction")) for row in rows)
    if len(rows) != int(source["rows"]):
        raise OwnerYoloDatasetError("positive row count drift")
    if counts != Counter(LONG=int(source["long"]), SHORT=int(source["short"])):
        raise OwnerYoloDatasetError(f"positive direction drift: {counts}")
    identities = [str(row["sample_id"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise OwnerYoloDatasetError("duplicate positive sample IDs")
    for row in rows:
        if int(row.get("boxes_per_image", -1)) != 1:
            raise OwnerYoloDatasetError("positive must have exactly one accepted box")
        if int(row["image_width"]) != SOURCE_WIDTH or int(row["image_height"]) != SOURCE_HEIGHT:
            raise OwnerYoloDatasetError("positive source dimensions drift")
        image = _repo_path(row["image_path"])
        _verify_pinned(image, str(row["image_sha256"]), "accepted review image")
        if utc(row["core_end_time"]) >= HOLDOUT_START:
            raise OwnerYoloDatasetError("positive core touches holdout")
    return sorted(rows, key=lambda row: int(row["source_order"]))


def plan_positives(
    rows: Sequence[Mapping[str, Any]], prereg: Mapping[str, Any]
) -> list[PositivePlan]:
    split_cfg = prereg["split"]
    delta = pd.Timedelta(minutes=int(split_cfg["bar_minutes"]))
    plans: list[PositivePlan] = []
    for row in rows:
        core_start = int(row["source_core_start_i"])
        core_end = int(row["source_core_end_i"])
        window_start = int(row["window_start_i"])
        window_end = int(row["window_end_i"])
        dependency_end = max(window_end, core_end + 5)
        core_end_time = utc(row["core_end_time"])
        start_time = core_end_time + (window_start - core_end) * delta
        end_time = core_end_time + (window_end - core_end) * delta
        dependency_end_time = core_end_time + (dependency_end - core_end) * delta
        split = interval_split(
            start_time,
            dependency_end_time,
            cutoff=split_cfg["cutoff"],
            purge_bars=int(split_cfg["purge_bars_each_side"]),
            bar_minutes=int(split_cfg["bar_minutes"]),
        )
        order = int(row["source_order"])
        negative_kind = "easy" if order % 2 else "hard"
        plans.append(
            PositivePlan(
                sample_id=str(row["sample_id"]),
                event_id=str(row["event_id"]),
                source_order=order,
                symbol=str(row["symbol"]),
                direction=str(row["direction"]),
                source_path=str(row["source_path"]),
                split=split,
                negative_kind=negative_kind,
                core_bars=int(row["core_bars"]),
                core_start_i=core_start,
                core_end_i=core_end,
                pre_core_context_bars=int(row["pre_core_context_bars"]),
                post_core_context_bars=int(row["post_core_context_bars"]),
                window_start_i=window_start,
                window_end_i=window_end,
                dependency_end_i=dependency_end,
                core_start_time=utc(row["core_start_time"]).isoformat(),
                core_end_time=core_end_time.isoformat(),
                window_start_time=start_time.isoformat(),
                window_end_time=end_time.isoformat(),
                dependency_end_time=dependency_end_time.isoformat(),
                time_block=calendar_halfyear(core_end_time),
                box=dict(row["box"]),
                accepted_image_path=str(row["image_path"]),
                accepted_image_sha256=str(row["image_sha256"]),
            )
        )
    kinds = Counter(plan.negative_kind for plan in plans)
    expected = prereg["negative_sampling"]
    if int(expected["negative_per_positive"]) == 1 and kinds != Counter(
        hard=int(expected["preferred_hard_total"]),
        easy=int(expected["preferred_easy_total"]),
    ):
        raise OwnerYoloDatasetError(f"negative kind assignment drift: {kinds}")
    for split in ("train", "val"):
        subset = [plan for plan in plans if plan.split == split]
        by_kind = Counter(plan.negative_kind for plan in subset)
        if (
            not subset
            or set(by_kind) != {"hard", "easy"}
            or min(by_kind.values(), default=0) / len(subset) < 0.45
        ):
            raise OwnerYoloDatasetError(f"{split} hard/easy balance collapsed: {by_kind}")
    return plans


def _rolling_max(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) >= length:
        out[length - 1 :] = np.lib.stride_tricks.sliding_window_view(values, length).max(axis=1)
    return out


def _rolling_min(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) >= length:
        out[length - 1 :] = np.lib.stride_tricks.sliding_window_view(values, length).min(axis=1)
    return out


def _future_max(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) > length:
        windows = np.lib.stride_tricks.sliding_window_view(values[1:], length)
        out[: len(windows)] = windows.max(axis=1)
    return out


def _future_min(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) > length:
        windows = np.lib.stride_tricks.sliding_window_view(values[1:], length)
        out[: len(windows)] = windows.min(axis=1)
    return out


def negative_feature_masks(
    enriched: pd.DataFrame,
    *,
    core_len: int,
    prereg: Mapping[str, Any],
) -> dict[str, np.ndarray]:
    """Compute no-launch, dense-hard and clearly non-dense easy masks.

    Columns used: open/high/low/close, ATR14 and causal SMA/EMA 20/60/120.
    Core rolling windows use only rows through the proposed core end.  Negative
    labels additionally use close/high/low through core end +5; those rows may
    be inside the matched completed-history render but never cross holdout.
    """

    arrays = frame_arrays(enriched)
    open_, high, low, close, atr = (
        arrays["open"],
        arrays["high"],
        arrays["low"],
        arrays["close"],
        arrays["atr"],
    )
    mas = np.stack(
        [arrays[name] for name in ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120")],
        axis=1,
    )
    ma_bar_max = np.max(mas, axis=1)
    ma_bar_min = np.min(mas, axis=1)
    min_close_to_ma_bar = np.min(np.abs(close[:, None] - mas), axis=1)
    atr_anchor = np.r_[atr[2:], np.nan, np.nan]
    with np.errstate(divide="ignore", invalid="ignore"):
        ma_envelope = (
            _rolling_max(ma_bar_max, core_len) - _rolling_min(ma_bar_min, core_len)
        ) / atr_anchor
        ma_spread_end = (ma_bar_max - ma_bar_min) / atr_anchor
        max_body = _rolling_max(np.abs(close - open_), core_len) / atr_anchor
        candle_envelope = (
            _rolling_max(high, core_len) - _rolling_min(low, core_len)
        ) / atr_anchor
        minimum_close_to_ma = _rolling_min(min_close_to_ma_bar, core_len) / atr_anchor
        close2 = np.abs(np.r_[close[2:], np.nan, np.nan] - close) / atr_anchor
        close3 = np.abs(np.r_[close[3:], np.nan, np.nan, np.nan] - close) / atr_anchor
        close5 = np.abs(np.r_[close[5:], np.full(5, np.nan)] - close) / atr_anchor
        excursion = np.maximum(_future_max(high, 5) - close, close - _future_min(low, 5)) / atr_anchor

    no_cfg = prereg["negative_sampling"]["completed_no_launch_condition"]
    finite = np.isfinite(
        np.stack(
            [
                ma_envelope,
                ma_spread_end,
                max_body,
                candle_envelope,
                minimum_close_to_ma,
                close2,
                close3,
                close5,
                excursion,
            ],
            axis=1,
        )
    ).all(axis=1)
    no_launch = (
        finite
        & (atr_anchor > 0)
        & (close2 <= float(no_cfg["abs_close_progress_atr_max_core_plus_2"]))
        & (close3 <= float(no_cfg["abs_close_progress_atr_max_core_plus_3"]))
        & (close5 <= float(no_cfg["abs_close_progress_atr_max_core_plus_5"]))
        & (
            excursion
            <= float(no_cfg["two_sided_high_low_excursion_atr_max_core_plus_1_to_5"])
        )
    )
    hard_cfg = prereg["negative_sampling"]["hard_definition"]
    hard = (
        no_launch
        & (ma_envelope <= float(hard_cfg["ma_envelope_atr_max"]))
        & (ma_spread_end <= float(hard_cfg["ma_spread_end_atr_max"]))
        & (max_body <= float(hard_cfg["max_body_atr_max"]))
        & (candle_envelope <= float(hard_cfg["candle_envelope_atr_max"]))
        & (minimum_close_to_ma <= float(hard_cfg["minimum_close_to_ma_atr_max"]))
    )
    easy_cfg = prereg["negative_sampling"]["easy_definition"]
    easy = no_launch & (
        (ma_envelope >= float(easy_cfg["ma_envelope_atr_min_any"]))
        | (ma_spread_end >= float(easy_cfg["ma_spread_end_atr_min_any"]))
        | (minimum_close_to_ma >= float(easy_cfg["minimum_close_to_ma_atr_min_any"]))
        | (candle_envelope >= float(easy_cfg["candle_envelope_atr_min_any"]))
    )
    if np.any(hard & easy):
        raise OwnerYoloDatasetError("hard/easy negative masks overlap")
    return {
        "hard": hard,
        "easy": easy,
        "ma_envelope_atr": ma_envelope,
        "ma_spread_end_atr": ma_spread_end,
        "max_body_atr": max_body,
        "candle_envelope_atr": candle_envelope,
        "minimum_close_to_ma_atr": minimum_close_to_ma,
        "close2": close2,
        "close3": close3,
        "close5": close5,
        "excursion": excursion,
    }


def _mark_interval(mask: np.ndarray, start: int, end: int) -> None:
    start = max(0, int(start))
    end = min(len(mask) - 1, int(end))
    if start <= end:
        mask[start : end + 1] = True


def _contiguous(segment: np.ndarray, start: int, end: int) -> bool:
    return 0 <= start <= end < len(segment) and segment[start] == segment[end]


def select_source_negatives(
    enriched: pd.DataFrame,
    *,
    source_path: str,
    symbol: str,
    positives: Sequence[PositivePlan],
    strict_candidates: Sequence[Mapping[str, Any]],
    prereg: Mapping[str, Any],
    seed_negatives: Sequence[NegativePlan] = (),
) -> tuple[list[NegativePlan], dict[str, Any]]:
    """Select exact paired negatives without weakening any hard constraint.

    A pinned predecessor plan can seed slot 1 of an additive expansion.  Seed
    rows are not trusted merely because their JSON hash matches: this function
    recomputes the hard/easy masks, split, geometry and every occupied interval
    before marking them.  New slots are then selected without overlap.
    """

    times = pd.to_datetime(enriched["open_time"], utc=True)
    segment = enriched["_segment_id"].to_numpy(dtype=int)
    occupied = np.zeros(len(enriched), dtype=bool)
    guard = prereg["negative_sampling"]["positive_guard"]
    for row in strict_candidates:
        _mark_interval(
            occupied,
            int(row["source_core_start_i"]) - int(guard["before_core_bars"]),
            int(row["source_core_end_i"])
            + max(6, 5)
            + int(guard["after_dependency_end_bars"]),
        )
    for plan in positives:
        _mark_interval(
            occupied,
            plan.core_start_i - int(guard["before_core_bars"]),
            plan.dependency_end_i + int(guard["after_dependency_end_bars"]),
        )

    masks = {
        core_len: negative_feature_masks(enriched, core_len=core_len, prereg=prereg)
        for core_len in (4, 5)
    }
    pools: dict[tuple[int, str, str], np.ndarray] = {}
    cursors: Counter[tuple[int, str, str]] = Counter()
    selected: list[NegativePlan] = []
    split_cfg = prereg["split"]
    negative_cfg = prereg["negative_sampling"]
    separation = int(prereg["negative_sampling"]["negative_separation_bars"])
    pool_rejections: Counter[str] = Counter()

    def pool_for(core_len: int, kind: str, block: str) -> np.ndarray:
        key = (core_len, kind, block)
        if key not in pools:
            year = int(block[:4])
            half = int(block[-1])
            month = 1 if half == 1 else 7
            start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
            end = start + pd.DateOffset(months=6)
            in_block = (times >= start) & (times < end)
            indices = np.flatnonzero(masks[core_len][kind] & in_block.to_numpy(dtype=bool))
            rng = np.random.default_rng(
                stable_int(prereg["protocol"], source_path, core_len, kind, block)
            )
            pools[key] = rng.permutation(indices)
        return pools[key]

    template_by_id = {plan.sample_id: plan for plan in positives}
    seed_by_positive: dict[str, list[NegativePlan]] = defaultdict(list)
    seed_ids: set[str] = set()
    for raw_seed in sorted(
        seed_negatives,
        key=lambda value: (value.paired_positive_source_order, value.sample_id),
    ):
        if raw_seed.sample_id in seed_ids:
            raise OwnerYoloDatasetError(f"duplicate seed negative: {raw_seed.sample_id}")
        seed_ids.add(raw_seed.sample_id)
        template = template_by_id.get(raw_seed.paired_positive_sample_id)
        if template is None:
            raise OwnerYoloDatasetError(
                f"seed has no positive in source: {raw_seed.paired_positive_sample_id}"
            )
        required_equal = (
            raw_seed.paired_positive_source_order == template.source_order
            and raw_seed.paired_direction == template.direction
            and raw_seed.symbol == symbol
            and raw_seed.source_path == source_path
            and raw_seed.split == template.split
            and raw_seed.core_bars == template.core_bars
            and raw_seed.pre_core_context_bars == template.pre_core_context_bars
            and raw_seed.post_core_context_bars == template.post_core_context_bars
            and raw_seed.time_block == template.time_block
        )
        if not required_equal:
            raise OwnerYoloDatasetError(f"seed pairing contract drift: {raw_seed.sample_id}")
        core_end = int(raw_seed.core_end_i)
        core_start = core_end - template.core_bars + 1
        window_start = core_start - template.pre_core_context_bars
        window_end = core_end + template.post_core_context_bars
        dependency_end = max(window_end, core_end + 5)
        if (
            window_start != raw_seed.window_start_i
            or window_end != raw_seed.window_end_i
            or dependency_end != raw_seed.dependency_end_i
            or not _contiguous(segment, window_start, dependency_end)
        ):
            raise OwnerYoloDatasetError(f"seed geometry drift: {raw_seed.sample_id}")
        if not bool(masks[template.core_bars][raw_seed.negative_kind][core_end]):
            raise OwnerYoloDatasetError(f"seed feature mask drift: {raw_seed.sample_id}")
        assigned = interval_split(
            times.iloc[window_start],
            times.iloc[dependency_end],
            cutoff=split_cfg["cutoff"],
            purge_bars=int(split_cfg["purge_bars_each_side"]),
            bar_minutes=int(split_cfg["bar_minutes"]),
        )
        if template.split != "excluded" and assigned != template.split:
            raise OwnerYoloDatasetError(f"seed split drift: {raw_seed.sample_id}")
        guarded_start = max(0, window_start - separation)
        guarded_end = min(len(occupied) - 1, dependency_end + separation)
        if occupied[guarded_start : guarded_end + 1].any():
            raise OwnerYoloDatasetError(f"seed overlaps protected interval: {raw_seed.sample_id}")
        normalized = replace(raw_seed, pair_slot=len(seed_by_positive[template.sample_id]) + 1)
        seed_by_positive[template.sample_id].append(normalized)
        selected.append(normalized)
        _mark_interval(occupied, guarded_start, guarded_end)

    fallback_counts: Counter[str] = Counter()
    for template in sorted(positives, key=lambda plan: plan.source_order):
        target_kinds = [
            str(value)
            for value in negative_cfg.get(
                "target_kinds_per_positive", [template.negative_kind]
            )
        ]
        seeded = seed_by_positive.get(template.sample_id, [])
        remaining = list(target_kinds)
        for seed in seeded:
            try:
                remaining.remove(seed.negative_kind)
            except ValueError as exc:
                raise OwnerYoloDatasetError(
                    f"seed kind exceeds per-pair target: {seed.sample_id}"
                ) from exc
        for pair_slot, preferred_kind in enumerate(remaining, start=len(seeded) + 1):
            found: NegativePlan | None = None
            attempted: list[tuple[tuple[int, str, str], int]] = []
            alternate = "easy" if preferred_kind == "hard" else "hard"
            for actual_kind in (preferred_kind, alternate):
                key = (template.core_bars, actual_kind, template.time_block)
                pool = pool_for(*key)
                attempted.append((key, len(pool)))
                while cursors[key] < len(pool):
                    core_end = int(pool[cursors[key]])
                    cursors[key] += 1
                    core_start = core_end - template.core_bars + 1
                    window_start = core_start - template.pre_core_context_bars
                    window_end = core_end + template.post_core_context_bars
                    dependency_end = max(window_end, core_end + 5)
                    if not _contiguous(segment, window_start, dependency_end):
                        pool_rejections["source_gap_or_bounds"] += 1
                        continue
                    assigned = interval_split(
                        times.iloc[window_start],
                        times.iloc[dependency_end],
                        cutoff=split_cfg["cutoff"],
                        purge_bars=int(split_cfg["purge_bars_each_side"]),
                        bar_minutes=int(split_cfg["bar_minutes"]),
                    )
                    if template.split != "excluded" and assigned != template.split:
                        pool_rejections["split_mismatch"] += 1
                        continue
                    guarded_start = max(0, window_start - separation)
                    guarded_end = min(len(occupied) - 1, dependency_end + separation)
                    if occupied[guarded_start : guarded_end + 1].any():
                        pool_rejections["protected_or_reused"] += 1
                        continue
                    features = masks[template.core_bars]
                    sample_id = stable_id(
                        prereg["protocol"],
                        source_path,
                        template.sample_id,
                        pair_slot,
                        core_end,
                        template.core_bars,
                        template.pre_core_context_bars,
                        template.post_core_context_bars,
                        actual_kind,
                    )
                    found = NegativePlan(
                        sample_id=sample_id,
                        paired_positive_sample_id=template.sample_id,
                        paired_positive_source_order=template.source_order,
                        pair_slot=pair_slot,
                        paired_direction=template.direction,
                        symbol=symbol,
                        source_path=source_path,
                        split=template.split,
                        negative_kind=actual_kind,
                        core_bars=template.core_bars,
                        core_end_i=core_end,
                        pre_core_context_bars=template.pre_core_context_bars,
                        post_core_context_bars=template.post_core_context_bars,
                        window_start_i=window_start,
                        window_end_i=window_end,
                        dependency_end_i=dependency_end,
                        core_end_time=times.iloc[core_end].isoformat(),
                        window_start_time=times.iloc[window_start].isoformat(),
                        window_end_time=times.iloc[window_end].isoformat(),
                        dependency_end_time=times.iloc[dependency_end].isoformat(),
                        time_block=template.time_block,
                        ma_envelope_atr=float(features["ma_envelope_atr"][core_end]),
                        ma_spread_end_atr=float(features["ma_spread_end_atr"][core_end]),
                        max_body_atr=float(features["max_body_atr"][core_end]),
                        candle_envelope_atr=float(features["candle_envelope_atr"][core_end]),
                        minimum_close_to_ma_atr=float(
                            features["minimum_close_to_ma_atr"][core_end]
                        ),
                        abs_close_progress_atr_core_plus_2=float(
                            features["close2"][core_end]
                        ),
                        abs_close_progress_atr_core_plus_3=float(
                            features["close3"][core_end]
                        ),
                        abs_close_progress_atr_core_plus_5=float(
                            features["close5"][core_end]
                        ),
                        two_sided_excursion_atr_core_plus_1_to_5=float(
                            features["excursion"][core_end]
                        ),
                    )
                    _mark_interval(occupied, guarded_start, guarded_end)
                    if actual_kind != preferred_kind:
                        fallback_counts[f"{preferred_kind}_to_{actual_kind}"] += 1
                    break
                if found is not None:
                    break
            if found is None:
                raise OwnerYoloDatasetError(
                    "insufficient negative capacity without relaxing constraints: "
                    f"{source_path} attempted={attempted} positive={template.sample_id} "
                    f"slot={pair_slot} preferred={preferred_kind}"
                )
            selected.append(found)

    return selected, {
        "source_path": source_path,
        "symbol": symbol,
        "positive_rows": len(positives),
        "strict_candidates_guarded": len(strict_candidates),
        "negative_rows": len(selected),
        "seed_negative_rows": len(seed_negatives),
        "negative_kinds": dict(Counter(plan.negative_kind for plan in selected)),
        "splits": dict(Counter(plan.split for plan in selected)),
        "pool_rejections": dict(pool_rejections),
        "safe_kind_fallbacks": dict(fallback_counts),
        "pool_keys": len(pools),
        "pool_rows_total": sum(len(values) for values in pools.values()),
    }


def _plan_paths(results_path: Path) -> tuple[Path, Path, Path, Path]:
    return (
        results_path / "positive_plan.jsonl",
        results_path / "negative_plan.jsonl",
        results_path / "negative_source_audit.jsonl",
        results_path / "plan_receipt.json",
    )


def plan_dataset(
    *,
    prereg_path: Path = DEFAULT_PREREG,
    results_path: Path = DEFAULT_RESULTS,
) -> dict[str, Any]:
    """Select all negatives and freeze the full no-image plan."""

    builder_commit = verify_builder_committed((MODULE_PATH, SCRIPT_PATH, prereg_path))
    prereg = load_preregistration(prereg_path)
    positive_rows = load_positive_rows(prereg)
    positives = plan_positives(positive_rows, prereg)
    positive_plan_path, negative_plan_path, audit_path, receipt_path = _plan_paths(results_path)
    if any(path.exists() for path in (positive_plan_path, negative_plan_path, audit_path, receipt_path)):
        raise FileExistsError(f"refusing to overwrite existing plan under {results_path}")

    autofill_prereg = read_json(_repo_path(prereg["positive_source"]["preregistration_path"]))
    references, reference_audits = load_reference_profiles(autofill_prereg)
    if sum(int(row["holdout_ohlcv_rows_materialized"]) for row in reference_audits) != 0:
        raise AssertionError("reference profile loading touched holdout")

    by_source: dict[str, list[PositivePlan]] = defaultdict(list)
    for plan in positives:
        by_source[plan.source_path].append(plan)
    seed_by_source: dict[str, list[NegativePlan]] = defaultdict(list)
    seed_cfg = prereg["negative_sampling"].get("seed_plan")
    seed_rows_total = 0
    seed_plan_sha256: str | None = None
    if seed_cfg:
        seed_path = _repo_path(seed_cfg["path"])
        _verify_pinned(seed_path, str(seed_cfg["sha256"]), "negative seed plan")
        seed_plan_sha256 = sha256_file(seed_path)
        seed_rows = [_negative_from_dict(row) for row in read_jsonl(seed_path)]
        seed_rows_total = len(seed_rows)
        if seed_rows_total != int(seed_cfg["rows"]):
            raise OwnerYoloDatasetError("negative seed row count drift")
        for seed in seed_rows:
            seed_by_source[seed.source_path].append(seed)
        if set(seed_by_source) - set(by_source):
            raise OwnerYoloDatasetError("negative seed plan contains an unknown source")
    negatives: list[NegativePlan] = []
    source_audits: list[dict[str, Any]] = []
    holdout_rows = 0
    total = len(by_source)
    for number, (source_path, source_positives) in enumerate(sorted(by_source.items()), 1):
        frame, source_audit = read_preholdout_prefix(
            _repo_path(source_path), end_exclusive=HOLDOUT_START
        )
        holdout_rows += int(source_audit["holdout_ohlcv_rows_materialized"])
        enriched = add_candidate_features(frame)
        strict, scan_counts = scan_source(
            frame,
            source_path=source_path,
            symbol=source_positives[0].symbol,
            prereg=autofill_prereg,
            references=references,
        )
        strict_ids = {
            (str(row["direction"]), int(row["source_core_end_i"])) for row in strict
        }
        missing = [
            plan.sample_id
            for plan in source_positives
            if (plan.direction, plan.core_end_i) not in strict_ids
        ]
        if missing:
            raise OwnerYoloDatasetError(
                f"accepted positives not rediscovered by frozen strict gate: {missing[:3]}"
            )
        selected, negative_audit = select_source_negatives(
            enriched,
            source_path=source_path,
            symbol=source_positives[0].symbol,
            positives=source_positives,
            strict_candidates=strict,
            prereg=prereg,
            seed_negatives=seed_by_source.get(source_path, ()),
        )
        negatives.extend(selected)
        source_audits.append(
            {
                **negative_audit,
                "rows_materialized": int(source_audit["rows_materialized"]),
                "holdout_ohlcv_rows_materialized": int(
                    source_audit["holdout_ohlcv_rows_materialized"]
                ),
                "strict_scan_counts": scan_counts,
            }
        )
        if number == 1 or number % 10 == 0 or number == total:
            print(
                f"negative plan {number:03d}/{total} {source_positives[0].symbol:<22} "
                f"pos={len(source_positives):>3} strict={len(strict):>3} "
                f"neg_total={len(negatives):>5}",
                flush=True,
            )

    if holdout_rows != 0:
        raise AssertionError("negative planning materialized holdout OHLCV")
    negative_cfg = prereg["negative_sampling"]
    target = int(negative_cfg["target"])
    per_positive = int(negative_cfg["negative_per_positive"])
    if len(negatives) != target:
        raise OwnerYoloDatasetError(
            f"negative plan row target drift: {len(negatives)} != {target}"
        )
    pair_counts = Counter(plan.paired_positive_sample_id for plan in negatives)
    if set(pair_counts) != {plan.sample_id for plan in positives} or set(
        pair_counts.values()
    ) != {per_positive}:
        raise OwnerYoloDatasetError("negative pairing multiplicity drift")
    slots_by_pair: dict[str, set[int]] = defaultdict(set)
    for plan in negatives:
        slots_by_pair[plan.paired_positive_sample_id].add(int(plan.pair_slot))
    expected_slots = set(range(1, per_positive + 1))
    if any(slots != expected_slots for slots in slots_by_pair.values()):
        raise OwnerYoloDatasetError("negative pair-slot coverage drift")
    negative_ids = [plan.sample_id for plan in negatives]
    if len(negative_ids) != len(set(negative_ids)):
        raise OwnerYoloDatasetError("duplicate negative identities")
    actual_kinds = Counter(plan.negative_kind for plan in negatives)
    if actual_kinds["hard"] / len(negatives) < float(
        negative_cfg["minimum_hard_share_overall"]
    ):
        raise OwnerYoloDatasetError(f"overall hard-negative share too low: {actual_kinds}")
    for split in ("train", "val"):
        split_kinds = Counter(
            plan.negative_kind for plan in negatives if plan.split == split
        )
        minimum = float(negative_cfg[f"minimum_hard_share_{split}"])
        if not split_kinds or split_kinds["hard"] / sum(split_kinds.values()) < minimum:
            raise OwnerYoloDatasetError(
                f"{split} hard-negative share too low: {split_kinds}"
            )

    write_jsonl(positive_plan_path, (asdict(plan) for plan in positives))
    write_jsonl(negative_plan_path, (asdict(plan) for plan in negatives))
    write_jsonl(audit_path, source_audits)
    positive_splits = Counter(plan.split for plan in positives)
    negative_splits = Counter(plan.split for plan in negatives)
    receipt = {
        "schema_version": 1,
        "experiment_id": prereg["experiment_id"],
        "protocol": prereg["protocol"],
        "builder_commit": builder_commit,
        "preregistration_sha256": sha256_file(prereg_path),
        "positive_source_manifest_sha256": prereg["positive_source"]["manifest_sha256"],
        "positive_rows": len(positives),
        "negative_rows": len(negatives),
        "negative_per_positive": per_positive,
        "seed_negative_rows": seed_rows_total,
        "seed_negative_plan_sha256": seed_plan_sha256,
        "positive_splits": dict(positive_splits),
        "negative_splits": dict(negative_splits),
        "positive_directions": dict(Counter(plan.direction for plan in positives)),
        "negative_kinds": dict(Counter(plan.negative_kind for plan in negatives)),
        "negative_kinds_by_split": {
            split: dict(
                Counter(plan.negative_kind for plan in negatives if plan.split == split)
            )
            for split in ("train", "val", "excluded")
        },
        "unique_sources": len(by_source),
        "unique_symbols": len({plan.symbol for plan in positives}),
        "strict_candidates_guarded": sum(
            int(row["strict_candidates_guarded"]) for row in source_audits
        ),
        "source_rows_materialized": sum(
            int(row["rows_materialized"]) for row in source_audits
        ),
        "holdout_ohlcv_rows_materialized": 0,
        "positive_plan_sha256": sha256_file(positive_plan_path),
        "negative_plan_sha256": sha256_file(negative_plan_path),
        "negative_source_audit_sha256": sha256_file(audit_path),
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(receipt_path, receipt)
    return receipt


def _positive_from_dict(row: Mapping[str, Any]) -> PositivePlan:
    return PositivePlan(**dict(row))


def _negative_from_dict(row: Mapping[str, Any]) -> NegativePlan:
    payload = dict(row)
    payload.setdefault("pair_slot", 0)
    return NegativePlan(**payload)


def _label_text(direction: str, box: Mapping[str, Any]) -> str:
    class_id = CLASS_IDS[direction]
    values = [
        float(box["cx_norm"]),
        float(box["cy_norm"]),
        float(box["w_norm"]),
        float(box["h_norm"]),
    ]
    if not all(0.0 < value <= 1.0 for value in values):
        raise OwnerYoloDatasetError("accepted normalized box is outside (0,1]")
    return f"{class_id} " + " ".join(f"{value:.9f}" for value in values) + "\n"


def _write_sample(
    building: Path,
    *,
    split: str,
    stem: str,
    image: np.ndarray,
    label: str,
) -> tuple[str, str, str, str]:
    image_rel = Path("images") / split / f"{stem}.png"
    label_rel = Path("labels") / split / f"{stem}.txt"
    image_path, label_path = building / image_rel, building / label_rel
    image_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = encode_png(image)
    label_bytes = label.encode("utf-8")
    temporary = image_path.with_suffix(".png.part")
    temporary.write_bytes(image_bytes)
    os.replace(temporary, image_path)
    label_path.write_bytes(label_bytes)
    return (
        str(image_rel),
        str(label_rel),
        hashlib.sha256(image_bytes).hexdigest(),
        hashlib.sha256(label_bytes).hexdigest(),
    )


def _preview_contact_sheet(
    items: Sequence[tuple[np.ndarray, str]], *, columns: int = 5
) -> np.ndarray:
    if not items:
        raise OwnerYoloDatasetError("preview has no images")
    thumb_w, thumb_h, caption_h = 480, 278, 34
    rows = math.ceil(len(items) / columns)
    canvas = np.full(
        (rows * (thumb_h + caption_h), columns * thumb_w, 3), 245, dtype=np.uint8
    )
    for index, (image, caption) in enumerate(items):
        thumb = cv2.resize(image, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        row, column = divmod(index, columns)
        x, y = column * thumb_w, row * (thumb_h + caption_h)
        canvas[y : y + thumb_h, x : x + thumb_w] = thumb
        cv2.putText(
            canvas,
            caption[:58],
            (x + 8, y + thumb_h + 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )
    return canvas


def _write_preview_html(
    path: Path,
    *,
    positives: Sequence[Mapping[str, Any]],
    negatives: Sequence[Mapping[str, Any]],
    dataset_path: Path,
) -> None:
    cards: list[str] = []
    for row in [*positives, *negatives]:
        image_path = dataset_path / str(row["image_path"])
        source = os.path.relpath(image_path, path.parent)
        if row["sample_kind"] == "positive":
            box = row["box"]
            left = (float(box["cx_norm"]) - float(box["w_norm"]) / 2) * 100
            top = (float(box["cy_norm"]) - float(box["h_norm"]) / 2) * 100
            width = float(box["w_norm"]) * 100
            height = float(box["h_norm"]) * 100
            overlay = (
                f'<span class="box" style="left:{left:.5f}%;top:{top:.5f}%;'
                f'width:{width:.5f}%;height:{height:.5f}%"></span>'
            )
            detail = f"{row['direction']} · label 单独存放"
        else:
            overlay = ""
            detail = f"{row['negative_kind']} negative · 空标签"
        cards.append(
            '<article><div class="frame">'
            f'<img src="{html.escape(source)}" loading="lazy">{overlay}</div>'
            f'<h3>{html.escape(str(row["symbol"]))}</h3>'
            f'<p>{html.escape(detail)} · {html.escape(str(row["split"]))}</p></article>'
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>模型实际输入抽样</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#111;color:#eee;margin:0}header{position:sticky;top:0;background:#181818;padding:16px 22px;z-index:2}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px;padding:18px}article{background:#202020;border:1px solid #444;border-radius:10px;padding:10px}.frame{position:relative;line-height:0}.frame img{width:100%;height:auto}.box{position:absolute;border:3px solid #f22;box-sizing:border-box;pointer-events:none}h3{margin:9px 0 2px;font-size:15px}p{margin:0;color:#bbb;font-size:13px}</style></head><body>
<header><h1>模型实际输入抽样：50 正 + 50 负</h1><p>底图均为数据集实际无框 PNG；正例红框仅由 HTML 根据独立 YOLO 标签叠加，负例标签为空。</p></header><main>"""
        + "".join(cards)
        + "</main></body></html>\n",
        encoding="utf-8",
    )


def _verify_full_dataset(
    dataset_path: Path,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    image_hashes: set[str] = set()
    positive_labels = 0
    negative_empty_labels = 0
    exact_red_total = 0
    split_counts: Counter[str] = Counter()
    for row in rows:
        image_path = dataset_path / str(row["image_path"])
        label_path = dataset_path / str(row["label_path"])
        if sha256_file(image_path) != str(row["image_sha256"]):
            raise OwnerYoloDatasetError(f"final image SHA drift: {image_path}")
        if sha256_file(label_path) != str(row["label_sha256"]):
            raise OwnerYoloDatasetError(f"final label SHA drift: {label_path}")
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or image.shape != (SOURCE_HEIGHT, SOURCE_WIDTH, 3):
            raise OwnerYoloDatasetError(f"final image dimensions drift: {image_path}")
        exact_red = int(np.all(image == EXACT_OVERLAY_RED, axis=2).sum())
        exact_red_total += exact_red
        if exact_red:
            raise OwnerYoloDatasetError(f"overlay red leaked into model input: {image_path}")
        image_hashes.add(str(row["image_sha256"]))
        label = label_path.read_text(encoding="utf-8")
        if row["sample_kind"] == "positive":
            parts = label.split()
            if len(parts) != 5 or int(parts[0]) not in (0, 1):
                raise OwnerYoloDatasetError(f"invalid positive YOLO label: {label_path}")
            values = [float(value) for value in parts[1:]]
            if not all(0.0 < value <= 1.0 for value in values):
                raise OwnerYoloDatasetError(f"invalid positive box range: {label_path}")
            positive_labels += 1
        else:
            if label != "":
                raise OwnerYoloDatasetError(f"negative label is not empty: {label_path}")
            negative_empty_labels += 1
        split_counts[f"{row['split']}/{row['sample_kind']}"] += 1
    if len(image_hashes) != len(rows):
        raise OwnerYoloDatasetError("duplicate image pixels across final dataset")
    return {
        "files_checked": len(rows) * 2,
        "images_checked": len(rows),
        "dimensions_1280x742": len(rows),
        "unique_image_hashes": len(image_hashes),
        "positive_labels_parsed": positive_labels,
        "negative_empty_labels": negative_empty_labels,
        "exact_overlay_red_pixels_in_model_inputs": exact_red_total,
        "split_counts": dict(split_counts),
        "passed": True,
    }


def _verify_lineage_baseline(
    prereg: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Require predecessor positive and seed-negative bytes to remain exact."""

    cfg = prereg.get("lineage_baseline")
    if not cfg:
        return {"configured": False, "rows_matched": 0, "passed": True}
    manifest_path = _repo_path(cfg["manifest_path"])
    _verify_pinned(manifest_path, str(cfg["manifest_sha256"]), "lineage baseline manifest")
    baseline_rows = read_jsonl(manifest_path)
    if len(baseline_rows) != int(cfg["rows"]):
        raise OwnerYoloDatasetError("lineage baseline row count drift")
    baseline = {
        (str(row["sample_kind"]), str(row["source_sample_id"])): row
        for row in baseline_rows
    }
    if len(baseline) != len(baseline_rows):
        raise OwnerYoloDatasetError("duplicate lineage baseline identity")
    current = {
        (str(row["sample_kind"]), str(row["source_sample_id"])): row for row in rows
    }
    missing = set(baseline) - set(current)
    if missing:
        raise OwnerYoloDatasetError(f"lineage baseline rows missing: {list(missing)[:3]}")
    mismatches: list[tuple[str, str]] = []
    for key, old in baseline.items():
        new = current[key]
        if (
            str(old["image_sha256"]) != str(new["image_sha256"])
            or str(old["label_sha256"]) != str(new["label_sha256"])
        ):
            mismatches.append(key)
    if mismatches:
        raise OwnerYoloDatasetError(
            f"lineage baseline image/label bytes drift: {mismatches[:3]}"
        )
    kinds = Counter(key[0] for key in baseline)
    return {
        "configured": True,
        "manifest_path": _relative(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "rows_matched": len(baseline),
        "positive_rows_matched": int(kinds["positive"]),
        "negative_rows_matched": int(kinds["negative"]),
        "image_and_label_sha_matches": len(baseline),
        "passed": True,
    }


def build_dataset(
    *,
    prereg_path: Path = DEFAULT_PREREG,
    dataset_path: Path = DEFAULT_DATASET,
    results_path: Path = DEFAULT_RESULTS,
) -> dict[str, Any]:
    """Materialize all clean images and labels from the frozen plan."""

    builder_commit = verify_builder_committed((MODULE_PATH, SCRIPT_PATH, prereg_path))
    prereg = load_preregistration(prereg_path)
    positive_plan_path, negative_plan_path, _, plan_receipt_path = _plan_paths(results_path)
    for path in (positive_plan_path, negative_plan_path, plan_receipt_path):
        if not path.is_file():
            raise OwnerYoloDatasetError(f"run --plan-only first; missing {path}")
    plan_receipt = read_json(plan_receipt_path)
    if plan_receipt.get("builder_commit") != builder_commit:
        raise OwnerYoloDatasetError("plan builder commit differs from materializer")
    if plan_receipt.get("preregistration_sha256") != sha256_file(prereg_path):
        raise OwnerYoloDatasetError("plan preregistration SHA drift")
    if plan_receipt.get("positive_plan_sha256") != sha256_file(positive_plan_path):
        raise OwnerYoloDatasetError("positive plan SHA drift")
    if plan_receipt.get("negative_plan_sha256") != sha256_file(negative_plan_path):
        raise OwnerYoloDatasetError("negative plan SHA drift")

    positives = [_positive_from_dict(row) for row in read_jsonl(positive_plan_path)]
    negatives = [_negative_from_dict(row) for row in read_jsonl(negative_plan_path)]
    final_dataset = dataset_path.resolve()
    building = final_dataset.with_name(f"{final_dataset.name}.building")
    if final_dataset.exists():
        raise FileExistsError(f"refusing to overwrite final dataset: {final_dataset}")
    for split in ("train", "val", "excluded"):
        (building / "images" / split).mkdir(parents=True, exist_ok=True)
        (building / "labels" / split).mkdir(parents=True, exist_ok=True)

    partial_path = building / "manifest.partial.jsonl"
    rendered = read_jsonl(partial_path) if partial_path.exists() else []
    rendered_by_id = {str(row["sample_id"]): row for row in rendered}
    if len(rendered_by_id) != len(rendered):
        raise OwnerYoloDatasetError("partial manifest contains duplicate sample IDs")
    for row in rendered:
        image_path = building / str(row["image_path"])
        label_path = building / str(row["label_path"])
        if not image_path.is_file() or sha256_file(image_path) != str(row["image_sha256"]):
            raise OwnerYoloDatasetError(f"partial image drift: {image_path}")
        if not label_path.is_file() or sha256_file(label_path) != str(row["label_sha256"]):
            raise OwnerYoloDatasetError(f"partial label drift: {label_path}")

    by_source_positive: dict[str, list[PositivePlan]] = defaultdict(list)
    by_source_negative: dict[str, list[NegativePlan]] = defaultdict(list)
    for plan in positives:
        if f"p:{plan.sample_id}" not in rendered_by_id:
            by_source_positive[plan.source_path].append(plan)
    for plan in negatives:
        if f"n:{plan.sample_id}" not in rendered_by_id:
            by_source_negative[plan.source_path].append(plan)
    source_paths = sorted(set(by_source_positive) | set(by_source_negative))
    overlay_parity = 0
    clean_red_pixels = 0
    with partial_path.open("a", encoding="utf-8") as handle:
        for source_number, source_path in enumerate(source_paths, 1):
            frame, audit = read_preholdout_prefix(
                _repo_path(source_path), end_exclusive=HOLDOUT_START
            )
            if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
                raise AssertionError("render reload materialized holdout OHLCV")
            enriched = add_candidate_features(frame)
            for plan in sorted(
                by_source_positive.get(source_path, []), key=lambda value: value.source_order
            ):
                window = enriched.iloc[plan.window_start_i : plan.window_end_i + 1].reset_index(
                    drop=True
                )
                expected = plan.pre_core_context_bars + plan.core_bars + plan.post_core_context_bars
                if len(window) != expected:
                    raise OwnerYoloDatasetError(f"positive window length drift: {plan.sample_id}")
                clean, _ = render_chart(
                    window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, out_path=None
                )
                red_pixels = int(np.all(clean == EXACT_OVERLAY_RED, axis=2).sum())
                clean_red_pixels += red_pixels
                if red_pixels:
                    raise OwnerYoloDatasetError(f"clean positive contains overlay red: {plan.sample_id}")
                overlay = clean.copy()
                draw_box(overlay, plan.box)
                if hashlib.sha256(encode_png(overlay)).hexdigest() != plan.accepted_image_sha256:
                    raise OwnerYoloDatasetError(
                        f"accepted boxed image is not reproduced exactly: {plan.sample_id}"
                    )
                overlay_parity += 1
                stem = f"pos_{plan.source_order:05d}_{plan.sample_id}"
                image_rel, label_rel, image_sha, label_sha = _write_sample(
                    building,
                    split=plan.split,
                    stem=stem,
                    image=clean,
                    label=_label_text(plan.direction, plan.box),
                )
                row = {
                    **asdict(plan),
                    "sample_id": f"p:{plan.sample_id}",
                    "source_sample_id": plan.sample_id,
                    "sample_kind": "positive",
                    "class_id": CLASS_IDS[plan.direction],
                    "class_name": prereg["renderer"]["classes"][str(CLASS_IDS[plan.direction])],
                    "image_path": image_rel,
                    "label_path": label_rel,
                    "image_sha256": image_sha,
                    "label_sha256": label_sha,
                    "accepted_overlay_parity": True,
                    "exact_overlay_red_pixels_in_model_input": 0,
                    "training_eligible": False,
                    "production_eligible": False,
                }
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                rendered_by_id[str(row["sample_id"])] = row

            for plan in sorted(
                by_source_negative.get(source_path, []),
                key=lambda value: value.paired_positive_source_order,
            ):
                window = enriched.iloc[plan.window_start_i : plan.window_end_i + 1].reset_index(
                    drop=True
                )
                expected = plan.pre_core_context_bars + plan.core_bars + plan.post_core_context_bars
                if len(window) != expected:
                    raise OwnerYoloDatasetError(f"negative window length drift: {plan.sample_id}")
                clean, _ = render_chart(
                    window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, out_path=None
                )
                red_pixels = int(np.all(clean == EXACT_OVERLAY_RED, axis=2).sum())
                clean_red_pixels += red_pixels
                if red_pixels:
                    raise OwnerYoloDatasetError(f"clean negative contains overlay red: {plan.sample_id}")
                stem = (
                    f"neg_{plan.negative_kind[0]}_{plan.paired_positive_source_order:05d}_"
                    f"s{plan.pair_slot}_{plan.sample_id}"
                )
                image_rel, label_rel, image_sha, label_sha = _write_sample(
                    building,
                    split=plan.split,
                    stem=stem,
                    image=clean,
                    label="",
                )
                row = {
                    **asdict(plan),
                    "sample_id": f"n:{plan.sample_id}",
                    "source_sample_id": plan.sample_id,
                    "sample_kind": "negative",
                    "class_id": None,
                    "class_name": None,
                    "image_path": image_rel,
                    "label_path": label_rel,
                    "image_sha256": image_sha,
                    "label_sha256": label_sha,
                    "accepted_overlay_parity": None,
                    "exact_overlay_red_pixels_in_model_input": 0,
                    "training_eligible": False,
                    "production_eligible": False,
                }
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                rendered_by_id[str(row["sample_id"])] = row
            if source_number == 1 or source_number % 10 == 0 or source_number == len(source_paths):
                print(
                    f"render {source_number:03d}/{len(source_paths)} {source_path:<75} "
                    f"complete={len(rendered_by_id):>5}/{len(positives) + len(negatives)}",
                    flush=True,
                )

    if len(rendered_by_id) != len(positives) + len(negatives):
        raise OwnerYoloDatasetError("materialization row count drift")
    rows = sorted(
        rendered_by_id.values(),
        key=lambda row: (
            int(row.get("source_order", row.get("paired_positive_source_order", 0))),
            str(row["sample_kind"]),
            int(row.get("pair_slot", 0)),
        ),
    )
    manifest_path = building / "manifest.jsonl"
    write_jsonl(manifest_path, rows)
    data_yaml = building / "data.yaml"
    data_yaml.write_text(
        f"path: {final_dataset}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: dense_long\n"
        "  1: dense_short\n",
        encoding="utf-8",
    )
    dataset_summary = {
        "schema_version": 1,
        "experiment_id": prereg["experiment_id"],
        "protocol": prereg["protocol"],
        "builder_commit": builder_commit,
        "positive_rows_total": len(positives),
        "negative_rows_total": len(negatives),
        "training_images_exposed_by_data_yaml": sum(
            row["split"] in {"train", "val"} for row in rows
        ),
        "excluded_lineage_images": sum(row["split"] == "excluded" for row in rows),
        "counts": dict(
            Counter(f"{row['split']}/{row['sample_kind']}" for row in rows)
        ),
        "negative_kinds": dict(
            Counter(
                str(row["negative_kind"])
                for row in rows
                if row["sample_kind"] == "negative"
            )
        ),
        "positive_directions": dict(
            Counter(
                str(row["direction"])
                for row in rows
                if row["sample_kind"] == "positive"
            )
        ),
        "accepted_positive_overlay_parity": sum(
            row.get("accepted_overlay_parity") is True for row in rows
        ),
        "exact_overlay_red_pixels_in_model_inputs": sum(
            int(row["exact_overlay_red_pixels_in_model_input"]) for row in rows
        ),
        "manifest_sha256": sha256_file(manifest_path),
        "data_yaml_sha256": sha256_file(data_yaml),
        "holdout_ohlcv_rows_materialized": 0,
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(building / "build_summary.json", dataset_summary)
    partial_path.unlink()
    building.rename(final_dataset)

    final_rows = read_jsonl(final_dataset / "manifest.jsonl")
    full_qa = _verify_full_dataset(final_dataset, final_rows)
    lineage_baseline = _verify_lineage_baseline(prereg, final_rows)
    results_path.mkdir(parents=True, exist_ok=True)

    positive_final = [row for row in final_rows if row["sample_kind"] == "positive"]
    negative_final = [row for row in final_rows if row["sample_kind"] == "negative"]
    indices = np.linspace(0, len(positive_final) - 1, 50, dtype=int)
    preview_positive_rows = [positive_final[int(index)] for index in indices]
    preview_negative_rows = [
        next(
            row
            for row in negative_final
            if int(row["paired_positive_source_order"])
            == int(positive_final[int(index)]["source_order"])
        )
        for index in indices
    ]
    positive_preview: list[tuple[np.ndarray, str]] = []
    negative_preview: list[tuple[np.ndarray, str]] = []
    for row in preview_positive_rows:
        image = cv2.imread(str(final_dataset / row["image_path"]), cv2.IMREAD_COLOR)
        overlay = image.copy()
        draw_box(overlay, row["box"])
        positive_preview.append(
            (overlay, f"#{int(row['source_order']):05d} {row['symbol']} {row['direction']}")
        )
    for row in preview_negative_rows:
        image = cv2.imread(str(final_dataset / row["image_path"]), cv2.IMREAD_COLOR)
        negative_preview.append(
            (
                image,
                f"pair#{int(row['paired_positive_source_order']):05d} "
                f"{row['symbol']} {row['negative_kind']}",
            )
        )
    positive_contact = results_path / "actual_positive_inputs_sample50_with_label_overlay.jpg"
    negative_contact = results_path / "actual_negative_inputs_sample50.jpg"
    cv2.imwrite(str(positive_contact), _preview_contact_sheet(positive_preview), [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(negative_contact), _preview_contact_sheet(negative_preview), [cv2.IMWRITE_JPEG_QUALITY, 94])
    preview_html = results_path / "actual_model_inputs_sample100.html"
    _write_preview_html(
        preview_html,
        positives=preview_positive_rows,
        negatives=preview_negative_rows,
        dataset_path=final_dataset,
    )

    null_rows = [positive_final[int(index)] for index in np.linspace(0, len(positive_final) - 1, 1000, dtype=int)]
    shifted_matches = 0
    for index, row in enumerate(null_rows):
        image = cv2.imread(str(final_dataset / row["image_path"]), cv2.IMREAD_COLOR)
        wrong_box = null_rows[(index + 1) % len(null_rows)]["box"]
        overlay = image.copy()
        draw_box(overlay, wrong_box)
        shifted_matches += (
            hashlib.sha256(encode_png(overlay)).hexdigest()
            == str(row["accepted_image_sha256"])
        )

    receipt = {
        **dataset_summary,
        "dataset_path": _relative(final_dataset),
        "dataset_bytes": sum(
            path.stat().st_size for path in final_dataset.rglob("*") if path.is_file()
        ),
        "full_qa": full_qa,
        "lineage_baseline": lineage_baseline,
        "shifted_box_null": {
            "rows": len(null_rows),
            "cyclic_next_box_overlay_sha_matches": int(shifted_matches),
            "passed": shifted_matches == 0,
        },
        "positive_contact_sheet": _relative(positive_contact),
        "positive_contact_sheet_sha256": sha256_file(positive_contact),
        "negative_contact_sheet": _relative(negative_contact),
        "negative_contact_sheet_sha256": sha256_file(negative_contact),
        "actual_model_inputs_html": _relative(preview_html),
        "actual_model_inputs_html_sha256": sha256_file(preview_html),
    }
    write_json(results_path / "dataset_build_receipt.json", receipt)
    return receipt
