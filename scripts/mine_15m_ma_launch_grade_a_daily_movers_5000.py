#!/usr/bin/env python3
"""Mine at least 5,000 novel pre-holdout daily-mover review events.

The search walks complete calendar months newest-first, ending at October 2025
so every scored input remains before the current detector's December validation
block and the 2026-05-04 holdout.  Each UTC day contributes up to the frozen
Top5 strictly positive gainers and Top5 strictly negative losers by completed-
day open-to-close return.  A one-sided day keeps every available member of the
scarce tail without zero/opposite-sign backfill.  The completed-day rank is
therefore a post-hoc P1 mining stratum, never a causal selector, feature,
backtest, or trading signal.

For compute efficiency this owner-requested scale-up uses one frozen W18 view
per endpoint and a causal prefilter: the minimum six-MA envelope inside the
visible W18 window must be <= 1.5 Pine-RMA ATR14.  The checkpoint, conf=0.25,
NMS=0.70, mapped core4/5, confirmation2--9, semantic gate, and five-bar event
spacing are unchanged.  Full calendar months are added newest-first until the
globally de-duplicated ``new_event_review`` count is at least 5,000.

The Mac is the authority for ranks, source hashes, temporal contracts,
training-overlap checks, semantic decisions, de-duplication, and deliverables.
The pinned Windows CUDA worker only renders exact W18 pixels and returns raw
normalized YOLO boxes.  This script never reads holdout OHLCV, mutates labels
or datasets, trains, tunes, promotes, deploys, changes forward state, sends
messages, or places orders.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
import types
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_15m_ma_launch_grade_a_daily_movers as prior  # noqa: E402
from scripts import scan_15m_ma_launch_t3_daily_movers as common  # noqa: E402
from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402
from scripts import scan_crypto_grade_a_yolo_mtf_latest as latest  # noqa: E402
from yoyo.datasets.fifteen_minute_launch_candidates import (  # noqa: E402
    add_candidate_features,
)
from yoyo.layers.l1_detection.data import ALL_MA_COLS  # noqa: E402


EXPERIMENT_ID = "exp-15m-ma-launch-grade-a-daily-movers-5000-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_AMENDMENT = (
    ROOT
    / "experiments"
    / "active"
    / EXPERIMENT_ID
    / "protocol_amendment_20260903_sign_tail_underflow.json"
)
DEFAULT_RUNTIME_AMENDMENT = (
    ROOT
    / "experiments"
    / "active"
    / EXPERIMENT_ID
    / "protocol_amendment_20260903_frozen_renderer_runtime.json"
)
DEFAULT_OUT = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
REMOTE_WORKER = ROOT / "scripts/remote_infer_15m_ma_launch_grade_a_taskpack.py"
PINNED_RUNTIME_COMMIT = "7931541abf9ac1edd8985924fb31db93bb617609"
PINNED_RENDER_PATH = "yoyo/layers/l1_detection/render.py"
PINNED_RENDER_SHA256 = "0962812feea57e0a666c4da62acea830cbbf53a3d66f6dbd722ae3e580ead3e7"
PARENT_GATE_PREREG = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1"
    / "preregistration.json"
)
BAR_DELTA = pd.Timedelta(minutes=15)
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
FRAME_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "sma20",
    "sma60",
    "sma120",
    "ema20",
    "ema60",
    "ema120",
)


class Mover5000Error(RuntimeError):
    """Fail closed on source, time, model, remote, or artifact drift."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def sha256_file(path: Path) -> str:
    """Return one streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    """Return SHA-256 for an in-memory immutable source blob."""

    return hashlib.sha256(value).hexdigest()


def git_blob_bytes(commit: str, relative_path: str) -> bytes:
    """Read one committed repository blob without consulting the working tree."""

    try:
        return subprocess.check_output(
            ["git", "show", f"{commit}:{relative_path}"], cwd=ROOT
        )
    except subprocess.CalledProcessError as exc:
        raise Mover5000Error(
            f"could not load immutable Git blob: {commit}:{relative_path}"
        ) from exc


def load_frozen_renderer() -> types.ModuleType:
    """Load the preregistered renderer from Git so concurrent edits cannot alter pixels."""

    raw = git_blob_bytes(PINNED_RUNTIME_COMMIT, PINNED_RENDER_PATH)
    if sha256_bytes(raw) != PINNED_RENDER_SHA256:
        raise Mover5000Error("committed frozen renderer SHA drifted")
    source = raw.decode("utf-8").replace(
        "from .data import ALL_MA_COLS",
        "from yoyo.layers.l1_detection.data import ALL_MA_COLS",
    )
    name = "_fable_frozen_renderer_0962812f"
    module = types.ModuleType(name)
    module.__file__ = f"<git:{PINNED_RUNTIME_COMMIT}:{PINNED_RENDER_PATH}>"
    module.__package__ = "yoyo.layers.l1_detection"
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


_FROZEN_RENDERER = load_frozen_renderer()
ChartTransform = _FROZEN_RENDERER.ChartTransform
render_chart = _FROZEN_RENDERER.render_chart


def pixel_sha256(image: np.ndarray) -> str:
    """Hash decoded BGR pixels."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def stable_json(value: Mapping[str, Any]) -> str:
    """Serialize deterministic JSON."""

    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write one readable JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write deterministic JSON Lines."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(stable_json(row) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSON Lines."""

    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def csv_ready(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Flatten nested cells for inspectable CSV output."""

    frame = pd.DataFrame([dict(row) for row in rows])
    for column in frame.columns:
        if frame[column].map(lambda value: isinstance(value, (list, dict, tuple))).any():
            frame[column] = frame[column].map(
                lambda value: stable_json(value)
                if isinstance(value, (list, dict, tuple))
                else value
            )
    return frame


def repo_relative(path: Path) -> str:
    """Return a repository-relative POSIX path."""

    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def month_start(value: str | pd.Timestamp) -> pd.Timestamp:
    """Normalize a month identity to its UTC first instant."""

    stamp = utc(value).tz_localize(None)
    return pd.Period(stamp, freq="M").start_time.tz_localize("UTC")


def month_id(value: str | pd.Timestamp) -> str:
    """Return YYYY-MM for one UTC timestamp."""

    return f"{month_start(value):%Y-%m}"


def adjacent_months(month: str) -> tuple[str, str, str]:
    """Return previous/current/next monthly archive identifiers."""

    current = pd.Period(month, freq="M")
    return str(current - 1), str(current), str(current + 1)


def search_months(prereg: Mapping[str, Any]) -> list[str]:
    """Return the preregistered newest-first full-month search order."""

    earliest = pd.Period(str(prereg["calendar"]["earliest_month"]), freq="M")
    latest_month = pd.Period(str(prereg["calendar"]["latest_month"]), freq="M")
    if earliest > latest_month:
        raise Mover5000Error("earliest month is after latest month")
    return [str(period) for period in reversed(pd.period_range(earliest, latest_month, freq="M"))]


def effective_protocol_metadata(prereg_path: Path) -> dict[str, Any]:
    """Load the append-only amendments and bind their complete protocol chain."""

    amendment = json.loads(DEFAULT_AMENDMENT.read_text(encoding="utf-8"))
    runtime_amendment = json.loads(DEFAULT_RUNTIME_AMENDMENT.read_text(encoding="utf-8"))
    parent_sha = sha256_file(prereg_path)
    amendment_sha = sha256_file(DEFAULT_AMENDMENT)
    runtime_amendment_sha = sha256_file(DEFAULT_RUNTIME_AMENDMENT)
    if amendment.get("experiment_id") != EXPERIMENT_ID:
        raise Mover5000Error("protocol amendment experiment_id drifted")
    if amendment.get("parent_preregistration_sha256") != parent_sha:
        raise Mover5000Error("protocol amendment parent SHA drifted")
    policy = amendment.get("underflow_policy", {})
    expected = {
        "positive_tail": "take_up_to_5_strictly_positive_returns",
        "negative_tail": "take_up_to_5_strictly_negative_returns",
        "zero_return_backfill": False,
        "opposite_sign_backfill": False,
        "skip_underflow_day": False,
    }
    if policy != expected:
        raise Mover5000Error("protocol amendment underflow policy drifted")
    if runtime_amendment.get("experiment_id") != EXPERIMENT_ID:
        raise Mover5000Error("runtime amendment experiment_id drifted")
    if runtime_amendment.get("parent_protocol_amendment_sha256") != amendment_sha:
        raise Mover5000Error("runtime amendment parent SHA drifted")
    frozen = runtime_amendment.get("frozen_runtime", {})
    expected_frozen = {
        "git_commit": PINNED_RUNTIME_COMMIT,
        "renderer_path": PINNED_RENDER_PATH,
        "renderer_sha256": PINNED_RENDER_SHA256,
        "mac_load": "execute_hash_verified_git_blob",
        "cuda_load": "copy_hash_verified_git_blobs_to_commit_addressed_runtime",
    }
    if frozen != expected_frozen:
        raise Mover5000Error("runtime amendment frozen source drifted")
    effective_sha = hashlib.sha256(
        f"{parent_sha}\n{amendment_sha}\n{runtime_amendment_sha}\n".encode()
    ).hexdigest()
    return {
        **amendment,
        "path": repo_relative(DEFAULT_AMENDMENT),
        "sha256": amendment_sha,
        "runtime_isolation": {
            **runtime_amendment,
            "path": repo_relative(DEFAULT_RUNTIME_AMENDMENT),
            "sha256": runtime_amendment_sha,
        },
        "amendment_sha256s": [amendment_sha, runtime_amendment_sha],
        "effective_protocol_sha256": effective_sha,
    }


def load_preregistration(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and enforce the exact pre-holdout 5,000-candidate contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise Mover5000Error("unexpected experiment_id")
    months = search_months(payload)
    if months[0] != "2025-10":
        raise Mover5000Error("latest search month must remain 2025-10")
    if months[-1] != "2021-01":
        raise Mover5000Error("earliest search month must remain 2021-01")
    latest_exclusive = month_start("2025-10-01") + pd.offsets.MonthBegin(1)
    if latest_exclusive != pd.Timestamp("2025-11-01T00:00:00Z"):
        raise Mover5000Error("latest end drifted")
    if latest_exclusive >= utc(payload["detector"]["existing_validation_first_window_start"]):
        raise Mover5000Error("search reached detector validation")
    if latest_exclusive >= HOLDOUT_START:
        raise Mover5000Error("search reached holdout")

    ranking = payload["ranking"]
    if int(ranking["top_gainers_per_day"]) != 5 or int(ranking["top_losers_per_day"]) != 5:
        raise Mover5000Error("daily Top5+Top5 board drifted")
    if ranking.get("causality") != "post_hoc_same_day_ranking_for_candidate_mining_only":
        raise Mover5000Error("ranking causality disclosure drifted")

    detector = payload["detector"]
    expected = {
        "weights_sha256": base.EXPECTED_WEIGHT_SHA256,
        "confidence": base.CONFIDENCE,
        "nms_iou": base.NMS_IOU,
        "imgsz": base.IMAGE_SIZE,
    }
    for key, value in expected.items():
        if detector.get(key) != value:
            raise Mover5000Error(f"detector {key} drifted")
    if list(map(int, detector["window_lengths"])) != [18]:
        raise Mover5000Error("discovery view must remain W18 only")
    if float(detector["causal_prefilter"]["max_min_six_ma_envelope_atr"]) != 1.5:
        raise Mover5000Error("causal prefilter drifted")
    if int(detector["target_novel_review_events_minimum"]) != 5000:
        raise Mover5000Error("target count drifted")
    if set(map(int, detector["mapped_core_length_bars_allowed"])) != set(base.ALLOWED_CORES):
        raise Mover5000Error("core contract drifted")
    if set(map(int, detector["mapped_confirmation_bars_allowed"])) != set(
        base.ALLOWED_CONFIRMATIONS
    ):
        raise Mover5000Error("confirmation contract drifted")
    if int(detector["same_symbol_event_gap_bars"]) != 5:
        raise Mover5000Error("event spacing drifted")

    if any(bool(value) for value in payload["safety"].values()):
        raise Mover5000Error("one or more safety mutation switches are enabled")
    authorization = payload["owner_authorization"]
    if authorization.get("holdout_read_authorized") is not False:
        raise Mover5000Error("holdout must remain unauthorized")
    if authorization.get("training_or_tuning_authorized") is not False:
        raise Mover5000Error("training must remain unauthorized")

    parent = json.loads(PARENT_GATE_PREREG.read_text(encoding="utf-8"))
    gates = dict(parent["treatment"]["frozen_morphology_gate"])
    if gates != dict(payload["semantic_gate"]["frozen_morphology_gate"]):
        raise Mover5000Error("semantic gate differs from frozen parent")
    payload["_protocol_amendment"] = effective_protocol_metadata(path)
    return payload, gates


def verify_immutable_sources(prereg_path: Path, prereg: Mapping[str, Any]) -> str:
    """Require main, committed builders/prereg, and every pinned source hash."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise Mover5000Error("official scan must run on main")
    coordinator = Path(__file__).resolve()
    sources = [coordinator, REMOTE_WORKER.resolve()]
    tracked = [
        path.relative_to(ROOT)
        for path in [
            *sources,
            prereg_path.resolve(),
            DEFAULT_AMENDMENT.resolve(),
            DEFAULT_RUNTIME_AMENDMENT.resolve(),
        ]
    ]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, tracked)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise Mover5000Error(f"builders and preregistration must be committed:\n{dirty}")
    commits = {
        path: subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", str(path.relative_to(ROOT))],
            cwd=ROOT,
            text=True,
        ).strip()
        for path in sources
    }
    amendment = prereg["_protocol_amendment"]
    runtime_amendment = amendment["runtime_isolation"]
    coordinator_commit = str(runtime_amendment["implementation_commit"])
    if commits[coordinator] != coordinator_commit:
        raise Mover5000Error("coordinator commit differs from protocol amendment")
    if commits[REMOTE_WORKER.resolve()] != str(prereg["source_commit"]):
        raise Mover5000Error("remote worker commit differs from parent preregistration")
    if sha256_file(coordinator) != str(runtime_amendment["coordinator_sha256"]):
        raise Mover5000Error("coordinator SHA differs from protocol amendment")

    pinned = [
        (ROOT / prereg["data"]["archive_fetch_summary"], prereg["data"]["archive_fetch_summary_sha256"]),
        (ROOT / prereg["data"]["admitted_symbols"], prereg["data"]["admitted_symbols_sha256"]),
        (ROOT / prereg["detector"]["training_manifest"], prereg["detector"]["training_manifest_sha256"]),
        (ROOT / prereg["semantic_gate"]["parent"], prereg["semantic_gate"]["parent_sha256"]),
        (ROOT / prereg["detector"]["weights"], prereg["detector"]["weights_sha256"]),
        (ROOT / prereg["remote_cuda"]["worker"], prereg["remote_cuda"]["worker_sha256"]),
        (ROOT / prereg["remote_cuda"]["renderer_data"], prereg["remote_cuda"]["renderer_data_sha256"]),
    ]
    for path, expected in pinned:
        if not path.is_file():
            raise Mover5000Error(f"pinned source missing: {path}")
        actual = sha256_file(path)
        if actual != str(expected):
            raise Mover5000Error(f"pinned source SHA drift: {path}")
    frozen_render = git_blob_bytes(PINNED_RUNTIME_COMMIT, PINNED_RENDER_PATH)
    if sha256_bytes(frozen_render) != str(prereg["remote_cuda"]["renderer_sha256"]):
        raise Mover5000Error("frozen Git renderer differs from preregistration")
    return coordinator_commit


def build_daily_tail_rows(
    prereg: Mapping[str, Any],
    *,
    month: str,
    day: pd.Timestamp,
    pool: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build one deterministic strict-sign board without synthetic tail backfill."""

    if not pool:
        raise Mover5000Error(f"{day:%Y-%m-%d} has no complete symbol-days")
    gain_n = int(prereg["ranking"]["top_gainers_per_day"])
    loss_n = int(prereg["ranking"]["top_losers_per_day"])
    gainers, losers = prior.select_daily_board(pool, gainers=gain_n, losers=loss_n)
    rows: list[dict[str, Any]] = []
    for bucket, selected in (("gainer", gainers), ("loser", losers)):
        for bucket_rank, source in enumerate(selected, 1):
            row = dict(source)
            row.update(
                {
                    "source_month": month,
                    "mover_bucket": bucket,
                    "bucket_rank": bucket_rank,
                    "rank_label": f"{'G' if bucket == 'gainer' else 'L'}{bucket_rank}",
                    "board_order": bucket_rank if bucket == "gainer" else gain_n + bucket_rank,
                    "eligible_symbol_days": len(pool),
                }
            )
            rows.append(row)
    return rows


def build_month_rankings(
    prereg: Mapping[str, Any],
    *,
    month: str,
    archive_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconstruct one complete month's universe and balanced daily boards."""

    admitted = json.loads((ROOT / prereg["data"]["admitted_symbols"]).read_text(encoding="utf-8"))
    symbols = sorted({str(row["symbol"]) for row in admitted})
    start = month_start(f"{month}-01")
    end = start + pd.offsets.MonthBegin(1)
    days = list(pd.date_range(start, end - pd.Timedelta(days=1), freq="D"))
    universe_rows: list[dict[str, Any]] = []
    archive_rows: list[dict[str, Any]] = []
    for symbol in symbols:
        path = prior.month_archive_path(archive_root, symbol, month)
        if not path.is_file():
            continue
        frame = prior.read_month_archive(path, symbol=symbol, month=month)
        source_sha = sha256_file(path)
        archive_rows.append(
            {
                "role": "ranking_month",
                "symbol": symbol,
                "month": month,
                "path": repo_relative(path),
                "sha256": source_sha,
                "size_bytes": path.stat().st_size,
                "rows": len(frame),
                "first_bar_open": utc(frame.iloc[0]["open_time"]).isoformat(),
                "last_bar_open": utc(frame.iloc[-1]["open_time"]).isoformat(),
            }
        )
        for day, part in frame.groupby(frame["open_time"].dt.floor("D"), sort=True):
            day = utc(day)
            if day < start or day >= end or not prior._is_exact_day(part, day):
                continue
            open_px = float(part.iloc[0]["open"])
            close_px = float(part.iloc[-1]["close"])
            universe_rows.append(
                {
                    "day": day.isoformat(),
                    "exchange_symbol": symbol,
                    "daily_return": close_px / open_px - 1.0,
                    "open": open_px,
                    "close": close_px,
                    "base_volume": float(part["volume"].sum()),
                    "bars": 96,
                    "source_zip_sha256": source_sha,
                }
            )

    by_day: defaultdict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    for row in universe_rows:
        by_day[utc(row["day"])].append(row)
    rankings: list[dict[str, Any]] = []
    for day in days:
        pool = by_day.get(utc(day), [])
        rankings.extend(build_daily_tail_rows(prereg, month=month, day=day, pool=pool))
    gain_n = int(prereg["ranking"]["top_gainers_per_day"])
    loss_n = int(prereg["ranking"]["top_losers_per_day"])
    expected = sum(
        min(gain_n, sum(float(row["daily_return"]) > 0 for row in by_day[utc(day)]))
        + min(loss_n, sum(float(row["daily_return"]) < 0 for row in by_day[utc(day)]))
        for day in days
    )
    if len(rankings) != expected:
        raise Mover5000Error(f"ranking count drifted for {month}: {len(rankings)} != {expected}")
    universe_rows.sort(key=lambda row: (str(row["day"]), str(row["exchange_symbol"])))
    rankings.sort(key=lambda row: (str(row["day"]), int(row["board_order"])))
    archive_rows.sort(key=lambda row: str(row["symbol"]))
    return universe_rows, rankings, archive_rows


def load_selected_frames(
    prereg: Mapping[str, Any],
    *,
    month: str,
    archive_root: Path,
    symbols: Sequence[str],
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    """Load selected symbols with bounded adjacent-month causal context."""

    target_start = month_start(f"{month}-01")
    start = target_start - pd.Timedelta(days=3)
    end = target_start + pd.offsets.MonthBegin(1) + pd.Timedelta(days=1)
    frames: dict[str, pd.DataFrame] = {}
    audits: list[dict[str, Any]] = []
    for symbol in sorted(set(symbols)):
        pieces: list[pd.DataFrame] = []
        for context_month in adjacent_months(month):
            path = prior.month_archive_path(archive_root, symbol, context_month)
            if not path.is_file():
                continue
            piece = prior.read_month_archive(path, symbol=symbol, month=context_month)
            pieces.append(piece)
            audits.append(
                {
                    "role": "selected_symbol_context",
                    "symbol": symbol,
                    "month": context_month,
                    "path": repo_relative(path),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                    "rows": len(piece),
                    "first_bar_open": utc(piece.iloc[0]["open_time"]).isoformat(),
                    "last_bar_open": utc(piece.iloc[-1]["open_time"]).isoformat(),
                }
            )
        if not pieces:
            raise Mover5000Error(f"selected symbol has no context: {month} {symbol}")
        frame = pd.concat(pieces, ignore_index=True)
        frame = frame.loc[(frame["open_time"] >= start) & (frame["open_time"] < end)].copy()
        frame = (
            frame.drop_duplicates("open_time", keep="last")
            .sort_values("open_time")
            .reset_index(drop=True)
        )
        if frame.empty or utc(frame.iloc[-1]["open_time"]) >= HOLDOUT_START:
            raise Mover5000Error(f"invalid selected frame: {month} {symbol}")
        frames[symbol] = add_candidate_features(frame)
    audits.sort(key=lambda row: (str(row["symbol"]), str(row["month"])))
    return frames, audits


class PackedTaskSequence(Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any]]]):
    """Small compatibility sequence used only by local tests and verification."""

    def __init__(self, frames: Mapping[str, pd.DataFrame], specs: Sequence[Mapping[str, Any]]) -> None:
        self.frames = frames
        self.specs = tuple(dict(row) for row in specs)

    def __len__(self) -> int:
        return len(self.specs)

    def __getitem__(
        self, index: int | slice
    ) -> tuple[np.ndarray, ChartTransform, dict[str, Any]] | list[
        tuple[np.ndarray, ChartTransform, dict[str, Any]]
    ]:
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        meta = dict(self.specs[index])
        frame = self.frames[str(meta["symbol"])]
        image, transform = render_chart(
            frame.iloc[int(meta["window_start_i"]) : int(meta["window_end_i"]) + 1],
            out_path=None,
        )
        meta["input_pixel_sha256"] = pixel_sha256(image)
        return image, transform, meta


def build_tasks(
    prereg: Mapping[str, Any],
    *,
    frames: Mapping[str, pd.DataFrame],
    rankings: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    """Build causal W18 tasks after the preregistered visible-window prefilter."""

    extension = int(prereg["detector"]["scan_endpoint_extension_after_day_bars"])
    history = int(prereg["detector"]["minimum_contiguous_history_bars_at_endpoint"])
    envelope_limit = float(
        prereg["detector"]["causal_prefilter"]["max_min_six_ma_envelope_atr"]
    )
    specs: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for board in rankings:
        symbol = str(board["exchange_symbol"])
        frame = frames[symbol]
        times = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True))
        bad = np.r_[0, (times[1:] - times[:-1] != BAR_DELTA).astype(np.int64)]
        bad_prefix = np.cumsum(bad)
        day = utc(board["day"])
        endpoint_end = day + pd.Timedelta(days=1) + extension * BAR_DELTA
        endpoints = np.flatnonzero((times >= day) & (times < endpoint_end))
        local: Counter[str] = Counter()
        before = len(specs)
        for endpoint_raw in endpoints:
            endpoint = int(endpoint_raw)
            history_start = endpoint - history + 1
            if history_start < 0:
                local["insufficient_history"] += 1
                continue
            if int(bad_prefix[endpoint] - bad_prefix[history_start]):
                local["non_contiguous_history"] += 1
                continue
            window_start = endpoint - 17
            window = frame.iloc[window_start : endpoint + 1]
            required = [*ALL_MA_COLS, "atr"]
            if len(window) != 18 or window.loc[:, required].isna().any().any():
                local["non_finite_features"] += 1
                continue
            ma = window.loc[:, list(ALL_MA_COLS)].to_numpy(dtype=float)
            atr = window["atr"].to_numpy(dtype=float)
            envelope = (ma.max(axis=1) - ma.min(axis=1)) / atr
            minimum = float(np.min(envelope))
            local["eligible_before_prefilter"] += 1
            if not math.isfinite(minimum) or minimum > envelope_limit:
                local["prefilter_reject"] += 1
                continue
            local["prefilter_pass"] += 1
            specs.append(
                {
                    "task_id": f"{board['source_month']}_{len(specs) + 1:07d}",
                    "source_month": str(board["source_month"]),
                    "symbol": symbol,
                    "exchange_symbol": symbol,
                    "day": day.isoformat(),
                    "mover_bucket": str(board["mover_bucket"]),
                    "bucket_rank": int(board["bucket_rank"]),
                    "rank_label": str(board["rank_label"]),
                    "board_order": int(board["board_order"]),
                    "daily_return": float(board["daily_return"]),
                    "eligible_symbol_days": int(board["eligible_symbol_days"]),
                    "window_len": 18,
                    "window_start_i": window_start,
                    "window_end_i": endpoint,
                    "window_start_time": utc(times[window_start]).isoformat(),
                    "window_end_time": utc(times[endpoint]).isoformat(),
                    "prefilter_min_six_ma_envelope_atr": minimum,
                }
            )
        totals.update(local)
        audits.append(
            {
                "source_month": str(board["source_month"]),
                "day": day.isoformat(),
                "exchange_symbol": symbol,
                "rank_label": str(board["rank_label"]),
                "endpoint_bars_present": len(endpoints),
                "model_windows": len(specs) - before,
                "scannable": len(specs) > before,
                "skip_counts": dict(sorted(local.items())),
            }
        )
    return specs, audits, totals


def create_task_pack(
    path: Path,
    *,
    month: str,
    frames: Mapping[str, pd.DataFrame],
    specs: Sequence[Mapping[str, Any]],
    config_hash: str,
) -> dict[str, Any]:
    """Create a compact immutable remote task pack from causal frame values."""

    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise FileExistsError(f"task-pack directory is not empty: {path}")
    frame_index: dict[str, str] = {}
    arrays: dict[str, np.ndarray] = {}
    frame_rows: dict[str, int] = {}
    task_symbols = {str(row["symbol"]) for row in specs}
    for index, symbol in enumerate(sorted(task_symbols)):
        key = f"f{index:04d}"
        values = frames[symbol].loc[:, list(FRAME_COLUMNS)].to_numpy(dtype=np.float64)
        if bool(np.isinf(values).any()):
            raise Mover5000Error(f"infinite packed frame: {month} {symbol}")
        frame_index[symbol] = key
        arrays[key] = values
        frame_rows[symbol] = len(values)
    frames_path = path / "frames.npz"
    np.savez_compressed(frames_path, **arrays)
    remote_tasks = [
        {
            "task_id": str(row["task_id"]),
            "symbol": str(row["symbol"]),
            "window_len": int(row["window_len"]),
            "window_start_i": int(row["window_start_i"]),
            "window_end_i": int(row["window_end_i"]),
        }
        for row in specs
    ]
    tasks_path = path / "tasks.jsonl"
    write_jsonl(tasks_path, remote_tasks)
    receipt = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "month": month,
        "config_hash": config_hash,
        "frame_columns": list(FRAME_COLUMNS),
        "frame_index": frame_index,
        "frame_rows": frame_rows,
        "task_count": len(remote_tasks),
        "frames_sha256": sha256_file(frames_path),
        "tasks_sha256": sha256_file(tasks_path),
    }
    write_json(path / "pack_receipt.json", receipt)
    receipt["pack_receipt_sha256"] = sha256_file(path / "pack_receipt.json")
    return receipt


def run_command(args: Sequence[str], *, capture: bool = False) -> str:
    """Run one checked local transport command."""

    result = subprocess.run(
        list(args),
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def ssh_args(prereg: Mapping[str, Any], host: str) -> list[str]:
    """Return strict SSH options pinned through the documented host alias."""

    alias = str(prereg["remote_cuda"]["known_hosts_alias"])
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"HostKeyAlias={alias}",
        host,
    ]


def scp_args(prereg: Mapping[str, Any]) -> list[str]:
    """Return strict SCP options matching the SSH identity contract."""

    alias = str(prereg["remote_cuda"]["known_hosts_alias"])
    return [
        "scp",
        "-q",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"HostKeyAlias={alias}",
    ]


def remote_sftp_path(host: str, path: str) -> str:
    """Return a Windows SFTP destination path."""

    if not path.startswith("C:/"):
        raise Mover5000Error(f"unexpected remote path: {path}")
    return f"{host}:/C:/{path[3:]}"


def remote_sha(prereg: Mapping[str, Any], host: str, path: str) -> str:
    """Read one remote file SHA-256 without mutating remote state."""

    command = (
        f"if (Test-Path -LiteralPath '{path}') "
        f"{{ (Get-FileHash -Algorithm SHA256 -LiteralPath '{path}').Hash.ToLower() }} "
        "else { 'missing' }"
    )
    return run_command([*ssh_args(prereg, host), command], capture=True).replace("\r", "")


def prepare_remote_runtime(
    prereg: Mapping[str, Any],
    *,
    host: str,
    source_commit: str,
) -> dict[str, str]:
    """Install hash-addressed inference-only runtime assets on the CUDA box."""

    remote_root = str(prereg["remote_cuda"]["root"])
    runtime_root = f"{remote_root}/runtime/{source_commit}"
    weight_root = f"{remote_root}/weights/{prereg['detector']['weights_sha256']}"
    directories = [
        runtime_root,
        f"{runtime_root}/yoyo",
        f"{runtime_root}/yoyo/layers",
        f"{runtime_root}/yoyo/layers/l1_detection",
        weight_root,
        f"{remote_root}/runs/{EXPERIMENT_ID}",
    ]
    mkdir = "; ".join(
        f"New-Item -ItemType Directory -Force -Path '{item}' | Out-Null" for item in directories
    )
    run_command([*ssh_args(prereg, host), mkdir])

    assets = [
        (str(prereg["remote_cuda"]["worker"]), f"{runtime_root}/worker.py"),
        ("yoyo/__init__.py", f"{runtime_root}/yoyo/__init__.py"),
        ("yoyo/layers/__init__.py", f"{runtime_root}/yoyo/layers/__init__.py"),
        (
            "yoyo/layers/l1_detection/__init__.py",
            f"{runtime_root}/yoyo/layers/l1_detection/__init__.py",
        ),
        (str(prereg["remote_cuda"]["renderer"]), f"{runtime_root}/yoyo/layers/l1_detection/render.py"),
        (
            str(prereg["remote_cuda"]["renderer_data"]),
            f"{runtime_root}/yoyo/layers/l1_detection/data.py",
        ),
    ]
    for relative, remote in assets:
        blob = git_blob_bytes(source_commit, relative)
        expected = sha256_bytes(blob)
        if relative == str(prereg["remote_cuda"]["worker"]):
            pinned_expected = str(prereg["remote_cuda"]["worker_sha256"])
        elif relative == str(prereg["remote_cuda"]["renderer"]):
            pinned_expected = str(prereg["remote_cuda"]["renderer_sha256"])
        elif relative == str(prereg["remote_cuda"]["renderer_data"]):
            pinned_expected = str(prereg["remote_cuda"]["renderer_data_sha256"])
        else:
            pinned_expected = expected
        if expected != pinned_expected:
            raise Mover5000Error(f"committed remote runtime SHA drift: {relative}")
        actual = remote_sha(prereg, host, remote)
        if actual == expected:
            continue
        if actual != "missing":
            raise Mover5000Error(f"remote immutable runtime collision: {remote}")
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(blob)
            handle.flush()
            run_command(
                [*scp_args(prereg), handle.name, remote_sftp_path(host, remote)]
            )
        if remote_sha(prereg, host, remote) != expected:
            raise Mover5000Error(f"remote runtime copy failed: {remote}")

    local_weight = ROOT / prereg["detector"]["weights"]
    remote_weight = f"{weight_root}/best.pt"
    actual_weight = remote_sha(prereg, host, remote_weight)
    if actual_weight == "missing":
        run_command(
            [*scp_args(prereg), str(local_weight), remote_sftp_path(host, remote_weight)]
        )
        actual_weight = remote_sha(prereg, host, remote_weight)
    if actual_weight != str(prereg["detector"]["weights_sha256"]):
        raise Mover5000Error("remote weight hash differs from pinned checkpoint")

    probe = str(prereg["remote_cuda"]["python_probe"])
    actual_probe = run_command([*ssh_args(prereg, host), probe], capture=True).replace("\r", "")
    if actual_probe != str(prereg["remote_cuda"]["expected_probe"]):
        raise Mover5000Error(f"remote dependency probe drifted: {actual_probe}")
    return {
        "runtime_root": runtime_root,
        "worker": f"{runtime_root}/worker.py",
        "weights": remote_weight,
        "run_root": f"{remote_root}/runs/{EXPERIMENT_ID}",
        "dependency_probe": actual_probe,
    }


def execute_remote_pack(
    prereg: Mapping[str, Any],
    *,
    host: str,
    month: str,
    pack_dir: Path,
    pack_receipt: Mapping[str, Any],
    runtime: Mapping[str, str],
    local_pull: Path,
) -> dict[str, Any]:
    """Copy one pack, run pinned CUDA inference, and pull hash-checked raw boxes."""

    remote_month = f"{runtime['run_root']}/{month}"
    remote_pack = f"{remote_month}/pack"
    remote_out = f"{remote_month}/out"
    exists = run_command(
        [
            *ssh_args(prereg, host),
            f"if (Test-Path -LiteralPath '{remote_month}') {{ 'exists' }} else {{ 'missing' }}",
        ],
        capture=True,
    ).replace("\r", "")
    if exists != "missing":
        raise Mover5000Error(f"remote month path already exists: {remote_month}")
    run_command(
        [
            *ssh_args(prereg, host),
            f"New-Item -ItemType Directory -Force -Path '{remote_pack}' | Out-Null",
        ]
    )
    for name in ("frames.npz", "tasks.jsonl", "pack_receipt.json"):
        run_command(
            [
                *scp_args(prereg),
                str(pack_dir / name),
                remote_sftp_path(host, f"{remote_pack}/{name}"),
            ]
        )

    python = str(prereg["remote_cuda"]["python"])
    command = (
        f"& '{python}' '{runtime['worker']}' "
        f"--pack-dir '{remote_pack}' --weights '{runtime['weights']}' "
        f"--runtime-source '{runtime['runtime_root']}' --out-dir '{remote_out}' "
        f"--expected-pack-sha256 '{pack_receipt['pack_receipt_sha256']}' "
        f"--expected-weights-sha256 '{prereg['detector']['weights_sha256']}' "
        f"--expected-render-sha256 '{prereg['remote_cuda']['renderer_sha256']}' "
        f"--batch-size {int(prereg['remote_cuda']['batch_size'])} "
        f"--render-workers {int(prereg['remote_cuda']['render_workers'])}"
    )
    run_command([*ssh_args(prereg, host), command])

    local_pull.mkdir(parents=True)
    for name in ("raw_boxes.jsonl", "receipt.json"):
        run_command(
            [
                *scp_args(prereg),
                remote_sftp_path(host, f"{remote_out}/{name}"),
                str(local_pull / name),
            ]
        )
    receipt = json.loads((local_pull / "receipt.json").read_text(encoding="utf-8"))
    if str(receipt["month"]) != month:
        raise Mover5000Error("remote receipt month drifted")
    if int(receipt["tasks"]) != int(pack_receipt["task_count"]):
        raise Mover5000Error("remote task count drifted")
    if sha256_file(local_pull / "raw_boxes.jsonl") != str(receipt["raw_boxes_sha256"]):
        raise Mover5000Error("pulled raw boxes SHA drifted")
    if str(receipt["pack_receipt_sha256"]) != str(pack_receipt["pack_receipt_sha256"]):
        raise Mover5000Error("remote pack identity drifted")

    remove = f"Remove-Item -LiteralPath '{remote_month}' -Recurse -Force"
    run_command([*ssh_args(prereg, host), remove])
    receipt["remote_temporary_month_removed_after_verified_pull"] = True
    return receipt


def map_remote_boxes(
    prereg: Mapping[str, Any],
    *,
    frames: Mapping[str, pd.DataFrame],
    specs: Sequence[Mapping[str, Any]],
    raw_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Map normalized CUDA boxes back to causal bars and verify rendered pixels."""

    by_id = {str(row["task_id"]): dict(row) for row in specs}
    if len(by_id) != len(specs):
        raise Mover5000Error("duplicate coordinator task IDs")
    stats: Counter[str] = Counter()
    output: list[dict[str, Any]] = []
    verified_tasks: set[str] = set()
    pixel_by_task: dict[str, str] = {}
    for raw in raw_rows:
        task_id = str(raw["task_id"])
        if task_id not in by_id:
            raise Mover5000Error(f"unknown remote task ID: {task_id}")
        meta = dict(by_id[task_id])
        if int(raw["class_id"]) not in common.CLASS_NAMES:
            stats["reject_unknown_class"] += 1
            continue
        remote_pixel = str(raw["input_pixel_sha256"])
        if task_id in pixel_by_task and pixel_by_task[task_id] != remote_pixel:
            raise Mover5000Error(f"remote pixel hash changed within task: {task_id}")
        pixel_by_task[task_id] = remote_pixel
        transform = ChartTransform(**dict(raw["transform"]))
        mapped = common.map_prediction_to_core(
            cx=float(raw["prediction_cx_norm"]),
            width=float(raw["prediction_w_norm"]),
            transform=transform,
            window_start_i=int(meta["window_start_i"]),
            window_end_i=int(meta["window_end_i"]),
        )
        stats["raw_boxes"] += 1
        if mapped["core_length_bars"] not in base.ALLOWED_CORES:
            stats["reject_core_length"] += 1
            continue
        if mapped["confirmation_bars"] not in base.ALLOWED_CONFIRMATIONS:
            stats["reject_confirmation"] += 1
            continue
        frame = frames[str(meta["symbol"])]
        times = pd.to_datetime(frame["open_time"], utc=True)
        if utc(times.iloc[mapped["core_end_i"]]).floor("D") != utc(meta["day"]):
            stats["reject_core_outside_ranked_day"] += 1
            continue
        if task_id not in verified_tasks:
            image, local_transform = render_chart(
                frame.iloc[int(meta["window_start_i"]) : int(meta["window_end_i"]) + 1],
                out_path=None,
            )
            if pixel_sha256(image) != remote_pixel:
                raise Mover5000Error(f"Mac/CUDA input pixel parity failed: {task_id}")
            if local_transform != transform:
                raise Mover5000Error(f"Mac/CUDA transform parity failed: {task_id}")
            verified_tasks.add(task_id)
        segment = frame.iloc[mapped["core_start_i"] : mapped["core_end_i"] + 1]
        class_id = int(raw["class_id"])
        output.append(
            {
                **meta,
                **mapped,
                "prediction_cx_norm": float(raw["prediction_cx_norm"]),
                "prediction_cy_norm": float(raw["prediction_cy_norm"]),
                "prediction_w_norm": float(raw["prediction_w_norm"]),
                "prediction_h_norm": float(raw["prediction_h_norm"]),
                "class_id": class_id,
                "class_name": common.CLASS_NAMES[class_id],
                "confidence": float(raw["confidence"]),
                "core_start_time": utc(times.iloc[mapped["core_start_i"]]).isoformat(),
                "core_end_time": utc(times.iloc[mapped["core_end_i"]]).isoformat(),
                "core_high": float(segment["high"].max()),
                "core_low": float(segment["low"].min()),
                "input_pixel_sha256": remote_pixel,
            }
        )
        stats["accepted_structural_boxes"] += 1
    stats["mac_cuda_pixel_parity_tasks"] = len(verified_tasks)
    return output, stats


def deduplicate_global_events(
    events: Sequence[Mapping[str, Any]], *, gap_bars: int
) -> list[dict[str, Any]]:
    """Collapse the rare same-symbol clusters that cross UTC day/month boundaries."""

    by_symbol: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_symbol[str(event["exchange_symbol"])].append(dict(event))
    merged: list[dict[str, Any]] = []
    gap = gap_bars * BAR_DELTA
    for symbol, rows in sorted(by_symbol.items()):
        rows.sort(key=lambda row: utc(row["core_end_time"]))
        clusters: list[list[dict[str, Any]]] = []
        for row in rows:
            if not clusters or utc(row["core_end_time"]) - utc(clusters[-1][-1]["core_end_time"]) >= gap:
                clusters.append([row])
            else:
                clusters[-1].append(row)
        for cluster in clusters:
            semantic = [row for row in cluster if bool(row["semantic_gate_pass"])]
            pool = semantic or cluster
            latest_endpoint = max(utc(row["window_end_time"]) for row in pool)
            representative = max(
                (row for row in pool if utc(row["window_end_time"]) == latest_endpoint),
                key=lambda row: float(row["confidence"]),
            )
            result = dict(representative)
            novelty_values = {str(row["novelty_status"]) for row in cluster}
            novelty = (
                "exact_training_input"
                if "exact_training_input" in novelty_values
                else "same_training_positive_event"
                if "same_training_positive_event" in novelty_values
                else "new_event_review"
            )
            result.update(
                {
                    "semantic_gate_pass": bool(semantic),
                    "review_bucket": "candidate_positive" if semantic else "candidate_hard_negative",
                    "first_detection_bar_open_time": min(
                        utc(row["first_detection_bar_open_time"]) for row in cluster
                    ).isoformat(),
                    "last_detection_bar_open_time": max(
                        utc(row["last_detection_bar_open_time"]) for row in cluster
                    ).isoformat(),
                    "event_peak_confidence": max(float(row["event_peak_confidence"]) for row in pool),
                    "candidate_count": sum(int(row["candidate_count"]) for row in cluster),
                    "semantic_candidate_count": sum(
                        int(row["semantic_candidate_count"]) for row in cluster
                    ),
                    "event_has_exact_training_input": any(
                        bool(row["event_has_exact_training_input"]) for row in cluster
                    ),
                    "event_near_training_positive": any(
                        bool(row["event_near_training_positive"]) for row in cluster
                    ),
                    "novelty_status": novelty,
                    "cross_day_cluster_members": len(cluster),
                    "ranking_is_post_hoc": True,
                }
            )
            result["first_available_at"] = (
                utc(result["first_detection_bar_open_time"]) + BAR_DELTA
            ).isoformat()
            merged.append(result)

    novelty_order = {
        "new_event_review": 0,
        "same_training_positive_event": 1,
        "exact_training_input": 2,
    }
    merged.sort(
        key=lambda row: (
            novelty_order[str(row["novelty_status"])],
            0 if bool(row["semantic_gate_pass"]) else 1,
            -float(row["confidence"]),
            str(row["source_month"]),
            str(row["exchange_symbol"]),
            str(row["core_end_time"]),
        )
    )
    for rank, event in enumerate(merged, 1):
        event["review_rank"] = rank
        event["event_id"] = (
            f"mover5000_{rank:05d}_{event['exchange_symbol']}_"
            f"{utc(event['core_end_time']):%Y%m%dT%H%M}"
        )
    return merged


def load_completed_shard(path: Path, *, month: str, config_hash: str) -> dict[str, Any]:
    """Validate and load a completed resumable month shard."""

    summary_path = path / "summary.json"
    if not summary_path.is_file():
        raise Mover5000Error(f"incomplete shard exists: {path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if str(summary["month"]) != month or str(summary["config_hash"]) != config_hash:
        raise Mover5000Error(f"shard identity drifted: {path}")
    for name, expected in dict(summary["artifact_sha256"]).items():
        if sha256_file(path / name) != str(expected):
            raise Mover5000Error(f"shard artifact SHA drift: {path / name}")
    return summary


def run_month(
    prereg: Mapping[str, Any],
    gates: Mapping[str, Any],
    *,
    month: str,
    shard_root: Path,
    training: Mapping[str, Any],
    source_commit: str,
    config_hash: str,
    remote_host: str,
    remote_runtime: Mapping[str, str],
) -> dict[str, Any]:
    """Build, infer, audit, and atomically publish one month shard."""

    target = shard_root / month
    if target.exists():
        return load_completed_shard(target, month=month, config_hash=config_hash)
    building = shard_root / f"{month}.building"
    if building.exists():
        raise Mover5000Error(f"partial month requires inspection before retry: {building}")
    building.mkdir(parents=True)
    started = time.perf_counter()
    archive_root = ROOT / prereg["data"]["archive_root"]
    try:
        universe, rankings, ranking_archives = build_month_rankings(
            prereg, month=month, archive_root=archive_root
        )
        frames, context_archives = load_selected_frames(
            prereg,
            month=month,
            archive_root=archive_root,
            symbols=[str(row["exchange_symbol"]) for row in rankings],
        )
        specs, board_audits, prefilter_stats = build_tasks(
            prereg, frames=frames, rankings=rankings
        )
        if not specs:
            raise Mover5000Error(f"no model tasks for {month}")

        temporary = Path(tempfile.mkdtemp(prefix=f"fable-mover5000-{month}-"))
        remote_pull = building / "remote_pull"
        try:
            pack_receipt = create_task_pack(
                temporary,
                month=month,
                frames=frames,
                specs=specs,
                config_hash=config_hash,
            )
            remote_receipt = execute_remote_pack(
                prereg,
                host=remote_host,
                month=month,
                pack_dir=temporary,
                pack_receipt=pack_receipt,
                runtime=remote_runtime,
                local_pull=remote_pull,
            )
        finally:
            expected_prefix = Path(tempfile.gettempdir()).resolve()
            if temporary.resolve().parent != expected_prefix:
                raise Mover5000Error(f"refusing unexpected temp cleanup: {temporary}")
            shutil.rmtree(temporary)

        raw_rows = read_jsonl(remote_pull / "raw_boxes.jsonl")
        structural, mapping_stats = map_remote_boxes(
            prereg,
            frames=frames,
            specs=specs,
            raw_rows=raw_rows,
        )
        structural = prior.annotate_training_overlap(structural, training)
        decisions = latest.evaluate_semantic_candidates(
            structural, frames, gates, timeframe="15m"
        )
        events = prior.deduplicate_review_events(
            decisions,
            gap_bars=int(prereg["detector"]["same_symbol_event_gap_bars"]),
        )
        for event in events:
            event["source_month"] = month
            event["event_id"] = (
                f"mover5000_{month.replace('-', '')}_{event['exchange_symbol']}_"
                f"{utc(event['core_end_time']):%Y%m%dT%H%M}"
            )

        csv_ready(universe).to_csv(building / "universe_daily_returns.csv", index=False)
        csv_ready(rankings).to_csv(building / "daily_rankings.csv", index=False)
        csv_ready(board_audits).to_csv(building / "board_scanability.csv", index=False)
        write_jsonl(building / "task_specs.jsonl", specs)
        write_jsonl(building / "semantic_decisions.jsonl", decisions)
        write_jsonl(building / "events.jsonl", events)
        write_json(building / "pack_receipt.json", pack_receipt)
        write_json(building / "remote_receipt.json", remote_receipt)

        archive_map: dict[str, dict[str, Any]] = {}
        for row in [*ranking_archives, *context_archives]:
            key = str(row["path"])
            merged = dict(row)
            roles = set(str(archive_map.get(key, {}).get("role", "")).split("|"))
            roles.add(str(row["role"]))
            merged["role"] = "|".join(sorted(role for role in roles if role))
            archive_map[key] = merged
        write_json(
            building / "source_manifest.json",
            {
                "schema_version": 1,
                "experiment_id": EXPERIMENT_ID,
                "month": month,
                "source_commit": source_commit,
                "network_market_reads": 0,
                "holdout_ohlcv_rows_materialized": 0,
                "archives": [archive_map[key] for key in sorted(archive_map)],
            },
        )

        artifact_names = [
            "universe_daily_returns.csv",
            "daily_rankings.csv",
            "board_scanability.csv",
            "task_specs.jsonl",
            "semantic_decisions.jsonl",
            "events.jsonl",
            "pack_receipt.json",
            "remote_receipt.json",
            "source_manifest.json",
        ]
        summary = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "month": month,
            "config_hash": config_hash,
            "source_commit": source_commit,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "universe_complete_symbol_days": len(universe),
                "ranked_symbol_days": len(rankings),
                "selected_unique_symbols": len(frames),
                "eligible_endpoints_before_prefilter": int(
                    prefilter_stats["eligible_before_prefilter"]
                ),
                "prefilter_pass_windows": len(specs),
                "prefilter_reject_windows": int(prefilter_stats["prefilter_reject"]),
                "raw_boxes": int(mapping_stats["raw_boxes"]),
                "structural_boxes": len(decisions),
                "semantic_boxes": sum(bool(row["semantic_gate_pass"]) for row in decisions),
                "review_events": len(events),
                "semantic_events": sum(bool(row["semantic_gate_pass"]) for row in events),
                "novel_events": sum(row["novelty_status"] == "new_event_review" for row in events),
                "mac_cuda_pixel_parity_tasks": int(mapping_stats["mac_cuda_pixel_parity_tasks"]),
            },
            "prefilter_stats": dict(sorted(prefilter_stats.items())),
            "mapping_stats": dict(sorted(mapping_stats.items())),
            "remote_wall_seconds": float(remote_receipt["wall_seconds"]),
            "wall_seconds": round(time.perf_counter() - started, 3),
            "artifact_sha256": {
                name: sha256_file(building / name) for name in artifact_names
            },
            "holdout_consumed": False,
            "network_market_reads": 0,
            "trained": False,
            "labels_or_dataset_mutated": False,
            "production_eligible": False,
            "training_eligible": False,
        }
        write_json(building / "summary.json", summary)
        shutil.rmtree(remote_pull)
        building.replace(target)
        print(
            f"month {month} tasks={len(specs)} structural={len(decisions)} "
            f"events={len(events)} novel={summary['counts']['novel_events']} "
            f"remote={remote_receipt['wall_seconds']}s",
            flush=True,
        )
        return summary
    except Exception as exc:
        write_json(
            building / "failure_receipt.json",
            {
                "month": month,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}:{exc}",
                "holdout_consumed": False,
                "trained": False,
                "trading_state_changed": False,
            },
        )
        raise


def load_all_events(shard_root: Path, months: Sequence[str]) -> list[dict[str, Any]]:
    """Load all completed per-day events in the current contiguous month prefix."""

    events: list[dict[str, Any]] = []
    for month in months:
        events.extend(read_jsonl(shard_root / month / "events.jsonl"))
    return events


def render_review_inputs(
    prereg: Mapping[str, Any],
    *,
    events: Sequence[dict[str, Any]],
    out: Path,
) -> None:
    """Render every globally de-duplicated exact input with its raw model box."""

    image_root = out / "model_inputs"
    image_root.mkdir(parents=True, exist_ok=True)
    archive_root = ROOT / prereg["data"]["archive_root"]
    by_month: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_month[str(event["source_month"])].append(event)
    for month, rows in sorted(by_month.items(), reverse=True):
        frames, _ = load_selected_frames(
            prereg,
            month=month,
            archive_root=archive_root,
            symbols=[str(row["exchange_symbol"]) for row in rows],
        )
        month_root = image_root / month
        month_root.mkdir()
        for event in rows:
            frame = frames[str(event["exchange_symbol"])]
            clean, _ = render_chart(
                frame.iloc[int(event["window_start_i"]) : int(event["window_end_i"]) + 1],
                out_path=None,
            )
            if pixel_sha256(clean) != str(event["input_pixel_sha256"]):
                raise Mover5000Error(f"final input pixel replay failed: {event['event_id']}")
            image = prior.render_exact_model_input(event, frame)
            relative = (
                Path("model_inputs")
                / month
                / f"{int(event['review_rank']):05d}_{event['exchange_symbol']}_{event['model_direction']}.png"
            )
            path = out / relative
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
                raise Mover5000Error(f"could not write {path}")
            event["model_input_chart"] = relative.as_posix()
            event["model_input_chart_sha256"] = sha256_file(path)
        print(f"rendered {month} events={len(rows)}", flush=True)


def build_overview(month_rows: Sequence[Mapping[str, Any]], counts: Mapping[str, Any]) -> np.ndarray:
    """Render a compact owner-facing scale and quality overview."""

    width, height = 1920, 1100
    image = np.full((height, width, 3), 250, dtype=np.uint8)
    cv2.putText(
        image,
        "DAILY TOP5 UP + TOP5 DOWN | GRADE-A | >=5,000 NOVEL REVIEW EVENTS",
        (55, 75),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.05,
        (25, 25, 25),
        2,
        cv2.LINE_AA,
    )
    lines = [
        f"actual months: {counts['actual_month_start']} .. {counts['actual_month_end']}  |  complete UTC days: {counts['complete_days']}",
        f"ranked symbol-days: {counts['ranked_symbol_days']:,}  |  CUDA windows: {counts['windows_scored']:,}  |  structural boxes: {counts['structural_boxes']:,}",
        f"global events: {counts['review_events']:,}  |  novel: {counts['novel_events']:,}  |  semantic-positive proposals: {counts['novel_semantic_events']:,}",
        f"novel hard negatives: {counts['novel_hard_negative_events']:,}  |  exact/near train overlap: {counts['training_overlap_events']:,}",
        "POST-HOC BOARD / P1 REVIEW ONLY / NOT GOLD / NOT VALIDATION / NOT A TRADING SIGNAL",
    ]
    for index, line in enumerate(lines):
        color = (30, 30, 180) if index == len(lines) - 1 else (40, 40, 40)
        cv2.putText(
            image,
            line,
            (60, 145 + 58 * index),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.78,
            color,
            2,
            cv2.LINE_AA,
        )
    rows = list(reversed(month_rows))
    if rows:
        x0, y0, plot_w, plot_h = 70, 510, 1780, 430
        maximum = max(int(row["global_novel_after_month"]) for row in rows)
        points: list[tuple[int, int]] = []
        for index, row in enumerate(rows):
            x = x0 + int(index / max(len(rows) - 1, 1) * plot_w)
            y = y0 + plot_h - int(int(row["global_novel_after_month"]) / maximum * plot_h)
            points.append((x, y))
        cv2.line(image, (x0, y0 + plot_h), (x0 + plot_w, y0 + plot_h), (120, 120, 120), 2)
        if len(points) >= 2:
            cv2.polylines(image, [np.asarray(points, dtype=np.int32)], False, (210, 110, 25), 5, cv2.LINE_AA)
        for x, y in points[:: max(1, len(points) // 12)]:
            cv2.circle(image, (x, y), 6, (45, 45, 210), -1, cv2.LINE_AA)
        cv2.putText(
            image,
            "Cumulative globally de-duplicated novel events (months scanned newest-first)",
            (70, 995),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (45, 45, 45),
            2,
            cv2.LINE_AA,
        )
    return image


def build_gallery(path: Path, events: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> None:
    """Build one lazy-loading searchable gallery for every review event."""

    cards: list[str] = []
    for row in events:
        semantic = "pass" if bool(row["semantic_gate_pass"]) else "reject"
        cards.append(
            "<article class='card' "
            f"data-month='{html.escape(str(row['source_month']))}' "
            f"data-side='{html.escape(str(row['model_direction']))}' "
            f"data-sem='{semantic}' data-novelty='{html.escape(str(row['novelty_status']))}' "
            f"data-symbol='{html.escape(str(row['exchange_symbol']).lower())}'>"
            f"<img loading='lazy' src='{html.escape(str(row['model_input_chart']))}'>"
            f"<div><b>#{int(row['review_rank']):05d} {html.escape(str(row['exchange_symbol']))}</b> "
            f"{html.escape(str(row['rank_label']))} {html.escape(str(row['model_direction']))} "
            f"conf={float(row['confidence']):.3f}</div>"
            f"<div>{html.escape(str(row['day']))[:10]} | day={float(row['daily_return'])*100:+.2f}% | "
            f"SEM={'PASS' if semantic == 'pass' else 'REJECT'} | {html.escape(str(row['novelty_status']))}</div>"
            "</article>"
        )
    counts = summary["counts"]
    path.write_text(
        f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Daily movers 5000 review</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f5f7;color:#222;margin:0}}main{{max-width:1880px;margin:auto;padding:24px}}.warn{{background:#fff3cd;border:1px solid #e5c365;padding:14px}}.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}.kpi{{background:white;padding:12px 18px;border-radius:9px}}.kpi b{{font-size:26px;display:block}}.controls{{position:sticky;top:0;background:#f4f5f7;padding:12px 0;z-index:3}}button,input{{font-size:15px;padding:8px 12px;margin:3px}}button.active{{background:#173f74;color:white}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}}.card{{background:white;border-radius:8px;padding:10px;box-shadow:0 1px 5px #bbb}}.card img{{width:100%;height:auto}}.card.hidden{{display:none}}</style></head><body><main>
<h1>每日 Top5 涨 / Top5 跌 · Grade-A · 5,000+ 候选复核图库</h1>
<p class='warn'><b>事后榜单，只用于 P1 人工复核。</b> 所有图是模型实际 W18 输入加原始框；模型提案不是 Gold、验证成绩或交易信号。未自动加入训练集。</p>
<div class='kpis'><div class='kpi'><b>{counts['review_events']:,}</b>总事件</div><div class='kpi'><b>{counts['novel_events']:,}</b>新事件</div><div class='kpi'><b>{counts['novel_semantic_events']:,}</b>新语义正候选</div><div class='kpi'><b>{counts['novel_hard_negative_events']:,}</b>新 hard-negative</div></div>
<div class='controls'><button class='active' data-filter='all'>全部</button><button data-filter='pass'>语义通过</button><button data-filter='reject'>语义拒绝</button><button data-filter='LONG'>LONG</button><button data-filter='SHORT'>SHORT</button><button data-filter='new_event_review'>仅新事件</button><input id='search' placeholder='搜索币种或月份'></div>
<div class='grid'>{''.join(cards)}</div>
<script>const cards=[...document.querySelectorAll('.card')],buttons=[...document.querySelectorAll('button[data-filter]')],search=document.querySelector('#search');let filter='all';function apply(){{const q=search.value.trim().toLowerCase();cards.forEach(c=>{{const ok=(filter==='all'||c.dataset.sem===filter||c.dataset.side===filter||c.dataset.novelty===filter)&&(!q||c.dataset.symbol.includes(q)||c.dataset.month.includes(q));c.classList.toggle('hidden',!ok)}})}}buttons.forEach(b=>b.onclick=()=>{{filter=b.dataset.filter;buttons.forEach(x=>x.classList.toggle('active',x===b));apply()}});search.oninput=apply;</script>
</main></body></html>""",
        encoding="utf-8",
    )


def finalize(
    prereg: Mapping[str, Any],
    *,
    out: Path,
    months: Sequence[str],
    config_hash: str,
    source_commit: str,
    remote_host: str,
    started: float,
) -> dict[str, Any]:
    """Build the global queue, exact-input folder, gallery, manifests, and receipt."""

    shard_root = out / "shards"
    per_day_events = load_all_events(shard_root, months)
    events = deduplicate_global_events(
        per_day_events,
        gap_bars=int(prereg["detector"]["same_symbol_event_gap_bars"]),
    )
    target = int(prereg["detector"]["target_novel_review_events_minimum"])
    novel = [row for row in events if row["novelty_status"] == "new_event_review"]
    if len(novel) < target:
        raise Mover5000Error(f"search range exhausted below target: {len(novel)} < {target}")

    render_review_inputs(prereg, events=events, out=out)
    write_jsonl(out / "review_queue.jsonl", events)
    csv_ready(events).to_csv(out / "review_queue.csv", index=False)

    month_summaries: list[dict[str, Any]] = []
    prefix: list[str] = []
    for month in months:
        prefix.append(month)
        summary = json.loads((shard_root / month / "summary.json").read_text(encoding="utf-8"))
        global_prefix = deduplicate_global_events(load_all_events(shard_root, prefix), gap_bars=5)
        month_summaries.append(
            {
                "month": month,
                **{f"month_{key}": value for key, value in summary["counts"].items()},
                "global_review_after_month": len(global_prefix),
                "global_novel_after_month": sum(
                    row["novelty_status"] == "new_event_review" for row in global_prefix
                ),
            }
        )
    csv_ready(month_summaries).to_csv(out / "month_summaries.csv", index=False)

    decisions: list[dict[str, Any]] = []
    source_archives: dict[str, dict[str, Any]] = {}
    shard_source_commits: set[str] = set()
    underflow_days: list[dict[str, Any]] = []
    windows = raw_boxes = structural = semantic_boxes = ranked = universe = pixel_checks = 0
    remote_seconds = 0.0
    for month in months:
        shard = shard_root / month
        month_summary = json.loads((shard / "summary.json").read_text(encoding="utf-8"))
        month_counts = month_summary["counts"]
        shard_source_commits.add(str(month_summary["source_commit"]))
        windows += int(month_counts["prefilter_pass_windows"])
        raw_boxes += int(month_counts["raw_boxes"])
        structural += int(month_counts["structural_boxes"])
        semantic_boxes += int(month_counts["semantic_boxes"])
        ranked += int(month_counts["ranked_symbol_days"])
        universe += int(month_counts["universe_complete_symbol_days"])
        pixel_checks += int(month_counts["mac_cuda_pixel_parity_tasks"])
        remote_seconds += float(month_summary["remote_wall_seconds"])
        decisions.extend(read_jsonl(shard / "semantic_decisions.jsonl"))
        universe_frame = pd.read_csv(shard / "universe_daily_returns.csv")
        for day, part in universe_frame.groupby("day", sort=True):
            positive = int((part["daily_return"] > 0).sum())
            negative = int((part["daily_return"] < 0).sum())
            missing_gainers = max(0, 5 - positive)
            missing_losers = max(0, 5 - negative)
            if missing_gainers or missing_losers:
                underflow_days.append(
                    {
                        "day": str(day),
                        "complete_symbol_days": len(part),
                        "positive_returns": positive,
                        "negative_returns": negative,
                        "zero_returns": int((part["daily_return"] == 0).sum()),
                        "missing_gainer_slots": missing_gainers,
                        "missing_loser_slots": missing_losers,
                    }
                )
        manifest = json.loads((shard / "source_manifest.json").read_text(encoding="utf-8"))
        for row in manifest["archives"]:
            key = str(row["path"])
            if key in source_archives and source_archives[key]["sha256"] != row["sha256"]:
                raise Mover5000Error(f"source archive identity conflict: {key}")
            source_archives[key] = dict(row)
    paired_null = latest.paired_direction_null(decisions)
    exact_overlap = sum(row["novelty_status"] == "exact_training_input" for row in events)
    near_overlap = sum(row["novelty_status"] == "same_training_positive_event" for row in events)
    semantic_events = [row for row in events if bool(row["semantic_gate_pass"])]
    novel_semantic = [row for row in semantic_events if row["novelty_status"] == "new_event_review"]
    novel_hard_negative = [
        row
        for row in events
        if not bool(row["semantic_gate_pass"]) and row["novelty_status"] == "new_event_review"
    ]
    complete_days = sum(pd.Period(month, freq="M").days_in_month for month in months)
    counts = {
        "actual_month_start": months[-1],
        "actual_month_end": months[0],
        "months_scanned": len(months),
        "complete_days": complete_days,
        "universe_complete_symbol_days": universe,
        "ranked_symbol_days": ranked,
        "windows_scored": windows,
        "raw_boxes": raw_boxes,
        "structural_boxes": structural,
        "semantic_boxes": semantic_boxes,
        "mac_cuda_pixel_parity_tasks": pixel_checks,
        "review_events": len(events),
        "novel_events": len(novel),
        "semantic_events": len(semantic_events),
        "novel_semantic_events": len(novel_semantic),
        "novel_hard_negative_events": len(novel_hard_negative),
        "exact_training_input_events": exact_overlap,
        "same_training_positive_events": near_overlap,
        "training_overlap_events": exact_overlap + near_overlap,
        "long_events": sum(row["model_direction"] == "LONG" for row in events),
        "short_events": sum(row["model_direction"] == "SHORT" for row in events),
        "sign_tail_underflow_days": len(underflow_days),
        "missing_gainer_slots": sum(row["missing_gainer_slots"] for row in underflow_days),
        "missing_loser_slots": sum(row["missing_loser_slots"] for row in underflow_days),
    }
    amendment = prereg["_protocol_amendment"]
    summary = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "config_hash": config_hash,
        "source_commits_used": sorted(shard_source_commits),
        "protocol_amendments": [
            {"path": amendment["path"], "sha256": amendment["sha256"]},
            {
                "path": amendment["runtime_isolation"]["path"],
                "sha256": amendment["runtime_isolation"]["sha256"],
            },
        ],
        "protocol_amendment": {
            "effective_protocol_sha256": amendment["effective_protocol_sha256"],
            "trigger_day": amendment["trigger_observation"]["day"],
        },
        "sign_tail_underflow_days": underflow_days,
        "model": prereg["detector"]["display_name"],
        "weights_sha256": prereg["detector"]["weights_sha256"],
        "remote_host_runtime_address": remote_host,
        "remote_host_key_fingerprint": prereg["remote_cuda"]["host_key_fingerprint"],
        "months_newest_first": list(months),
        "counts": counts,
        "paired_direction_null": paired_null,
        "remote_inference_wall_seconds_sum": round(remote_seconds, 3),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "ranking_is_post_hoc": True,
        "economic_backtest": False,
        "automatic_gold_or_label_mutation": False,
        "holdout_consumed": False,
        "holdout_ohlcv_rows_materialized": 0,
        "network_market_reads": 0,
        "trained": False,
        "threshold_or_weight_changed": False,
        "promoted": False,
        "active_or_frozen_changed": False,
        "forward_state_changed": False,
        "deployed": False,
        "telegram_sent": False,
        "orders_placed": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    overview = build_overview(month_summaries, counts)
    if not cv2.imwrite(str(out / "overview.png"), overview, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
        raise Mover5000Error("could not write overview")
    build_gallery(out / "gallery.html", events, summary)
    write_json(
        out / "source_manifest.json",
        {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "source_commit": source_commit,
            "network_market_reads": 0,
            "holdout_ohlcv_rows_materialized": 0,
            "archives": [source_archives[key] for key in sorted(source_archives)],
        },
    )
    summary["artifacts"] = {
        name: {
            "sha256": sha256_file(out / name),
            "size_bytes": (out / name).stat().st_size,
        }
        for name in (
            "review_queue.csv",
            "review_queue.jsonl",
            "month_summaries.csv",
            "overview.png",
            "gallery.html",
            "source_manifest.json",
        )
    }
    summary["model_input_charts"] = {
        "count": len(events),
        "directory": "model_inputs",
        "manifest_sha256": hashlib.sha256(
            "\n".join(
                f"{row['model_input_chart_sha256']}  {row['model_input_chart']}" for row in events
            ).encode("utf-8")
        ).hexdigest(),
    }
    write_json(out / "summary.json", summary)
    return summary


def run_scan(
    prereg: Mapping[str, Any],
    gates: Mapping[str, Any],
    *,
    out: Path,
    source_commit: str,
    remote_host: str,
) -> dict[str, Any]:
    """Resume monthly CUDA shards until the preregistered novel-event target is met."""

    if (out / "summary.json").exists():
        raise FileExistsError(f"official result already finalized: {out}")
    out.mkdir(parents=True, exist_ok=True)
    shard_root = out / "shards"
    shard_root.mkdir(exist_ok=True)
    started = time.perf_counter()
    config_hash = sha256_file(DEFAULT_PREREG)
    amendment = prereg["_protocol_amendment"]
    training = prior.load_training_index(prereg)
    remote_runtime = prepare_remote_runtime(
        prereg, host=remote_host, source_commit=str(prereg["source_commit"])
    )
    completed: list[str] = []
    target = int(prereg["detector"]["target_novel_review_events_minimum"])
    for month in search_months(prereg):
        run_month(
            prereg,
            gates,
            month=month,
            shard_root=shard_root,
            training=training,
            source_commit=source_commit,
            config_hash=config_hash,
            remote_host=remote_host,
            remote_runtime=remote_runtime,
        )
        completed.append(month)
        global_events = deduplicate_global_events(load_all_events(shard_root, completed), gap_bars=5)
        novel = sum(row["novelty_status"] == "new_event_review" for row in global_events)
        print(
            f"progress months={len(completed)} range={month}..{completed[0]} "
            f"global_events={len(global_events)} novel={novel}/{target}",
            flush=True,
        )
        write_json(
            out / "run_state.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "source_commit": source_commit,
                "config_hash": config_hash,
                "protocol_amendment_sha256s": amendment["amendment_sha256s"],
                "effective_protocol_sha256": amendment["effective_protocol_sha256"],
                "months_newest_first": completed,
                "global_review_events": len(global_events),
                "global_novel_events": novel,
                "target_novel_events": target,
                "holdout_consumed": False,
                "trained": False,
            },
        )
        if novel >= target:
            break
    return finalize(
        prereg,
        out=out,
        months=completed,
        config_hash=config_hash,
        source_commit=source_commit,
        remote_host=remote_host,
        started=started,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--remote-host", required=True)
    args = parser.parse_args()
    prereg_path = args.prereg.resolve()
    prereg, gates = load_preregistration(prereg_path)
    source_commit = verify_immutable_sources(prereg_path, prereg)
    summary = run_scan(
        prereg,
        gates,
        out=args.out.resolve(),
        source_commit=source_commit,
        remote_host=args.remote_host,
    )
    print(
        f"complete novel={summary['counts']['novel_events']} "
        f"events={summary['counts']['review_events']} -> {args.out.resolve()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
