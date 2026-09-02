#!/usr/bin/env python3
"""Freeze and scan standard-retail A shares at completed 1h and session-4h bars.

This owner-authorized experiment applies the existing crypto-15m Grade-A YOLO
checkpoint unchanged to two out-of-distribution A-share timeframes.  It has
three deliberately separated phases:

``--fetch``
    Reuse the already frozen 2026-09-02 11:30 CST all-A-share identity file,
    retain only ordinary Shanghai/Shenzhen main-board names, then freeze QFQ
    60-minute Eastmoney rows through the preregistered 14:00 CST cutoff.  A
    consumption-start receipt is written before the first current-bar request.

``--scan``
    Use only frozen CSV bytes.  The 1h view scores the one latest completed
    endpoint.  The session-4h view first aggregates exactly four same-date 60m
    bars (09:30-11:30 and 13:00-15:00 trading sessions) and scores the last five
    complete trading-day endpoints.  ``add_candidate_features`` consumes only
    OHLCV rows at or before each endpoint; W18/W19 inputs and the causal semantic
    gate cannot see a later row.

``--verify``
    Re-hash every frozen candle, rebuild session bars, replay every structural
    input pixel hash and semantic decision, and rerender each delivered chart.
    It performs neither network reads nor model inference.

Both detector classes are retained in audit ledgers.  Only LONG semantic events
are charted and delivered, and every delivered identity contains a six-digit
code plus an exchange-qualified ``SHxxxxxx``/``SZxxxxxx`` search key.

These are completed-history OOD research proposals, not validated trade signals
or recommendations.  The checkpoint was trained on crypto 15m charts, not on
A-share 1h or 4h charts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_15m_ashare_yolo_latest as base
from scripts.filter_ashare_signals_for_standard_retail import (
    STANDARD_BOARDS,
    classify_board,
    normalize_code,
    restricted_name_reason,
)
from scripts.scan_15m_ma_launch_t3_daily_movers import (
    choose_device,
    deduplicate_hits,
)
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.data.ashare_sessions import (
    ASHARE_HOURLY_CLOSE_SLOTS,
    aggregate_complete_session_4h,
)
from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.render import ChartTransform, render_chart

EXPERIMENT_ID = "exp-ashare-grade-a-yolo-1h4h-long-20260902-v1"
PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ashare_1h4h_long_20260902_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
SOURCE_UNIVERSE = (
    ROOT / "analysis/output/ashare_15m_yolo_latest_20260902_v1/universe.csv"
)
SOURCE_FETCH_RECEIPT = (
    ROOT / "analysis/output/ashare_15m_yolo_latest_20260902_v1/fetch_receipt.json"
)
RETAIL_FILTER = ROOT / "scripts/filter_ashare_signals_for_standard_retail.py"
SESSION_AGGREGATOR = ROOT / "yoyo/data/ashare_sessions.py"
WEIGHTS = base.WEIGHTS
AUTOFILL_PREREG = base.AUTOFILL_PREREG

EXPECTED_WEIGHT_SHA256 = base.EXPECTED_WEIGHT_SHA256
EXPECTED_UNIVERSE_SHA256 = (
    "9c4bda359560c64f756a9a6079e748a9b0ff46b9a3a21028009050b14c158508"
)
EXPECTED_SOURCE_RECEIPT_SHA256 = (
    "eeb07cd1d364ffb2587ba28bfb87705a16750ecd1a990512e08b23926b891089"
)
EXPECTED_RETAIL_FILTER_SHA256 = (
    "9acb475c271e22fcb6199803fdd8f0cc7ace9ffb633b322219d3d5a42adeabaa"
)
EXPECTED_SESSION_AGGREGATOR_SHA256 = (
    "78d1801d4a46052c2ce63e85dda044299ac5c06a27e79dd54770922a2dabfdec"
)
EXPECTED_UNIVERSE_ROWS = 3111
EXPECTED_BOARD_COUNTS = {"SH_MAIN": 1666, "SZ_MAIN": 1445}

KLINE_URL = base.KLINE_URL
REFERENCE_SECID = base.REFERENCE_SECID
REFERENCE_NAME = base.REFERENCE_NAME
KLINE_LIMIT = 1024
SOURCE_BAR_DELTA = pd.Timedelta(hours=1)
ONE_HOUR_CUTOFF_CST = pd.Timestamp("2026-09-02T14:00:00+08:00")
FOUR_HOUR_CUTOFF_CST = pd.Timestamp("2026-09-01T15:00:00+08:00")
ONE_HOUR_SCHEDULE_MATCH_BARS = 160
FOUR_HOUR_SCHEDULE_MATCH_BARS = 160
FOUR_HOUR_RECENT_ENDPOINTS = 5
MINIMUM_TIMEFRAME_COVERAGE = 0.80
MAX_NETWORK_FAILURE_RATE = 0.01
HOURLY_CLOSE_SLOTS = ASHARE_HOURLY_CLOSE_SLOTS
HOURLY_CLOSE_SLOT_SET = frozenset(HOURLY_CLOSE_SLOTS)

IMAGE_SIZE = base.IMAGE_SIZE
WINDOW_LENGTHS = base.WINDOW_LENGTHS
CONFIDENCE = base.CONFIDENCE
NMS_IOU = base.NMS_IOU
ALLOWED_CORES = base.ALLOWED_CORES
ALLOWED_CONFIRMATIONS = base.ALLOWED_CONFIRMATIONS
EVENT_GAP_BARS = base.EVENT_GAP_BARS
CLASS_NAMES = base.CLASS_NAMES
CLASS_COLORS = base.CLASS_COLORS

CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1400
MAIN_X = 20
MAIN_Y = 112
MAIN_WIDTH = 1880
MAIN_HEIGHT = 760
CONTEXT_BARS = 128
INSET_WIDTH = 700
INSET_HEIGHT = 406


class AShareMultiTimeframeError(RuntimeError):
    """Raised when source, schedule, contract, model, or artifact identity drifts."""


sha256_file = base.sha256_file
pixel_sha256 = base.pixel_sha256
read_json = base.read_json
write_json = base.write_json
write_jsonl = base.write_jsonl
read_jsonl = base.read_jsonl
utc = base.utc
cst = base.cst


def _git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def require_builder_committed() -> str:
    """Refuse any holdout read or artifact build from an uncommitted contract."""

    relative = [
        str(Path(__file__).resolve().relative_to(ROOT)),
        str(PREREG.relative_to(ROOT)),
        str(RETAIL_FILTER.relative_to(ROOT)),
        str(SESSION_AGGREGATOR.relative_to(ROOT)),
    ]
    for path in relative:
        _git_output("ls-files", "--error-unmatch", path)
    dirty = _git_output("status", "--porcelain", "--", *relative)
    if dirty:
        raise AShareMultiTimeframeError(
            f"builder/preregistration/filter must be committed first: {dirty}"
        )
    if _git_output("branch", "--show-current") != "main":
        raise AShareMultiTimeframeError("experiment must run from main")
    return _git_output("rev-parse", "HEAD")


def verify_frozen_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify preregistered timeframes, immutable model inputs, and safety flags."""

    prereg = read_json(PREREG)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise AShareMultiTimeframeError("preregistration experiment identity drifted")
    configs = prereg.get("configuration_consumptions")
    expected_configs = [
        (
            "mainland_A_share_1h_latest_completed_endpoint",
            9,
            ONE_HOUR_CUTOFF_CST.isoformat(),
            1,
        ),
        (
            "mainland_A_share_session_4h_recent_five_complete_days",
            10,
            FOUR_HOUR_CUTOFF_CST.isoformat(),
            FOUR_HOUR_RECENT_ENDPOINTS,
        ),
    ]
    actual_configs = [
        (
            str(item["configuration"]),
            int(item["holdout_consumption_number_for_checkpoint"]),
            str(item["cutoff_close_cst"]),
            int(item["latest_endpoints_per_symbol"]),
        )
        for item in configs or []
    ]
    if actual_configs != expected_configs:
        raise AShareMultiTimeframeError("configuration/holdout contract drifted")
    safety = prereg["safety"]
    if safety.get("holdout_consumed") is not True or safety.get(
        "holdout_consumption_numbers_for_checkpoint"
    ) != {"1h": 9, "4h": 10}:
        raise AShareMultiTimeframeError("holdout consumption identity drifted")
    for key in (
        "training",
        "threshold_or_weight_change",
        "active_or_frozen_change",
        "promotion",
        "deployment",
        "forward_state_change",
        "telegram_send",
        "order_action",
        "training_eligible",
        "production_eligible",
    ):
        if safety.get(key) is not False:
            raise AShareMultiTimeframeError(f"unsafe preregistration switch: {key}")
    pinned = {
        WEIGHTS: prereg["model_contract"]["weights_sha256"],
        ROOT / prereg["model_contract"]["renderer_path"]: prereg["model_contract"][
            "renderer_sha256"
        ],
        ROOT / prereg["model_contract"]["ma_builder_path"]: prereg[
            "model_contract"
        ]["ma_builder_sha256"],
        ROOT / prereg["semantic_gate_contract"]["module_path"]: prereg[
            "semantic_gate_contract"
        ]["module_sha256"],
        ROOT / prereg["semantic_gate_contract"]["threshold_source_path"]: prereg[
            "semantic_gate_contract"
        ]["threshold_source_sha256"],
        SOURCE_UNIVERSE: EXPECTED_UNIVERSE_SHA256,
        SOURCE_FETCH_RECEIPT: EXPECTED_SOURCE_RECEIPT_SHA256,
        RETAIL_FILTER: EXPECTED_RETAIL_FILTER_SHA256,
        SESSION_AGGREGATOR: EXPECTED_SESSION_AGGREGATOR_SHA256,
    }
    for path, expected in pinned.items():
        if not path.is_file() or sha256_file(path) != str(expected):
            raise AShareMultiTimeframeError(f"frozen input SHA drift: {path}")
    if sha256_file(WEIGHTS) != EXPECTED_WEIGHT_SHA256:
        raise AShareMultiTimeframeError("checkpoint identity drifted")
    gates = dict(read_json(AUTOFILL_PREREG)["morphology_gate"])
    return prereg, gates


def load_standard_retail_universe() -> pd.DataFrame:
    """Rebuild the preregistered basic-account universe from frozen identities."""

    rows = pd.read_csv(SOURCE_UNIVERSE, dtype={"code": str, "secid": str})
    required = {"market", "code", "name"}
    if not required.issubset(rows.columns):
        raise AShareMultiTimeframeError("source universe schema drifted")
    rows["code"] = rows["code"].map(normalize_code)
    rows["market"] = pd.to_numeric(rows["market"], errors="raise").astype(int)
    rows["board"] = [
        classify_board(code, market)
        for code, market in zip(rows["code"], rows["market"])
    ]
    rows["restriction"] = rows["name"].map(restricted_name_reason)
    rows = rows[
        rows["board"].isin(STANDARD_BOARDS) & rows["restriction"].eq("")
    ].copy()
    rows["exchange"] = np.where(rows["market"].eq(1), "SH", "SZ")
    rows["search_key"] = rows["exchange"] + rows["code"]
    rows["secid"] = rows["market"].astype(str) + "." + rows["code"]
    rows.sort_values(["market", "code"], inplace=True, ignore_index=True)
    counts = rows["board"].value_counts().sort_index().to_dict()
    if len(rows) != EXPECTED_UNIVERSE_ROWS or counts != EXPECTED_BOARD_COUNTS:
        raise AShareMultiTimeframeError(
            f"standard-retail universe drifted: rows={len(rows)} boards={counts}"
        )
    if rows["secid"].duplicated().any() or rows["search_key"].duplicated().any():
        raise AShareMultiTimeframeError("duplicate standard-retail identity")
    return rows


def _parse_hourly_payload(
    payload: Mapping[str, Any], *, secid: str, adjustment: str
) -> pd.DataFrame:
    """Parse 60m close-labelled rows through the frozen 1h cutoff."""

    raw_rows = (payload.get("data") or {}).get("klines") or []
    parsed: list[list[Any]] = []
    for raw in raw_rows:
        fields = str(raw).split(",")
        if len(fields) < 7:
            continue
        close_time = pd.Timestamp(fields[0])
        if close_time.tzinfo is None:
            close_time = close_time.tz_localize("Asia/Shanghai")
        else:
            close_time = close_time.tz_convert("Asia/Shanghai")
        if close_time > ONE_HOUR_CUTOFF_CST:
            continue
        parsed.append(
            [
                close_time,
                (close_time - SOURCE_BAR_DELTA).tz_convert("UTC"),
                float(fields[1]),
                float(fields[3]),
                float(fields[4]),
                float(fields[2]),
                float(fields[5]),
                float(fields[6]),
            ]
        )
    frame = pd.DataFrame(
        parsed,
        columns=[
            "raw_close_time",
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ],
    )
    if frame.empty:
        raise AShareMultiTimeframeError(f"no retained 60m bars:{secid}")
    frame.sort_values("raw_close_time", inplace=True, ignore_index=True)
    frame.drop_duplicates(
        "raw_close_time", keep="last", inplace=True, ignore_index=True
    )
    numeric = frame[["open", "high", "low", "close", "volume", "amount"]].to_numpy(
        dtype=float
    )
    if not np.isfinite(numeric).all():
        raise AShareMultiTimeframeError(f"non-finite OHLCVA:{secid}")
    if bool((frame[["open", "high", "low", "close"]] <= 0).any().any()):
        raise AShareMultiTimeframeError(f"non-positive OHLC:{secid}")
    body_high = frame[["open", "close"]].max(axis=1)
    body_low = frame[["open", "close"]].min(axis=1)
    if bool((frame["high"] < body_high).any()) or bool(
        (frame["low"] > body_low).any()
    ):
        raise AShareMultiTimeframeError(f"invalid candle bounds:{secid}")
    if bool((frame[["volume", "amount"]] < 0).any().any()):
        raise AShareMultiTimeframeError(f"negative volume/amount:{secid}")
    slots = set(frame["raw_close_time"].dt.strftime("%H:%M"))
    unexpected = sorted(slots - HOURLY_CLOSE_SLOT_SET)
    if unexpected:
        raise AShareMultiTimeframeError(
            f"unexpected 60m close labels {unexpected}:{secid}"
        )
    frame["secid"] = secid
    frame["adjustment"] = adjustment
    return frame.tail(KLINE_LIMIT).reset_index(drop=True)


def fetch_hourly(secid: str, *, fqt: str, adjustment: str) -> pd.DataFrame:
    """Fetch one bounded 60m page with the frozen AKShare-compatible fields."""

    payload = base.request_json(
        KLINE_URL,
        {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "60",
            "fqt": fqt,
            "secid": secid,
            "beg": "0",
            "end": "20500000",
            "lmt": str(KLINE_LIMIT),
        },
    )
    return _parse_hourly_payload(payload, secid=secid, adjustment=adjustment)


def load_hourly(path: Path) -> pd.DataFrame:
    """Load one frozen source CSV with explicit timestamp and numeric types."""

    frame = pd.read_csv(path, dtype={"secid": str, "adjustment": str})
    required = {
        "raw_close_time",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "secid",
        "adjustment",
    }
    if not required.issubset(frame.columns):
        raise AShareMultiTimeframeError(f"hourly snapshot schema drift:{path}")
    frame["raw_close_time"] = pd.to_datetime(
        frame["raw_close_time"], utc=True
    ).dt.tz_convert("Asia/Shanghai")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume", "amount"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def validate_one_hour_schedule(
    frame: pd.DataFrame, reference: pd.DataFrame, *, secid: str
) -> None:
    """Require the exact completed 1h endpoint and trailing reference schedule."""

    if len(frame) < ONE_HOUR_SCHEDULE_MATCH_BARS:
        raise AShareMultiTimeframeError(f"1h_insufficient_history:{len(frame)}")
    latest = pd.Timestamp(frame.iloc[-1]["raw_close_time"])
    if latest != ONE_HOUR_CUTOFF_CST:
        raise AShareMultiTimeframeError(f"1h_stale_latest:{latest.isoformat()}")
    actual = pd.DatetimeIndex(
        frame["raw_close_time"].iloc[-ONE_HOUR_SCHEDULE_MATCH_BARS:]
    )
    expected = pd.DatetimeIndex(
        reference["raw_close_time"].iloc[-ONE_HOUR_SCHEDULE_MATCH_BARS:]
    )
    if not actual.equals(expected):
        raise AShareMultiTimeframeError("1h_schedule_mismatch")
    opens = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True))
    expected_opens = (
        pd.DatetimeIndex(pd.to_datetime(frame["raw_close_time"], utc=True))
        - SOURCE_BAR_DELTA
    )
    if not opens.equals(expected_opens):
        raise AShareMultiTimeframeError(f"1h_open_conversion_drift:{secid}")


def aggregate_session_four_hour(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate exactly one four-trading-hour A-share session per complete date."""

    return aggregate_complete_session_4h(
        frame,
        cutoff_close=FOUR_HOUR_CUTOFF_CST,
    )


def validate_four_hour_schedule(
    frame: pd.DataFrame, reference: pd.DataFrame, *, secid: str
) -> None:
    """Require 160 exact complete sessions ending at the preregistered cutoff."""

    if len(frame) < FOUR_HOUR_SCHEDULE_MATCH_BARS:
        raise AShareMultiTimeframeError(f"4h_insufficient_history:{len(frame)}")
    latest = pd.Timestamp(frame.iloc[-1]["raw_close_time"])
    if latest != FOUR_HOUR_CUTOFF_CST:
        raise AShareMultiTimeframeError(f"4h_stale_latest:{latest.isoformat()}")
    actual = pd.DatetimeIndex(
        frame["raw_close_time"].iloc[-FOUR_HOUR_SCHEDULE_MATCH_BARS:]
    )
    expected = pd.DatetimeIndex(
        reference["raw_close_time"].iloc[-FOUR_HOUR_SCHEDULE_MATCH_BARS:]
    )
    if not actual.equals(expected):
        raise AShareMultiTimeframeError("4h_schedule_mismatch")
    local_opens = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True)).tz_convert(
        "Asia/Shanghai"
    )
    if set(local_opens.strftime("%H:%M")) != {"09:30"}:
        raise AShareMultiTimeframeError(f"4h_open_conversion_drift:{secid}")
    if set(frame["raw_close_time"].dt.strftime("%H:%M")) != {"15:00"}:
        raise AShareMultiTimeframeError(f"4h_close_conversion_drift:{secid}")
    if not bool(frame["source_rows"].eq(4).all()):
        raise AShareMultiTimeframeError(f"4h_source_count_drift:{secid}")


def select_scan_endpoints(frame: pd.DataFrame, timeframe: str) -> list[int]:
    """Return the preregistered endpoint indices for one eligible frame."""

    if timeframe == "1h":
        return [len(frame) - 1]
    if timeframe == "4h":
        if len(frame) < FOUR_HOUR_RECENT_ENDPOINTS:
            raise AShareMultiTimeframeError("4h endpoint history unexpectedly short")
        return list(range(len(frame) - FOUR_HOUR_RECENT_ENDPOINTS, len(frame)))
    raise AShareMultiTimeframeError(f"unsupported timeframe:{timeframe}")


def _error_reason(error: str) -> str:
    for prefix in (
        "1h_insufficient_history",
        "1h_stale_latest",
        "1h_schedule_mismatch",
        "4h_insufficient_history",
        "4h_stale_latest",
        "4h_schedule_mismatch",
        "no retained 60m bars",
        "same-source request failed",
        "unexpected 60m close labels",
    ):
        if prefix in error:
            return prefix
    return "other"


def _identity(row: Mapping[str, Any]) -> dict[str, Any]:
    code = normalize_code(row["code"])
    market = int(row["market"])
    exchange = "SH" if market == 1 else "SZ"
    return {
        "secid": f"{market}.{code}",
        "market": market,
        "code": code,
        "name": str(row["name"]),
        "board": str(row["board"]),
        "exchange": exchange,
        "search_key": f"{exchange}{code}",
    }


def fetch_snapshot(out: Path, *, workers: int) -> dict[str, Any]:
    """Freeze standard-retail 60m histories and both timeframe eligibility sets."""

    source_commit = require_builder_committed()
    verify_frozen_contract()
    if out.exists():
        raise FileExistsError(f"refusing to overwrite frozen snapshot:{out}")
    building = out.with_name(out.name + ".building")
    hourly_dir = building / "hourly"
    building.mkdir(parents=True, exist_ok=True)
    hourly_dir.mkdir(exist_ok=True)
    plan_path = building / "fetch_plan.json"
    universe_path = building / "universe.csv"
    consumption_path = building / "holdout_consumption_started.json"
    if plan_path.is_file() and universe_path.is_file():
        plan = read_json(plan_path)
        universe = pd.read_csv(universe_path, dtype={"code": str, "secid": str})
        if sha256_file(universe_path) != str(plan["universe_sha256"]):
            raise AShareMultiTimeframeError("resumed universe bytes drifted")
        print(f"resuming frozen standard-retail universe rows={len(universe)}", flush=True)
    else:
        universe = load_standard_retail_universe()
        universe.to_csv(universe_path, index=False)
        plan = {
            "experiment_id": EXPERIMENT_ID,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "source_universe_sha256": EXPECTED_UNIVERSE_SHA256,
            "universe_rows": len(universe),
            "universe_sha256": sha256_file(universe_path),
            "cutoffs_cst": {
                "1h": ONE_HOUR_CUTOFF_CST.isoformat(),
                "4h": FOUR_HOUR_CUTOFF_CST.isoformat(),
            },
            "holdout_consumption_numbers_for_checkpoint": {"1h": 9, "4h": 10},
        }
        write_json(plan_path, plan)
    if len(universe) != EXPECTED_UNIVERSE_ROWS:
        raise AShareMultiTimeframeError("frozen standard-retail count drifted")
    if consumption_path.is_file():
        consumption = read_json(consumption_path)
        if consumption.get("holdout_consumption_numbers_for_checkpoint") != {
            "1h": 9,
            "4h": 10,
        }:
            raise AShareMultiTimeframeError("resumed consumption ledger drifted")
    else:
        write_json(
            consumption_path,
            {
                "experiment_id": EXPERIMENT_ID,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "source_commit": source_commit,
                "holdout_consumption_numbers_for_checkpoint": {"1h": 9, "4h": 10},
                "warning": "Any current-bar read after this ledger exists consumes both preregistered configurations even if the source or later scan fails.",
            },
        )

    reference_path = building / "reference_60m.csv"
    if reference_path.is_file():
        reference_1h = load_hourly(reference_path)
    else:
        reference_1h = fetch_hourly(REFERENCE_SECID, fqt="0", adjustment="none")
        reference_1h.to_csv(reference_path, index=False)
    validate_one_hour_schedule(reference_1h, reference_1h, secid=REFERENCE_SECID)
    reference_4h = aggregate_session_four_hour(reference_1h)
    validate_four_hour_schedule(reference_4h, reference_4h, secid=REFERENCE_SECID)

    snapshots: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reused = 0

    def one(
        row: Mapping[str, Any],
    ) -> tuple[dict[str, Any], pd.DataFrame | None, dict[str, Any], bool]:
        identity = _identity(row)
        path = hourly_dir / f"{identity['market']}_{identity['code']}.csv"
        try:
            was_reused = path.is_file()
            hourly = (
                load_hourly(path)
                if was_reused
                else fetch_hourly(identity["secid"], fqt="1", adjustment="qfq")
            )
        except Exception as exc:  # noqa: BLE001 - failure is fully receipted
            error = f"{type(exc).__name__}:{exc}"
            return identity, None, {
                "eligible_1h": False,
                "eligible_4h": False,
                "reason_1h": _error_reason(error),
                "reason_4h": _error_reason(error),
                "error_1h": error,
                "error_4h": error,
            }, False
        status: dict[str, Any] = {}
        try:
            validate_one_hour_schedule(hourly, reference_1h, secid=identity["secid"])
            status.update(eligible_1h=True, reason_1h="", error_1h="")
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}:{exc}"
            status.update(
                eligible_1h=False,
                reason_1h=_error_reason(error),
                error_1h=error,
            )
        four_hour = aggregate_session_four_hour(hourly)
        try:
            validate_four_hour_schedule(
                four_hour, reference_4h, secid=identity["secid"]
            )
            status.update(eligible_4h=True, reason_4h="", error_4h="")
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}:{exc}"
            status.update(
                eligible_4h=False,
                reason_4h=_error_reason(error),
                error_4h=error,
            )
        status["four_hour_rows"] = len(four_hour)
        return identity, hourly, status, was_reused

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(one, row): row for row in universe.to_dict("records")
        }
        for number, future in enumerate(as_completed(futures), 1):
            identity, hourly, status, was_reused = future.result()
            if hourly is None or not (
                bool(status["eligible_1h"]) or bool(status["eligible_4h"])
            ):
                failures.append({**identity, **status})
            else:
                path = hourly_dir / f"{identity['market']}_{identity['code']}.csv"
                if not path.is_file():
                    hourly.to_csv(path, index=False)
                reused += int(was_reused)
                snapshots.append(
                    {
                        **identity,
                        **status,
                        "path": f"hourly/{path.name}",
                        "sha256": sha256_file(path),
                        "rows_1h": len(hourly),
                        "first_close_cst": pd.Timestamp(
                            hourly.iloc[0]["raw_close_time"]
                        ).isoformat(),
                        "last_close_cst": pd.Timestamp(
                            hourly.iloc[-1]["raw_close_time"]
                        ).isoformat(),
                    }
                )
            if number % 100 == 0 or number == len(universe):
                usable_1h = sum(bool(item["eligible_1h"]) for item in snapshots)
                usable_4h = sum(bool(item["eligible_4h"]) for item in snapshots)
                print(
                    f"fetch {number}/{len(universe)} usable_1h={usable_1h} usable_4h={usable_4h} neither={len(failures)} reused={reused}",
                    flush=True,
                )

    snapshots.sort(key=lambda row: (int(row["market"]), str(row["code"])))
    failures.sort(key=lambda row: (int(row["market"]), str(row["code"])))
    usable = {
        "1h": sum(bool(item["eligible_1h"]) for item in snapshots),
        "4h": sum(bool(item["eligible_4h"]) for item in snapshots),
    }
    coverage = {key: value / len(universe) for key, value in usable.items()}
    all_status_rows = [*snapshots, *failures]
    reasons = {
        timeframe: dict(
            sorted(
                Counter(
                    str(row[f"reason_{timeframe}"])
                    for row in all_status_rows
                    if not bool(row[f"eligible_{timeframe}"])
                ).items()
            )
        )
        for timeframe in ("1h", "4h")
    }
    network_failures = sum(
        "same-source request failed" in str(row.get("error_1h", ""))
        for row in all_status_rows
    )
    if (
        min(coverage.values()) < MINIMUM_TIMEFRAME_COVERAGE
        or network_failures > max(10, int(len(universe) * MAX_NETWORK_FAILURE_RATE))
    ):
        write_json(
            building / "incomplete_fetch_receipt.json",
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "universe_rows": len(universe),
                "usable_symbols": usable,
                "coverage": coverage,
                "failure_reasons": reasons,
                "network_failures": network_failures,
                "holdout_consumption_numbers_for_checkpoint": {"1h": 9, "4h": 10},
                "resume_allowed": True,
            },
        )
        raise AShareMultiTimeframeError(
            f"snapshot coverage failed closed: coverage={coverage} network_failures={network_failures}"
        )
    receipt = {
        "protocol": "ashare_standard_retail_60m_source_for_1h_session4h_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "owner_authorized_holdout_read": True,
        "holdout_consumption_numbers_for_checkpoint": {"1h": 9, "4h": 10},
        "upstream": {
            "source": "Eastmoney request shape pinned to AKShare documentation/implementation",
            "kline_url": KLINE_URL,
            "period": "60",
            "eastmoney_public_api_contract": "unpublished_and_unsupported",
        },
        "universe_rule": "SH_MAIN/SZ_MAIN and no ST/*ST/PT/delisting name",
        "universe_rows": len(universe),
        "universe_csv": "universe.csv",
        "universe_sha256": sha256_file(universe_path),
        "source_universe_sha256": EXPECTED_UNIVERSE_SHA256,
        "usable_symbols": usable,
        "coverage": coverage,
        "failure_reasons": reasons,
        "failures": failures,
        "cutoffs_cst": {
            "1h": ONE_HOUR_CUTOFF_CST.isoformat(),
            "4h": FOUR_HOUR_CUTOFF_CST.isoformat(),
        },
        "adjustment": "qfq",
        "requested_rows_per_symbol": KLINE_LIMIT,
        "schedule_match_bars": {
            "1h": ONE_HOUR_SCHEDULE_MATCH_BARS,
            "4h": FOUR_HOUR_SCHEDULE_MATCH_BARS,
        },
        "reference": {
            "secid": REFERENCE_SECID,
            "name": REFERENCE_NAME,
            "path": "reference_60m.csv",
            "sha256": sha256_file(reference_path),
            "rows_1h": len(reference_1h),
            "rows_4h": len(reference_4h),
        },
        "snapshots": snapshots,
        "network_reads": "One SSE Composite 60m request plus one bounded qfq 60m request per frozen standard-retail identity, with bounded same-source retries only",
        "model_loaded": False,
        "training_or_tuning": False,
        "threshold_or_weight_changed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "telegram_sent": False,
        "orders_placed": False,
        "production_eligible": False,
    }
    write_json(building / "fetch_receipt.json", receipt)
    os.replace(building, out)
    print(
        f"snapshot complete universe={len(universe)} usable_1h={usable['1h']} usable_4h={usable['4h']} -> {out}",
        flush=True,
    )
    return receipt


def load_snapshot(
    out: Path,
) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Verify snapshot identity and load both reference schedules."""

    receipt = read_json(out / "fetch_receipt.json")
    if receipt.get("experiment_id") != EXPERIMENT_ID:
        raise AShareMultiTimeframeError("fetch receipt experiment identity drifted")
    if receipt.get("holdout_consumption_numbers_for_checkpoint") != {
        "1h": 9,
        "4h": 10,
    }:
        raise AShareMultiTimeframeError("fetch receipt holdout identity drifted")
    universe_path = out / str(receipt["universe_csv"])
    if sha256_file(universe_path) != str(receipt["universe_sha256"]):
        raise AShareMultiTimeframeError("snapshot universe bytes drifted")
    reference_path = out / str(receipt["reference"]["path"])
    if sha256_file(reference_path) != str(receipt["reference"]["sha256"]):
        raise AShareMultiTimeframeError("reference schedule bytes drifted")
    reference_1h = load_hourly(reference_path)
    reference_4h = aggregate_session_four_hour(reference_1h)
    validate_one_hour_schedule(reference_1h, reference_1h, secid=REFERENCE_SECID)
    validate_four_hour_schedule(reference_4h, reference_4h, secid=REFERENCE_SECID)
    snapshots = list(receipt["snapshots"])
    if sum(bool(row["eligible_1h"]) for row in snapshots) != int(
        receipt["usable_symbols"]["1h"]
    ) or sum(bool(row["eligible_4h"]) for row in snapshots) != int(
        receipt["usable_symbols"]["4h"]
    ):
        raise AShareMultiTimeframeError("snapshot receipt counts drifted")
    return snapshots, reference_1h, reference_4h, receipt


def _frame_for_timeframe(hourly: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "1h":
        return hourly
    if timeframe == "4h":
        return aggregate_session_four_hour(hourly)
    raise AShareMultiTimeframeError(f"unsupported timeframe:{timeframe}")


def _deduplicate_semantic(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate each symbol/timeframe/direction under the frozen five-bar gap."""

    grouped: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for row in rows:
        if bool(row.get("semantic_pass")):
            grouped[
                (str(row["timeframe"]), str(row["secid"]), str(row["direction"]))
            ].append(dict(row))
    events: list[dict[str, Any]] = []
    for (timeframe, secid, direction), candidates in sorted(grouped.items()):
        kept = deduplicate_hits(candidates, gap_bars=EVENT_GAP_BARS)
        for peak in kept:
            related = [
                item
                for item in candidates
                if abs(int(item["core_end_i"]) - int(peak["core_end_i"]))
                < EVENT_GAP_BARS
            ]
            latest_available = max(utc(item["window_available_at"]) for item in related)
            latest_rows = [
                item
                for item in related
                if utc(item["window_available_at"]) == latest_available
            ]
            representative = max(
                latest_rows,
                key=lambda item: (float(item["confidence"]), int(item["window_len"])),
            )
            event = dict(representative)
            event.update(
                {
                    "timeframe": timeframe,
                    "secid": secid,
                    "direction": direction,
                    "first_available_at": min(
                        utc(item["window_available_at"]) for item in related
                    ).isoformat(),
                    "last_available_at": latest_available.isoformat(),
                    "event_peak_confidence": float(peak["confidence"]),
                    "event_peak_available_at": utc(
                        peak["window_available_at"]
                    ).isoformat(),
                    "candidate_count": len(related),
                    "window_lengths_observed": sorted(
                        {int(item["window_len"]) for item in related}
                    ),
                    "representative_rule": "latest detection endpoint then highest confidence; peak confidence retained separately",
                }
            )
            events.append(event)
    order = {"1h": 0, "4h": 1}
    events.sort(
        key=lambda row: (
            order[str(row["timeframe"])],
            -utc(row["last_available_at"]).value,
            -float(row["confidence"]),
            str(row["search_key"]),
        )
    )
    for index, row in enumerate(events, 1):
        row["audit_event_id"] = (
            f"ashare_{row['timeframe']}_{index:04d}_{row['search_key']}_{row['direction'].lower()}"
        )
    return events


def select_delivery_events(
    audit_events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only ordinary-main-board LONG events without changing audit evidence."""

    delivered: list[dict[str, Any]] = []
    for row in audit_events:
        if str(row.get("direction")) != "LONG":
            continue
        if str(row.get("board")) not in STANDARD_BOARDS:
            raise AShareMultiTimeframeError("nonstandard board reached delivery")
        if restricted_name_reason(row.get("name")):
            raise AShareMultiTimeframeError("restricted name reached delivery")
        expected_search = ("SH" if int(row["market"]) == 1 else "SZ") + normalize_code(
            row["code"]
        )
        if str(row.get("search_key")) != expected_search:
            raise AShareMultiTimeframeError("venue-qualified search key drifted")
        delivered.append(dict(row))
    for rank, row in enumerate(delivered, 1):
        row["delivery_rank"] = rank
        row["event_id"] = (
            f"ashare_{row['timeframe']}_long_{rank:04d}_{row['search_key']}"
        )
    return delivered


def _put_text(
    image: np.ndarray,
    text: str,
    position: tuple[int, int],
    *,
    scale: float,
    color: tuple[int, int, int] = (35, 35, 35),
    thickness: int = 1,
) -> None:
    cv2.putText(
        image,
        text,
        position,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def render_event(
    row: Mapping[str, Any], *, frame: pd.DataFrame, order: int, total: int
) -> np.ndarray:
    """Render causal context ending at detection plus the exact scored input."""

    start_i = int(row["window_start_i"])
    end_i = int(row["window_end_i"])
    model_window = frame.iloc[start_i : end_i + 1]
    clean, input_tf = render_chart(model_window, out_path=None)
    if pixel_sha256(clean) != str(row["input_pixel_sha256"]):
        raise AShareMultiTimeframeError(
            f"model input pixel replay drifted:{row['event_id']}"
        )
    overlay = clean.copy()
    raw = base._normalized_box_corners(row, input_tf.width, input_tf.height)
    color = CLASS_COLORS[int(row["class_id"])]
    cv2.rectangle(overlay, (raw[0], raw[1]), (raw[2], raw[3]), color, 4, cv2.LINE_AA)

    context_start_i = max(0, end_i - CONTEXT_BARS + 1)
    context = frame.iloc[context_start_i : end_i + 1]
    main, context_tf = render_chart(
        context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None
    )
    projected = base._project_raw_box(
        row,
        input_tf=input_tf,
        context_tf=context_tf,
        context_start_i=context_start_i,
    )
    cv2.rectangle(
        main,
        (projected[0], projected[1]),
        (projected[2], projected[3]),
        color,
        5,
        cv2.LINE_AA,
    )

    canvas = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 247, dtype=np.uint8)
    timeframe = str(row["timeframe"])
    available = cst(row["window_available_at"])
    core_start = cst(row["core_start_time"])
    core_end = cst(row["core_end_time"])
    _put_text(
        canvas,
        f"{row['search_key']} / {row['code']} | A-SHARE {timeframe} OOD LONG | conf {float(row['confidence']):.3f} | {order:03d}/{total:03d}",
        (24, 38),
        scale=0.69,
        thickness=2,
    )
    _put_text(
        canvas,
        f"core opens {core_start:%m-%d %H:%M}..{core_end:%m-%d %H:%M} CST | proposal available {available:%m-%d %H:%M} CST | ordinary SH/SZ main-board screen",
        (24, 72),
        scale=0.47,
        color=(55, 55, 55),
    )
    _put_text(
        canvas,
        f"Frozen Grade-A full40 native-1280 | W{int(row['window_len'])} core{int(row['core_length_bars'])} post{int(row['confirmation_bars'])} | semantic gate PASS",
        (24, 102),
        scale=0.47,
        color=(65, 65, 65),
    )
    canvas[MAIN_Y : MAIN_Y + MAIN_HEIGHT, MAIN_X : MAIN_X + MAIN_WIDTH] = main
    times = (
        pd.to_datetime(context["open_time"], utc=True)
        .dt.tz_convert("Asia/Shanghai")
        .reset_index(drop=True)
    )
    for local_i in np.linspace(0, len(context) - 1, 6).round().astype(int):
        x = MAIN_X + base._x_at_float(context_tf, int(local_i))
        stamp = times.iloc[int(local_i)]
        _put_text(
            canvas,
            f"{stamp:%m-%d %H:%M}",
            (max(0, x - 48), MAIN_Y + MAIN_HEIGHT + 24),
            scale=0.40,
            color=(80, 80, 80),
        )
    footer_y = 926
    _put_text(canvas, "HOW TO READ", (28, footer_y), scale=0.64, thickness=2)
    bar_definition = (
        f"one completed {row.get('source_provider', 'Eastmoney')} 60m row"
        if timeframe == "1h"
        else "one complete 09:30-15:00 session aggregated from four 60m rows"
    )
    _put_text(
        canvas,
        f"Top: up to 128 causal A-share {timeframe} bars ending at detection; each bar is {bar_definition}.",
        (28, footer_y + 34),
        scale=0.42,
    )
    _put_text(
        canvas,
        "Right: exact W18/W19 1280x742 model input with the unchanged raw YOLO box; no later bar is shown or scored.",
        (28, footer_y + 64),
        scale=0.42,
    )
    _put_text(
        canvas,
        "Research proposal only: crypto-15m-trained and unvalidated on A-share 1h/4h; confidence is not win probability.",
        (28, footer_y + 94),
        scale=0.42,
        color=(45, 45, 180),
        thickness=2,
    )
    _put_text(
        canvas,
        "EXACT MODEL INPUT",
        (CANVAS_WIDTH - INSET_WIDTH - 18, footer_y),
        scale=0.60,
        thickness=2,
    )
    inset = cv2.resize(
        overlay, (INSET_WIDTH, INSET_HEIGHT), interpolation=cv2.INTER_AREA
    )
    inset_x, inset_y = CANVAS_WIDTH - INSET_WIDTH - 18, footer_y + 18
    canvas[inset_y : inset_y + INSET_HEIGHT, inset_x : inset_x + INSET_WIDTH] = inset
    cv2.rectangle(
        canvas,
        (inset_x, inset_y),
        (inset_x + INSET_WIDTH - 1, inset_y + INSET_HEIGHT - 1),
        (65, 65, 65),
        2,
    )
    return canvas


def build_overview(
    events: Sequence[Mapping[str, Any]], results: Path, *, timeframe: str
) -> list[str]:
    """Build one paged LONG-only contact sheet for a timeframe."""

    subset = [row for row in events if str(row["timeframe"]) == timeframe]
    if not subset:
        blank = np.full((720, 1280, 3), 247, dtype=np.uint8)
        _put_text(
            blank,
            f"A-SHARE {timeframe} OOD LONG: ZERO SEMANTIC SURVIVORS",
            (145, 315),
            scale=0.78,
            thickness=2,
        )
        _put_text(
            blank,
            "standard-retail SH/SZ main boards | frozen thresholds | no retuning",
            (185, 372),
            scale=0.53,
        )
        filename = f"overview_{timeframe}.png"
        cv2.imwrite(str(results / filename), blank)
        return [filename]
    pages: list[str] = []
    page_size = 9
    for page_number, start in enumerate(range(0, len(subset), page_size), 1):
        page = subset[start : start + page_size]
        thumb_w, thumb_h = 620, 426
        sheet = np.full((3 * thumb_h + 82, 3 * thumb_w, 3), 240, dtype=np.uint8)
        _put_text(
            sheet,
            f"A-SHARE {timeframe} OOD LONG | standard-retail main boards | page {page_number}",
            (24, 34),
            scale=0.67,
            thickness=2,
        )
        _put_text(
            sheet,
            "crypto-15m-trained detector + frozen semantic gate; NOT validated trade signals",
            (24, 66),
            scale=0.48,
            color=(45, 45, 180),
            thickness=2,
        )
        for slot, event in enumerate(page):
            path = results / str(event["chart"])
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise AShareMultiTimeframeError(f"could not read chart:{path}")
            thumb = cv2.resize(image, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
            row, col = divmod(slot, 3)
            y, x = 82 + row * thumb_h, col * thumb_w
            sheet[y : y + thumb_h, x : x + thumb_w] = thumb
            label = f"{start + slot + 1:03d} {event['search_key']} LONG {float(event['confidence']):.3f}"
            cv2.rectangle(sheet, (x + 4, y + 4), (x + 380, y + 31), (250, 250, 250), -1)
            _put_text(sheet, label, (x + 10, y + 25), scale=0.53, thickness=2)
        filename = f"overview_{timeframe}_page_{page_number:02d}.png"
        cv2.imwrite(str(results / filename), sheet)
        pages.append(filename)
    shutil.copyfile(results / pages[0], results / f"overview_{timeframe}.png")
    return pages


def _write_chart_zip(results: Path, events: Sequence[Mapping[str, Any]]) -> Path:
    path = results / f"ashare_1h4h_long_charts_{len(events)}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for event in events:
            chart = results / str(event["chart"])
            archive.write(chart, arcname=f"charts/{chart.name}")
    return path


def scan_snapshot(
    out: Path, results: Path, *, device_arg: str | None, batch_size: int
) -> dict[str, Any]:
    """Run the two preregistered configurations and deliver LONG events only."""

    source_commit = require_builder_committed()
    prereg, gates = verify_frozen_contract()
    snapshots, reference_1h, reference_4h, fetch_receipt = load_snapshot(out)
    if results.exists():
        raise FileExistsError(f"refusing to overwrite scan results:{results}")
    building = results.with_name(results.name + ".building")
    if building.exists():
        raise FileExistsError(f"stale results building directory:{building}")
    chart_dir = building / "charts"
    building.mkdir(parents=True)
    chart_dir.mkdir()
    started = time.perf_counter()
    device = choose_device(device_arg)
    from ultralytics import YOLO

    model = YOLO(str(WEIGHTS))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != CLASS_NAMES:
        raise AShareMultiTimeframeError(f"class map drifted:{names}")
    all_boxes: list[dict[str, Any]] = []
    batch: list[tuple[np.ndarray, ChartTransform, dict[str, Any], pd.DataFrame]] = []
    windows_scored = Counter()
    total_windows = int(fetch_receipt["usable_symbols"]["1h"]) * len(
        WINDOW_LENGTHS
    ) + int(fetch_receipt["usable_symbols"]["4h"]) * FOUR_HOUR_RECENT_ENDPOINTS * len(
        WINDOW_LENGTHS
    )

    def flush() -> None:
        nonlocal batch
        if not batch:
            return
        all_boxes.extend(base._run_batch(model, batch, device=device, gates=gates))
        windows_scored.update(item[2]["timeframe"] for item in batch)
        batch = []
        done = sum(windows_scored.values())
        if done % (max(1, batch_size) * 10) == 0 or done == total_windows:
            print(
                f"inference {done}/{total_windows} raw_boxes={len(all_boxes)}",
                flush=True,
            )

    for symbol_number, identity in enumerate(snapshots, 1):
        path = out / str(identity["path"])
        if sha256_file(path) != str(identity["sha256"]):
            raise AShareMultiTimeframeError(
                f"snapshot bytes drifted before scan:{identity['secid']}"
            )
        hourly = load_hourly(path)
        for timeframe, eligible, reference in (
            ("1h", bool(identity["eligible_1h"]), reference_1h),
            ("4h", bool(identity["eligible_4h"]), reference_4h),
        ):
            if not eligible:
                continue
            frame = _frame_for_timeframe(hourly, timeframe)
            if timeframe == "1h":
                validate_one_hour_schedule(frame, reference, secid=str(identity["secid"]))
            else:
                validate_four_hour_schedule(
                    frame, reference, secid=str(identity["secid"])
                )
            enriched = add_candidate_features(frame)
            endpoints = select_scan_endpoints(enriched, timeframe)
            for endpoint in endpoints:
                for window_len in WINDOW_LENGTHS:
                    start_i = endpoint - window_len + 1
                    window = enriched.iloc[start_i : endpoint + 1]
                    if start_i < 0 or window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                        raise AShareMultiTimeframeError(
                            f"MA warmup failed:{identity['secid']}:{timeframe}"
                        )
                    image, transform = render_chart(window, out_path=None)
                    row = enriched.iloc[endpoint]
                    batch.append(
                        (
                            image,
                            transform,
                            {
                                "secid": str(identity["secid"]),
                                "market": int(identity["market"]),
                                "code": str(identity["code"]),
                                "name": str(identity["name"]),
                                "board": str(identity["board"]),
                                "exchange": str(identity["exchange"]),
                                "search_key": str(identity["search_key"]),
                                "snapshot_path": str(identity["path"]),
                                "snapshot_sha256": str(identity["sha256"]),
                                "timeframe": timeframe,
                                "bar_minutes": 60 if timeframe == "1h" else 240,
                                "endpoint_offset_trading_bars": len(enriched) - 1 - endpoint,
                                "is_latest_endpoint": endpoint == len(enriched) - 1,
                                "window_len": window_len,
                                "window_start_i": start_i,
                                "window_end_i": endpoint,
                                "window_end_time": utc(row["open_time"]).isoformat(),
                                "window_available_at": utc(
                                    row["raw_close_time"]
                                ).isoformat(),
                            },
                            enriched,
                        )
                    )
                    if len(batch) >= batch_size:
                        flush()
        if symbol_number % 250 == 0:
            print(f"prepared {symbol_number}/{len(snapshots)} symbols", flush=True)
    flush()

    structural = [row for row in all_boxes if bool(row.get("structural_pass"))]
    semantic = [row for row in structural if bool(row.get("semantic_pass"))]
    audit_events = _deduplicate_semantic(semantic)
    events = select_delivery_events(audit_events)
    snapshot_by_secid = {str(item["secid"]): item for item in snapshots}
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
    chart_paths: list[Path] = []
    for order, event in enumerate(events, 1):
        cache_key = (str(event["secid"]), str(event["timeframe"]))
        if cache_key not in frame_cache:
            identity = snapshot_by_secid[cache_key[0]]
            hourly = load_hourly(out / str(identity["path"]))
            frame_cache[cache_key] = add_candidate_features(
                _frame_for_timeframe(hourly, cache_key[1])
            )
        canvas = render_event(
            event, frame=frame_cache[cache_key], order=order, total=len(events)
        )
        path = chart_dir / (
            f"{order:03d}_{event['timeframe']}_{event['search_key']}_long.png"
        )
        if not cv2.imwrite(str(path), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            raise AShareMultiTimeframeError(f"failed to write chart:{path}")
        event["chart"] = f"charts/{path.name}"
        event["chart_sha256"] = sha256_file(path)
        chart_paths.append(path)

    overview_pages = {
        timeframe: build_overview(events, building, timeframe=timeframe)
        for timeframe in ("1h", "4h")
    }
    write_jsonl(building / "raw_boxes.jsonl", all_boxes)
    write_jsonl(building / "structural_boxes.jsonl", structural)
    write_jsonl(building / "semantic_boxes.jsonl", semantic)
    write_jsonl(building / "audit_events_all_directions.jsonl", audit_events)
    write_jsonl(building / "signals.jsonl", events)
    delivery_columns = [
        "delivery_rank",
        "timeframe",
        "exchange",
        "code",
        "search_key",
        "name",
        "board",
        "direction",
        "confidence",
        "event_peak_confidence",
        "first_available_at",
        "last_available_at",
        "endpoint_offset_trading_bars",
        "window_len",
        "core_length_bars",
        "confirmation_bars",
        "chart",
        "chart_sha256",
    ]
    event_frame = pd.DataFrame(events)
    if event_frame.empty:
        event_frame = pd.DataFrame(columns=delivery_columns)
    else:
        remaining = [c for c in event_frame.columns if c not in delivery_columns]
        event_frame = event_frame[[*delivery_columns, *remaining]]
    event_frame.to_csv(building / "signals.csv", index=False)
    side_counts = {
        timeframe: dict(
            Counter(
                str(row["direction"])
                for row in audit_events
                if str(row["timeframe"]) == timeframe
            )
        )
        for timeframe in ("1h", "4h")
    }
    delivered_counts = Counter(str(row["timeframe"]) for row in events)
    structural_counts = Counter(str(row["timeframe"]) for row in structural)
    semantic_counts = Counter(str(row["timeframe"]) for row in semantic)
    raw_counts = Counter(str(row["timeframe"]) for row in all_boxes)
    failed_checks = Counter(
        check for row in structural for check in row.get("semantic_failed_checks", [])
    )
    summary = {
        "protocol": "ashare_grade_a_1h_session4h_long_delivery_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "fetch_receipt_sha256": sha256_file(out / "fetch_receipt.json"),
        "weights": str(WEIGHTS.relative_to(ROOT)),
        "weights_sha256": EXPECTED_WEIGHT_SHA256,
        "model": prereg["model_contract"]["name"],
        "source_domain": "crypto_15m",
        "inference_domains": ["mainland_A_share_1h", "mainland_A_share_session_4h"],
        "out_of_distribution": True,
        "research_only": True,
        "holdout_consumed": True,
        "holdout_consumption_numbers_for_checkpoint": {"1h": 9, "4h": 10},
        "cutoffs_cst": {
            "1h": ONE_HOUR_CUTOFF_CST.isoformat(),
            "4h": FOUR_HOUR_CUTOFF_CST.isoformat(),
        },
        "universe_symbols": int(fetch_receipt["universe_rows"]),
        "usable_symbols": fetch_receipt["usable_symbols"],
        "coverage": fetch_receipt["coverage"],
        "windows_scored": dict(windows_scored),
        "raw_boxes_by_timeframe": dict(raw_counts),
        "structural_boxes_by_timeframe": dict(structural_counts),
        "semantic_boxes_by_timeframe": dict(semantic_counts),
        "audit_events_by_timeframe_and_direction": side_counts,
        "delivered_long_events_by_timeframe": {
            timeframe: int(delivered_counts[timeframe]) for timeframe in ("1h", "4h")
        },
        "short_events_excluded_from_delivery": sum(
            int(side_counts[tf].get("SHORT", 0)) for tf in ("1h", "4h")
        ),
        "direction_flip_semantic_survivors": sum(
            bool(row.get("flipped_semantic_pass")) for row in structural
        ),
        "semantic_failure_checks": dict(sorted(failed_checks.items())),
        "overview_pages": overview_pages,
        "detector_contract": {
            "imgsz": IMAGE_SIZE,
            "confidence": CONFIDENCE,
            "nms_iou": NMS_IOU,
            "window_lengths": list(WINDOW_LENGTHS),
            "allowed_core_lengths": sorted(ALLOWED_CORES),
            "allowed_confirmation_bars": sorted(ALLOWED_CONFIRMATIONS),
            "same_symbol_event_gap_bars": EVENT_GAP_BARS,
            "latest_endpoints_per_symbol": {"1h": 1, "4h": FOUR_HOUR_RECENT_ENDPOINTS},
        },
        "signals": events,
        "network_reads_during_scan": 0,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "threshold_or_weight_changed": False,
        "trained": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "telegram_sent": False,
        "orders_placed": False,
        "production_eligible": False,
    }
    write_json(building / "summary.json", summary)
    zip_path = _write_chart_zip(building, events)
    summary["chart_zip"] = zip_path.name
    summary["chart_zip_sha256"] = sha256_file(zip_path)
    write_json(building / "summary.json", summary)
    os.replace(building, results)
    print(
        f"scan complete windows={sum(windows_scored.values())} audit_events={len(audit_events)} delivered_long={len(events)} -> {results}",
        flush=True,
    )
    return summary


def verify_results(out: Path, results: Path) -> dict[str, Any]:
    """Replay frozen candles, structural pixels, semantic decisions, and charts."""

    source_commit = require_builder_committed()
    _, gates = verify_frozen_contract()
    snapshots, reference_1h, reference_4h, receipt = load_snapshot(out)
    summary = read_json(results / "summary.json")
    raw_boxes = read_jsonl(results / "raw_boxes.jsonl")
    structural = read_jsonl(results / "structural_boxes.jsonl")
    audit_events = read_jsonl(results / "audit_events_all_directions.jsonl")
    signals = read_jsonl(results / "signals.jsonl")
    if len(raw_boxes) != sum(int(v) for v in summary["raw_boxes_by_timeframe"].values()):
        raise AShareMultiTimeframeError("saved raw box count drifted")
    by_key: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in structural:
        by_key[(str(row["secid"]), str(row["timeframe"]))].append(row)
    snapshot_by_secid = {str(item["secid"]): item for item in snapshots}
    candle_hash_checks = 0
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for identity in snapshots:
        path = out / str(identity["path"])
        if sha256_file(path) != str(identity["sha256"]):
            raise AShareMultiTimeframeError(
                f"verification candle SHA failed:{identity['secid']}"
            )
        hourly = load_hourly(path)
        if bool(identity["eligible_1h"]):
            validate_one_hour_schedule(
                hourly, reference_1h, secid=str(identity["secid"])
            )
        if bool(identity["eligible_4h"]):
            validate_four_hour_schedule(
                aggregate_session_four_hour(hourly),
                reference_4h,
                secid=str(identity["secid"]),
            )
        candle_hash_checks += 1

    pixel_checks = 0
    semantic_checks = 0
    meta_keys = (
        "secid",
        "market",
        "code",
        "name",
        "board",
        "exchange",
        "search_key",
        "snapshot_path",
        "snapshot_sha256",
        "timeframe",
        "bar_minutes",
        "endpoint_offset_trading_bars",
        "is_latest_endpoint",
        "window_len",
        "window_start_i",
        "window_end_i",
        "window_end_time",
        "window_available_at",
    )
    for key, rows in sorted(by_key.items()):
        secid, timeframe = key
        if key not in frame_cache:
            identity = snapshot_by_secid[secid]
            hourly = load_hourly(out / str(identity["path"]))
            frame_cache[key] = add_candidate_features(
                _frame_for_timeframe(hourly, timeframe)
            )
        frame = frame_cache[key]
        rendered: dict[tuple[int, int], tuple[np.ndarray, ChartTransform]] = {}
        for saved in rows:
            window_key = (int(saved["window_start_i"]), int(saved["window_end_i"]))
            if window_key not in rendered:
                rendered[window_key] = render_chart(
                    frame.iloc[window_key[0] : window_key[1] + 1], out_path=None
                )
            image, transform = rendered[window_key]
            if pixel_sha256(image) != str(saved["input_pixel_sha256"]):
                raise AShareMultiTimeframeError(
                    f"verification pixel hash failed:{secid}:{timeframe}"
                )
            pixel_checks += 1
            recomputed = base._prediction_record(
                xywhn=[
                    float(saved["prediction_cx_norm"]),
                    float(saved["prediction_cy_norm"]),
                    float(saved["prediction_w_norm"]),
                    float(saved["prediction_h_norm"]),
                ],
                class_id=int(saved["class_id"]),
                confidence=float(saved["confidence"]),
                transform=transform,
                meta={name: saved[name] for name in meta_keys},
                frame=frame,
                input_hash=str(saved["input_pixel_sha256"]),
                gates=gates,
            )
            comparable = (
                "core_start_i",
                "core_end_i",
                "core_length_bars",
                "confirmation_bars",
                "structural_pass",
                "semantic_pass",
                "semantic_failed_checks",
                "flipped_semantic_pass",
                "flipped_semantic_failed_checks",
            )
            if any(recomputed.get(name) != saved.get(name) for name in comparable):
                raise AShareMultiTimeframeError(
                    f"verification semantic decision failed:{secid}:{timeframe}"
                )
            semantic_checks += 1

    if any(str(row["direction"]) != "LONG" for row in signals):
        raise AShareMultiTimeframeError("SHORT row reached delivery ledger")
    expected_events = select_delivery_events(audit_events)
    event_identity = [
        (str(row["timeframe"]), str(row["search_key"]), str(row["last_available_at"]))
        for row in signals
    ]
    expected_identity = [
        (str(row["timeframe"]), str(row["search_key"]), str(row["last_available_at"]))
        for row in expected_events
    ]
    if event_identity != expected_identity:
        raise AShareMultiTimeframeError("LONG delivery selection drifted")
    chart_checks = 0
    for order, signal in enumerate(signals, 1):
        key = (str(signal["secid"]), str(signal["timeframe"]))
        if key not in frame_cache:
            identity = snapshot_by_secid[key[0]]
            hourly = load_hourly(out / str(identity["path"]))
            frame_cache[key] = add_candidate_features(
                _frame_for_timeframe(hourly, key[1])
            )
        rerendered = render_event(
            signal, frame=frame_cache[key], order=order, total=len(signals)
        )
        saved_path = results / str(signal["chart"])
        saved = cv2.imread(str(saved_path), cv2.IMREAD_COLOR)
        if saved is None or not np.array_equal(saved, rerendered):
            raise AShareMultiTimeframeError(
                f"verification chart pixels failed:{signal['event_id']}"
            )
        if sha256_file(saved_path) != str(signal["chart_sha256"]):
            raise AShareMultiTimeframeError(
                f"verification chart SHA failed:{signal['event_id']}"
            )
        chart_checks += 1
    zip_path = results / str(summary["chart_zip"])
    if sha256_file(zip_path) != str(summary["chart_zip_sha256"]):
        raise AShareMultiTimeframeError("chart ZIP SHA drifted")
    verification = {
        "protocol": "ashare_grade_a_1h_session4h_independent_replay_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "fetch_receipt_sha256": sha256_file(out / "fetch_receipt.json"),
        "summary_sha256": sha256_file(results / "summary.json"),
        "universe_symbols": int(receipt["universe_rows"]),
        "usable_symbols": receipt["usable_symbols"],
        "candle_sha_and_schedule_checks": candle_hash_checks,
        "structural_input_pixel_checks": pixel_checks,
        "semantic_decision_checks": semantic_checks,
        "long_delivery_selection_checks": len(signals),
        "chart_pixel_and_sha_checks": chart_checks,
        "network_reads": 0,
        "model_inference": 0,
        "passed": True,
    }
    write_json(results / "verification.json", verification)
    print(
        f"verification PASS candles={candle_hash_checks} decisions={semantic_checks} charts={chart_checks}",
        flush=True,
    )
    return verification


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--fetch", action="store_true")
    phase.add_argument("--scan", action="store_true")
    phase.add_argument("--verify", action="store_true")
    phase.add_argument("--all", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()
    out = args.out.resolve()
    results = args.results.resolve()
    if args.fetch or args.all:
        fetch_snapshot(out, workers=max(1, args.workers))
    if args.scan or args.all:
        scan_snapshot(
            out, results, device_arg=args.device, batch_size=max(1, args.batch_size)
        )
    if args.verify or args.all:
        verify_results(out, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
