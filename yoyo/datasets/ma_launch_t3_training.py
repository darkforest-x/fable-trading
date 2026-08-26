"""Build the Owner-authorized 15m t-3 weak-label YOLO dataset.

The positive source is the hash-pinned 10,000-row completed-launch candidate
union.  For each source selection bar ``t`` the training core ends at ``t-3``;
the core is 4--7 bars and the rendered input is 14--22 bars ending after 3--5
confirmation bars.  Input pixels therefore use OHLCV only through at most
``t+2``.  The completed candidate label may use the already-recorded path
through ``t+11``; that future is label provenance and is never rendered.

Negative selection reads ``open/high/low/close`` through the pre-holdout prefix.
It computes causal SMA/EMA 20/60/120 and Wilder ATR14.  A negative's label uses
the 12-bar path beginning at pseudo-``t`` to require no completed move, while
its input uses only the same 14--22-bar, 3--5-confirmation rendering contract.
All candidates, including purged rows, create same-source exclusion guards.

This is explicitly an Owner-authorized weak-label experiment, not Gold data.
Every artifact remains ``production_eligible=false`` and this module never
touches model pointers, forward state, deployment state or order state.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
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
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.layers.l1_detection.render import render_chart


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-15m-ma-launch-t3-yolo10000-v1"
DEFAULT_PREREG = (
    ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
)
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_t3_10000_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
CLASS_IDS = {"LONG": 0, "SHORT": 1}


class T3DatasetError(ValueError):
    """Fail-closed weak-label dataset construction error."""


@dataclass(frozen=True)
class Geometry:
    """One legal small-core placement relative to source selection bar ``t``."""

    window_len: int
    core_len: int
    confirmation_bars: int
    core_end_offset: int = -3

    @property
    def core_start_offset(self) -> int:
        return self.core_end_offset - self.core_len + 1

    @property
    def window_end_offset(self) -> int:
        return self.core_end_offset + self.confirmation_bars

    @property
    def window_start_offset(self) -> int:
        return self.window_end_offset - self.window_len + 1

    @property
    def core_start_local(self) -> int:
        return self.core_start_offset - self.window_start_offset

    @property
    def core_end_local(self) -> int:
        return self.core_end_offset - self.window_start_offset

    @property
    def center_fraction(self) -> float:
        return (
            (self.core_start_local + self.core_end_local)
            / 2.0
            / max(self.window_len - 1, 1)
        )

    @property
    def position_bin(self) -> str:
        return position_bin(self.center_fraction)


@dataclass(frozen=True)
class PositivePlan:
    """One positive render/label plan derived from an existing candidate row."""

    sample_id: str
    event_id: str
    symbol: str
    direction: str
    source_path: str
    source_anchor_i: int
    anchor_time: str
    split: str | None
    geometry: Geometry
    core_start_i: int
    core_end_i: int
    window_start_i: int
    window_end_i: int
    selection_label_end_i: int
    render_start_time: str
    render_end_time: str
    selection_label_end_time: str


@dataclass(frozen=True)
class NegativePlan:
    """One empty-label render plan with a future-defined background receipt."""

    sample_id: str
    symbol: str
    source_path: str
    split: str
    negative_kind: str
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


def stable_int(*parts: object) -> int:
    """Return a deterministic unsigned integer from identity fields."""

    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:16], 16)


def position_bin(center_fraction: float) -> str:
    """Map a normalized box center to the frozen left/middle/right thirds."""

    if center_fraction < 1.0 / 3.0:
        return "left"
    if center_fraction < 2.0 / 3.0:
        return "middle"
    return "right"


def legal_geometries(prereg: Mapping[str, Any]) -> tuple[Geometry, ...]:
    """Enumerate every preregistered 14--22 / 4--7 / 3--5 placement."""

    contract = prereg["positive_geometry"]
    offset = int(contract["core_end_offset_from_t_bars"])
    options = tuple(
        Geometry(int(window), int(core), int(confirm), offset)
        for window in contract["input_window_length_choices"]
        for core in contract["core_length_choices"]
        for confirm in contract["confirmation_bars_choices"]
    )
    if not options:
        raise T3DatasetError("positive geometry option set is empty")
    for option in options:
        if option.core_start_local < 0 or option.core_end_local >= option.window_len:
            raise T3DatasetError(f"core does not fit legal window: {option}")
        if option.window_end_offset > int(
            contract["maximum_window_end_offset_from_t_bars"]
        ):
            raise T3DatasetError(f"window reads too far after t: {option}")
    return options


def assign_geometry(identity: str, prereg: Mapping[str, Any]) -> Geometry:
    """Assign one legal placement by stable hash, never by dataset order."""

    options = legal_geometries(prereg)
    return options[stable_int(prereg["protocol"], identity) % len(options)]


def split_for_interval(
    start: object,
    end: object,
    *,
    cutoff: object,
    purge_bars: int,
    bar_minutes: int,
) -> str | None:
    """Apply the frozen split to the full input-plus-label dependency interval."""

    start_ts, end_ts, cutoff_ts = utc(start), utc(end), utc(cutoff)
    if end_ts < start_ts:
        raise T3DatasetError("render interval ends before it starts")
    purge = pd.Timedelta(minutes=int(purge_bars) * int(bar_minutes))
    if end_ts <= cutoff_ts - purge:
        return "train"
    if start_ts >= cutoff_ts + purge:
        return "val"
    return None


def _repo_path(value: object) -> Path:
    path = (ROOT / str(value)).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise T3DatasetError(f"path escapes repository: {value}") from exc
    return path


def load_preregistration(path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    """Load and validate the exact Owner-authorized, no-production contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise T3DatasetError("unexpected experiment_id")
    auth = payload["owner_authorization"]
    if auth.get("review_waiver") is not True or auth.get("training_authorized") is not True:
        raise T3DatasetError("Owner weak-label training authorization is absent")
    if auth.get("production_or_promotion_authorized") is not False:
        raise T3DatasetError("production or promotion must remain unauthorized")
    safety = payload["safety"]
    forbidden = (
        "holdout_read",
        "active_or_frozen_change",
        "promote",
        "deployment",
        "forward_state_change",
        "order_state_change",
        "production_eligible",
    )
    if any(safety.get(field) is not False for field in forbidden):
        raise T3DatasetError("one or more safety switches drifted from false")
    if int(payload["sources"]["holdout_ohlcv_rows_allowed"]) != 0:
        raise T3DatasetError("holdout OHLCV allowance must be zero")
    legal_geometries(payload)
    return payload


def load_candidate_union(prereg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read the two hash-pinned manifests and verify their 10,000-row union."""

    rows: list[dict[str, Any]] = []
    for contract in prereg["sources"]["candidate_manifests"]:
        path = _repo_path(contract["path"])
        if sha256_file(path) != str(contract["sha256"]):
            raise T3DatasetError(f"candidate manifest hash drifted: {path}")
        part = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        counts = Counter(str(row.get("direction")) for row in part)
        if len(part) != int(contract["rows"]):
            raise T3DatasetError(f"candidate row count drifted: {path}")
        if counts != Counter(LONG=int(contract["long"]), SHORT=int(contract["short"])):
            raise T3DatasetError(f"candidate side counts drifted: {path}")
        rows.extend(part)

    identities = [
        (str(row["symbol"]), str(row["direction"]), utc(row["anchor_time"]).isoformat())
        for row in rows
    ]
    if len(identities) != len(set(identities)):
        raise T3DatasetError("candidate union contains duplicate symbol/side/time rows")
    counts = Counter(str(row["direction"]) for row in rows)
    source = prereg["sources"]
    if len(rows) != int(source["expected_union_rows"]):
        raise T3DatasetError("candidate union row count drifted")
    if counts != Counter(
        LONG=int(source["expected_union_long"]),
        SHORT=int(source["expected_union_short"]),
    ):
        raise T3DatasetError("candidate union side counts drifted")
    for row in rows:
        if str(row["direction"]) not in CLASS_IDS:
            raise T3DatasetError("candidate direction is not LONG/SHORT")
        path = _repo_path(row["source_path"])
        if not path.is_file():
            raise T3DatasetError(f"candidate source is missing: {path}")
        if utc(row["anchor_time"]) >= utc(source["holdout_start"]):
            raise T3DatasetError("candidate anchor touches holdout")
    return rows


def plan_positives(
    rows: Sequence[Mapping[str, Any]], prereg: Mapping[str, Any]
) -> list[PositivePlan]:
    """Derive t-3 geometry and chronological split without reading OHLCV."""

    split = prereg["split"]
    bar_minutes = int(prereg["sources"]["bar_minutes"])
    delta = pd.Timedelta(minutes=bar_minutes)
    plans: list[PositivePlan] = []
    for row in rows:
        identity = (
            f"{row['symbol']}|{row['direction']}|{utc(row['anchor_time']).isoformat()}"
        )
        geometry = assign_geometry(identity, prereg)
        anchor_i = int(row["source_anchor_i"])
        anchor_time = utc(row["anchor_time"])
        start_time = anchor_time + geometry.window_start_offset * delta
        end_time = anchor_time + geometry.window_end_offset * delta
        label_end_time = anchor_time + 11 * delta
        assigned = split_for_interval(
            start_time,
            label_end_time,
            cutoff=split["cutoff"],
            purge_bars=int(split["purge_bars"]),
            bar_minutes=bar_minutes,
        )
        sample_id = hashlib.sha256(
            f"{prereg['protocol']}|{identity}".encode("utf-8")
        ).hexdigest()[:24]
        plans.append(
            PositivePlan(
                sample_id=sample_id,
                event_id=str(row["event_id"]),
                symbol=str(row["symbol"]),
                direction=str(row["direction"]),
                source_path=str(row["source_path"]),
                source_anchor_i=anchor_i,
                anchor_time=anchor_time.isoformat(),
                split=assigned,
                geometry=geometry,
                core_start_i=anchor_i + geometry.core_start_offset,
                core_end_i=anchor_i + geometry.core_end_offset,
                window_start_i=anchor_i + geometry.window_start_offset,
                window_end_i=anchor_i + geometry.window_end_offset,
                selection_label_end_i=anchor_i + 11,
                render_start_time=start_time.isoformat(),
                render_end_time=end_time.isoformat(),
                selection_label_end_time=label_end_time.isoformat(),
            )
        )
    return plans


def geometry_audit(plans: Iterable[PositivePlan], prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Fail if hash placement collapses to a fixed-position shortcut."""

    kept = [plan for plan in plans if plan.split is not None]
    if not kept:
        raise T3DatasetError("no positive rows survive the split")
    fractions = np.asarray([plan.geometry.center_fraction for plan in kept], dtype=float)
    bins = Counter(plan.geometry.position_bin for plan in kept)
    windows = Counter(plan.geometry.window_len for plan in kept)
    cores = Counter(plan.geometry.core_len for plan in kept)
    confirmations = Counter(plan.geometry.confirmation_bars for plan in kept)
    gate = prereg["positive_geometry"]["position_gate"]
    missing = [name for name in gate["required_nonempty_bins"] if bins[name] == 0]
    if missing:
        raise T3DatasetError(f"position bins are missing: {missing}")
    max_share = max(bins.values()) / len(kept)
    if max_share > float(gate["maximum_single_bin_share"]):
        raise T3DatasetError(f"position shortcut gate failed: max share {max_share:.4f}")
    std = float(fractions.std())
    if std < float(gate["minimum_center_fraction_std"]):
        raise T3DatasetError(f"position spread std is too small: {std:.4f}")
    unique = len({round(float(value), 4) for value in fractions})
    if unique < int(gate["minimum_unique_center_fractions_rounded_4dp"]):
        raise T3DatasetError(f"too few distinct center positions: {unique}")
    return {
        "rows": len(kept),
        "position_bins": dict(sorted(bins.items())),
        "maximum_single_bin_share": max_share,
        "center_fraction_min": float(fractions.min()),
        "center_fraction_median": float(np.median(fractions)),
        "center_fraction_max": float(fractions.max()),
        "center_fraction_std": std,
        "unique_center_fractions_rounded_4dp": unique,
        "window_lengths": {str(k): v for k, v in sorted(windows.items())},
        "core_lengths": {str(k): v for k, v in sorted(cores.items())},
        "confirmation_bars": {str(k): v for k, v in sorted(confirmations.items())},
        "passed": True,
    }


def pine_rma(values: Sequence[float], length: int) -> np.ndarray:
    """Exact Pine/Wilder RMA used by the candidate collector."""

    array = np.asarray(values, dtype=float)
    out = np.full(array.shape, np.nan, dtype=float)
    if length <= 0:
        raise ValueError("length must be positive")
    for start in range(0, max(0, len(array) - length + 1)):
        seed = array[start : start + length]
        if np.isfinite(seed).all():
            seed_i = start + length - 1
            out[seed_i] = float(seed.mean())
            for index in range(seed_i + 1, len(array)):
                value = array[index]
                out[index] = (
                    out[index - 1]
                    if not np.isfinite(value)
                    else (out[index - 1] * (length - 1) + value) / length
                )
            break
    return out


def negative_feature_arrays(
    enriched: pd.DataFrame, prereg: Mapping[str, Any]
) -> dict[str, np.ndarray]:
    """Compute shared no-launch and dense-no-launch masks for pseudo-``t`` rows.

    Source columns are ``open/high/low/close`` plus causal SMA/EMA 20/60/120.
    ATR14 uses rows through pseudo-``t``.  Negative labels use highs/lows and
    the terminal close from pseudo-``t`` through pseudo-``t+11``; rendered
    inputs are separately bounded to at most pseudo-``t+2``.
    """

    n = len(enriched)
    open_ = enriched["open"].to_numpy(dtype=float)
    high = enriched["high"].to_numpy(dtype=float)
    low = enriched["low"].to_numpy(dtype=float)
    close = enriched["close"].to_numpy(dtype=float)
    previous = np.r_[np.nan, close[:-1]]
    tr = np.nanmax(
        np.vstack((high - low, np.abs(high - previous), np.abs(low - previous))),
        axis=0,
    )
    atr = pine_rma(tr, 14)

    future = 12
    future_high = np.full(n, np.nan, dtype=float)
    future_low = np.full(n, np.nan, dtype=float)
    future_close = np.full(n, np.nan, dtype=float)
    if n >= future:
        windows_high = np.lib.stride_tricks.sliding_window_view(high, future)
        windows_low = np.lib.stride_tricks.sliding_window_view(low, future)
        count = len(windows_high)
        future_high[:count] = windows_high.max(axis=1)
        future_low[:count] = windows_low.min(axis=1)
        future_close[:count] = close[future - 1 :]

    with np.errstate(divide="ignore", invalid="ignore"):
        close_abs = np.abs(future_close - open_) / atr
        favorable_up = (future_high - open_) / atr
        favorable_down = (open_ - future_low) / atr
        two_sided = np.maximum(favorable_up, favorable_down)

    mas = enriched.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
    bandwidth = np.full(n, np.nan, dtype=float)
    if n > 1:
        prior = mas[:-1]
        prior_close = close[:-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            bandwidth[1:] = (np.nanmax(prior, axis=1) - np.nanmin(prior, axis=1)) / prior_close * 100.0

    no_launch_cfg = prereg["negative_sampling"]["completed_no_launch_condition"]
    finite = np.isfinite(close_abs) & np.isfinite(two_sided) & np.isfinite(atr) & (atr > 0)
    no_launch = (
        finite
        & (close_abs <= float(no_launch_cfg["pseudo_t_close_abs_atr_max_over_12_bars"]))
        & (
            two_sided
            <= float(no_launch_cfg["pseudo_t_two_sided_favorable_abs_atr_max_over_12_bars"])
        )
    )
    hard = no_launch & np.isfinite(bandwidth) & (
        bandwidth <= float(prereg["negative_sampling"]["hard_definition"]["six_ma_bandwidth_pct_max"])
    )
    return {
        "atr": atr,
        "bandwidth_pct": bandwidth,
        "close_abs_atr": close_abs,
        "two_sided_favorable_abs_atr": two_sided,
        "no_launch": no_launch,
        "hard": hard,
    }


def mark_positive_guards(
    occupied: np.ndarray,
    source_candidates: Sequence[Mapping[str, Any]],
    prereg: Mapping[str, Any],
) -> None:
    """Protect every candidate, including purge rows, before sampling backgrounds."""

    contract = prereg["negative_sampling"]["positive_guard"]
    max_core = max(int(v) for v in prereg["positive_geometry"]["core_length_choices"])
    core_end_offset = int(prereg["positive_geometry"]["core_end_offset_from_t_bars"])
    latest_end = int(prereg["positive_geometry"]["maximum_window_end_offset_from_t_bars"])
    for row in source_candidates:
        anchor = int(row["source_anchor_i"])
        core_start = anchor + core_end_offset - max_core + 1
        start = max(0, core_start - int(contract["before_core_bars"]))
        end = min(
            len(occupied) - 1,
            anchor + latest_end + int(contract["after_latest_possible_window_end_bars"]),
        )
        occupied[start : end + 1] = True


def _interval_is_contiguous(segment_ids: np.ndarray, start: int, end: int) -> bool:
    return 0 <= start <= end < len(segment_ids) and segment_ids[start] == segment_ids[end]


def _negative_pool(
    features: Mapping[str, np.ndarray],
    *,
    kind: str,
    seed_parts: Sequence[object],
) -> np.ndarray:
    if kind == "hard":
        mask = np.asarray(features["hard"], dtype=bool)
    elif kind == "easy":
        mask = np.asarray(features["no_launch"], dtype=bool) & ~np.asarray(
            features["hard"], dtype=bool
        )
    else:
        raise T3DatasetError(f"unknown negative kind: {kind}")
    indices = np.flatnonzero(mask)
    rng = np.random.default_rng(stable_int(*seed_parts))
    return rng.permutation(indices)


def select_negative_plans(
    enriched: pd.DataFrame,
    *,
    source_path: str,
    symbol: str,
    source_positives: Sequence[PositivePlan],
    source_candidates: Sequence[Mapping[str, Any]],
    prereg: Mapping[str, Any],
) -> list[NegativePlan]:
    """Select exact same-source easy/hard ratios without weakening exclusions."""

    features = negative_feature_arrays(enriched, prereg)
    times = pd.to_datetime(enriched["open_time"], utc=True)
    segments = enriched["_segment_id"].to_numpy(dtype=int)
    occupied = np.zeros(len(enriched), dtype=bool)
    mark_positive_guards(occupied, source_candidates, prereg)
    split_cfg = prereg["split"]
    bar_minutes = int(prereg["sources"]["bar_minutes"])
    ratios = prereg["negative_sampling"]

    templates: dict[tuple[str, str], list[PositivePlan]] = defaultdict(list)
    for plan in source_positives:
        if plan.split is None:
            continue
        easy_n = int(ratios[f"{plan.split}_easy_per_positive"])
        hard_n = int(ratios[f"{plan.split}_hard_per_positive"])
        templates[(plan.split, "easy")].extend([plan] * easy_n)
        templates[(plan.split, "hard")].extend([plan] * hard_n)

    selected: list[NegativePlan] = []
    for split_name in ("train", "val"):
        for kind in ("hard", "easy"):
            wanted = templates[(split_name, kind)]
            if not wanted:
                continue
            pool = _negative_pool(
                features,
                kind=kind,
                seed_parts=(prereg["protocol"], source_path, split_name, kind),
            )
            cursor = 0
            for ordinal, template in enumerate(wanted):
                found: NegativePlan | None = None
                while cursor < len(pool):
                    pseudo_t = int(pool[cursor])
                    cursor += 1
                    window_end = pseudo_t + template.geometry.confirmation_bars - 3
                    window_start = window_end - template.geometry.window_len + 1
                    label_future_end = pseudo_t + 11
                    if not _interval_is_contiguous(segments, window_start, label_future_end):
                        continue
                    if occupied[window_start : window_end + 1].any():
                        continue
                    assigned = split_for_interval(
                        times.iloc[window_start],
                        times.iloc[label_future_end],
                        cutoff=split_cfg["cutoff"],
                        purge_bars=int(split_cfg["purge_bars"]),
                        bar_minutes=bar_minutes,
                    )
                    if assigned != split_name:
                        continue
                    if times.iloc[label_future_end] >= utc(prereg["sources"]["holdout_start"]):
                        continue
                    sample_id = hashlib.sha256(
                        (
                            f"{prereg['protocol']}|{source_path}|{split_name}|{kind}|"
                            f"{pseudo_t}|{template.geometry.window_len}|"
                            f"{template.geometry.confirmation_bars}|{ordinal}"
                        ).encode("utf-8")
                    ).hexdigest()[:24]
                    found = NegativePlan(
                        sample_id=sample_id,
                        symbol=symbol,
                        source_path=source_path,
                        split=split_name,
                        negative_kind=kind,
                        pseudo_t_i=pseudo_t,
                        pseudo_t_time=times.iloc[pseudo_t].isoformat(),
                        window_len=template.geometry.window_len,
                        confirmation_bars=template.geometry.confirmation_bars,
                        window_start_i=window_start,
                        window_end_i=window_end,
                        label_future_end_i=label_future_end,
                        bandwidth_pct=float(features["bandwidth_pct"][pseudo_t]),
                        close_abs_atr=float(features["close_abs_atr"][pseudo_t]),
                        two_sided_favorable_abs_atr=float(
                            features["two_sided_favorable_abs_atr"][pseudo_t]
                        ),
                    )
                    occupied[window_start : window_end + 1] = True
                    break
                if found is None:
                    raise T3DatasetError(
                        f"insufficient {kind} negatives for {source_path}/{split_name}: "
                        f"made {ordinal}/{len(wanted)} from pool {len(pool)}"
                    )
                selected.append(found)
    return selected


def yolo_box_from_core(
    transform: Any,
    window: pd.DataFrame,
    core_start_local: int,
    core_end_local: int,
) -> tuple[float, float, float, float]:
    """Return one clipping-safe YOLO box covering exactly 4--7 core bars."""

    if not 0 <= core_start_local <= core_end_local < len(window):
        raise T3DatasetError("core is outside rendered window")
    segment = window.iloc[core_start_local : core_end_local + 1]
    x0 = transform.x_at(core_start_local) - transform.candle_half_w - 2
    x1 = transform.x_at(core_end_local) + transform.candle_half_w + 2
    y0 = transform.y_at(float(segment["high"].max())) - 2
    y1 = transform.y_at(float(segment["low"].min())) + 2
    x0 = float(np.clip(x0, 0, transform.width - 1))
    x1 = float(np.clip(x1, 1, transform.width))
    y0 = float(np.clip(y0, 0, transform.height - 1))
    y1 = float(np.clip(y1, 1, transform.height))
    if x1 <= x0 or y1 <= y0:
        raise T3DatasetError("degenerate YOLO core box")
    return (
        (x0 + x1) / 2.0 / transform.width,
        (y0 + y1) / 2.0 / transform.height,
        (x1 - x0) / transform.width,
        (y1 - y0) / transform.height,
    )


def _encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise OSError("OpenCV failed to encode training PNG")
    return encoded.tobytes()


def _write_sample(
    dataset_root: Path,
    *,
    stem: str,
    split: str,
    image: np.ndarray,
    label_text: str,
) -> tuple[str, str, str, str]:
    image_rel = Path("images") / split / f"{stem}.png"
    label_rel = Path("labels") / split / f"{stem}.txt"
    image_path, label_path = dataset_root / image_rel, dataset_root / label_rel
    image_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = _encode_png(image)
    label_bytes = label_text.encode("utf-8")
    image_path.write_bytes(image_bytes)
    label_path.write_bytes(label_bytes)
    return (
        str(image_rel),
        str(label_rel),
        hashlib.sha256(image_bytes).hexdigest(),
        hashlib.sha256(label_bytes).hexdigest(),
    )


def _verify_positive_source(
    plan: PositivePlan, enriched: pd.DataFrame, prereg: Mapping[str, Any]
) -> None:
    times = pd.to_datetime(enriched["open_time"], utc=True)
    if not 0 <= plan.window_start_i <= plan.window_end_i < len(enriched):
        raise T3DatasetError(f"positive window out of bounds: {plan.sample_id}")
    if times.iloc[plan.source_anchor_i] != utc(plan.anchor_time):
        raise T3DatasetError(f"positive source index/time mismatch: {plan.sample_id}")
    segments = enriched["_segment_id"].to_numpy(dtype=int)
    if not _interval_is_contiguous(segments, plan.window_start_i, plan.selection_label_end_i):
        raise T3DatasetError(f"positive input/label dependency crosses a source gap: {plan.sample_id}")
    if times.iloc[plan.selection_label_end_i] >= utc(prereg["sources"]["holdout_start"]):
        raise T3DatasetError(f"positive label dependency touches holdout: {plan.sample_id}")


def _draw_preview_box(
    image: np.ndarray, box: tuple[float, float, float, float], class_id: int
) -> np.ndarray:
    out = image.copy()
    height, width = out.shape[:2]
    cx, cy, bw, bh = box
    x0, x1 = int((cx - bw / 2) * width), int((cx + bw / 2) * width)
    y0, y1 = int((cy - bh / 2) * height), int((cy + bh / 2) * height)
    color = (30, 150, 30) if class_id == 0 else (30, 30, 210)
    cv2.rectangle(out, (x0, y0), (x1, y1), color, 3, cv2.LINE_AA)
    return out


def _contact_sheet(images: Sequence[np.ndarray], *, columns: int = 4) -> np.ndarray:
    if not images:
        raise T3DatasetError("preview contact sheet has no images")
    thumbs: list[np.ndarray] = []
    for image in images:
        width = 420
        scale = width / image.shape[1]
        thumbs.append(
            cv2.resize(
                image,
                (width, int(round(image.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        )
    height = max(image.shape[0] for image in thumbs)
    rows = math.ceil(len(thumbs) / columns)
    canvas = np.full((rows * height, columns * 420, 3), 245, dtype=np.uint8)
    for index, image in enumerate(thumbs):
        row, column = divmod(index, columns)
        y, x = row * height, column * 420
        canvas[y : y + image.shape[0], x : x + image.shape[1]] = image
    return canvas


def _json_ready_positive(plan: PositivePlan) -> dict[str, Any]:
    row = asdict(plan)
    row["geometry"] = asdict(plan.geometry)
    row["center_fraction"] = plan.geometry.center_fraction
    row["position_bin"] = plan.geometry.position_bin
    return row


def verify_builder_committed(paths: Sequence[Path]) -> str:
    """Require main and committed builder inputs before generating artifacts."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise T3DatasetError("dataset builder must run on main")
    relative = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise T3DatasetError(f"builder inputs are not committed:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", relative[0]],
        cwd=ROOT,
        text=True,
    ).strip()
    if len(commit) != 40:
        raise T3DatasetError("could not resolve builder commit")
    return commit


def build_dataset(
    *,
    prereg_path: Path = DEFAULT_PREREG,
    dataset_path: Path = DEFAULT_DATASET,
    results_path: Path = DEFAULT_RESULTS,
    materialize: bool = True,
    builder_commit: str | None = None,
) -> dict[str, Any]:
    """Plan or materialize the complete deterministic pre-holdout dataset."""

    prereg = load_preregistration(prereg_path)
    candidates = load_candidate_union(prereg)
    positives = plan_positives(candidates, prereg)
    position = geometry_audit(positives, prereg)
    kept_positives = [plan for plan in positives if plan.split is not None]
    by_source_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_source_positives: dict[str, list[PositivePlan]] = defaultdict(list)
    for row in candidates:
        by_source_candidates[str(row["source_path"])].append(dict(row))
    for plan in kept_positives:
        by_source_positives[plan.source_path].append(plan)

    final_dataset = dataset_path.resolve()
    building = final_dataset.with_name(f"{final_dataset.name}.building")
    if materialize and (final_dataset.exists() or building.exists()):
        raise FileExistsError(f"refusing to overwrite dataset/build path: {final_dataset}")
    if materialize:
        for relative in ("images/train", "images/val", "labels/train", "labels/val"):
            (building / relative).mkdir(parents=True, exist_ok=False)

    counts: Counter[str] = Counter()
    source_audits: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    preview_images: list[np.ndarray] = []
    source_items = sorted(by_source_candidates.items())
    total_sources = len(source_items)
    for source_number, (source_path, source_candidates) in enumerate(source_items, 1):
        absolute_source = _repo_path(source_path)
        frame, audit = read_preholdout_prefix(
            absolute_source,
            end_exclusive=utc(prereg["sources"]["holdout_start"]),
        )
        audit["source_path"] = source_path
        audit["symbol"] = str(source_candidates[0]["symbol"])
        source_audits.append(audit)
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("holdout OHLCV row materialized")
        enriched = add_six_mas(frame)
        source_positives = by_source_positives.get(source_path, [])
        for plan in source_positives:
            _verify_positive_source(plan, enriched, prereg)

        negatives = select_negative_plans(
            enriched,
            source_path=source_path,
            symbol=str(source_candidates[0]["symbol"]),
            source_positives=source_positives,
            source_candidates=source_candidates,
            prereg=prereg,
        )
        for plan in source_positives:
            counts[f"{plan.split}/positive"] += 1
            counts[f"{plan.split}/positive/{plan.direction.lower()}"] += 1
            if not materialize:
                continue
            window = enriched.iloc[plan.window_start_i : plan.window_end_i + 1].reset_index(drop=True)
            image, transform = render_chart(window, out_path=None)
            box = yolo_box_from_core(
                transform,
                window,
                plan.geometry.core_start_local,
                plan.geometry.core_end_local,
            )
            class_id = CLASS_IDS[plan.direction]
            label = f"{class_id} {box[0]:.6f} {box[1]:.6f} {box[2]:.6f} {box[3]:.6f}\n"
            stem = f"pos_{plan.sample_id}"
            image_rel, label_rel, image_sha, label_sha = _write_sample(
                building,
                stem=stem,
                split=str(plan.split),
                image=image,
                label_text=label,
            )
            row = _json_ready_positive(plan)
            row.update(
                {
                    "sample_kind": "positive_weak",
                    "class_id": class_id,
                    "class_name": prereg["positive_geometry"]["class_names"][class_id],
                    "image_path": image_rel,
                    "label_path": label_rel,
                    "image_sha256": image_sha,
                    "label_sha256": label_sha,
                    "input_latest_offset_from_t": plan.geometry.window_end_offset,
                    "selection_label_future_latest_offset_from_t": 11,
                    "production_eligible": False,
                }
            )
            manifest_rows.append(row)
            if len(preview_images) < 24:
                preview_images.append(_draw_preview_box(image, box, class_id))

        for plan in negatives:
            counts[f"{plan.split}/negative"] += 1
            counts[f"{plan.split}/negative/{plan.negative_kind}"] += 1
            if not materialize:
                continue
            window = enriched.iloc[plan.window_start_i : plan.window_end_i + 1].reset_index(drop=True)
            image, _ = render_chart(window, out_path=None)
            stem = f"neg_{plan.negative_kind[0]}_{plan.sample_id}"
            image_rel, label_rel, image_sha, label_sha = _write_sample(
                building,
                stem=stem,
                split=plan.split,
                image=image,
                label_text="",
            )
            row = asdict(plan)
            row.update(
                {
                    "sample_kind": f"negative_{plan.negative_kind}",
                    "class_id": None,
                    "class_name": None,
                    "image_path": image_rel,
                    "label_path": label_rel,
                    "image_sha256": image_sha,
                    "label_sha256": label_sha,
                    "input_latest_offset_from_pseudo_t": plan.confirmation_bars - 3,
                    "negative_label_future_latest_offset_from_pseudo_t": 11,
                    "production_eligible": False,
                }
            )
            manifest_rows.append(row)

        if source_number == 1 or source_number % 10 == 0 or source_number == total_sources:
            print(
                f"source {source_number:03d}/{total_sources} {source_path:<70} "
                f"pos={len(source_positives):>3} neg={len(negatives):>4} "
                f"total={sum(counts.values())}",
                flush=True,
            )

    expected_train_pos = counts["train/positive"]
    expected_val_pos = counts["val/positive"]
    ratio_checks = {
        "train_easy": counts["train/negative/easy"] == expected_train_pos,
        "train_hard": counts["train/negative/hard"] == 2 * expected_train_pos,
        "val_easy": counts["val/negative/easy"] == expected_val_pos,
        "val_hard_zero": counts["val/negative/hard"] == 0,
    }
    if not all(ratio_checks.values()):
        raise T3DatasetError(f"negative ratio contract failed: {ratio_checks} {dict(counts)}")
    if sum(int(row["holdout_ohlcv_rows_materialized"]) for row in source_audits) != 0:
        raise AssertionError("source audits report holdout OHLCV")

    summary: dict[str, Any] = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "mode": "materialized" if materialize else "plan_only",
        "owner_label_classification": prereg["owner_authorization"]["classification"],
        "candidate_union_rows": len(candidates),
        "candidate_union_per_side": dict(Counter(row["direction"] for row in candidates)),
        "positive_split": {
            "train": counts["train/positive"],
            "val": counts["val/positive"],
            "purged": sum(plan.split is None for plan in positives),
        },
        "counts": dict(sorted(counts.items())),
        "ratio_checks": ratio_checks,
        "position_audit": position,
        "sources": {
            "files": len(source_audits),
            "rows_materialized": sum(int(row["rows_materialized"]) for row in source_audits),
            "boundary_timestamp_rows_inspected": sum(
                int(row["boundary_timestamp_rows_inspected"]) for row in source_audits
            ),
            "holdout_ohlcv_rows_materialized": 0,
        },
        "safety": {
            "training_images_materialized": len(manifest_rows) if materialize else 0,
            "labels_materialized": len(manifest_rows) if materialize else 0,
            "models_created": 0,
            "remote_writes": 0,
            "active_or_frozen_changed": False,
            "promoted": False,
            "production_eligible": False,
        },
    }

    results_path.mkdir(parents=True, exist_ok=True)
    if materialize:
        manifest_path = building / "manifest.jsonl"
        with manifest_path.open("w", encoding="utf-8") as handle:
            for row in sorted(
                manifest_rows,
                key=lambda item: (str(item["split"]), str(item["sample_id"])),
            ):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        source_audit_path = building / "source_audit.jsonl"
        with source_audit_path.open("w", encoding="utf-8") as handle:
            for row in source_audits:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
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
        summary.update(
            {
                "dataset_path": str(final_dataset),
                "manifest_sha256": sha256_file(manifest_path),
                "manifest_rows": len(manifest_rows),
                "source_audit_sha256": sha256_file(source_audit_path),
                "data_yaml_sha256": sha256_file(data_yaml),
            }
        )
        summary_path = building / "build_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary["build_summary_sha256_before_dataset_rename"] = sha256_file(summary_path)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        building.rename(final_dataset)
        contact = _contact_sheet(preview_images)
        preview_path = results_path / "preview_contact_sheet.png"
        preview_path.write_bytes(_encode_png(contact))
        summary["preview_contact_sheet_sha256"] = sha256_file(preview_path)
        summary["dataset_bytes"] = sum(
            path.stat().st_size for path in final_dataset.rglob("*") if path.is_file()
        )

    receipt_name = "dataset_build_receipt.json" if materialize else "dataset_plan_receipt.json"
    receipt_path = results_path / receipt_name
    if receipt_path.exists():
        raise FileExistsError(f"refusing to overwrite receipt: {receipt_path}")
    receipt_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
