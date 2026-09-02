#!/usr/bin/env python3
"""Freeze and scan the latest completed mainland A-share 15m endpoint.

This is an owner-authorized cross-market research snapshot.  The existing
Grade-A crypto 15m checkpoint is applied unchanged to Shanghai, Shenzhen and
Beijing A-share bars.  It is intentionally split into three phases:

``--fetch``
    Freeze the Eastmoney universe and QFQ 15m candle bytes.  The request shape
    is pinned to AKShare's official implementation at commit
    8e95744b79ae22326308ccd2b4e62650c5b53c55:
    https://github.com/akfamily/akshare/blob/8e95744b79ae22326308ccd2b4e62650c5b53c55/akshare/stock_feature/stock_hist_em.py
    Eastmoney does not publish a supported public API contract for these
    endpoints, so the upstream field semantics remain an explicit limitation.

``--scan``
    Load only the frozen CSVs, render the exact repository 1280x742 W18/W19
    model inputs, run the immutable checkpoint at conf=0.25/NMS=0.70, apply
    the existing core4/5+post2-9 mapping, and then apply the frozen causal
    semantic gate.  This phase performs zero network reads.

``--verify``
    Re-hash every source file and recompute every structurally legal box's
    pixel input and semantic decision without model inference or network I/O.

Only bars whose Eastmoney close-label is at or before 2026-09-02 11:30 CST
are retained.  A raw 15m close-label is converted to the repository's
``open_time`` convention by subtracting fifteen minutes.  A symbol is eligible
only when its last 160 close-labels exactly match the same frozen SSE Composite
schedule, which fails closed on stale, suspended, or irregular histories.

The outputs are completed-history model proposals, not validated trade signals.
The model was not trained on A shares, and ordinary A-share cash positions
cannot generally execute its SHORT class.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.scan_15m_ma_launch_t3_daily_movers import (
    choose_device,
    deduplicate_hits,
    map_prediction_to_core,
)
from yoyo.datasets.fifteen_minute_launch_candidates import (
    add_candidate_features,
)
from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.render import (
    ChartTransform,
    render_chart,
)
from yoyo.layers.l1_detection.semantic_gate import (
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)

EXPERIMENT_ID = "exp-15m-ashare-grade-a-yolo-latest-20260902-v1"
PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ashare_15m_yolo_latest_20260902_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
WEIGHTS = (
    ROOT
    / "analysis/output/ma_launch_owner_grade_a8000_neg24000_v1"
    / "ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt"
)
AUTOFILL_PREREG = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"
)
EXPECTED_WEIGHT_SHA256 = (
    "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
)
EXPECTED_AKSHARE_COMMIT = "8e95744b79ae22326308ccd2b4e62650c5b53c55"
EXPECTED_AKSHARE_STOCK_SOURCE_SHA256 = (
    "d2a4c09d55d9362c8c7e58ec82f78d198cf6d2c2daf004033eef42ded915050d"
)

UNIVERSE_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
UNIVERSE_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
UNIVERSE_PAGE_SIZE = 100
KLINE_LIMIT = 512
REFERENCE_SECID = "1.000001"
REFERENCE_NAME = "SSE Composite"
BAR_DELTA = pd.Timedelta(minutes=15)
CUTOFF_CLOSE_CST = pd.Timestamp("2026-09-02T11:30:00+08:00")
CUTOFF_OPEN_UTC = (CUTOFF_CLOSE_CST - BAR_DELTA).tz_convert("UTC")
SCHEDULE_MATCH_BARS = 160

IMAGE_SIZE = 1280
WINDOW_LENGTHS = (18, 19)
CONFIDENCE = 0.25
NMS_IOU = 0.70
ALLOWED_CORES = frozenset((4, 5))
ALLOWED_CONFIRMATIONS = frozenset(range(2, 10))
EVENT_GAP_BARS = 5
CLASS_NAMES = {0: "dense_long", 1: "dense_short"}
CLASS_COLORS = {0: (48, 155, 70), 1: (62, 62, 225)}

CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1400
MAIN_X = 20
MAIN_Y = 112
MAIN_WIDTH = 1880
MAIN_HEIGHT = 760
CONTEXT_BARS = 128
INSET_WIDTH = 700
INSET_HEIGHT = 406

SESSION_CLOSE_SLOTS = frozenset(
    [
        "09:45",
        "10:00",
        "10:15",
        "10:30",
        "10:45",
        "11:00",
        "11:15",
        "11:30",
        "13:15",
        "13:30",
        "13:45",
        "14:00",
        "14:15",
        "14:30",
        "14:45",
        "15:00",
    ]
)
CODE_RE = re.compile(r"^\d{6}$")
_THREAD_LOCAL = threading.local()


class AShareScanError(RuntimeError):
    """Raised when source, model, time, pixel, or output identity drifts."""


def sha256_file(path: Path) -> str:
    """Return a streaming file SHA-256."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    """Hash exact BGR pixels, independent of PNG container metadata."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def cst(value: object) -> pd.Timestamp:
    return utc(value).tz_convert("Asia/Shanghai")


def _git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def require_builder_committed() -> str:
    """Refuse artifact generation from an uncommitted builder or contract."""

    relative = [
        str(Path(__file__).resolve().relative_to(ROOT)),
        str(PREREG.relative_to(ROOT)),
    ]
    for path in relative:
        _git_output("ls-files", "--error-unmatch", path)
    dirty = _git_output("status", "--porcelain", "--", *relative)
    if dirty:
        raise AShareScanError(
            f"builder/preregistration must be committed first: {dirty}"
        )
    return _git_output("rev-parse", "HEAD")


def verify_frozen_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify checkpoint, source modules, thresholds, and explicit safety flags."""

    prereg = read_json(PREREG)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise AShareScanError("preregistration experiment identity drifted")
    if int(prereg["safety"]["holdout_consumption_number_for_checkpoint"]) != 8:
        raise AShareScanError("holdout consumption number drifted")
    if prereg["safety"]["holdout_consumed"] is not True:
        raise AShareScanError(
            "latest-market read must remain declared holdout consumption"
        )
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
        if prereg["safety"][key] is not False:
            raise AShareScanError(f"unsafe preregistration switch changed: {key}")
    pinned = {
        WEIGHTS: prereg["model_contract"]["weights_sha256"],
        ROOT / prereg["model_contract"]["renderer_path"]: prereg["model_contract"][
            "renderer_sha256"
        ],
        ROOT / prereg["model_contract"]["ma_builder_path"]: prereg["model_contract"][
            "ma_builder_sha256"
        ],
        ROOT / prereg["semantic_gate_contract"]["module_path"]: prereg[
            "semantic_gate_contract"
        ]["module_sha256"],
        ROOT / prereg["semantic_gate_contract"]["threshold_source_path"]: prereg[
            "semantic_gate_contract"
        ]["threshold_source_sha256"],
    }
    for path, expected in pinned.items():
        if not path.is_file() or sha256_file(path) != str(expected):
            raise AShareScanError(f"frozen input SHA drift: {path}")
    if sha256_file(WEIGHTS) != EXPECTED_WEIGHT_SHA256:
        raise AShareScanError("checkpoint identity drifted")
    threshold_source = read_json(AUTOFILL_PREREG)
    gates = dict(threshold_source["morphology_gate"])
    return prereg, gates


def _session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://quote.eastmoney.com/",
            }
        )
        _THREAD_LOCAL.session = session
    return session


def request_json(
    url: str, params: Mapping[str, Any], *, attempts: int = 6
) -> dict[str, Any]:
    """GET one JSON endpoint with bounded same-source retries."""

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = _session().get(url, params=dict(params), timeout=(5, 20))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise AShareScanError("upstream returned non-object JSON")
            return payload
        except Exception as exc:  # noqa: BLE001 - receipt needs the final upstream class
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(8.0, 0.4 * (2**attempt)))
    raise AShareScanError(
        f"same-source request failed after {attempts} attempts: {type(last).__name__}: {last}"
    )


def _normalize_diff(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        return [
            dict(item)
            for _, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if isinstance(item, Mapping)
        ]
    return []


def fetch_universe() -> list[dict[str, Any]]:
    """Freeze the exact AKShare Shanghai/Shenzhen/Beijing A-share filter."""

    base = {
        "pn": "1",
        "pz": str(UNIVERSE_PAGE_SIZE),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": UNIVERSE_FS,
        "fields": "f2,f5,f6,f12,f13,f14,f20,f21",
    }
    first = request_json(UNIVERSE_URL, base)
    data = first.get("data") or {}
    first_rows = _normalize_diff(data.get("diff"))
    total = int(data.get("total") or 0)
    if not first_rows or total <= 0:
        raise AShareScanError("A-share universe endpoint returned no rows")
    total_pages = math.ceil(total / len(first_rows))
    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    for page in range(2, total_pages + 1):
        params = dict(base)
        params["pn"] = str(page)
        payload = request_json(UNIVERSE_URL, params)
        rows = _normalize_diff((payload.get("data") or {}).get("diff"))
        if not rows:
            raise AShareScanError(
                f"universe pagination returned an empty page: {page}/{total_pages}"
            )
        pages[page] = rows
        if page % 10 == 0 or page == total_pages:
            print(f"universe page {page}/{total_pages}", flush=True)
        time.sleep(0.05)
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in sorted(pages):
        for raw in pages[page]:
            code = str(raw.get("f12") or "").strip()
            name = str(raw.get("f14") or "").strip()
            market = int(raw.get("f13"))
            secid = f"{market}.{code}"
            if not CODE_RE.fullmatch(code) or market not in {0, 1} or not name:
                raise AShareScanError(f"invalid universe identity row: {raw}")
            if secid in seen:
                raise AShareScanError(f"duplicate universe secid: {secid}")
            seen.add(secid)
            output.append(
                {
                    "secid": secid,
                    "market": market,
                    "code": code,
                    "name": name,
                    "spot_price": pd.to_numeric(raw.get("f2"), errors="coerce"),
                    "spot_volume": pd.to_numeric(raw.get("f5"), errors="coerce"),
                    "spot_amount": pd.to_numeric(raw.get("f6"), errors="coerce"),
                    "total_market_cap": pd.to_numeric(raw.get("f20"), errors="coerce"),
                    "float_market_cap": pd.to_numeric(raw.get("f21"), errors="coerce"),
                }
            )
    if len(output) != total:
        raise AShareScanError(
            f"universe count mismatch: parsed={len(output)} declared={total}"
        )
    output.sort(key=lambda row: (int(row["market"]), str(row["code"])))
    return output


def _parse_kline_payload(
    payload: Mapping[str, Any], *, secid: str, adjustment: str
) -> pd.DataFrame:
    """Parse Eastmoney 15m close-labelled rows into repository open-time rows."""

    data = payload.get("data") or {}
    raw_rows = data.get("klines") or []
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
        if close_time > CUTOFF_CLOSE_CST:
            continue
        parsed.append(
            [
                close_time,
                (close_time - BAR_DELTA).tz_convert("UTC"),
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
        raise AShareScanError(f"no retained 15m bars: {secid}")
    frame.sort_values("raw_close_time", inplace=True, ignore_index=True)
    frame.drop_duplicates(
        "raw_close_time", keep="last", inplace=True, ignore_index=True
    )
    numeric = frame[["open", "high", "low", "close", "volume", "amount"]].to_numpy(
        dtype=float
    )
    if not np.isfinite(numeric).all():
        raise AShareScanError(f"non-finite OHLCVA: {secid}")
    if bool((frame[["open", "high", "low", "close"]] <= 0).any().any()):
        raise AShareScanError(f"non-positive OHLC: {secid}")
    body_high = frame[["open", "close"]].max(axis=1)
    body_low = frame[["open", "close"]].min(axis=1)
    if bool((frame["high"] < body_high).any()) or bool((frame["low"] > body_low).any()):
        raise AShareScanError(f"invalid candle bounds: {secid}")
    if bool((frame[["volume", "amount"]] < 0).any().any()):
        raise AShareScanError(f"negative volume/amount: {secid}")
    slots = set(frame["raw_close_time"].dt.strftime("%H:%M"))
    unexpected = sorted(slots - SESSION_CLOSE_SLOTS)
    if unexpected:
        raise AShareScanError(f"unexpected 15m close labels {unexpected}: {secid}")
    frame["secid"] = secid
    frame["adjustment"] = adjustment
    return frame.tail(KLINE_LIMIT).reset_index(drop=True)


def fetch_kline(secid: str, *, fqt: str, adjustment: str) -> pd.DataFrame:
    """Fetch one bounded 15m page with AKShare-compatible field semantics."""

    payload = request_json(
        KLINE_URL,
        {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "15",
            "fqt": fqt,
            "secid": secid,
            "beg": "0",
            "end": "20500000",
            # ``lmt`` is used by the same upstream kline family elsewhere in
            # AKShare, but Eastmoney publishes no supported public API spec.
            # It is therefore pinned and audited here, not claimed as stable.
            "lmt": str(KLINE_LIMIT),
        },
    )
    return _parse_kline_payload(payload, secid=secid, adjustment=adjustment)


def validate_against_schedule(
    frame: pd.DataFrame, reference: pd.DataFrame, *, secid: str
) -> None:
    """Require an exact current endpoint and exact trailing market schedule."""

    if len(frame) < SCHEDULE_MATCH_BARS:
        raise AShareScanError(f"insufficient_history:{len(frame)}")
    latest = pd.Timestamp(frame.iloc[-1]["raw_close_time"])
    if latest != CUTOFF_CLOSE_CST:
        raise AShareScanError(f"stale_latest:{latest.isoformat()}")
    actual = pd.DatetimeIndex(frame["raw_close_time"].iloc[-SCHEDULE_MATCH_BARS:])
    expected = pd.DatetimeIndex(reference["raw_close_time"].iloc[-SCHEDULE_MATCH_BARS:])
    if not actual.equals(expected):
        mismatch = (
            int(np.flatnonzero(actual.to_numpy() != expected.to_numpy())[0])
            if len(actual) == len(expected)
            else -1
        )
        raise AShareScanError(f"schedule_mismatch:{mismatch}")
    opens = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True))
    expected_opens = (
        pd.DatetimeIndex(pd.to_datetime(frame["raw_close_time"], utc=True)) - BAR_DELTA
    )
    if not opens.equals(expected_opens):
        raise AShareScanError(f"close_to_open_conversion_drift:{secid}")


def load_candle(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
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
        raise AShareScanError(f"snapshot schema drift: {path}")
    frame["raw_close_time"] = pd.to_datetime(
        frame["raw_close_time"], utc=True
    ).dt.tz_convert("Asia/Shanghai")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume", "amount"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def _error_reason(error: str) -> str:
    for prefix in (
        "insufficient_history",
        "stale_latest",
        "schedule_mismatch",
        "no retained 15m bars",
        "same-source request failed",
        "unexpected 15m close labels",
    ):
        if prefix in error:
            return prefix
    return "other"


def fetch_snapshot(out: Path, *, workers: int) -> dict[str, Any]:
    """Freeze the all-A-share universe and exact candle bytes before inference."""

    source_commit = require_builder_committed()
    _prereg, _ = verify_frozen_contract()
    if out.exists():
        raise FileExistsError(f"refusing to overwrite frozen snapshot: {out}")
    building = out.with_name(out.name + ".building")
    candle_dir = building / "candles"
    building.mkdir(parents=True, exist_ok=True)
    candle_dir.mkdir(exist_ok=True)
    plan_path = building / "fetch_plan.json"
    universe_path = building / "universe.csv"
    if plan_path.is_file() and universe_path.is_file():
        plan = read_json(plan_path)
        universe_rows = pd.read_csv(universe_path, dtype={"code": str}).to_dict(
            "records"
        )
        if sha256_file(universe_path) != str(plan["universe_sha256"]):
            raise AShareScanError("resumed universe bytes drifted")
        print(f"resuming frozen universe with {len(universe_rows)} rows", flush=True)
    else:
        universe_rows = fetch_universe()
        pd.DataFrame(universe_rows).to_csv(universe_path, index=False)
        plan = {
            "experiment_id": EXPERIMENT_ID,
            "frozen_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "universe_rows": len(universe_rows),
            "universe_sha256": sha256_file(universe_path),
            "cutoff_close_cst": CUTOFF_CLOSE_CST.isoformat(),
            "cutoff_open_utc": CUTOFF_OPEN_UTC.isoformat(),
            "holdout_consumption_number_for_checkpoint": 8,
        }
        write_json(plan_path, plan)
    if int(plan["universe_rows"]) != len(universe_rows):
        raise AShareScanError("frozen universe count drifted")

    reference_path = building / "reference_schedule.csv"
    if reference_path.is_file():
        reference = load_candle(reference_path)
    else:
        reference = fetch_kline(REFERENCE_SECID, fqt="0", adjustment="none")
        if (
            len(reference) < SCHEDULE_MATCH_BARS
            or pd.Timestamp(reference.iloc[-1]["raw_close_time"]) != CUTOFF_CLOSE_CST
        ):
            raise AShareScanError("reference schedule does not reach the frozen cutoff")
        reference.to_csv(reference_path, index=False)
    reference_sha = sha256_file(reference_path)

    identities: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reused = 0

    def one(
        row: Mapping[str, Any],
    ) -> tuple[dict[str, Any], pd.DataFrame | None, str | None, bool]:
        identity = {
            "secid": str(row["secid"]),
            "market": int(row["market"]),
            "code": str(row["code"]).zfill(6),
            "name": str(row["name"]),
        }
        path = candle_dir / f"{identity['market']}_{identity['code']}.csv"
        try:
            if path.is_file():
                frame = load_candle(path)
                validate_against_schedule(frame, reference, secid=identity["secid"])
                return identity, frame, None, True
            frame = fetch_kline(identity["secid"], fqt="1", adjustment="qfq")
            validate_against_schedule(frame, reference, secid=identity["secid"])
            return identity, frame, None, False
        except Exception as exc:  # noqa: BLE001 - every exclusion is receipted
            return identity, None, f"{type(exc).__name__}:{exc}", False

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(one, row): row for row in universe_rows}
        for number, future in enumerate(as_completed(futures), 1):
            identity, frame, error, was_reused = future.result()
            if error is not None or frame is None:
                failures.append(
                    {
                        **identity,
                        "error": str(error),
                        "reason": _error_reason(str(error)),
                    }
                )
            else:
                path = candle_dir / f"{identity['market']}_{identity['code']}.csv"
                if not path.is_file():
                    frame.to_csv(path, index=False)
                reused += int(was_reused)
                identities.append(
                    {
                        **identity,
                        "path": f"candles/{path.name}",
                        "sha256": sha256_file(path),
                        "rows": len(frame),
                        "first_close_cst": pd.Timestamp(
                            frame.iloc[0]["raw_close_time"]
                        ).isoformat(),
                        "last_close_cst": pd.Timestamp(
                            frame.iloc[-1]["raw_close_time"]
                        ).isoformat(),
                        "last_open_utc": utc(frame.iloc[-1]["open_time"]).isoformat(),
                    }
                )
            if number % 100 == 0 or number == len(universe_rows):
                print(
                    f"fetch {number}/{len(universe_rows)} usable={len(identities)} excluded={len(failures)} reused={reused}",
                    flush=True,
                )
    identities.sort(key=lambda row: (int(row["market"]), str(row["code"])))
    failures.sort(key=lambda row: (int(row["market"]), str(row["code"])))
    reason_counts = Counter(str(row["reason"]) for row in failures)
    network_failures = int(reason_counts["same-source request failed"])
    coverage = len(identities) / max(1, len(universe_rows))
    if coverage < 0.80 or network_failures > max(10, int(len(universe_rows) * 0.01)):
        write_json(
            building / "incomplete_fetch_receipt.json",
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "usable": len(identities),
                "universe": len(universe_rows),
                "coverage": coverage,
                "failure_reasons": dict(sorted(reason_counts.items())),
                "resume_allowed": True,
            },
        )
        raise AShareScanError(
            f"snapshot coverage failed closed: usable={len(identities)}/{len(universe_rows)} network_failures={network_failures}"
        )
    receipt = {
        "protocol": "ashare_all_market_latest_completed_15m_qfq_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "owner_authorized_holdout_read": True,
        "holdout_consumption_number_for_checkpoint": 8,
        "upstream": {
            "akshare_main_commit": EXPECTED_AKSHARE_COMMIT,
            "akshare_stock_source_sha256": EXPECTED_AKSHARE_STOCK_SOURCE_SHA256,
            "universe_url": UNIVERSE_URL,
            "kline_url": KLINE_URL,
            "eastmoney_public_api_contract": "unpublished_and_unsupported",
        },
        "universe_rule": UNIVERSE_FS,
        "universe_rows": len(universe_rows),
        "universe_csv": "universe.csv",
        "universe_sha256": sha256_file(universe_path),
        "usable_symbols": len(identities),
        "coverage": coverage,
        "excluded_symbols": len(failures),
        "failure_reasons": dict(sorted(reason_counts.items())),
        "failures": failures,
        "cutoff_close_cst": CUTOFF_CLOSE_CST.isoformat(),
        "cutoff_open_utc": CUTOFF_OPEN_UTC.isoformat(),
        "adjustment": "qfq",
        "requested_rows_per_symbol": KLINE_LIMIT,
        "schedule_match_bars": SCHEDULE_MATCH_BARS,
        "reference": {
            "secid": REFERENCE_SECID,
            "name": REFERENCE_NAME,
            "path": "reference_schedule.csv",
            "sha256": reference_sha,
            "rows": len(reference),
        },
        "snapshots": identities,
        "network_reads": "Eastmoney universe plus one SSE Composite schedule and one bounded qfq 15m request per A-share identity; bounded same-source retries only",
        "model_loaded": False,
        "training_or_tuning": False,
        "threshold_or_weight_changed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "production_eligible": False,
    }
    write_json(building / "fetch_receipt.json", receipt)
    os.replace(building, out)
    print(
        f"snapshot complete usable={len(identities)}/{len(universe_rows)} -> {out}",
        flush=True,
    )
    return receipt


def load_snapshot(
    out: Path,
) -> tuple[list[dict[str, Any]], pd.DataFrame, dict[str, Any]]:
    """Verify and load the frozen snapshot without a network path."""

    receipt = read_json(out / "fetch_receipt.json")
    if receipt.get("experiment_id") != EXPERIMENT_ID:
        raise AShareScanError("fetch receipt experiment identity drifted")
    if int(receipt.get("holdout_consumption_number_for_checkpoint", -1)) != 8:
        raise AShareScanError("fetch receipt holdout identity drifted")
    universe_path = out / str(receipt["universe_csv"])
    if sha256_file(universe_path) != str(receipt["universe_sha256"]):
        raise AShareScanError("frozen universe bytes drifted")
    reference_path = out / str(receipt["reference"]["path"])
    if sha256_file(reference_path) != str(receipt["reference"]["sha256"]):
        raise AShareScanError("reference schedule bytes drifted")
    reference = load_candle(reference_path)
    snapshots = list(receipt["snapshots"])
    if len(snapshots) != int(receipt["usable_symbols"]):
        raise AShareScanError("snapshot receipt count drifted")
    return snapshots, reference, receipt


def _prediction_record(
    *,
    xywhn: Sequence[float],
    class_id: int,
    confidence: float,
    transform: ChartTransform,
    meta: Mapping[str, Any],
    frame: pd.DataFrame,
    input_hash: str,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    cid = int(class_id)
    if cid not in CLASS_NAMES:
        return {
            **meta,
            "class_id": cid,
            "confidence": float(confidence),
            "structural_pass": False,
            "structural_rejection_reason": "unknown_class",
        }
    mapped = map_prediction_to_core(
        cx=float(xywhn[0]),
        width=float(xywhn[2]),
        transform=transform,
        window_start_i=int(meta["window_start_i"]),
        window_end_i=int(meta["window_end_i"]),
    )
    reason = ""
    if int(mapped["core_length_bars"]) not in ALLOWED_CORES:
        reason = "core_length"
    elif int(mapped["confirmation_bars"]) not in ALLOWED_CONFIRMATIONS:
        reason = "confirmation_bars"
    direction = "LONG" if cid == 0 else "SHORT"
    record: dict[str, Any] = {
        **meta,
        **mapped,
        "prediction_cx_norm": float(xywhn[0]),
        "prediction_cy_norm": float(xywhn[1]),
        "prediction_w_norm": float(xywhn[2]),
        "prediction_h_norm": float(xywhn[3]),
        "class_id": cid,
        "class_name": CLASS_NAMES[cid],
        "direction": direction,
        "confidence": float(confidence),
        "input_pixel_sha256": input_hash,
        "structural_pass": not reason,
        "structural_rejection_reason": reason,
        "semantic_pass": False,
    }
    if reason:
        return record
    core_start = int(mapped["core_start_i"])
    core_end = int(mapped["core_end_i"])
    observed = int(meta["window_end_i"])
    features = compute_causal_core_semantics(
        frame,
        core_start_i=core_start,
        core_end_i=core_end,
        observed_end_i=observed,
        direction=direction,
    )
    result = evaluate_causal_semantic_gate(features, gates)
    flipped_direction = "SHORT" if direction == "LONG" else "LONG"
    flipped_features = compute_causal_core_semantics(
        frame,
        core_start_i=core_start,
        core_end_i=core_end,
        observed_end_i=observed,
        direction=flipped_direction,
    )
    flipped = evaluate_causal_semantic_gate(flipped_features, gates)
    times = pd.to_datetime(frame["open_time"], utc=True)
    segment = frame.iloc[core_start : core_end + 1]
    record.update(
        {
            "core_start_time": utc(times.iloc[core_start]).isoformat(),
            "core_end_time": utc(times.iloc[core_end]).isoformat(),
            "core_high": float(segment["high"].max()),
            "core_low": float(segment["low"].min()),
            "semantic_pass": bool(result.passed),
            "semantic_checks": result.checks,
            "semantic_failed_checks": list(result.failed_checks),
            "semantic_features": features.to_dict(),
            "flipped_semantic_pass": bool(flipped.passed),
            "flipped_semantic_failed_checks": list(flipped.failed_checks),
        }
    )
    return record


def _run_batch(
    model: Any,
    batch: Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any], pd.DataFrame]],
    *,
    device: str,
    gates: Mapping[str, Any],
) -> list[dict[str, Any]]:
    predictions = model.predict(
        source=[item[0] for item in batch],
        imgsz=IMAGE_SIZE,
        conf=CONFIDENCE,
        iou=NMS_IOU,
        batch=len(batch),
        device=device,
        verbose=False,
    )
    if len(predictions) != len(batch):
        raise AShareScanError("model prediction/task count mismatch")
    rows: list[dict[str, Any]] = []
    for prediction, (image, transform, meta, frame) in zip(predictions, batch):
        boxes = prediction.boxes
        if boxes is None or len(boxes) == 0:
            continue
        input_hash = pixel_sha256(image)
        for xywhn, class_id, confidence in zip(
            boxes.xywhn.cpu().numpy(), boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy()
        ):
            rows.append(
                _prediction_record(
                    xywhn=xywhn,
                    class_id=int(class_id),
                    confidence=float(confidence),
                    transform=transform,
                    meta=meta,
                    frame=frame,
                    input_hash=input_hash,
                    gates=gates,
                )
            )
    return rows


def _deduplicate_semantic(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if bool(row.get("semantic_pass")):
            by_symbol[str(row["secid"])].append(dict(row))
    events: list[dict[str, Any]] = []
    for secid, candidates in sorted(by_symbol.items()):
        kept = deduplicate_hits(candidates, gap_bars=EVENT_GAP_BARS)
        for peak in kept:
            related = [
                item
                for item in candidates
                if abs(int(item["core_end_i"]) - int(peak["core_end_i"]))
                < EVENT_GAP_BARS
            ]
            representative = max(
                related,
                key=lambda item: (float(item["confidence"]), int(item["window_len"])),
            )
            event = dict(representative)
            event["event_peak_confidence"] = float(peak["confidence"])
            event["classes_observed"] = sorted(
                {str(item["direction"]) for item in related}
            )
            event["candidate_count"] = len(related)
            event["secid"] = secid
            events.append(event)
    events.sort(
        key=lambda row: (
            -float(row["confidence"]),
            str(row["secid"]),
            int(row["core_end_i"]),
        )
    )
    for index, row in enumerate(events, 1):
        row["event_id"] = (
            f"ashare15m_{index:04d}_{row['code']}_{row['direction'].lower()}"
        )
    return events


def _put_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    scale: float = 0.55,
    color: tuple[int, int, int] = (28, 28, 28),
    thickness: int = 1,
) -> None:
    cv2.putText(
        image,
        text,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def _x_at_float(transform: ChartTransform, index: float) -> int:
    if transform.n_bars <= 1:
        return transform.left
    return round(
        transform.left + (float(index) / (transform.n_bars - 1)) * transform.plot_w
    )


def _normalized_box_corners(
    row: Mapping[str, Any], width: int, height: int
) -> tuple[int, int, int, int]:
    cx = float(row["prediction_cx_norm"])
    cy = float(row["prediction_cy_norm"])
    bw = float(row["prediction_w_norm"])
    bh = float(row["prediction_h_norm"])
    return (
        round((cx - bw / 2) * width),
        round((cy - bh / 2) * height),
        round((cx + bw / 2) * width),
        round((cy + bh / 2) * height),
    )


def _inverse_y(transform: ChartTransform, y: float) -> float:
    return float(
        transform.price_max
        - ((float(y) - transform.top) / transform.plot_h)
        * (transform.price_max - transform.price_min)
    )


def _project_raw_box(
    row: Mapping[str, Any],
    *,
    input_tf: ChartTransform,
    context_tf: ChartTransform,
    context_start_i: int,
) -> tuple[int, int, int, int]:
    raw = _normalized_box_corners(row, input_tf.width, input_tf.height)
    local_x0 = (
        (float(raw[0]) - input_tf.left) / input_tf.plot_w * max(1, input_tf.n_bars - 1)
    )
    local_x1 = (
        (float(raw[2]) - input_tf.left) / input_tf.plot_w * max(1, input_tf.n_bars - 1)
    )
    global_x0 = int(row["window_start_i"]) + local_x0
    global_x1 = int(row["window_start_i"]) + local_x1
    x0 = _x_at_float(context_tf, global_x0 - context_start_i)
    x1 = _x_at_float(context_tf, global_x1 - context_start_i)
    price_top = _inverse_y(input_tf, raw[1])
    price_bottom = _inverse_y(input_tf, raw[3])
    y0 = context_tf.y_at(price_top)
    y1 = context_tf.y_at(price_bottom)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def render_event(
    row: Mapping[str, Any], *, frame: pd.DataFrame, order: int, total: int
) -> np.ndarray:
    """Render full causal context plus the exact scored input and raw box."""

    start_i = int(row["window_start_i"])
    end_i = int(row["window_end_i"])
    model_window = frame.iloc[start_i : end_i + 1]
    clean, input_tf = render_chart(model_window, out_path=None)
    if pixel_sha256(clean) != str(row["input_pixel_sha256"]):
        raise AShareScanError(f"model input pixel replay drifted: {row['event_id']}")
    overlay = clean.copy()
    raw = _normalized_box_corners(row, input_tf.width, input_tf.height)
    color = CLASS_COLORS[int(row["class_id"])]
    cv2.rectangle(overlay, (raw[0], raw[1]), (raw[2], raw[3]), color, 4, cv2.LINE_AA)

    context_start_i = max(0, end_i - CONTEXT_BARS + 1)
    context = frame.iloc[context_start_i : end_i + 1]
    main, context_tf = render_chart(
        context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None
    )
    projected = _project_raw_box(
        row, input_tf=input_tf, context_tf=context_tf, context_start_i=context_start_i
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
    direction = str(row["direction"])
    detect_open = cst(row["window_end_time"])
    detect_available = detect_open + BAR_DELTA
    core_start = cst(row["core_start_time"])
    core_end = cst(row["core_end_time"])
    _put_text(
        canvas,
        f"{row['code']} | A-SHARE 15m OOD RESEARCH | {direction} conf {float(row['confidence']):.3f} | {order:03d}/{total:03d}",
        (24, 38),
        scale=0.70,
        thickness=2,
    )
    _put_text(
        canvas,
        f"core opens {core_start:%m-%d %H:%M}..{core_end:%H:%M} CST | latest model input closes {detect_available:%m-%d %H:%M} CST",
        (24, 72),
        scale=0.49,
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
        x = MAIN_X + _x_at_float(context_tf, int(local_i))
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
    _put_text(
        canvas,
        "Top: 128 completed A-share 15m bars through the frozen 11:30 CST cutoff. Rectangle is the unchanged YOLO box.",
        (28, footer_y + 34),
        scale=0.43,
    )
    _put_text(
        canvas,
        "Right: exact W18/W19 1280x742 image scored by the model; the same frozen numeric semantic gate also passed.",
        (28, footer_y + 64),
        scale=0.43,
    )
    _put_text(
        canvas,
        "Research proposal only: crypto-trained and unvalidated on A shares; confidence is not win probability. SHORT is not a cash-stock order.",
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
    chart_paths: Sequence[Path], events: Sequence[Mapping[str, Any]], results: Path
) -> list[str]:
    """Create compact paged contact sheets for all semantic survivors."""

    if not chart_paths:
        blank = np.full((720, 1280, 3), 247, dtype=np.uint8)
        _put_text(
            blank,
            "A-SHARE 15m OOD RESEARCH: ZERO SEMANTIC-GATE SURVIVORS",
            (110, 320),
            scale=0.85,
            thickness=2,
        )
        _put_text(
            blank,
            "all-market frozen universe | latest completed 11:30 CST endpoint | thresholds unchanged",
            (130, 375),
            scale=0.57,
        )
        cv2.imwrite(str(results / "overview.png"), blank)
        return ["overview.png"]
    pages: list[str] = []
    page_size = 9
    for page_number, start in enumerate(range(0, len(chart_paths), page_size), 1):
        subset = chart_paths[start : start + page_size]
        thumb_w, thumb_h = 620, 426
        sheet = np.full((3 * thumb_h + 82, 3 * thumb_w, 3), 240, dtype=np.uint8)
        _put_text(
            sheet,
            f"A-SHARE 15m OOD RESEARCH | semantic survivors | page {page_number}",
            (24, 34),
            scale=0.72,
            thickness=2,
        )
        _put_text(
            sheet,
            "crypto-trained Grade-A detector + frozen semantic gate; NOT validated trade signals",
            (24, 66),
            scale=0.48,
            color=(45, 45, 180),
            thickness=2,
        )
        for slot, path in enumerate(subset):
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise AShareScanError(f"could not read chart for overview: {path}")
            thumb = cv2.resize(image, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
            row, col = divmod(slot, 3)
            y, x = 82 + row * thumb_h, col * thumb_w
            sheet[y : y + thumb_h, x : x + thumb_w] = thumb
            event = events[start + slot]
            label = f"{start + slot + 1:03d} {event['code']} {event['direction']} {float(event['confidence']):.3f}"
            cv2.rectangle(sheet, (x + 4, y + 4), (x + 320, y + 31), (250, 250, 250), -1)
            _put_text(sheet, label, (x + 10, y + 25), scale=0.53, thickness=2)
        filename = f"overview_page_{page_number:02d}.png"
        cv2.imwrite(str(results / filename), sheet)
        pages.append(filename)
    shutil.copyfile(results / pages[0], results / "overview.png")
    return pages


def scan_snapshot(
    out: Path, results: Path, *, device_arg: str | None, batch_size: int
) -> dict[str, Any]:
    """Run exactly two latest-endpoint images per eligible frozen symbol."""

    source_commit = require_builder_committed()
    prereg, gates = verify_frozen_contract()
    snapshots, reference, fetch_receipt = load_snapshot(out)
    if results.exists():
        raise FileExistsError(f"refusing to overwrite scan results: {results}")
    building = results.with_name(results.name + ".building")
    if building.exists():
        raise FileExistsError(f"stale results building directory exists: {building}")
    chart_dir = building / "charts"
    building.mkdir(parents=True)
    chart_dir.mkdir()
    started = time.perf_counter()
    device = choose_device(device_arg)
    from ultralytics import YOLO

    model = YOLO(str(WEIGHTS))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != CLASS_NAMES:
        raise AShareScanError(f"class map drifted: {names}")
    all_boxes: list[dict[str, Any]] = []
    batch: list[tuple[np.ndarray, ChartTransform, dict[str, Any], pd.DataFrame]] = []
    windows_scored = 0
    for symbol_number, identity in enumerate(snapshots, 1):
        path = out / str(identity["path"])
        if sha256_file(path) != str(identity["sha256"]):
            raise AShareScanError(
                f"snapshot bytes drifted before scan: {identity['secid']}"
            )
        frame = load_candle(path)
        validate_against_schedule(frame, reference, secid=str(identity["secid"]))
        enriched = add_candidate_features(frame)
        endpoint = len(enriched) - 1
        for window_len in WINDOW_LENGTHS:
            start_i = endpoint - window_len + 1
            window = enriched.iloc[start_i : endpoint + 1]
            if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                raise AShareScanError(
                    f"MA warmup failed despite schedule contract: {identity['secid']}"
                )
            image, transform = render_chart(window, out_path=None)
            batch.append(
                (
                    image,
                    transform,
                    {
                        "secid": str(identity["secid"]),
                        "market": int(identity["market"]),
                        "code": str(identity["code"]),
                        "name": str(identity["name"]),
                        "snapshot_path": str(identity["path"]),
                        "snapshot_sha256": str(identity["sha256"]),
                        "window_len": window_len,
                        "window_start_i": start_i,
                        "window_end_i": endpoint,
                        "window_end_time": utc(
                            enriched.iloc[endpoint]["open_time"]
                        ).isoformat(),
                        "window_available_at": (
                            utc(enriched.iloc[endpoint]["open_time"]) + BAR_DELTA
                        ).isoformat(),
                    },
                    enriched,
                )
            )
            if len(batch) >= batch_size:
                all_boxes.extend(_run_batch(model, batch, device=device, gates=gates))
                windows_scored += len(batch)
                batch = []
                if windows_scored % (batch_size * 10) == 0:
                    print(
                        f"inference {windows_scored}/{len(snapshots) * len(WINDOW_LENGTHS)} raw_boxes={len(all_boxes)}",
                        flush=True,
                    )
        if symbol_number % 500 == 0:
            print(f"prepared {symbol_number}/{len(snapshots)} symbols", flush=True)
    if batch:
        all_boxes.extend(_run_batch(model, batch, device=device, gates=gates))
        windows_scored += len(batch)
    structural = [row for row in all_boxes if bool(row.get("structural_pass"))]
    semantic = [row for row in structural if bool(row.get("semantic_pass"))]
    events = _deduplicate_semantic(semantic)

    chart_paths: list[Path] = []
    snapshot_by_secid = {str(item["secid"]): item for item in snapshots}
    enriched_cache: dict[str, pd.DataFrame] = {}
    for order, event in enumerate(events, 1):
        secid = str(event["secid"])
        if secid not in enriched_cache:
            identity = snapshot_by_secid[secid]
            enriched_cache[secid] = add_candidate_features(
                load_candle(out / str(identity["path"]))
            )
        canvas = render_event(
            event, frame=enriched_cache[secid], order=order, total=len(events)
        )
        path = (
            chart_dir
            / f"{order:03d}_{event['code']}_{str(event['direction']).lower()}.png"
        )
        if not cv2.imwrite(str(path), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            raise AShareScanError(f"failed to write chart: {path}")
        event["chart"] = f"charts/{path.name}"
        event["chart_sha256"] = sha256_file(path)
        chart_paths.append(path)
    pages = build_overview(chart_paths, events, building)
    write_jsonl(building / "raw_boxes.jsonl", all_boxes)
    write_jsonl(building / "structural_boxes.jsonl", structural)
    write_jsonl(building / "semantic_boxes.jsonl", semantic)
    write_jsonl(building / "signals.jsonl", events)
    pd.DataFrame(events).to_csv(building / "signals.csv", index=False)
    side_counts = Counter(str(row["direction"]) for row in events)
    raw_side_counts = Counter(
        str(row.get("direction") or "UNKNOWN") for row in structural
    )
    failed_checks = Counter(
        check for row in structural for check in row.get("semantic_failed_checks", [])
    )
    summary = {
        "protocol": "ashare_grade_a_latest_endpoint_yolo_semantic_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "fetch_receipt_sha256": sha256_file(out / "fetch_receipt.json"),
        "weights": str(WEIGHTS.relative_to(ROOT)),
        "weights_sha256": EXPECTED_WEIGHT_SHA256,
        "model": prereg["model_contract"]["name"],
        "source_domain": "crypto_15m",
        "inference_domain": "mainland_A_share_15m",
        "out_of_distribution": True,
        "research_only": True,
        "holdout_consumed": True,
        "holdout_consumption_number_for_checkpoint": 8,
        "cutoff_close_cst": CUTOFF_CLOSE_CST.isoformat(),
        "cutoff_open_utc": CUTOFF_OPEN_UTC.isoformat(),
        "universe_symbols": int(fetch_receipt["universe_rows"]),
        "usable_symbols": len(snapshots),
        "excluded_symbols": int(fetch_receipt["excluded_symbols"]),
        "coverage": float(fetch_receipt["coverage"]),
        "windows_scored": windows_scored,
        "raw_boxes": len(all_boxes),
        "structural_boxes": len(structural),
        "structural_long_boxes": int(raw_side_counts["LONG"]),
        "structural_short_boxes": int(raw_side_counts["SHORT"]),
        "semantic_boxes": len(semantic),
        "semantic_events": len(events),
        "long_events": int(side_counts["LONG"]),
        "short_events": int(side_counts["SHORT"]),
        "direction_flip_semantic_survivors": sum(
            bool(row.get("flipped_semantic_pass")) for row in structural
        ),
        "semantic_failure_checks": dict(sorted(failed_checks.items())),
        "overview_pages": pages,
        "detector_contract": {
            "imgsz": IMAGE_SIZE,
            "confidence": CONFIDENCE,
            "nms_iou": NMS_IOU,
            "window_lengths": list(WINDOW_LENGTHS),
            "allowed_core_lengths": sorted(ALLOWED_CORES),
            "allowed_confirmation_bars": sorted(ALLOWED_CONFIRMATIONS),
            "same_symbol_event_gap_bars": EVENT_GAP_BARS,
            "latest_endpoints_per_symbol": 1,
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
        "orders_placed": False,
        "production_eligible": False,
    }
    write_json(building / "summary.json", summary)
    os.replace(building, results)
    print(
        f"scan complete windows={windows_scored} raw={len(all_boxes)} structural={len(structural)} semantic_events={len(events)} -> {results}",
        flush=True,
    )
    return summary


def verify_results(out: Path, results: Path) -> dict[str, Any]:
    """Recompute all material box decisions and delivered chart identities."""

    source_commit = require_builder_committed()
    _, gates = verify_frozen_contract()
    snapshots, reference, receipt = load_snapshot(out)
    summary = read_json(results / "summary.json")
    raw_boxes = read_jsonl(results / "raw_boxes.jsonl")
    structural = read_jsonl(results / "structural_boxes.jsonl")
    signals = read_jsonl(results / "signals.jsonl")
    if len(raw_boxes) != int(summary["raw_boxes"]) or len(structural) != int(
        summary["structural_boxes"]
    ):
        raise AShareScanError("saved box counts drifted")
    by_secid: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in structural:
        by_secid[str(row["secid"])].append(row)
    snapshot_by_secid = {str(item["secid"]): item for item in snapshots}
    candle_hash_checks = 0
    pixel_checks = 0
    semantic_checks = 0
    for identity in snapshots:
        path = out / str(identity["path"])
        if sha256_file(path) != str(identity["sha256"]):
            raise AShareScanError(
                f"verification candle SHA failed: {identity['secid']}"
            )
        candle_hash_checks += 1
    enriched_cache: dict[str, pd.DataFrame] = {}
    for secid, rows in sorted(by_secid.items()):
        identity = snapshot_by_secid[secid]
        frame = load_candle(out / str(identity["path"]))
        validate_against_schedule(frame, reference, secid=secid)
        enriched = add_candidate_features(frame)
        enriched_cache[secid] = enriched
        rendered: dict[tuple[int, int], tuple[np.ndarray, ChartTransform]] = {}
        for saved in rows:
            key = (int(saved["window_start_i"]), int(saved["window_end_i"]))
            if key not in rendered:
                rendered[key] = render_chart(
                    enriched.iloc[key[0] : key[1] + 1], out_path=None
                )
            image, transform = rendered[key]
            if pixel_sha256(image) != str(saved["input_pixel_sha256"]):
                raise AShareScanError(
                    f"verification pixel hash failed: {secid} W{saved['window_len']}"
                )
            pixel_checks += 1
            xywhn = [
                float(saved["prediction_cx_norm"]),
                float(saved["prediction_cy_norm"]),
                float(saved["prediction_w_norm"]),
                float(saved["prediction_h_norm"]),
            ]
            recomputed = _prediction_record(
                xywhn=xywhn,
                class_id=int(saved["class_id"]),
                confidence=float(saved["confidence"]),
                transform=transform,
                meta={
                    key: saved[key]
                    for key in (
                        "secid",
                        "market",
                        "code",
                        "name",
                        "snapshot_path",
                        "snapshot_sha256",
                        "window_len",
                        "window_start_i",
                        "window_end_i",
                        "window_end_time",
                        "window_available_at",
                    )
                },
                frame=enriched,
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
            if any(recomputed.get(key) != saved.get(key) for key in comparable):
                raise AShareScanError(
                    f"verification semantic decision failed: {secid} {saved['code']}"
                )
            semantic_checks += 1
    chart_checks = 0
    for order, signal in enumerate(signals, 1):
        secid = str(signal["secid"])
        if secid not in enriched_cache:
            identity = snapshot_by_secid[secid]
            enriched_cache[secid] = add_candidate_features(
                load_candle(out / str(identity["path"]))
            )
        rerendered = render_event(
            signal, frame=enriched_cache[secid], order=order, total=len(signals)
        )
        encoded_ok, _encoded = cv2.imencode(
            ".png", rerendered, [cv2.IMWRITE_PNG_COMPRESSION, 3]
        )
        if not encoded_ok:
            raise AShareScanError("verification could not encode chart")
        saved_path = results / str(signal["chart"])
        saved = cv2.imread(str(saved_path), cv2.IMREAD_COLOR)
        if saved is None or not np.array_equal(saved, rerendered):
            raise AShareScanError(
                f"verification chart pixels failed: {signal['event_id']}"
            )
        if sha256_file(saved_path) != str(signal["chart_sha256"]):
            raise AShareScanError(
                f"verification chart SHA failed: {signal['event_id']}"
            )
        chart_checks += 1
    verification = {
        "protocol": "ashare_grade_a_latest_endpoint_independent_replay_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "fetch_receipt_sha256": sha256_file(out / "fetch_receipt.json"),
        "summary_sha256": sha256_file(results / "summary.json"),
        "universe_symbols": int(receipt["universe_rows"]),
        "usable_symbols": len(snapshots),
        "candle_sha_checks": candle_hash_checks,
        "structural_input_pixel_checks": pixel_checks,
        "semantic_decision_checks": semantic_checks,
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
