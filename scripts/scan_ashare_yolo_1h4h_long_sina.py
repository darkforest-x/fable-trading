#!/usr/bin/env python3
"""Run owner-authorized A-share 1h/session-4h LONG research from Sina 60m.

This is the replacement for the consumed-but-failed Eastmoney configurations
#9/#10.  Owner authorized new holdout configurations #11/#12 after the first
source exposed only 127 hourly rows.  The source phase is deliberately gated:

1. write the #11/#12 consumption-start ledger;
2. prove Sina's ``sh000001`` response has >=640 exact completed 60m bars and
   >=160 complete A-share sessions through 2026-09-02 15:00 CST;
3. compare the overlapping reference and two fixed QFQ sentinels with the
   already used Eastmoney endpoint under preregistered parity tolerances;
4. only then fan out to the frozen 3,111-name ordinary SH/SZ main-board pool.

The scan phase loads no network client.  It scores one latest 1h endpoint and
the last five complete session-4h endpoints, uses only causal rows at each
endpoint, audits both model classes, and renders/delivers LONG survivors only.
Verification replays candle hashes, QFQ arithmetic, model-input pixels,
semantic decisions, LONG selection and chart pixels without inference/network.

Input columns are Sina raw OHLCVA plus a date-causal QFQ factor.  Features use
only adjusted OHLCV at or before the scored bar; no future bar is read.  These
remain crypto-15m -> A-share-1h/4h OOD completed-history model proposals, not
validated trade signals, recommendations, win probabilities, or orders.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import cv2
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_15m_ashare_yolo_latest as base
from scripts import scan_ashare_yolo_1h4h_long as shared
from scripts.scan_15m_ma_launch_t3_daily_movers import choose_device
from yoyo.data.ashare_sessions import aggregate_complete_session_4h
from yoyo.data.sina_ashare import (
    SINA_HOURLY_CLOSE_SLOTS,
    apply_sina_qfq,
    parse_sina_hourly_jsonp,
    parse_sina_qfq_factor_js,
)
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.render import ChartTransform, render_chart

EXPERIMENT_ID = "exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2"
PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
TRANSPORT_RECOVERY = (
    ROOT / "experiments" / "active" / EXPERIMENT_ID / "transport_recovery.json"
)
CACHED_PARITY_AUTHORIZATION = (
    ROOT
    / "experiments"
    / "active"
    / EXPERIMENT_ID
    / "cached_parity_authorization.json"
)
DEFAULT_OUT = ROOT / "analysis/output/ashare_1h4h_long_sina_20260902_v2"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
SINA_PARSER = ROOT / "yoyo/data/sina_ashare.py"
SESSION_AGGREGATOR = ROOT / "yoyo/data/ashare_sessions.py"
SHARED_ORCHESTRATION = ROOT / "scripts/scan_ashare_yolo_1h4h_long.py"
RETAIL_FILTER = ROOT / "scripts/filter_ashare_signals_for_standard_retail.py"
SOURCE_UNIVERSE = shared.SOURCE_UNIVERSE
SOURCE_FETCH_RECEIPT = shared.SOURCE_FETCH_RECEIPT
WEIGHTS = base.WEIGHTS
AUTOFILL_PREREG = base.AUTOFILL_PREREG

EXPECTED_SINA_PARSER_SHA256 = (
    "89a86728211128fc1b07b3c53b87e047b4c0464254c81f381176259a4f774f35"
)
EXPECTED_SESSION_AGGREGATOR_SHA256 = (
    "78d1801d4a46052c2ce63e85dda044299ac5c06a27e79dd54770922a2dabfdec"
)
EXPECTED_SHARED_ORCHESTRATION_SHA256 = (
    "951a1777ed7fc0443389d42d6f01f2dde8d51cf42ea73ba8b847ab00e12d842b"
)
EXPECTED_AKSHARE_SINA_SHA256 = (
    "80c9d622a9e8cab5324605d63ccce589d4c3e771d259692c5d93cf7da8e99547"
)
EXPECTED_AKSHARE_CONS_SHA256 = (
    "435bf1763531d0eb33d9a8ba25fbc9ab3c5bcc11bbd7834978cbdfdec4c0bbb9"
)
EXPECTED_TRANSPORT_RECOVERY_SHA256 = (
    "3a4ce5750267c010b75db6671d0679325ce56c0c1a44afe7e172d419a7c3a2c9"
)
EXPECTED_CACHED_PARITY_AUTHORIZATION_SHA256 = (
    "02a6de34d2aa0c5da49f6e32572acab41bf37b6215ea9e89e3faa158bc5b7baa"
)

SINA_MINUTE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/"
    "CN_MarketDataService.getKLineData"
)
SINA_MINUTE_FALLBACK = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20_{symbol}_60_1658852984203=/CN_MarketDataService.getKLineData"
)
SINA_QFQ_URL = "https://finance.sina.com.cn/realstock/company/{symbol}/qfq.js"
SINA_DATALEN = 1970
REFERENCE_SINA_SYMBOL = "sh000001"
REFERENCE_SECID = "1.000001"
SENTINELS = ("sh600000", "sz000001")

CUTOFF_CST = pd.Timestamp("2026-09-02T15:00:00+08:00")
SCHEDULE_MATCH_1H = 160
SCHEDULE_MATCH_4H = 160
MIN_REFERENCE_60M_ROWS = 640
RECENT_4H_ENDPOINTS = 5
MIN_COVERAGE = 0.80
MAX_NETWORK_FAILURE_RATE = 0.01
REFERENCE_PARITY_MIN_SHARED = 100
REFERENCE_PARITY_MEDIAN_MAX = 0.001
REFERENCE_PARITY_P99_MAX = 0.005
QFQ_PARITY_MIN_SHARED = 100
QFQ_PARITY_MEDIAN_MAX = 0.003
QFQ_PARITY_P99_MAX = 0.02

WINDOW_LENGTHS = base.WINDOW_LENGTHS
CLASS_NAMES = base.CLASS_NAMES
EXPECTED_WEIGHT_SHA256 = base.EXPECTED_WEIGHT_SHA256
_THREAD_LOCAL = threading.local()


class AShareSinaScanError(RuntimeError):
    """Raised when preregistration, source, time, model, or evidence drifts."""


sha256_file = base.sha256_file
pixel_sha256 = base.pixel_sha256
read_json = base.read_json
write_json = base.write_json
write_jsonl = base.write_jsonl
read_jsonl = base.read_jsonl
utc = base.utc


def _git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def require_builder_committed() -> str:
    """Require every executable/pinned local input committed on main."""

    relative = [
        str(Path(__file__).resolve().relative_to(ROOT)),
        str(PREREG.relative_to(ROOT)),
        str(TRANSPORT_RECOVERY.relative_to(ROOT)),
        str(CACHED_PARITY_AUTHORIZATION.relative_to(ROOT)),
        str(SINA_PARSER.relative_to(ROOT)),
        str(SESSION_AGGREGATOR.relative_to(ROOT)),
        str(SHARED_ORCHESTRATION.relative_to(ROOT)),
        str(RETAIL_FILTER.relative_to(ROOT)),
    ]
    for path in relative:
        _git_output("ls-files", "--error-unmatch", path)
    dirty = _git_output("status", "--porcelain", "--", *relative)
    if dirty:
        raise AShareSinaScanError(f"runtime inputs must be committed first:{dirty}")
    if _git_output("branch", "--show-current") != "main":
        raise AShareSinaScanError("experiment must run from main")
    return _git_output("rev-parse", "HEAD")


def verify_frozen_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify source/model hashes, two consumptions, and every safety switch."""

    prereg = read_json(PREREG)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise AShareSinaScanError("preregistration experiment identity drifted")
    recovery = read_json(TRANSPORT_RECOVERY)
    if (
        recovery.get("experiment_id") != EXPERIMENT_ID
        or sha256_file(TRANSPORT_RECOVERY) != EXPECTED_TRANSPORT_RECOVERY_SHA256
    ):
        raise AShareSinaScanError("transport recovery identity drifted")
    invariants = recovery.get("frozen_invariants") or {}
    if any(value is not False for value in invariants.values()):
        raise AShareSinaScanError("transport recovery changed a frozen invariant")
    cached_authorization = read_json(CACHED_PARITY_AUTHORIZATION)
    if (
        cached_authorization.get("experiment_id") != EXPERIMENT_ID
        or sha256_file(CACHED_PARITY_AUTHORIZATION)
        != EXPECTED_CACHED_PARITY_AUTHORIZATION_SHA256
    ):
        raise AShareSinaScanError("cached parity authorization identity drifted")
    cached_invariants = cached_authorization.get("frozen_invariants") or {}
    if any(value is not False for value in cached_invariants.values()):
        raise AShareSinaScanError("cached parity changed a frozen invariant")
    if cached_authorization["owner_authorization"].get(
        "new_holdout_configuration_created"
    ) is not False:
        raise AShareSinaScanError("cached parity created an unauthorized configuration")
    configs = prereg.get("configuration_consumptions") or []
    actual = [
        (
            str(row["configuration"]),
            int(row["holdout_consumption_number_for_checkpoint"]),
            str(row["cutoff_close_cst"]),
            int(row["latest_endpoints_per_symbol"]),
        )
        for row in configs
    ]
    expected = [
        (
            "sina_qfq_mainland_A_share_1h_latest_completed_endpoint",
            11,
            CUTOFF_CST.isoformat(),
            1,
        ),
        (
            "sina_qfq_mainland_A_share_session_4h_recent_five_complete_days",
            12,
            CUTOFF_CST.isoformat(),
            RECENT_4H_ENDPOINTS,
        ),
    ]
    if actual != expected:
        raise AShareSinaScanError("configuration/holdout contract drifted")
    safety = prereg["safety"]
    if safety.get("holdout_consumed") is not True or safety.get(
        "holdout_consumption_numbers_for_checkpoint"
    ) != {"1h": 11, "4h": 12}:
        raise AShareSinaScanError("holdout identity drifted")
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
            raise AShareSinaScanError(f"unsafe preregistration switch:{key}")
    source = prereg["source_contract"]
    if source["library_reference"]["minute_source_sha256"] != EXPECTED_AKSHARE_SINA_SHA256:
        raise AShareSinaScanError("pinned AKShare Sina source identity drifted")
    if source["library_reference"]["constants_source_sha256"] != EXPECTED_AKSHARE_CONS_SHA256:
        raise AShareSinaScanError("pinned AKShare constants identity drifted")
    pinned = {
        SINA_PARSER: source["library_reference"]["local_parser_sha256"],
        SESSION_AGGREGATOR: source["session_4h"]["aggregator_sha256"],
        SHARED_ORCHESTRATION: source["library_reference"][
            "shared_orchestration_sha256"
        ],
        RETAIL_FILTER: source["universe"]["filter_module_sha256"],
        SOURCE_UNIVERSE: source["universe"]["source_sha256"],
        SOURCE_FETCH_RECEIPT: source["universe"]["source_fetch_receipt_sha256"],
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
    }
    for path, expected_hash in pinned.items():
        if not path.is_file() or sha256_file(path) != str(expected_hash):
            raise AShareSinaScanError(f"frozen input SHA drift:{path}")
    if sha256_file(SINA_PARSER) != EXPECTED_SINA_PARSER_SHA256:
        raise AShareSinaScanError("Sina parser identity drifted")
    if sha256_file(SESSION_AGGREGATOR) != EXPECTED_SESSION_AGGREGATOR_SHA256:
        raise AShareSinaScanError("session aggregator identity drifted")
    if sha256_file(SHARED_ORCHESTRATION) != EXPECTED_SHARED_ORCHESTRATION_SHA256:
        raise AShareSinaScanError("shared chart/selection implementation drifted")
    if sha256_file(WEIGHTS) != EXPECTED_WEIGHT_SHA256:
        raise AShareSinaScanError("checkpoint identity drifted")
    gates = dict(read_json(AUTOFILL_PREREG)["morphology_gate"])
    return prereg, gates


def _session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json,text/javascript,*/*;q=0.8",
                "Referer": "https://finance.sina.com.cn/",
            }
        )
        _THREAD_LOCAL.session = session
    return session


def request_text(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    attempts: int = 6,
) -> str:
    """Read one text endpoint with bounded retries and nonempty response gate."""

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = _session().get(
                url, params=dict(params or {}), timeout=(5, 25)
            )
            response.raise_for_status()
            if not response.text.strip():
                raise AShareSinaScanError("upstream returned empty text")
            return response.text
        except Exception as exc:  # noqa: BLE001 - final class is evidence
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(10.0, 0.5 * (2**attempt)))
    raise AShareSinaScanError(
        f"same-source request failed after {attempts} attempts:{type(last).__name__}:{last}"
    )


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _public_ipv6_addresses(host: str) -> list[str]:
    """Resolve globally routable AAAA records without the local fake-IP DNS layer."""

    if host != "push2his.eastmoney.com":
        raise AShareSinaScanError(f"IPv6 transport host is not allowlisted:{host}")
    completed = subprocess.run(
        ["dig", "+short", "@1.1.1.1", host, "AAAA"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        raise AShareSinaScanError(
            f"public AAAA lookup failed:{completed.stderr.strip()}"
        )
    addresses: list[str] = []
    for line in completed.stdout.splitlines():
        candidate = line.strip().rstrip(".")
        try:
            address = ipaddress.IPv6Address(candidate)
        except ipaddress.AddressValueError:
            continue
        if address.is_global:
            addresses.append(str(address))
    if not addresses:
        raise AShareSinaScanError(f"no public AAAA record:{host}")
    return list(dict.fromkeys(addresses))


def _request_json_via_public_ipv6(
    url: str, params: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retry the identical Eastmoney request over validated public IPv6."""

    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or host != "push2his.eastmoney.com"
        or parsed.path != "/api/qt/stock/kline/get"
        or parsed.query
    ):
        raise AShareSinaScanError("Eastmoney IPv6 URL is outside recovery contract")
    errors: list[str] = []
    for address in _public_ipv6_addresses(host):
        command = [
            "curl",
            "--ipv6",
            "--noproxy",
            "*",
            "--resolve",
            f"{host}:443:[{address}]",
            "--proto",
            "=https",
            "--tlsv1.2",
            "--connect-timeout",
            "5",
            "--max-time",
            "25",
            "--retry",
            "2",
            "--retry-all-errors",
            "--retry-delay",
            "1",
            "--silent",
            "--show-error",
            "--fail-with-body",
            "--get",
            "--header",
            "Accept: application/json,text/plain,*/*",
            "--header",
            "Referer: https://quote.eastmoney.com/",
            "--user-agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            url,
        ]
        for key, value in params.items():
            command.extend(["--data-urlencode", f"{key}={value}"])
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=90,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{address}:{type(exc).__name__}:{exc}")
            continue
        if completed.returncode != 0:
            errors.append(
                f"{address}:curl_exit_{completed.returncode}:{completed.stderr.strip()}"
            )
            continue
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            errors.append(f"{address}:JSONDecodeError:{exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{address}:non_object_JSON")
            continue
        return payload, {
            "mode": "public_ipv6_same_https_endpoint",
            "hostname": host,
            "path": parsed.path,
            "resolved_ipv6": address,
            "public_dns_server": "1.1.1.1",
            "certificate_verification": True,
            "response_sha256": _text_sha256(completed.stdout),
            "response_chars": len(completed.stdout),
        }
    raise AShareSinaScanError(
        "Eastmoney public-IPv6 transport failed:" + " | ".join(errors)
    )


def fetch_sina_raw(symbol: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch one 1970-row Sina 60m JSONP payload, with pinned URL fallback."""

    params = {
        "symbol": symbol,
        "scale": "60",
        "ma": "no",
        "datalen": str(SINA_DATALEN),
    }
    errors: list[str] = []
    for variant, url in (
        ("primary", SINA_MINUTE_URL),
        ("akshare_fallback", SINA_MINUTE_FALLBACK.format(symbol=symbol)),
    ):
        try:
            text = request_text(url, params)
            frame = parse_sina_hourly_jsonp(
                text, symbol=symbol, cutoff_close=CUTOFF_CST
            )
            return frame, {
                "minute_url_variant": variant,
                "minute_response_sha256": _text_sha256(text),
                "minute_response_chars": len(text),
            }
        except Exception as exc:  # noqa: BLE001 - both variants are receipted
            errors.append(f"{variant}:{type(exc).__name__}:{exc}")
    raise AShareSinaScanError(
        f"Sina minute primary/fallback failed:{symbol}:{' | '.join(errors)}"
    )


def fetch_sina_qfq(
    symbol: str,
    *,
    secid: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch raw Sina 60m plus sparse factor and return frozen QFQ rows."""

    raw, meta = fetch_sina_raw(symbol)
    factor_text = request_text(SINA_QFQ_URL.format(symbol=symbol))
    factors = parse_sina_qfq_factor_js(factor_text, symbol=symbol)
    adjusted = apply_sina_qfq(raw, factors, symbol=symbol)
    adjusted["secid"] = secid
    meta.update(
        {
            "factor_response_sha256": _text_sha256(factor_text),
            "factor_response_chars": len(factor_text),
            "factor_rows": len(factors),
            "first_factor_date": str(factors.iloc[0]["factor_date"].date()),
            "last_factor_date": str(factors.iloc[-1]["factor_date"].date()),
        }
    )
    return adjusted, meta


def _parse_eastmoney_overlap(
    payload: Mapping[str, Any], *, secid: str, adjustment: str
) -> pd.DataFrame:
    """Parse only the short Eastmoney overlap used by frozen source parity."""

    rows = (payload.get("data") or {}).get("klines") or []
    parsed: list[list[Any]] = []
    for raw in rows:
        fields = str(raw).split(",")
        if len(fields) < 7:
            continue
        close_time = pd.Timestamp(fields[0])
        close_time = (
            close_time.tz_localize("Asia/Shanghai")
            if close_time.tzinfo is None
            else close_time.tz_convert("Asia/Shanghai")
        )
        if close_time > CUTOFF_CST:
            continue
        parsed.append(
            [
                close_time,
                (close_time - pd.Timedelta(hours=1)).tz_convert("UTC"),
                float(fields[1]),
                float(fields[3]),
                float(fields[4]),
                float(fields[2]),
                float(fields[5]),
                float(fields[6]),
                secid,
                adjustment,
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
            "secid",
            "adjustment",
        ],
    )
    frame.sort_values("raw_close_time", inplace=True, ignore_index=True)
    if frame.empty or frame["raw_close_time"].duplicated().any():
        raise AShareSinaScanError(f"invalid Eastmoney parity payload:{secid}")
    slots = set(frame["raw_close_time"].dt.strftime("%H:%M"))
    if not slots.issubset(set(SINA_HOURLY_CLOSE_SLOTS)):
        raise AShareSinaScanError(f"Eastmoney parity time slots drifted:{secid}")
    return frame


def fetch_eastmoney_overlap(secid: str, *, fqt: str, adjustment: str) -> pd.DataFrame:
    """Fetch the frozen short Eastmoney overlap; never use it as model input."""

    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": "60",
        "fqt": fqt,
        "secid": secid,
        "beg": "0",
        "end": "20500000",
        "lmt": "512",
    }
    primary_error = ""
    try:
        payload = base.request_json(base.KLINE_URL, params)
        transport = {
            "mode": "repository_requests_default",
            "hostname": "push2his.eastmoney.com",
            "path": "/api/qt/stock/kline/get",
        }
    except Exception as exc:  # noqa: BLE001 - exact recovery reason is receipted
        primary_error = f"{type(exc).__name__}:{exc}"
        payload, transport = _request_json_via_public_ipv6(base.KLINE_URL, params)
        transport["primary_transport_error"] = primary_error
    frame = _parse_eastmoney_overlap(
        payload, secid=secid, adjustment=adjustment
    )
    frame.attrs["transport_receipt"] = transport
    return frame


def load_hourly(path: Path) -> pd.DataFrame:
    """Load a frozen Sina source/adjusted CSV with explicit types."""

    frame = pd.read_csv(
        path,
        dtype={"secid": str, "sina_symbol": str, "adjustment": str},
    )
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
        raise AShareSinaScanError(f"snapshot schema drift:{path}")
    frame["raw_close_time"] = pd.to_datetime(
        frame["raw_close_time"], utc=True
    ).dt.tz_convert("Asia/Shanghai")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]
    numeric_columns += [
        column
        for column in ("qfq_factor", "raw_open", "raw_high", "raw_low", "raw_close")
        if column in frame.columns
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


_CACHED_15M_GROUPS: dict[str, tuple[str, ...]] = {
    "10:30": ("09:45", "10:00", "10:15", "10:30"),
    "11:30": ("10:45", "11:00", "11:15", "11:30"),
    "14:00": ("13:15", "13:30", "13:45", "14:00"),
    "15:00": ("14:15", "14:30", "14:45", "15:00"),
}


def _cached_input(
    authorization: Mapping[str, Any], key: str
) -> tuple[Path, Mapping[str, Any]]:
    item = authorization["cached_inputs"][key]
    path = (ROOT / str(item["path"])).resolve()
    if ROOT not in path.parents or not path.is_file():
        raise AShareSinaScanError(f"cached parity input missing:{key}")
    if sha256_file(path) != str(item["sha256"]):
        raise AShareSinaScanError(f"cached parity input SHA drift:{key}")
    return path, item


def _aggregate_cached_eastmoney_15m(
    path: Path,
    *,
    secid: str,
    expected_rows: int,
) -> pd.DataFrame:
    """Aggregate exact four-row A-share 15m groups into parity-only 60m rows."""

    source = pd.read_csv(path, dtype={"secid": str, "adjustment": str})
    required = {
        "raw_close_time",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adjustment",
    }
    if len(source) != expected_rows or not required.issubset(source.columns):
        raise AShareSinaScanError(f"cached 15m schema/row drift:{secid}")
    source["raw_close_time"] = pd.to_datetime(
        source["raw_close_time"], utc=True
    ).dt.tz_convert("Asia/Shanghai")
    source["open_time"] = pd.to_datetime(source["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume", "amount"):
        source[column] = pd.to_numeric(source[column], errors="raise")
    if source["raw_close_time"].duplicated().any():
        raise AShareSinaScanError(f"duplicate cached 15m close-label:{secid}")
    if set(source["adjustment"].astype(str)) != {"qfq"}:
        raise AShareSinaScanError(f"cached sentinel is not QFQ:{secid}")
    source.sort_values("raw_close_time", inplace=True, ignore_index=True)
    source_to_target = {
        source_slot: target_slot
        for target_slot, source_slots in _CACHED_15M_GROUPS.items()
        for source_slot in source_slots
    }
    source["source_slot"] = source["raw_close_time"].dt.strftime("%H:%M")
    unexpected = sorted(set(source["source_slot"]) - set(source_to_target))
    if unexpected:
        raise AShareSinaScanError(
            f"unexpected cached 15m close labels {unexpected}:{secid}"
        )
    source["target_slot"] = source["source_slot"].map(source_to_target)
    source["session_date"] = source["raw_close_time"].dt.date
    rows: list[dict[str, Any]] = []
    for (session_date, target_slot), group in source.groupby(
        ["session_date", "target_slot"], sort=True
    ):
        group = group.sort_values("raw_close_time")
        actual = tuple(group["source_slot"])
        if actual != _CACHED_15M_GROUPS[str(target_slot)]:
            continue
        close_time = pd.Timestamp(group.iloc[-1]["raw_close_time"])
        first_open = pd.Timestamp(group.iloc[0]["open_time"])
        if (
            close_time.strftime("%H:%M") != str(target_slot)
            or first_open != (close_time - pd.Timedelta(hours=1)).tz_convert("UTC")
        ):
            raise AShareSinaScanError(f"cached 15m time semantics drift:{secid}")
        rows.append(
            {
                "raw_close_time": close_time,
                "open_time": first_open,
                "open": float(group.iloc[0]["open"]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group.iloc[-1]["close"]),
                "volume": float(group["volume"].sum()),
                "amount": float(group["amount"].sum()),
                "secid": secid,
                "adjustment": "qfq",
            }
        )
    result = pd.DataFrame(rows)
    if len(result) < QFQ_PARITY_MIN_SHARED:
        raise AShareSinaScanError(
            f"cached 15m aggregation too short:{secid}:{len(result)}"
        )
    result.sort_values("raw_close_time", inplace=True, ignore_index=True)
    numeric = result[
        ["open", "high", "low", "close", "volume", "amount"]
    ].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise AShareSinaScanError(f"non-finite cached 60m OHLCVA:{secid}")
    body_high = result[["open", "close"]].max(axis=1)
    body_low = result[["open", "close"]].min(axis=1)
    if bool((result["high"] < body_high).any()) or bool(
        (result["low"] > body_low).any()
    ):
        raise AShareSinaScanError(f"invalid cached 60m candle bounds:{secid}")
    return result


def _authorized_cached_eastmoney_overlap(
    secid: str, *, fqt: str, adjustment: str
) -> pd.DataFrame:
    """Load the owner-authorized, hash-pinned Eastmoney parity fallback."""

    authorization = read_json(CACHED_PARITY_AUTHORIZATION)
    if sha256_file(CACHED_PARITY_AUTHORIZATION) != (
        EXPECTED_CACHED_PARITY_AUTHORIZATION_SHA256
    ):
        raise AShareSinaScanError("cached parity authorization SHA drifted")
    parent_path, parent = _cached_input(
        authorization, "qfq_parent_fetch_receipt"
    )
    mapping = {
        "1.600000": "qfq_sh600000_15m",
        "0.000001": "qfq_sz000001_15m",
    }
    if secid == REFERENCE_SECID and fqt == "0" and adjustment == "none":
        path, item = _cached_input(authorization, "unadjusted_reference_60m")
        frame = load_hourly(path)
        if len(frame) != int(item["rows"]):
            raise AShareSinaScanError("cached reference 60m row count drifted")
        frame["secid"] = secid
        frame["adjustment"] = "none"
        derivation = "direct_frozen_60m_bytes"
    elif secid in mapping and fqt == "1" and adjustment == "qfq":
        key = mapping[secid]
        path, item = _cached_input(authorization, key)
        frame = _aggregate_cached_eastmoney_15m(
            path, secid=secid, expected_rows=int(item["rows"])
        )
        derivation = "exact_four_by_15m_to_60m"
    else:
        raise AShareSinaScanError(
            f"cached parity request outside owner authorization:{secid}:{fqt}"
        )
    frame.attrs["transport_receipt"] = {
        "mode": "owner_authorized_frozen_eastmoney_cache",
        "source_path": str(path.relative_to(ROOT)),
        "source_sha256": str(item["sha256"]),
        "source_rows": int(item["rows"]),
        "derived_rows_60m": len(frame),
        "derivation": derivation,
        "parent_fetch_receipt_path": str(parent_path.relative_to(ROOT)),
        "parent_fetch_receipt_sha256": str(parent["sha256"]),
        "authorization_path": str(CACHED_PARITY_AUTHORIZATION.relative_to(ROOT)),
        "authorization_sha256": EXPECTED_CACHED_PARITY_AUTHORIZATION_SHA256,
    }
    return frame


def fetch_eastmoney_overlap_with_authorized_cache(
    secid: str, *, fqt: str, adjustment: str
) -> pd.DataFrame:
    """Use online Eastmoney first, then the explicitly authorized frozen cache."""

    try:
        return fetch_eastmoney_overlap(secid, fqt=fqt, adjustment=adjustment)
    except Exception as exc:  # noqa: BLE001 - the fallback reason is receipted
        frame = _authorized_cached_eastmoney_overlap(
            secid, fqt=fqt, adjustment=adjustment
        )
        transport = dict(frame.attrs["transport_receipt"])
        transport["online_transport_error"] = f"{type(exc).__name__}:{exc}"
        frame.attrs["transport_receipt"] = transport
        return frame


def aggregate_4h(frame: pd.DataFrame) -> pd.DataFrame:
    """Build exact complete four-trading-hour sessions through the new cutoff."""

    return aggregate_complete_session_4h(frame, cutoff_close=CUTOFF_CST)


def validate_1h(frame: pd.DataFrame, reference: pd.DataFrame, *, secid: str) -> None:
    """Require a completed 15:00 endpoint and exact trailing reference schedule."""

    if len(frame) < SCHEDULE_MATCH_1H:
        raise AShareSinaScanError(f"1h_insufficient_history:{len(frame)}")
    latest = pd.Timestamp(frame.iloc[-1]["raw_close_time"])
    if latest != CUTOFF_CST:
        raise AShareSinaScanError(f"1h_stale_latest:{latest.isoformat()}")
    actual = pd.DatetimeIndex(frame["raw_close_time"].iloc[-SCHEDULE_MATCH_1H:])
    expected = pd.DatetimeIndex(reference["raw_close_time"].iloc[-SCHEDULE_MATCH_1H:])
    if not actual.equals(expected):
        raise AShareSinaScanError("1h_schedule_mismatch")
    opens = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True))
    expected_opens = (
        pd.DatetimeIndex(pd.to_datetime(frame["raw_close_time"], utc=True))
        - pd.Timedelta(hours=1)
    )
    if not opens.equals(expected_opens):
        raise AShareSinaScanError(f"1h_open_conversion_drift:{secid}")


def validate_4h(frame: pd.DataFrame, reference: pd.DataFrame, *, secid: str) -> None:
    """Require 160 exact complete A-share sessions through today's close."""

    if len(frame) < SCHEDULE_MATCH_4H:
        raise AShareSinaScanError(f"4h_insufficient_history:{len(frame)}")
    latest = pd.Timestamp(frame.iloc[-1]["raw_close_time"])
    if latest != CUTOFF_CST:
        raise AShareSinaScanError(f"4h_stale_latest:{latest.isoformat()}")
    actual = pd.DatetimeIndex(frame["raw_close_time"].iloc[-SCHEDULE_MATCH_4H:])
    expected = pd.DatetimeIndex(reference["raw_close_time"].iloc[-SCHEDULE_MATCH_4H:])
    if not actual.equals(expected):
        raise AShareSinaScanError("4h_schedule_mismatch")
    local_opens = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True)).tz_convert(
        "Asia/Shanghai"
    )
    if set(local_opens.strftime("%H:%M")) != {"09:30"}:
        raise AShareSinaScanError(f"4h_open_conversion_drift:{secid}")
    if set(frame["raw_close_time"].dt.strftime("%H:%M")) != {"15:00"}:
        raise AShareSinaScanError(f"4h_close_conversion_drift:{secid}")


def _parity_stats(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    columns: Sequence[str],
    minimum_shared: int,
    median_max: float,
    p99_max: float,
    label: str,
) -> dict[str, Any]:
    """Compare exact shared close labels under preregistered relative tolerances."""

    merged = pd.merge(
        left[["raw_close_time", *columns]],
        right[["raw_close_time", *columns]],
        on="raw_close_time",
        suffixes=("_left", "_right"),
        how="inner",
    )
    if len(merged) < minimum_shared:
        raise AShareSinaScanError(
            f"{label} shared rows below preregistration:{len(merged)}<{minimum_shared}"
        )
    values: list[float] = []
    per_column: dict[str, dict[str, float]] = {}
    for column in columns:
        a = merged[f"{column}_left"].to_numpy(dtype=float)
        b = merged[f"{column}_right"].to_numpy(dtype=float)
        relative = np.abs(a - b) / np.maximum(np.abs(b), 1e-12)
        values.extend(relative.tolist())
        per_column[column] = {
            "median_relative_difference": float(np.median(relative)),
            "p99_relative_difference": float(np.quantile(relative, 0.99)),
            "max_relative_difference": float(np.max(relative)),
        }
    all_values = np.asarray(values, dtype=float)
    median = float(np.median(all_values))
    p99 = float(np.quantile(all_values, 0.99))
    if median > median_max or p99 > p99_max:
        raise AShareSinaScanError(
            f"{label} parity failed:median={median:.8f}>{median_max} or p99={p99:.8f}>{p99_max}"
        )
    return {
        "label": label,
        "shared_rows": len(merged),
        "first_shared_close_cst": pd.Timestamp(
            merged.iloc[0]["raw_close_time"]
        ).isoformat(),
        "last_shared_close_cst": pd.Timestamp(
            merged.iloc[-1]["raw_close_time"]
        ).isoformat(),
        "columns": list(columns),
        "median_relative_difference": median,
        "p99_relative_difference": p99,
        "median_limit": median_max,
        "p99_limit": p99_max,
        "per_column": per_column,
        "passed": True,
    }


def _sina_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    identity = shared._identity(row)
    identity["sina_symbol"] = identity["exchange"].lower() + identity["code"]
    return identity


def _error_reason(error: str) -> str:
    for prefix in (
        "1h_insufficient_history",
        "1h_stale_latest",
        "1h_schedule_mismatch",
        "4h_insufficient_history",
        "4h_stale_latest",
        "4h_schedule_mismatch",
        "Sina minute primary/fallback failed",
        "same-source request failed",
        "empty Sina QFQ factors",
        "Sina QFQ factor does not cover",
        "unexpected Sina 60m close labels",
    ):
        if prefix in error:
            return prefix
    return "other"


def _preflight_paths(building: Path) -> dict[str, Path]:
    preflight = building / "preflight"
    preflight.mkdir(exist_ok=True)
    return {
        "dir": preflight,
        "reference_sina": preflight / "reference_sina_60m.csv",
        "reference_eastmoney": preflight / "reference_eastmoney_60m.csv",
        "reference_eastmoney_meta": preflight / "reference_eastmoney_meta.json",
        "receipt": preflight / "source_preflight.json",
    }


def run_source_preflight(building: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Prove history, session semantics, QFQ and cross-source overlap before fanout."""

    paths = _preflight_paths(building)
    if paths["receipt"].is_file():
        receipt = read_json(paths["receipt"])
        if receipt.get("passed") is not True:
            raise AShareSinaScanError("saved source preflight is not passing")
        for item in receipt["files"]:
            path = building / str(item["path"])
            if sha256_file(path) != str(item["sha256"]):
                raise AShareSinaScanError(f"preflight file SHA drift:{path}")
        reference = load_hourly(paths["reference_sina"])
        validate_1h(reference, reference, secid=REFERENCE_SECID)
        validate_4h(aggregate_4h(reference), aggregate_4h(reference), secid=REFERENCE_SECID)
        print("resuming passing Sina source preflight", flush=True)
        return reference, receipt

    if paths["reference_sina"].is_file():
        reference = load_hourly(paths["reference_sina"])
        reference_meta = read_json(paths["dir"] / "reference_sina_meta.json")
    else:
        reference, reference_meta = fetch_sina_raw(REFERENCE_SINA_SYMBOL)
        reference["secid"] = REFERENCE_SECID
        reference.to_csv(paths["reference_sina"], index=False)
        write_json(paths["dir"] / "reference_sina_meta.json", reference_meta)
    if len(reference) < MIN_REFERENCE_60M_ROWS:
        raise AShareSinaScanError(
            f"Sina reference history insufficient:{len(reference)}<{MIN_REFERENCE_60M_ROWS}"
        )
    validate_1h(reference, reference, secid=REFERENCE_SECID)
    reference_4h = aggregate_4h(reference)
    validate_4h(reference_4h, reference_4h, secid=REFERENCE_SECID)

    if paths["reference_eastmoney"].is_file() or paths[
        "reference_eastmoney_meta"
    ].is_file():
        if not (
            paths["reference_eastmoney"].is_file()
            and paths["reference_eastmoney_meta"].is_file()
        ):
            raise AShareSinaScanError("partial Eastmoney reference parity artifact")
        east_reference = load_hourly(paths["reference_eastmoney"])
        east_reference_transport = read_json(paths["reference_eastmoney_meta"])
    else:
        east_reference = fetch_eastmoney_overlap_with_authorized_cache(
            REFERENCE_SECID, fqt="0", adjustment="none"
        )
        east_reference_transport = dict(
            east_reference.attrs.get("transport_receipt") or {}
        )
        east_reference.to_csv(paths["reference_eastmoney"], index=False)
        write_json(paths["reference_eastmoney_meta"], east_reference_transport)
    reference_parity = _parity_stats(
        reference,
        east_reference,
        columns=("close",),
        minimum_shared=REFERENCE_PARITY_MIN_SHARED,
        median_max=REFERENCE_PARITY_MEDIAN_MAX,
        p99_max=REFERENCE_PARITY_P99_MAX,
        label="Sina_vs_Eastmoney_unadjusted_reference",
    )
    reference_parity["transport_receipt"] = east_reference_transport

    sentinel_receipts: list[dict[str, Any]] = []
    sentinel_identities = {
        identity["sina_symbol"]: identity
        for identity in (
            _sina_identity(row)
            for row in shared.load_standard_retail_universe().to_dict("records")
        )
        if identity["sina_symbol"] in SENTINELS
    }
    if set(sentinel_identities) != set(SENTINELS):
        raise AShareSinaScanError("fixed QFQ sentinel missing from frozen universe")
    for symbol in SENTINELS:
        identity = sentinel_identities[symbol]
        sina_path = paths["dir"] / f"{symbol}_sina_qfq.csv"
        meta_path = paths["dir"] / f"{symbol}_sina_qfq_meta.json"
        east_path = paths["dir"] / f"{symbol}_eastmoney_qfq.csv"
        east_meta_path = paths["dir"] / f"{symbol}_eastmoney_qfq_meta.json"
        if sina_path.is_file() and meta_path.is_file():
            sina = load_hourly(sina_path)
            meta = read_json(meta_path)
        else:
            sina, meta = fetch_sina_qfq(symbol, secid=identity["secid"])
            sina.to_csv(sina_path, index=False)
            write_json(meta_path, meta)
        validate_1h(sina, reference, secid=identity["secid"])
        validate_4h(aggregate_4h(sina), reference_4h, secid=identity["secid"])
        if east_path.is_file() or east_meta_path.is_file():
            if not (east_path.is_file() and east_meta_path.is_file()):
                raise AShareSinaScanError(
                    f"partial Eastmoney sentinel parity artifact:{symbol}"
                )
            east = load_hourly(east_path)
            east_transport = read_json(east_meta_path)
        else:
            east = fetch_eastmoney_overlap_with_authorized_cache(
                identity["secid"], fqt="1", adjustment="qfq"
            )
            east_transport = dict(east.attrs.get("transport_receipt") or {})
            east.to_csv(east_path, index=False)
            write_json(east_meta_path, east_transport)
        parity = _parity_stats(
            sina,
            east,
            columns=("open", "high", "low", "close"),
            minimum_shared=QFQ_PARITY_MIN_SHARED,
            median_max=QFQ_PARITY_MEDIAN_MAX,
            p99_max=QFQ_PARITY_P99_MAX,
            label=f"Sina_factor_QFQ_vs_Eastmoney_QFQ_{symbol}",
        )
        parity["transport_receipt"] = east_transport
        sentinel_receipts.append(
            {
                "sina_symbol": symbol,
                "secid": identity["secid"],
                "source_meta": meta,
                "sina_path": str(sina_path.relative_to(building)),
                "sina_sha256": sha256_file(sina_path),
                "eastmoney_path": str(east_path.relative_to(building)),
                "eastmoney_sha256": sha256_file(east_path),
                "parity": parity,
            }
        )
    files = []
    for path in sorted(paths["dir"].glob("*")):
        if path.is_file() and path != paths["receipt"]:
            files.append(
                {
                    "path": str(path.relative_to(building)),
                    "sha256": sha256_file(path),
                }
            )
    receipt = {
        "protocol": "sina_60m_reference_and_qfq_overlap_preflight_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_numbers_for_checkpoint": {"1h": 11, "4h": 12},
        "transport_recovery_path": str(TRANSPORT_RECOVERY.relative_to(ROOT)),
        "transport_recovery_sha256": EXPECTED_TRANSPORT_RECOVERY_SHA256,
        "cached_parity_authorization_path": str(
            CACHED_PARITY_AUTHORIZATION.relative_to(ROOT)
        ),
        "cached_parity_authorization_sha256": (
            EXPECTED_CACHED_PARITY_AUTHORIZATION_SHA256
        ),
        "reference_symbol": REFERENCE_SINA_SYMBOL,
        "reference_rows_1h": len(reference),
        "reference_rows_4h": len(reference_4h),
        "reference_first_close_cst": pd.Timestamp(
            reference.iloc[0]["raw_close_time"]
        ).isoformat(),
        "reference_last_close_cst": pd.Timestamp(
            reference.iloc[-1]["raw_close_time"]
        ).isoformat(),
        "reference_source_meta": reference_meta,
        "reference_parity": reference_parity,
        "qfq_sentinels": sentinel_receipts,
        "files": files,
        "individual_universe_fanout_started": False,
        "passed": True,
    }
    write_json(paths["receipt"], receipt)
    print(
        f"source preflight PASS reference_1h={len(reference)} reference_4h={len(reference_4h)}",
        flush=True,
    )
    return reference, receipt


def fetch_snapshot(out: Path, *, workers: int) -> dict[str, Any]:
    """Freeze QFQ hourly histories after the mandatory source preflight."""

    source_commit = require_builder_committed()
    verify_frozen_contract()
    if out.exists():
        raise FileExistsError(f"refusing to overwrite frozen snapshot:{out}")
    building = out.with_name(out.name + ".building")
    hourly_dir = building / "hourly"
    meta_dir = building / "source_meta"
    building.mkdir(parents=True, exist_ok=True)
    hourly_dir.mkdir(exist_ok=True)
    meta_dir.mkdir(exist_ok=True)
    universe_path = building / "universe.csv"
    plan_path = building / "fetch_plan.json"
    consumption_path = building / "holdout_consumption_started.json"
    if plan_path.is_file() != universe_path.is_file():
        raise AShareSinaScanError("partial resumed fetch plan/universe artifact")
    if plan_path.is_file() and universe_path.is_file():
        plan = read_json(plan_path)
        universe = pd.read_csv(universe_path, dtype={"code": str, "secid": str})
        if sha256_file(universe_path) != str(plan["universe_sha256"]):
            raise AShareSinaScanError("resumed universe bytes drifted")
    else:
        universe = shared.load_standard_retail_universe()
        universe.to_csv(universe_path, index=False)
        plan = {
            "experiment_id": EXPERIMENT_ID,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "universe_rows": len(universe),
            "universe_sha256": sha256_file(universe_path),
            "cutoff_close_cst": CUTOFF_CST.isoformat(),
            "holdout_consumption_numbers_for_checkpoint": {"1h": 11, "4h": 12},
        }
        write_json(plan_path, plan)
    if (
        plan.get("experiment_id") != EXPERIMENT_ID
        or plan.get("cutoff_close_cst") != CUTOFF_CST.isoformat()
        or plan.get("holdout_consumption_numbers_for_checkpoint")
        != {"1h": 11, "4h": 12}
    ):
        raise AShareSinaScanError("resumed fetch plan identity drifted")
    initial_source_commit = str(plan["source_commit"])
    if source_commit != initial_source_commit:
        recovery = read_json(TRANSPORT_RECOVERY)
        resume_commits = [str(value) for value in plan.get("resume_source_commits", [])]
        if source_commit not in resume_commits:
            preflight_receipt = building / "preflight/source_preflight.json"
            if any(hourly_dir.glob("*.csv")) or preflight_receipt.is_file():
                raise AShareSinaScanError(
                    "new source commit cannot enter after individual fanout authorization"
                )
            reference_path = building / "preflight/reference_sina_60m.csv"
            expected_reference_hash = recovery["recovery_contract"][
                "reuse_frozen_sina_reference_sha256"
            ]
            if (
                initial_source_commit != recovery["initial_source_commit"]
                or not reference_path.is_file()
                or sha256_file(reference_path) != expected_reference_hash
            ):
                raise AShareSinaScanError("transport-only resume evidence drifted")
            resume_commits.append(source_commit)
            plan["resume_source_commits"] = resume_commits
            plan["transport_recovery_path"] = str(
                TRANSPORT_RECOVERY.relative_to(ROOT)
            )
            plan["transport_recovery_sha256"] = EXPECTED_TRANSPORT_RECOVERY_SHA256
            plan["cached_parity_authorization_path"] = str(
                CACHED_PARITY_AUTHORIZATION.relative_to(ROOT)
            )
            plan["cached_parity_authorization_sha256"] = (
                EXPECTED_CACHED_PARITY_AUTHORIZATION_SHA256
            )
            write_json(plan_path, plan)
    if len(universe) != shared.EXPECTED_UNIVERSE_ROWS:
        raise AShareSinaScanError("standard-retail universe count drifted")
    if not consumption_path.is_file():
        write_json(
            consumption_path,
            {
                "experiment_id": EXPERIMENT_ID,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "source_commit": source_commit,
                "holdout_consumption_numbers_for_checkpoint": {"1h": 11, "4h": 12},
                "prior_failed_consumptions": [9, 10],
                "warning": "A failed source/preflight/model run still consumes #11/#12.",
            },
        )
    else:
        consumption = read_json(consumption_path)
        if consumption.get("holdout_consumption_numbers_for_checkpoint") != {
            "1h": 11,
            "4h": 12,
        }:
            raise AShareSinaScanError("resumed consumption ledger drifted")
        if source_commit != str(consumption.get("source_commit")):
            if source_commit not in plan.get("resume_source_commits", []):
                raise AShareSinaScanError("unrecorded source commit entered resume")
            consumption["resume_source_commits"] = list(
                plan["resume_source_commits"]
            )
            consumption["transport_recovery_path"] = str(
                TRANSPORT_RECOVERY.relative_to(ROOT)
            )
            consumption[
                "transport_recovery_sha256"
            ] = EXPECTED_TRANSPORT_RECOVERY_SHA256
            consumption["cached_parity_authorization_path"] = str(
                CACHED_PARITY_AUTHORIZATION.relative_to(ROOT)
            )
            consumption["cached_parity_authorization_sha256"] = (
                EXPECTED_CACHED_PARITY_AUTHORIZATION_SHA256
            )
            write_json(consumption_path, consumption)

    try:
        reference_1h, preflight = run_source_preflight(building)
    except Exception as exc:
        write_json(
            building / "source_preflight_failure.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "source_commit": source_commit,
                "holdout_consumption_numbers_for_checkpoint": {"1h": 11, "4h": 12},
                "error": f"{type(exc).__name__}:{exc}",
                "individual_universe_fanout_started": False,
                "model_loaded": False,
                "signals_computed": False,
            },
        )
        raise
    reference_4h = aggregate_4h(reference_1h)
    write_json(
        building / "preflight" / "source_preflight.json",
        {**preflight, "individual_universe_fanout_started": True},
    )

    sentinel_paths = {
        symbol: building / "preflight" / f"{symbol}_sina_qfq.csv"
        for symbol in SENTINELS
    }
    sentinel_meta_paths = {
        symbol: building / "preflight" / f"{symbol}_sina_qfq_meta.json"
        for symbol in SENTINELS
    }
    snapshots: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reused = 0

    def one(
        row: Mapping[str, Any],
    ) -> tuple[dict[str, Any], pd.DataFrame | None, dict[str, Any], dict[str, Any], bool]:
        identity = _sina_identity(row)
        path = hourly_dir / f"{identity['market']}_{identity['code']}.csv"
        meta_path = meta_dir / f"{identity['market']}_{identity['code']}.json"
        try:
            if path.is_file() or meta_path.is_file():
                if not (path.is_file() and meta_path.is_file()):
                    raise AShareSinaScanError("partial resumed symbol artifact")
                hourly = load_hourly(path)
                source_meta = read_json(meta_path)
                was_reused = True
            elif identity["sina_symbol"] in sentinel_paths:
                hourly = load_hourly(sentinel_paths[identity["sina_symbol"]])
                source_meta = read_json(sentinel_meta_paths[identity["sina_symbol"]])
                was_reused = True
            else:
                hourly, source_meta = fetch_sina_qfq(
                    identity["sina_symbol"], secid=identity["secid"]
                )
                was_reused = False
        except Exception as exc:  # noqa: BLE001 - exclusion evidence
            error = f"{type(exc).__name__}:{exc}"
            return identity, None, {}, {
                "eligible_1h": False,
                "eligible_4h": False,
                "reason_1h": _error_reason(error),
                "reason_4h": _error_reason(error),
                "error_1h": error,
                "error_4h": error,
            }, False
        status: dict[str, Any] = {}
        try:
            validate_1h(hourly, reference_1h, secid=identity["secid"])
            status.update(eligible_1h=True, reason_1h="", error_1h="")
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}:{exc}"
            status.update(
                eligible_1h=False,
                reason_1h=_error_reason(error),
                error_1h=error,
            )
        four_hour = aggregate_4h(hourly)
        try:
            validate_4h(four_hour, reference_4h, secid=identity["secid"])
            status.update(eligible_4h=True, reason_4h="", error_4h="")
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}:{exc}"
            status.update(
                eligible_4h=False,
                reason_4h=_error_reason(error),
                error_4h=error,
            )
        status["rows_4h"] = len(four_hour)
        return identity, hourly, source_meta, status, was_reused

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(one, row): row for row in universe.to_dict("records")
        }
        for number, future in enumerate(as_completed(futures), 1):
            identity, hourly, source_meta, status, was_reused = future.result()
            if hourly is None or not (
                bool(status["eligible_1h"]) or bool(status["eligible_4h"])
            ):
                failures.append({**identity, **status})
            else:
                path = hourly_dir / f"{identity['market']}_{identity['code']}.csv"
                meta_path = meta_dir / f"{identity['market']}_{identity['code']}.json"
                if not path.is_file():
                    hourly.to_csv(path, index=False)
                if not meta_path.is_file():
                    write_json(meta_path, source_meta)
                reused += int(was_reused)
                snapshots.append(
                    {
                        **identity,
                        **status,
                        "path": str(path.relative_to(building)),
                        "sha256": sha256_file(path),
                        "source_meta_path": str(meta_path.relative_to(building)),
                        "source_meta_sha256": sha256_file(meta_path),
                        "rows_1h": len(hourly),
                        "first_close_cst": pd.Timestamp(
                            hourly.iloc[0]["raw_close_time"]
                        ).isoformat(),
                        "last_close_cst": pd.Timestamp(
                            hourly.iloc[-1]["raw_close_time"]
                        ).isoformat(),
                    }
                )
            if number % 50 == 0 or number == len(universe):
                usable_1h = sum(bool(row["eligible_1h"]) for row in snapshots)
                usable_4h = sum(bool(row["eligible_4h"]) for row in snapshots)
                print(
                    f"fetch {number}/{len(universe)} usable_1h={usable_1h} usable_4h={usable_4h} neither={len(failures)} reused={reused}",
                    flush=True,
                )

    snapshots.sort(key=lambda row: (int(row["market"]), str(row["code"])))
    failures.sort(key=lambda row: (int(row["market"]), str(row["code"])))
    usable = {
        "1h": sum(bool(row["eligible_1h"]) for row in snapshots),
        "4h": sum(bool(row["eligible_4h"]) for row in snapshots),
    }
    coverage = {key: value / len(universe) for key, value in usable.items()}
    all_rows = [*snapshots, *failures]
    reasons = {
        timeframe: dict(
            sorted(
                Counter(
                    str(row[f"reason_{timeframe}"])
                    for row in all_rows
                    if not bool(row[f"eligible_{timeframe}"])
                ).items()
            )
        )
        for timeframe in ("1h", "4h")
    }
    network_failures = sum(
        (
            "same-source request failed" in str(row.get("error_1h", ""))
            or "minute primary/fallback failed" in str(row.get("error_1h", ""))
        )
        for row in all_rows
    )
    if (
        min(coverage.values()) < MIN_COVERAGE
        or network_failures > max(10, int(len(universe) * MAX_NETWORK_FAILURE_RATE))
    ):
        write_json(
            building / "incomplete_fetch_receipt.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "universe_rows": len(universe),
                "usable_symbols": usable,
                "coverage": coverage,
                "failure_reasons": reasons,
                "network_failures": network_failures,
                "holdout_consumption_numbers_for_checkpoint": {"1h": 11, "4h": 12},
                "resume_allowed": True,
            },
        )
        raise AShareSinaScanError(
            f"snapshot coverage failed closed:coverage={coverage} network_failures={network_failures}"
        )
    receipt = {
        "protocol": "ashare_standard_retail_sina_qfq_60m_for_1h_session4h_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "initial_source_commit": initial_source_commit,
        "resume_source_commits": list(plan.get("resume_source_commits", [])),
        "transport_recovery_path": str(TRANSPORT_RECOVERY.relative_to(ROOT)),
        "transport_recovery_sha256": EXPECTED_TRANSPORT_RECOVERY_SHA256,
        "cached_parity_authorization_path": str(
            CACHED_PARITY_AUTHORIZATION.relative_to(ROOT)
        ),
        "cached_parity_authorization_sha256": (
            EXPECTED_CACHED_PARITY_AUTHORIZATION_SHA256
        ),
        "owner_authorized_holdout_read": True,
        "holdout_consumption_numbers_for_checkpoint": {"1h": 11, "4h": 12},
        "prior_failed_consumptions": [9, 10],
        "source_preflight_path": "preflight/source_preflight.json",
        "source_preflight_sha256": sha256_file(
            building / "preflight/source_preflight.json"
        ),
        "universe_rows": len(universe),
        "universe_csv": "universe.csv",
        "universe_sha256": sha256_file(universe_path),
        "usable_symbols": usable,
        "coverage": coverage,
        "failure_reasons": reasons,
        "failures": failures,
        "cutoff_close_cst": CUTOFF_CST.isoformat(),
        "adjustment": "sina_qfq_factor_date_causal",
        "requested_rows_per_symbol": SINA_DATALEN,
        "schedule_match_bars": {"1h": SCHEDULE_MATCH_1H, "4h": SCHEDULE_MATCH_4H},
        "reference": {
            "sina_symbol": REFERENCE_SINA_SYMBOL,
            "path": "preflight/reference_sina_60m.csv",
            "sha256": sha256_file(building / "preflight/reference_sina_60m.csv"),
            "rows_1h": len(reference_1h),
            "rows_4h": len(reference_4h),
        },
        "snapshots": snapshots,
        "network_reads": "Sina 60m plus Sina qfq.js per frozen identity after passing reference/sentinel preflight; Eastmoney overlap is preflight-only; bounded retries",
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
    """Load and authenticate the frozen source snapshot without network."""

    receipt = read_json(out / "fetch_receipt.json")
    if receipt.get("experiment_id") != EXPERIMENT_ID or receipt.get(
        "holdout_consumption_numbers_for_checkpoint"
    ) != {"1h": 11, "4h": 12}:
        raise AShareSinaScanError("fetch receipt identity drifted")
    universe_path = out / str(receipt["universe_csv"])
    if sha256_file(universe_path) != str(receipt["universe_sha256"]):
        raise AShareSinaScanError("frozen universe SHA drifted")
    preflight_path = out / str(receipt["source_preflight_path"])
    if sha256_file(preflight_path) != str(receipt["source_preflight_sha256"]):
        raise AShareSinaScanError("source preflight SHA drifted")
    reference_path = out / str(receipt["reference"]["path"])
    if sha256_file(reference_path) != str(receipt["reference"]["sha256"]):
        raise AShareSinaScanError("reference SHA drifted")
    reference_1h = load_hourly(reference_path)
    reference_4h = aggregate_4h(reference_1h)
    validate_1h(reference_1h, reference_1h, secid=REFERENCE_SECID)
    validate_4h(reference_4h, reference_4h, secid=REFERENCE_SECID)
    snapshots = list(receipt["snapshots"])
    for timeframe in ("1h", "4h"):
        if sum(bool(row[f"eligible_{timeframe}"]) for row in snapshots) != int(
            receipt["usable_symbols"][timeframe]
        ):
            raise AShareSinaScanError(f"snapshot {timeframe} count drifted")
    return snapshots, reference_1h, reference_4h, receipt


def _frame_for_timeframe(hourly: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "1h":
        return hourly
    if timeframe == "4h":
        return aggregate_4h(hourly)
    raise AShareSinaScanError(f"unsupported timeframe:{timeframe}")


def _endpoints(frame: pd.DataFrame, timeframe: str) -> list[int]:
    if timeframe == "1h":
        return [len(frame) - 1]
    if timeframe == "4h":
        return list(range(len(frame) - RECENT_4H_ENDPOINTS, len(frame)))
    raise AShareSinaScanError(f"unsupported timeframe:{timeframe}")


def scan_snapshot(
    out: Path, results: Path, *, device_arg: str | None, batch_size: int
) -> dict[str, Any]:
    """Run frozen YOLO/semantic inference and chart LONG audit survivors only."""

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
        raise AShareSinaScanError(f"class map drifted:{names}")
    total_windows = int(fetch_receipt["usable_symbols"]["1h"]) * len(
        WINDOW_LENGTHS
    ) + int(fetch_receipt["usable_symbols"]["4h"]) * RECENT_4H_ENDPOINTS * len(
        WINDOW_LENGTHS
    )
    all_boxes: list[dict[str, Any]] = []
    batch: list[tuple[np.ndarray, ChartTransform, dict[str, Any], pd.DataFrame]] = []
    windows_scored = Counter()

    def flush() -> None:
        nonlocal batch
        if not batch:
            return
        all_boxes.extend(base._run_batch(model, batch, device=device, gates=gates))
        windows_scored.update(row[2]["timeframe"] for row in batch)
        batch = []
        done = sum(windows_scored.values())
        if done % (max(1, batch_size) * 10) == 0 or done == total_windows:
            print(f"inference {done}/{total_windows} raw_boxes={len(all_boxes)}", flush=True)

    for symbol_number, identity in enumerate(snapshots, 1):
        path = out / str(identity["path"])
        if sha256_file(path) != str(identity["sha256"]):
            raise AShareSinaScanError(f"snapshot bytes drift:{identity['secid']}")
        hourly = load_hourly(path)
        for timeframe, eligible, reference in (
            ("1h", bool(identity["eligible_1h"]), reference_1h),
            ("4h", bool(identity["eligible_4h"]), reference_4h),
        ):
            if not eligible:
                continue
            frame = _frame_for_timeframe(hourly, timeframe)
            if timeframe == "1h":
                validate_1h(frame, reference, secid=str(identity["secid"]))
            else:
                validate_4h(frame, reference, secid=str(identity["secid"]))
            enriched = add_candidate_features(frame)
            for endpoint in _endpoints(enriched, timeframe):
                for window_len in WINDOW_LENGTHS:
                    start_i = endpoint - window_len + 1
                    window = enriched.iloc[start_i : endpoint + 1]
                    if start_i < 0 or window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                        raise AShareSinaScanError(
                            f"MA warmup failed:{identity['secid']}:{timeframe}"
                        )
                    image, transform = render_chart(window, out_path=None)
                    endpoint_row = enriched.iloc[endpoint]
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
                                "sina_symbol": str(identity["sina_symbol"]),
                                "source_provider": "Sina",
                                "snapshot_path": str(identity["path"]),
                                "snapshot_sha256": str(identity["sha256"]),
                                "timeframe": timeframe,
                                "bar_minutes": 60 if timeframe == "1h" else 240,
                                "endpoint_offset_trading_bars": len(enriched) - 1 - endpoint,
                                "is_latest_endpoint": endpoint == len(enriched) - 1,
                                "window_len": window_len,
                                "window_start_i": start_i,
                                "window_end_i": endpoint,
                                "window_end_time": utc(endpoint_row["open_time"]).isoformat(),
                                "window_available_at": utc(
                                    endpoint_row["raw_close_time"]
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
    audit_events = shared._deduplicate_semantic(semantic)
    events = shared.select_delivery_events(audit_events)
    snapshot_by_secid = {str(row["secid"]): row for row in snapshots}
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for order, event in enumerate(events, 1):
        key = (str(event["secid"]), str(event["timeframe"]))
        if key not in frame_cache:
            identity = snapshot_by_secid[key[0]]
            hourly = load_hourly(out / str(identity["path"]))
            frame_cache[key] = add_candidate_features(
                _frame_for_timeframe(hourly, key[1])
            )
        canvas = shared.render_event(
            event, frame=frame_cache[key], order=order, total=len(events)
        )
        chart_path = chart_dir / (
            f"{order:03d}_{event['timeframe']}_{event['search_key']}_long.png"
        )
        if not cv2.imwrite(str(chart_path), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            raise AShareSinaScanError(f"failed to write chart:{chart_path}")
        event["chart"] = f"charts/{chart_path.name}"
        event["chart_sha256"] = sha256_file(chart_path)

    overview_pages = {
        timeframe: shared.build_overview(events, building, timeframe=timeframe)
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
        remaining = [column for column in event_frame.columns if column not in delivery_columns]
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
    raw_counts = Counter(str(row["timeframe"]) for row in all_boxes)
    structural_counts = Counter(str(row["timeframe"]) for row in structural)
    semantic_counts = Counter(str(row["timeframe"]) for row in semantic)
    failed_checks = Counter(
        check for row in structural for check in row.get("semantic_failed_checks", [])
    )
    summary = {
        "protocol": "ashare_grade_a_sina_qfq_1h_session4h_long_delivery_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "fetch_receipt_sha256": sha256_file(out / "fetch_receipt.json"),
        "source_preflight_sha256": str(fetch_receipt["source_preflight_sha256"]),
        "weights": str(WEIGHTS.relative_to(ROOT)),
        "weights_sha256": EXPECTED_WEIGHT_SHA256,
        "model": prereg["model_contract"]["name"],
        "source_domain": "crypto_15m",
        "inference_domains": ["mainland_A_share_1h", "mainland_A_share_session_4h"],
        "market_data_source": "Sina 60m + date-causal Sina QFQ factors",
        "out_of_distribution": True,
        "research_only": True,
        "holdout_consumed": True,
        "holdout_consumption_numbers_for_checkpoint": {"1h": 11, "4h": 12},
        "prior_failed_consumptions": [9, 10],
        "cutoff_close_cst": CUTOFF_CST.isoformat(),
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
            int(side_counts[timeframe].get("SHORT", 0))
            for timeframe in ("1h", "4h")
        ),
        "direction_flip_semantic_survivors": sum(
            bool(row.get("flipped_semantic_pass")) for row in structural
        ),
        "semantic_failure_checks": dict(sorted(failed_checks.items())),
        "overview_pages": overview_pages,
        "detector_contract": {
            "imgsz": base.IMAGE_SIZE,
            "confidence": base.CONFIDENCE,
            "nms_iou": base.NMS_IOU,
            "window_lengths": list(WINDOW_LENGTHS),
            "allowed_core_lengths": sorted(base.ALLOWED_CORES),
            "allowed_confirmation_bars": sorted(base.ALLOWED_CONFIRMATIONS),
            "same_symbol_event_gap_bars": base.EVENT_GAP_BARS,
            "latest_endpoints_per_symbol": {"1h": 1, "4h": RECENT_4H_ENDPOINTS},
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
    zip_path = shared._write_chart_zip(building, events)
    summary["chart_zip"] = zip_path.name
    summary["chart_zip_sha256"] = sha256_file(zip_path)
    write_json(building / "summary.json", summary)
    os.replace(building, results)
    print(
        f"scan complete windows={sum(windows_scored.values())} audit_events={len(audit_events)} delivered_long={len(events)} -> {results}",
        flush=True,
    )
    return summary


def _verify_qfq_arithmetic(frame: pd.DataFrame, *, secid: str) -> int:
    """Verify every saved adjusted OHLC equals round(raw/factor, 2)."""

    required = {"qfq_factor", "raw_open", "raw_high", "raw_low", "raw_close"}
    if not required.issubset(frame.columns):
        raise AShareSinaScanError(f"QFQ audit columns missing:{secid}")
    if bool((frame["qfq_factor"] <= 0).any()):
        raise AShareSinaScanError(f"non-positive saved QFQ factor:{secid}")
    for column in ("open", "high", "low", "close"):
        expected = (frame[f"raw_{column}"] / frame["qfq_factor"]).round(2)
        if not np.array_equal(
            expected.to_numpy(dtype=float), frame[column].to_numpy(dtype=float)
        ):
            raise AShareSinaScanError(f"saved QFQ arithmetic drift:{secid}:{column}")
    return len(frame)


def verify_results(out: Path, results: Path) -> dict[str, Any]:
    """Replay all frozen source/model/delivery evidence without network/inference."""

    source_commit = require_builder_committed()
    _, gates = verify_frozen_contract()
    snapshots, reference_1h, reference_4h, receipt = load_snapshot(out)
    summary = read_json(results / "summary.json")
    raw_boxes = read_jsonl(results / "raw_boxes.jsonl")
    structural = read_jsonl(results / "structural_boxes.jsonl")
    audit_events = read_jsonl(results / "audit_events_all_directions.jsonl")
    signals = read_jsonl(results / "signals.jsonl")
    if len(raw_boxes) != sum(int(value) for value in summary["raw_boxes_by_timeframe"].values()):
        raise AShareSinaScanError("saved raw box count drifted")
    snapshot_by_secid = {str(row["secid"]): row for row in snapshots}
    by_key: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in structural:
        by_key[(str(row["secid"]), str(row["timeframe"]))].append(row)
    candle_checks = 0
    qfq_row_checks = 0
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for identity in snapshots:
        path = out / str(identity["path"])
        meta_path = out / str(identity["source_meta_path"])
        if sha256_file(path) != str(identity["sha256"]) or sha256_file(
            meta_path
        ) != str(identity["source_meta_sha256"]):
            raise AShareSinaScanError(f"verification source SHA failed:{identity['secid']}")
        hourly = load_hourly(path)
        qfq_row_checks += _verify_qfq_arithmetic(hourly, secid=str(identity["secid"]))
        if bool(identity["eligible_1h"]):
            validate_1h(hourly, reference_1h, secid=str(identity["secid"]))
        if bool(identity["eligible_4h"]):
            validate_4h(
                aggregate_4h(hourly), reference_4h, secid=str(identity["secid"])
            )
        candle_checks += 1

    meta_keys = (
        "secid",
        "market",
        "code",
        "name",
        "board",
        "exchange",
        "search_key",
        "sina_symbol",
        "source_provider",
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
    pixel_checks = 0
    semantic_checks = 0
    for key, rows in sorted(by_key.items()):
        secid, timeframe = key
        identity = snapshot_by_secid[secid]
        hourly = load_hourly(out / str(identity["path"]))
        frame = add_candidate_features(_frame_for_timeframe(hourly, timeframe))
        frame_cache[key] = frame
        rendered: dict[tuple[int, int], tuple[np.ndarray, ChartTransform]] = {}
        for saved in rows:
            window_key = (int(saved["window_start_i"]), int(saved["window_end_i"]))
            if window_key not in rendered:
                rendered[window_key] = render_chart(
                    frame.iloc[window_key[0] : window_key[1] + 1], out_path=None
                )
            image, transform = rendered[window_key]
            if pixel_sha256(image) != str(saved["input_pixel_sha256"]):
                raise AShareSinaScanError(f"verification pixel failed:{secid}:{timeframe}")
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
                raise AShareSinaScanError(
                    f"verification semantic failed:{secid}:{timeframe}"
                )
            semantic_checks += 1

    if any(str(row["direction"]) != "LONG" for row in signals):
        raise AShareSinaScanError("SHORT reached delivery ledger")
    expected_events = shared.select_delivery_events(audit_events)
    identity_fields = lambda row: (  # noqa: E731 - compact immutable comparison
        str(row["timeframe"]),
        str(row["search_key"]),
        str(row["last_available_at"]),
    )
    if [identity_fields(row) for row in signals] != [
        identity_fields(row) for row in expected_events
    ]:
        raise AShareSinaScanError("LONG selection replay drifted")
    chart_checks = 0
    for order, signal in enumerate(signals, 1):
        key = (str(signal["secid"]), str(signal["timeframe"]))
        if key not in frame_cache:
            identity = snapshot_by_secid[key[0]]
            hourly = load_hourly(out / str(identity["path"]))
            frame_cache[key] = add_candidate_features(
                _frame_for_timeframe(hourly, key[1])
            )
        rerendered = shared.render_event(
            signal, frame=frame_cache[key], order=order, total=len(signals)
        )
        path = results / str(signal["chart"])
        saved = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if saved is None or not np.array_equal(saved, rerendered):
            raise AShareSinaScanError(f"verification chart pixels failed:{signal['event_id']}")
        if sha256_file(path) != str(signal["chart_sha256"]):
            raise AShareSinaScanError(f"verification chart SHA failed:{signal['event_id']}")
        chart_checks += 1
    zip_path = results / str(summary["chart_zip"])
    if sha256_file(zip_path) != str(summary["chart_zip_sha256"]):
        raise AShareSinaScanError("chart ZIP SHA drifted")
    verification = {
        "protocol": "ashare_grade_a_sina_qfq_1h_session4h_independent_replay_v1",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "fetch_receipt_sha256": sha256_file(out / "fetch_receipt.json"),
        "summary_sha256": sha256_file(results / "summary.json"),
        "universe_symbols": int(receipt["universe_rows"]),
        "usable_symbols": receipt["usable_symbols"],
        "candle_sha_and_schedule_checks": candle_checks,
        "qfq_arithmetic_row_checks": qfq_row_checks,
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
        f"verification PASS candles={candle_checks} qfq_rows={qfq_row_checks} decisions={semantic_checks} charts={chart_checks}",
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
    parser.add_argument("--workers", type=int, default=4)
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
