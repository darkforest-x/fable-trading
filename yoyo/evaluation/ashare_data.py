"""Freeze past-universe A-share daily records from BaoStock 0.9.3.

Source: https://pypi.org/project/baostock/0.9.3/ (official maintainer).
The past query_all_stock(day) universe is stratified by board, then selected by
SHA256('spike-ashare-v1|' + code), never today's status or future returns.
Indicators use backward-adjusted OHLC; same-date unadjusted prices, preclose,
historical tradestatus and isST remain available for execution restrictions.
Explicit date bounds are mandatory. Raw CSV bytes and request fingerprints are
hashed; incompatible or altered caches fail closed. No source is substituted.
Install the pinned client into an isolated target, never the model environment.
"""
from __future__ import annotations

import argparse
import atexit
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import importlib
import importlib.metadata
import json
import multiprocessing
from pathlib import Path
import re
import signal
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
import pandas as pd

BAOSTOCK_VERSION = "0.9.3"
DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
DEFAULT_QUOTAS = {"main_sh": 100, "main_sz": 100, "chinext": 80, "star": 80}
PRICE_FIELDS = ("open", "high", "low", "close", "preclose")
_PROCESS_CLIENT: Any = None


class AShareDataError(ValueError):
    """Invalid, incomplete, or mismatched source evidence."""


class _ProviderWallTimeout(BaseException):
    """Escape the SDK's broad except Exception without caching partial pages."""


def iso_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise AShareDataError("explicit YYYY-MM-DD required")
    date.fromisoformat(value)
    return value


def board_for_code(code: str) -> str | None:
    """Exclude indices/B shares; preserve main board, ChiNext and STAR strata."""
    if re.fullmatch(r"sh\.60[0135]\d{3}", code):
        return "main_sh"
    if re.fullmatch(r"sz\.00[0123]\d{3}", code):
        return "main_sz"
    if re.fullmatch(r"sz\.30[01]\d{3}", code):
        return "chinext"
    if re.fullmatch(r"sh\.688\d{3}", code):
        return "star"
    return None


def select_universe(frame: pd.DataFrame, *, day: str, quotas: Mapping[str, int], seed: str) -> pd.DataFrame:
    """Choose only from the supplied historical universe, without outcome data."""
    iso_date(day)
    if not seed or not quotas or not set(quotas).issubset(DEFAULT_QUOTAS) or any(v <= 0 for v in quotas.values()):
        raise AShareDataError("explicit seed and positive quotas for recognized boards required")
    if not {"code", "tradeStatus", "code_name"}.issubset(frame.columns):
        raise AShareDataError("universe fields missing")
    if frame["code"].duplicated().any():
        raise AShareDataError("duplicate universe code")
    eligible = frame.copy()
    eligible["board"] = eligible["code"].map(board_for_code)
    eligible = eligible[eligible["board"].notna()].copy()
    eligible["selection_key"] = eligible["code"].map(
        lambda code: hashlib.sha256(f"{seed}|{code}".encode()).hexdigest())
    pieces = [eligible[eligible["board"] == board].sort_values(["selection_key", "code"]).head(count)
              for board, count in quotas.items()]
    result = pd.concat(pieces, ignore_index=True)
    result["universe_date"] = day
    return result


def result_frame(result: Any) -> pd.DataFrame:
    """Consume every page and reject errors even if they occur after first rows."""
    if str(result.error_code) != "0":
        raise AShareDataError(f"BaoStock error {result.error_code}: {result.error_msg}")
    rows = []
    while result.next():
        if str(result.error_code) != "0":
            raise AShareDataError(f"BaoStock paging error {result.error_code}")
        row = result.get_row_data()
        if len(row) != len(result.fields):
            raise AShareDataError("BaoStock row/field length mismatch")
        rows.append(row)
    if str(result.error_code) != "0":
        raise AShareDataError(f"BaoStock final paging error {result.error_code}")
    if (hasattr(result, "data") and hasattr(result, "per_page_count")
            and len(result.data) == int(result.per_page_count)):
        # The SDK returns False on transport failure without setting error_code.
        # A successfully exhausted exact-full page must first fetch an empty page.
        raise AShareDataError("BaoStock pagination ended on a full page; completeness unproven")
    return pd.DataFrame(rows, columns=result.fields)


def cached_query(path: Path, request: Mapping[str, Any], query: Any) -> pd.DataFrame:
    """Read hash-checked frozen bytes or create a first source snapshot."""
    receipt = path.with_suffix(path.suffix + ".json")
    key = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if path.exists() or receipt.exists():
        if not path.exists() or not receipt.exists():
            raise AShareDataError(f"incomplete cache pair: {path}")
        meta = json.loads(receipt.read_text())
        if meta.get("request_key") != key or meta.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            raise AShareDataError(f"cache request or hash mismatch: {path}")
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    frame = result_frame(query())
    raw = frame.to_csv(index=False, lineterminator="\n").encode()
    meta = {"request": dict(request), "request_key": key,
            "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(frame),
            "retrieved_at": datetime.now(timezone.utc).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(raw)
    temp.replace(path)
    receipt.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return frame


def validate_daily(frame: pd.DataFrame, *, code: str, start: str, end: str, adjustment: str) -> pd.DataFrame:
    """Check same-session prices/status; no missing session is synthesized."""
    iso_date(start); iso_date(end)
    if start > end or list(frame.columns) != DAILY_FIELDS.split(","):
        raise AShareDataError("daily date range or source fields mismatch")
    frame = frame.copy()
    if frame.empty:
        return frame
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise AShareDataError("daily dates must be unique and increasing")
    for value in frame["date"]:
        iso_date(value)
    if (frame["date"] < start).any() or (frame["date"] > end).any():
        raise AShareDataError("source records outside explicit date bounds")
    if not (frame["code"] == code).all() or not (frame["adjustflag"] == adjustment).all():
        raise AShareDataError("source code/adjustment mismatch")
    for column in [*PRICE_FIELDS, "volume", "amount", "turn", "pctChg"]:
        frame[column] = pd.to_numeric(frame[column].replace("", np.nan), errors="raise")
    for column in ["tradestatus", "isST"]:
        if not frame[column].isin(["0", "1"]).all():
            raise AShareDataError(f"invalid historical {column}")
        frame[column] = frame[column].astype(int)
    if not np.isfinite(frame[list(PRICE_FIELDS)].to_numpy(dtype=float)).all():
        raise AShareDataError("nonfinite daily OHLC/preclose")
    if (frame[list(PRICE_FIELDS)] <= 0).any().any():
        raise AShareDataError("nonpositive daily OHLC/preclose")
    traded = frame["tradestatus"] == 1
    volume_cols = ["volume", "amount", "turn"]
    if not np.isfinite(frame.loc[traded, volume_cols].to_numpy(dtype=float)).all():
        raise AShareDataError("nonfinite traded volume/amount/turn")
    if (frame.loc[traded, volume_cols] < 0).any().any():
        raise AShareDataError("negative traded volume/amount/turn")
    tolerance = frame["close"].abs() * 1e-8 + 1e-8
    if ((frame["high"] + tolerance < frame[["open", "close"]].max(axis=1)).any()
            or (frame["low"] - tolerance > frame[["open", "close"]].min(axis=1)).any()):
        raise AShareDataError("invalid daily candle geometry")
    return frame


def merge_adjusted(raw: pd.DataFrame, adjusted: pd.DataFrame) -> pd.DataFrame:
    """Align same-date HFQ with raw execution evidence; reject ratio mismatches.

    O/H/L/C use the same date's adjustment factor, never a future backfill.
    Preclose may differ on ex-dividend sessions and is not ratio-tested.
    This is a total-return price coordinate, not actual dividend entitlements.
    """
    if not raw[["date", "code"]].equals(adjusted[["date", "code"]]):
        raise AShareDataError("raw/adjusted daily rows do not align")
    if raw.empty:
        return adjusted.assign(**{"raw_" + name: pd.Series(dtype=float) for name in PRICE_FIELDS},
                               factor=pd.Series(dtype=float), board=pd.Series(dtype=str))
    result = adjusted.copy()
    for column in ["tradestatus", "isST"]:
        if not raw[column].equals(adjusted[column]):
            raise AShareDataError(f"raw/adjusted historical {column} mismatch")
    for column in PRICE_FIELDS:
        result["raw_" + column] = raw[column].to_numpy()
    for column in ["volume", "amount", "turn", "pctChg"]:
        result[column] = raw[column].to_numpy()
    result["factor"] = result["close"] / result["raw_close"]
    for column in ["open", "high", "low"]:
        if not np.allclose(result[column], result["raw_" + column] * result["factor"], rtol=5e-4, atol=1e-8):
            raise AShareDataError("adjustment ratios differ within candle")
    result["board"] = result["code"].map(board_for_code)
    return result


@contextmanager
def query_timeout(seconds: int = 60) -> Iterator[None]:
    """Bound the SDK's EOF-spin risk with a process wall-clock alarm."""
    previous = signal.getsignal(signal.SIGALRM)
    def timeout(*_: Any) -> None:
        raise _ProviderWallTimeout()
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(seconds)
    try:
        yield
    except _ProviderWallTimeout:
        raise AShareDataError("BaoStock query wall-clock timeout") from None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@contextmanager
def baostock_session() -> Iterator[Any]:
    if importlib.metadata.version("baostock") != BAOSTOCK_VERSION:
        raise AShareDataError(f"isolated BaoStock {BAOSTOCK_VERSION} required")
    bs = importlib.import_module("baostock")
    with query_timeout():
        login = bs.login()
    if str(login.error_code) != "0":
        raise AShareDataError(f"BaoStock login failed: {login.error_code}")
    try:
        yield bs
    finally:
        with query_timeout():
            bs.logout()


def freeze_universe(client: Any, destination: Path, *, day: str, quotas: Mapping[str, int], seed: str) -> pd.DataFrame:
    """Persist the historical selection before any price-outcome query."""
    iso_date(day)
    request = {"provider": "baostock", "version": BAOSTOCK_VERSION,
               "method": "query_all_stock", "day": day}
    with query_timeout():
        source = cached_query(destination / "universe_raw.csv", request, lambda: client.query_all_stock(day))
    selected = select_universe(source, day=day, quotas=quotas, seed=seed)
    selection = {"date": day, "quotas": dict(quotas), "seed": seed,
                 "codes": selected["code"].tolist(), "by_board": selected.groupby("board").size().to_dict(),
                 "rule": "sha256(seed|code) within past-date board; no current status/return filter"}
    manifest = destination / "universe.json"
    if manifest.exists() and json.loads(manifest.read_text()) != selection:
        raise AShareDataError("frozen universe differs; use a new experiment")
    manifest.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n")
    selected.to_csv(destination / "universe_selected.csv", index=False)
    return selected


def fetch_daily(client: Any, destination: Path, *, code: str, start: str, end: str) -> pd.DataFrame:
    """Read only explicit raw/HFQ daily date intervals; preserve failures."""
    iso_date(start); iso_date(end)
    if not re.fullmatch(r"(?:sh|sz)\.\d{6}", code) or start > end:
        raise AShareDataError("invalid daily request")
    frames = []
    # CSI 300 is a price index, not an equity with stock corporate actions.
    # BaoStock returns adjustflag=3 even when HFQ is requested; request only its
    # native raw index level and explicitly use factor 1 for the benchmark.
    adjustments = ("3",) if code == "sh.000300" else ("3", "1")
    for adjustment in adjustments:
        request = {"provider": "baostock", "version": BAOSTOCK_VERSION,
                   "method": "query_history_k_data_plus", "code": code, "fields": DAILY_FIELDS,
                   "start_date": start, "end_date": end, "frequency": "d", "adjustflag": adjustment}
        path = destination / "source" / f"{code}_{adjustment}.csv"
        with query_timeout():
            frame = cached_query(path, request, lambda: client.query_history_k_data_plus(
                code, DAILY_FIELDS, start_date=start, end_date=end, frequency="d", adjustflag=adjustment))
        frames.append(validate_daily(frame, code=code, start=start, end=end, adjustment=adjustment))
    if len(frames) == 1:
        frames.append(frames[0].copy())
    result = merge_adjusted(*frames)
    output = destination / "daily" / f"{code}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    return result


def _close_worker_socket() -> None:
    """Close this connection without sending shared-anonymous logout."""
    context = importlib.import_module("baostock.common.context")
    connection = getattr(context, "default_socket", None)
    if connection is not None:
        connection.close()


def _reset_worker_client() -> None:
    """Discard a failed transport before another symbol can read stale pages."""
    global _PROCESS_CLIENT
    try:
        _close_worker_socket()
    finally:
        _PROCESS_CLIENT = None


def _worker_client() -> Any:
    """Reuse one client session per process; no per-stock login/logout churn."""
    global _PROCESS_CLIENT
    if _PROCESS_CLIENT is None:
        if importlib.metadata.version("baostock") != BAOSTOCK_VERSION:
            raise AShareDataError(f"isolated BaoStock {BAOSTOCK_VERSION} required")
        client = importlib.import_module("baostock")
        with query_timeout():
            login = client.login()
        if str(login.error_code) != "0":
            raise AShareDataError(f"BaoStock worker login failed: {login.error_code}")
        _PROCESS_CLIENT = client
        atexit.register(_close_worker_socket)
    return _PROCESS_CLIENT


def _fetch_worker(arguments: tuple[str, str, str, str]) -> dict[str, Any]:
    """Each process owns its BaoStock session; SDK globals are never threaded."""
    destination, code, start, end = arguments
    try:
        bs = _worker_client()
        frame = fetch_daily(bs, Path(destination), code=code, start=start, end=end)
        request = {"provider": "baostock", "version": BAOSTOCK_VERSION,
                   "method": "query_stock_basic", "code": code, "metadata_only": True}
        try:
            with query_timeout():
                basic = cached_query(Path(destination) / "basic" / f"{code}.csv", request,
                                     lambda: bs.query_stock_basic(code))
            if (not {"code", "ipoDate", "outDate"}.issubset(basic.columns)
                    or len(basic) != 1 or basic.iloc[0]["code"] != code):
                raise AShareDataError("stock basic code/IPO/delisting metadata invalid")
            metadata = basic[["code", "ipoDate", "outDate"]].iloc[0].to_dict()
        except Exception as exc:
            _reset_worker_client()
            metadata = {"code": code, "error": f"{type(exc).__name__}: {exc}"}
        return {"code": code, "rows": len(frame), "metadata": metadata}
    except Exception as exc:
        _reset_worker_client()
        return {"code": code, "error": f"{type(exc).__name__}: {exc}"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--universe-date", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--quotas", default=json.dumps(DEFAULT_QUOTAS))
    parser.add_argument("--seed", default="spike-ashare-v1")
    parser.add_argument("--universe-only", action="store_true")
    parser.add_argument("--benchmark", default="sh.000300")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=1)
    args = parser.parse_args(argv)
    if not iso_date(args.start) <= iso_date(args.universe_date) <= iso_date(args.end):
        raise AShareDataError("universe must fall within requested history")
    with baostock_session() as bs:
        selected = freeze_universe(bs, args.destination, day=args.universe_date,
                                   quotas=json.loads(args.quotas), seed=args.seed)
        print(json.dumps({"selected": len(selected), "boards": selected.groupby("board").size().to_dict()}), flush=True)
        if args.universe_only:
            return 0
    errors, listing_metadata = [], []
    codes = selected["code"].tolist() + ([args.benchmark] if args.benchmark else [])
    arguments = [(str(args.destination), code, args.start, args.end) for code in codes]
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as executor:
        for number, result in enumerate(executor.map(_fetch_worker, arguments), 1):
            if "error" in result:
                errors.append(result)
            metadata = result.pop("metadata", {"code": result["code"], "error": "price_fetch_failed_no_listing_metadata"})
            listing_metadata.append(metadata)
            (args.destination / "listing_metadata.json").write_text(json.dumps({"role": "current retrieval for audit only; never selection or predictors", "records": listing_metadata}, indent=2) + "\n")
            print(json.dumps({"index": number, **result}), flush=True)
            (args.destination / "fetch_errors.json").write_text(json.dumps(errors, indent=2) + "\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
