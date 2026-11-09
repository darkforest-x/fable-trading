"""Verify and, only with continuous native trade IDs, recover one CVC H1 bar."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-close-cvc-trade-hour-20261109-v1"
CACHE = ROOT / "data/research/cloud_close_cvc_trade_hour"
CFG = EXP / "config.json"
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
OHLC = ["open", "high", "low", "close"]


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_trade_archive(url: str, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        last_error = None
        for attempt in range(4):
            try:
                response = requests.get(url, timeout=(10, 90))
                if response.status_code == 404:
                    raise FileNotFoundError(f"Official OKX archive returned 404: {url}")
                response.raise_for_status()
                temp = dest.with_suffix(dest.suffix + ".part")
                temp.write_bytes(response.content)
                temp.replace(dest)
                headers = dict(response.headers)
                break
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        else:  # pragma: no cover - loop always breaks or raises
            raise RuntimeError(f"Could not fetch official archive: {last_error}")
    else:
        headers = {"cache": "reused local official archive"}
    with zipfile.ZipFile(dest) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"Corrupt official archive member: {bad_member}")
        csv_files = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_files) != 1:
            raise ValueError(f"Expected one CSV in official archive; got {csv_files}")
    return {
        "url": url,
        "local_path": str(dest.relative_to(ROOT)),
        "bytes": dest.stat().st_size,
        "sha256": _sha256(dest),
        "zip_member": csv_files[0],
        "response_headers": headers,
    }


def _read_trade_rows(path: Path, member: str, instrument: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive, archive.open(member) as stream:
        raw = pd.read_csv(stream, float_precision="round_trip")
    required = ["instrument_name", "trade_id", "price", "size", "created_time"]
    if not set(required).issubset(raw.columns):
        raise ValueError(f"Unexpected OKX trade archive schema: {raw.columns.tolist()}")
    raw = raw[required].copy()
    if set(raw.instrument_name.dropna().unique()) != {instrument}:
        raise ValueError("Trade archive instrument does not exactly match CVC-USDT-SWAP")
    for col in ["trade_id", "created_time"]:
        raw[col] = pd.to_numeric(raw[col], errors="raise").astype("int64")
    for col in ["price", "size"]:
        raw[col] = pd.to_numeric(raw[col], errors="raise").astype("float64")
    if (not np.isfinite(raw[["price", "size"]]).all().all()
            or not raw.price.gt(0).all() or not raw["size"].gt(0).all()):
        raise ValueError("Invalid/nonpositive native trade price or size")

    # The WAVES acceptance deduplicates buyer/seller receipts only when a repeated
    # trade ID agrees on price, size, and UTC hour. Keep the earliest timestamp.
    duplicate = raw[raw.trade_id.duplicated(keep=False)].copy()
    if len(duplicate):
        duplicate["utc_hour"] = duplicate.created_time // HOUR_MS
        if duplicate.groupby("trade_id")[["price", "size", "utc_hour"]].nunique().gt(1).any().any():
            raise ValueError("Conflicting duplicate native trade identity")
    trades = raw.sort_values(["created_time", "trade_id"]).drop_duplicates("trade_id", keep="first")
    if trades.trade_id.duplicated().any():
        raise AssertionError("Trade IDs should be unique after duplicate handling")
    # Require the official identity sequence to move forward with event time.
    by_id = trades.sort_values("trade_id")
    if not by_id.created_time.is_monotonic_increasing:
        raise ValueError("Trade IDs and native execution timestamps disagree")
    return trades.sort_values(["created_time", "trade_id"]).reset_index(drop=True)


def _trade_hour_bars(trades: pd.DataFrame, start_ms: int, end_ms: int) -> pd.DataFrame:
    focus = trades.loc[trades.created_time.ge(start_ms) & trades.created_time.lt(end_ms)].copy()
    if focus.empty:
        return pd.DataFrame(columns=OHLC + ["first_id", "last_id", "prints", "first_trade_ms", "last_trade_ms"])
    focus["hour_ms"] = focus.created_time // HOUR_MS * HOUR_MS
    rows = []
    for hour_ms, group in focus.groupby("hour_ms", sort=True):
        group = group.sort_values(["created_time", "trade_id"])
        ids = group.trade_id.to_numpy(dtype="int64")
        rows.append({
            "time": int(hour_ms),
            "open": float(group.price.iloc[0]),
            "high": float(group.price.max()),
            "low": float(group.price.min()),
            "close": float(group.price.iloc[-1]),
            "first_id": int(ids[0]),
            "last_id": int(ids[-1]),
            "prints": int(len(ids)),
            "first_trade_ms": int(group.created_time.iloc[0]),
            "last_trade_ms": int(group.created_time.iloc[-1]),
            "ids_contiguous": bool(len(ids) == 1 or np.all(np.diff(ids) == 1)),
            "id_count_matches": bool(int(ids[-1] - ids[0] + 1) == len(ids)),
        })
    return pd.DataFrame(rows).set_index("time").sort_index()


def _minute_hour_rows(path: Path, instrument: str, start_ms: int, end_ms: int) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(
        path,
        usecols=["instrument_name", "open_time", *OHLC, "confirm"],
        float_precision="round_trip",
    )
    if set(raw.instrument_name.unique()) != {instrument}:
        raise ValueError("Minute archive instrument mismatch")
    if not raw.confirm.isin([0, 1]).all():
        raise ValueError("Unknown native candle confirmation flag")
    rows = raw.loc[raw.confirm.eq(1) & raw.open_time.ge(start_ms) & raw.open_time.lt(end_ms)].copy()
    if rows.open_time.duplicated().any() or (rows.open_time % MINUTE_MS).any():
        raise ValueError("Conflicting or off-grid confirmed native minute bars")
    rows = rows.sort_values("open_time")
    rows["hour_ms"] = rows.open_time // HOUR_MS * HOUR_MS
    minute_hours = rows.groupby("hour_ms", sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), minutes=("open_time", "size"),
        first_minute_ms=("open_time", "first"), last_minute_ms=("open_time", "last"),
    )
    expected = range(start_ms, end_ms, HOUR_MS)
    return minute_hours, {
        "confirmed_minute_rows_in_focus": int(len(rows)),
        "missing_confirmed_minutes_by_hour": {
            str(int(hour)): [
                int(t) for t in range(int(hour), int(hour) + HOUR_MS, MINUTE_MS)
                if t not in set(rows.loc[rows.hour_ms.eq(hour), "open_time"].tolist())
            ]
            for hour in sorted(expected)
            if int(rows.loc[rows.hour_ms.eq(hour), "open_time"].nunique()) != 60
        },
        "hour_rows": int(len(minute_hours)),
    }


def _close_enough(left: pd.Series, right: pd.Series) -> bool:
    return bool(np.isclose(left[OHLC].to_numpy(dtype=float), right[OHLC].to_numpy(dtype=float),
                           rtol=1e-9, atol=1e-13).all())


def _read_candle_zip(path: Path, instrument: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"Expected one candle CSV in {path}: {members}")
        with archive.open(members[0]) as stream:
            raw = pd.read_csv(stream, usecols=["instrument_name", "open_time", *OHLC, "vol", "confirm"],
                              float_precision="round_trip")
    if set(raw.instrument_name.dropna().unique()) != {instrument}:
        raise ValueError(f"Native candle archive instrument mismatch: {path}")
    raw["open_time"] = pd.to_numeric(raw.open_time, errors="raise").astype("int64")
    raw["vol"] = pd.to_numeric(raw.vol, errors="raise").astype("float64")
    raw["confirm"] = pd.to_numeric(raw.confirm, errors="raise").astype("int64")
    for col in OHLC:
        raw[col] = pd.to_numeric(raw[col], errors="raise").astype("float64")
    if raw.open_time.duplicated().any() or (raw.open_time % MINUTE_MS).any():
        raise ValueError(f"Duplicate or off-grid native minutes in {path}")
    if not raw.confirm.isin([0, 1]).all():
        raise ValueError(f"Unknown candle confirmation in {path}")
    if (not np.isfinite(raw[OHLC + ["vol"]]).all().all()
            or not raw.low.gt(0).all() or not raw.vol.ge(0).all()
            or not raw.low.le(raw[["open", "close"]].min(axis=1)).all()
            or not raw.high.ge(raw[["open", "close"]].max(axis=1)).all()):
        raise ValueError(f"Invalid native candle OHLCV in {path}")
    return raw.set_index("open_time").sort_index()


def _direct_history_check(instrument: str, bar: str, timestamp_ms: int, label: str) -> dict:
    cache = CACHE / "direct_api" / f"{label}.json"
    if cache.is_file():
        receipt = json.loads(cache.read_text())
        payload = receipt["response"]
        url = receipt["url"]
        http_status = receipt["http_status"]
    else:
        url = "https://www.okx.com/api/v5/market/history-candles"
        response = requests.get(url, params={
            "instId": instrument, "bar": bar, "after": str(timestamp_ms + 1), "limit": "5",
        }, timeout=(10, 30))
        http_status = int(response.status_code)
        response.raise_for_status()
        payload = response.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(cache, {"url": response.url, "http_status": http_status,
                             "headers": dict(response.headers), "response": payload})
        url = response.url
    exact = []
    for row in payload.get("data", []):
        if int(row[0]) == timestamp_ms:
            exact.append(row)
    return {
        "api": "/api/v5/market/history-candles",
        "url": url,
        "cache_path": str(cache.relative_to(ROOT)),
        "http_status": http_status,
        "api_code": payload.get("code"),
        "api_message": payload.get("msg"),
        "requested_bar": bar,
        "requested_open_ms": int(timestamp_ms),
        "exact_timestamp_rows": exact,
        "confirmed_exact_row": bool(len(exact) == 1 and str(exact[0][8]) == "1"),
    }


def build_v2() -> dict:
    """Use a second official daily source and exact price bounds; preserve v1's rejection."""
    cfg = json.loads(CFG.read_text())
    inst = cfg["instrument"]
    target_ms = int(pd.Timestamp(cfg["target_hour_utc"]).value // 1_000_000)
    missing_minute_ms = int(pd.Timestamp(cfg["expected_missing_minute_utc"]).value // 1_000_000)
    focus_start, focus_end = target_ms - HOUR_MS, target_ms + 2 * HOUR_MS
    direct_checks = [
        _direct_history_check(inst, "1m", missing_minute_ms, "history_1m_cvc_20221026T0311"),
        _direct_history_check(inst, "1H", target_ms, "history_1H_cvc_20221026T0300"),
    ]
    day = pd.Timestamp(cfg["target_hour_utc"]).strftime("%Y-%m-%d")
    day_compact = pd.Timestamp(cfg["target_hour_utc"]).strftime("%Y%m%d")
    daily_base = cfg["official_daily_archive_base"]
    daily_cache = CACHE / "daily" / inst
    trade_name = f"{inst}-trades-{day}.zip"
    candle_name = f"{inst}-candlesticks-{day}.zip"
    daily_trade_receipt = _download_trade_archive(
        f"{daily_base}/trades/daily/{day_compact}/{trade_name}", daily_cache / trade_name)
    daily_candle_receipt = _download_trade_archive(
        f"{daily_base}/candlesticks/daily/{day_compact}/{candle_name}", daily_cache / candle_name)

    daily_trades = _read_trade_rows(daily_cache / trade_name, daily_trade_receipt["zip_member"], inst)
    daily_candles = _read_candle_zip(daily_cache / candle_name, inst)
    monthly_trade_path = CACHE / "raw" / inst / f"{inst}-trades-2022-10.zip"
    with zipfile.ZipFile(monthly_trade_path) as archive:
        monthly_member = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
    monthly_trades = _read_trade_rows(monthly_trade_path, monthly_member, inst)
    minute_zip = ROOT / cfg["source_native_minute_archive"]
    monthly_candles = _read_candle_zip(minute_zip, inst)

    # Repeated official daily/monthly exports must agree on all records that exist
    # in both before either is used to close the single missing-minute interval.
    common_minutes = daily_candles.index.intersection(monthly_candles.index)
    minute_columns = OHLC + ["vol", "confirm"]
    common_same = np.isclose(
        daily_candles.loc[common_minutes, minute_columns].to_numpy(dtype=float),
        monthly_candles.loc[common_minutes, minute_columns].to_numpy(dtype=float),
        rtol=1e-10, atol=1e-13,
    ).all(axis=1)
    same_daily_monthly = bool(len(common_minutes) and common_same.all())
    daily_missing = sorted(set(range(target_ms, target_ms + HOUR_MS, MINUTE_MS)) -
                           set(daily_candles.index.intersection(range(target_ms, target_ms + HOUR_MS, MINUTE_MS))))
    monthly_missing = sorted(set(range(target_ms, target_ms + HOUR_MS, MINUTE_MS)) -
                             set(monthly_candles.index.intersection(range(target_ms, target_ms + HOUR_MS, MINUTE_MS))))

    trade_bars = _trade_hour_bars(daily_trades, focus_start, focus_end)
    checks = []
    frozen = pd.read_csv(ROOT / cfg["source_h1_series"], index_col="time", float_precision="round_trip")
    if target_ms in frozen.index:
        raise ValueError("Target bar is already present in the frozen source")
    if target_ms - HOUR_MS not in frozen.index or target_ms + HOUR_MS not in frozen.index:
        raise ValueError("Both adjacent frozen H1 reference bars are required")
    for hour_ms in [target_ms - HOUR_MS, target_ms, target_ms + HOUR_MS]:
        has_trade = hour_ms in trade_bars.index
        bar = trade_bars.loc[hour_ms] if has_trade else None
        known_minute_rows = daily_candles.loc[
            (daily_candles.index >= hour_ms) & (daily_candles.index < hour_ms + HOUR_MS)
        ]
        frozen_match = bool(has_trade and hour_ms != target_ms and _close_enough(bar, frozen.loc[hour_ms]))
        checks.append({
            "time": int(hour_ms),
            "hour_utc": pd.to_datetime(hour_ms, unit="ms", utc=True).isoformat(),
            "trade_prints": int(bar.prints) if has_trade else 0,
            "trade_first_id": int(bar.first_id) if has_trade else None,
            "trade_last_id": int(bar.last_id) if has_trade else None,
            "trade_ids_contiguous_within_hour": bool(bar.ids_contiguous) if has_trade else False,
            "trade_id_span_matches_print_count": bool(bar.id_count_matches) if has_trade else False,
            "confirmed_daily_minutes": int(known_minute_rows.confirm.sum()) if len(known_minute_rows) else 0,
            "daily_minute_rows": int(len(known_minute_rows)),
            "trade_matches_frozen_h1": frozen_match,
            **{col: float(bar[col]) if has_trade else None for col in OHLC},
        })
    bridges = [{
        "from_time": int(left["time"]), "to_time": int(right["time"]),
        "consecutive_ids": bool(right["trade_first_id"] == left["trade_last_id"] + 1),
    } for left, right in zip(checks[:-1], checks[1:])]

    day_by_id = daily_trades.set_index("trade_id", drop=False)
    month_by_id = monthly_trades.set_index("trade_id", drop=False)
    gap_before_id = int(cfg["expected_missing_trade_id"])
    before = daily_trades.loc[daily_trades.trade_id.eq(gap_before_id - 1)]
    after = daily_trades.loc[daily_trades.trade_id.eq(gap_before_id + 1)]
    month_before = monthly_trades.loc[monthly_trades.trade_id.eq(gap_before_id - 1)]
    month_after = monthly_trades.loc[monthly_trades.trade_id.eq(gap_before_id + 1)]
    if any(len(frame) != 1 for frame in [before, after, month_before, month_after]):
        raise ValueError("The unique trade-ID gap is not bracketed in both official trade archives")
    before_row, after_row = before.iloc[0], after.iloc[0]
    day_neighbor_match = bool(
        np.isclose([before_row.price, before_row.size, before_row.created_time],
                   [month_before.iloc[0].price, month_before.iloc[0].size, month_before.iloc[0].created_time],
                   rtol=0, atol=0).all()
        and np.isclose([after_row.price, after_row.size, after_row.created_time],
                       [month_after.iloc[0].price, month_after.iloc[0].size, month_after.iloc[0].created_time],
                       rtol=0, atol=0).all()
    )
    minute_at = lambda stamp: daily_candles.loc[int(stamp)] if int(stamp) in daily_candles.index else None
    minute_before = minute_at(missing_minute_ms - MINUTE_MS)
    minute_after = minute_at(missing_minute_ms + MINUTE_MS)
    minute_after2 = minute_at(missing_minute_ms + 2 * MINUTE_MS)
    if any(row is None for row in [minute_before, minute_after, minute_after2]):
        raise ValueError("Daily candle evidence around missing 03:11 minute is incomplete")

    target_window = daily_trades.loc[daily_trades.created_time.ge(target_ms)
                                     & daily_trades.created_time.lt(target_ms + HOUR_MS)]
    known_0311 = daily_trades.loc[daily_trades.created_time.ge(missing_minute_ms)
                                  & daily_trades.created_time.lt(missing_minute_ms + MINUTE_MS)]
    known_0312 = daily_trades.loc[daily_trades.created_time.ge(missing_minute_ms + MINUTE_MS)
                                  & daily_trades.created_time.lt(missing_minute_ms + 2 * MINUTE_MS)]
    known_0313 = daily_trades.loc[daily_trades.created_time.ge(missing_minute_ms + 2 * MINUTE_MS)
                                  & daily_trades.created_time.lt(missing_minute_ms + 3 * MINUTE_MS)]
    candle_0312_zero = bool(float(minute_after.vol) == 0.0 and np.isclose(
        minute_after[OHLC].to_numpy(float), np.repeat(float(minute_after.close), 4), rtol=0, atol=1e-13).all()
        and int(minute_after.confirm) == 1)
    candle_0313_volume_matches = bool(
        len(known_0313) == 1 and int(known_0313.trade_id.iloc[0]) == gap_before_id + 1
        and float(known_0313["size"].sum()) == float(minute_after2.vol) == 14.0
        and int(minute_after2.confirm) == 1
        and np.isclose(minute_after2[OHLC].to_numpy(float), np.repeat(float(minute_after2.close), 4),
                       rtol=0, atol=1e-13).all()
    )
    no_trades_0312 = bool(known_0312.empty)
    trade_id_time_monotonic = bool(daily_trades.sort_values("trade_id").created_time.is_monotonic_increasing)
    only_target_hour_gap = sorted({
        missing for left, right in zip(sorted(target_window.trade_id), sorted(target_window.trade_id)[1:])
        for missing in range(int(left + 1), int(right))
    })
    one_missing_id = only_target_hour_gap == [gap_before_id]
    gap_bracket_ms = [int(before_row.created_time), int(after_row.created_time)]
    before_in_missing_minute = bool(
        missing_minute_ms <= int(before_row.created_time) < missing_minute_ms + MINUTE_MS
    )
    known_0311_is_single_bracketed_trade = bool(
        len(known_0311) == 1 and int(known_0311.trade_id.iloc[0]) == gap_before_id - 1
    )
    target_id_bracketed_in_daily_and_monthly = bool(
        len(day_by_id.loc[[gap_before_id - 1, gap_before_id + 1]]) == 2
        and len(month_by_id.loc[[gap_before_id - 1, gap_before_id + 1]]) == 2
    )
    zero_trade_carry_rule = cfg["official_zero_trade_candle_rule"]
    missing_minute_price = float(minute_after.close)
    known_missing_hour_trades_match_month = bool(
        np.allclose(
            target_window.sort_values("trade_id")[["price", "size", "created_time"]].to_numpy(float),
            monthly_trades.loc[monthly_trades.created_time.ge(target_ms)
                               & monthly_trades.created_time.lt(target_ms + HOUR_MS)]
            .sort_values("trade_id")[["price", "size", "created_time"]].to_numpy(float),
            rtol=0, atol=0,
        )
    )

    minute_rows = daily_candles.loc[
        (daily_candles.index >= target_ms) & (daily_candles.index < target_ms + HOUR_MS)
    ].copy()
    minute_rows["price_source"] = "official_daily_confirmed_1m"
    minute_rows["price_exact"] = True
    minute_rows["volume_known"] = True
    minute_rows["confirm"] = minute_rows.confirm.astype("float64")
    if missing_minute_ms in minute_rows.index:
        raise ValueError("The expected missing minute unexpectedly exists in daily archive")
    minute_rows.loc[missing_minute_ms, OHLC] = missing_minute_price
    minute_rows.loc[missing_minute_ms, "vol"] = np.nan
    minute_rows.loc[missing_minute_ms, "confirm"] = np.nan
    minute_rows.loc[missing_minute_ms, "price_source"] = "exact_price_bounded_by_trade_id_and_zero_volume_candle"
    minute_rows.loc[missing_minute_ms, "price_exact"] = True
    minute_rows.loc[missing_minute_ms, "volume_known"] = False
    minute_rows.index.name = "open_time"
    minute_rows = minute_rows.sort_index()
    expected_clock = pd.Index(range(target_ms, target_ms + HOUR_MS, MINUTE_MS), name="open_time")
    complete_minute_clock = minute_rows.index.equals(expected_clock)
    reconstructed = pd.Series({
        "open": float(minute_rows.open.iloc[0]),
        "high": float(minute_rows.high.max()),
        "low": float(minute_rows.low.min()),
        "close": float(minute_rows.close.iloc[-1]),
    })
    daily_source_supports_minute = bool(
        same_daily_monthly and daily_missing == monthly_missing == [missing_minute_ms]
        and complete_minute_clock and len(minute_rows) == 60
    )
    target_trade_bar = trade_bars.loc[target_ms] if target_ms in trade_bars.index else None
    trade_ohlc_crosscheck = bool(target_trade_bar is not None and _close_enough(reconstructed, target_trade_bar))
    adjacent_supported = bool(
        checks[0]["trade_ids_contiguous_within_hour"]
        and checks[0]["trade_matches_frozen_h1"]
        and checks[2]["trade_ids_contiguous_within_hour"]
        and checks[2]["trade_matches_frozen_h1"]
        and all(bridge["consecutive_ids"] for bridge in bridges)
    )
    source_dir = (ROOT / cfg["source_h1_series"]).parent
    month_receipt = next(row for row in json.loads((source_dir / "months.json").read_text())
                         if row["month"] == cfg["month"])
    source_receipt = json.loads((source_dir / "source.json").read_text())
    price_proven = bool(
        source_receipt.get("instrument") == inst
        and month_receipt.get("status") == "native_archive"
        and month_receipt.get("incomplete_hours") == 1
        and one_missing_id and target_id_bracketed_in_daily_and_monthly
        and day_neighbor_match and known_missing_hour_trades_match_month
        and trade_id_time_monotonic and before_in_missing_minute
        and known_0311_is_single_bracketed_trade
        and no_trades_0312 and candle_0312_zero and candle_0313_volume_matches
        and float(before_row.price) == missing_minute_price
        and float(minute_after.close) == missing_minute_price
        and daily_source_supports_minute and trade_ohlc_crosscheck and adjacent_supported
        and target_ms not in frozen.index
    )

    direct_checks_consistent = True
    for check in direct_checks:
        if not check["confirmed_exact_row"]:
            continue
        direct_row = check["exact_timestamp_rows"][0]
        direct_ohlc = np.asarray(list(map(float, direct_row[1:5])))
        expected_ohlc = (reconstructed[OHLC].to_numpy(float)
                         if check["requested_bar"] == "1H"
                         else np.repeat(missing_minute_price, 4))
        direct_checks_consistent &= bool(np.isclose(direct_ohlc, expected_ohlc, rtol=1e-9, atol=1e-13).all())
    price_proven &= direct_checks_consistent

    output_series = CACHE / "series" / inst / "bars1h_v2.csv.gz"
    output_series.parent.mkdir(parents=True, exist_ok=True)
    if price_proven:
        target_frame = reconstructed[OHLC].to_frame().T
        target_frame.index = pd.Index([target_ms], name="time", dtype="int64")
        augmented = pd.concat([frozen, target_frame]).sort_index()
    else:
        augmented = frozen.copy()
    augmented.index = augmented.index.astype("int64")
    augmented.index.name = "time"
    temp_series = output_series.with_suffix(output_series.suffix + ".part")
    augmented.to_csv(temp_series, float_format="%.15g", compression="gzip")
    temp_series.replace(output_series)
    persisted = pd.read_csv(output_series, index_col="time", float_precision="round_trip")
    if price_proven:
        expected_index = frozen.index.union(pd.Index([target_ms], dtype="int64")).sort_values()
        if not persisted.index.equals(expected_index):
            raise AssertionError("Repaired H1 output index differs from the frozen source by more than target hour")
        if not np.array_equal(persisted.loc[frozen.index, OHLC].to_numpy(float),
                              frozen.loc[:, OHLC].to_numpy(float)):
            raise AssertionError("An existing CVC H1 row changed during target-hour insertion")
        if not np.allclose(persisted.loc[target_ms, OHLC].to_numpy(float), reconstructed[OHLC].to_numpy(float),
                           rtol=1e-12, atol=1e-15):
            raise AssertionError("Persisted target H1 OHLC does not match the verified reconstruction")
    elif not persisted.index.equals(frozen.index):
        raise AssertionError("Unverified target hour must not change the frozen series")
    minute_evidence_path = EXP / "minute_price_evidence_v2.csv.gz"
    minute_temp = minute_evidence_path.with_suffix(minute_evidence_path.suffix + ".part")
    minute_rows.reset_index().to_csv(minute_temp, index=False, float_format="%.15g", compression="gzip")
    minute_temp.replace(minute_evidence_path)

    for check in checks:
        check["price_ohlc_proven_by_candle_id_bounds"] = bool(check["time"] == target_ms and price_proven)
        check["eligible_for_admission"] = bool(check["time"] == target_ms and price_proven)
        check["price_evidence_type"] = (
            "exact_price_bounded_missing_trade_id" if check["time"] == target_ms and price_proven
            else "native_trade_id_contiguous" if check["trade_ids_contiguous_within_hour"]
            else "unverified"
        )
    checks_path = EXP / "hour_checks_v2.csv.gz"
    checks_temp = checks_path.with_suffix(checks_path.suffix + ".part")
    pd.DataFrame(checks).to_csv(checks_temp, index=False, float_format="%.15g", compression="gzip")
    checks_temp.replace(checks_path)

    result = {
        "version": 2,
        "instrument": inst,
        "target_hour_utc": cfg["target_hour_utc"],
        "target_hour_ms": target_ms,
        "verified": price_proven,
        "eligible_for_admission": price_proven,
        "price_ohlc_proven": price_proven,
        "volume_proven": False,
        "missing_trade_id": gap_before_id,
        "missing_trade_size": None,
        "target_trade_hour_had_missing_id": True,
        "target_minute_archive_has_prints_in_missing_minute": int(len(known_0311)),
        "new_h1_row": ({"time": target_ms, **{key: float(reconstructed[key]) for key in OHLC}}
                        if price_proven else None),
        "missing_minute_exact_price_ohlc": ({key: missing_minute_price for key in OHLC}
                                              if price_proven else None),
        "missing_minute_open_ms": missing_minute_ms,
        "missing_minute_trade_id_neighbors": {
            "previous_id": int(before_row.trade_id),
            "previous_time_ms": int(before_row.created_time),
            "previous_price": float(before_row.price),
            "next_id": int(after_row.trade_id),
            "next_time_ms": int(after_row.created_time),
            "next_price": float(after_row.price),
            "inclusive_time_bounds_ms": gap_bracket_ms,
            "possible_minutes_after_constraints": ["2022-10-26T03:11:00Z"],
        },
        "price_reconstruction_logic": {
            "daily_and_monthly_trade_archives_both_omit_one_id": one_missing_id,
            "native_trade_ids_and_timestamps_monotonic": trade_id_time_monotonic,
            "03_12_confirmed_zero_volume": candle_0312_zero,
            "03_13_volume_equals_known_next_trade_size": candle_0313_volume_matches,
            "official_no_trade_candle_carries_previous_close": zero_trade_carry_rule,
            "deduced_missing_03_11_ohlc": {key: missing_minute_price for key in OHLC},
            "known_03_11_trade_price": float(before_row.price),
            "remaining_unknowns": ["missing trade record size", "minute and hourly total volume"],
        },
        "official_direct_rest_checks": direct_checks,
        "daily_trade_archive": daily_trade_receipt,
        "daily_candle_archive": daily_candle_receipt,
        "daily_monthly_candle_overlap": {
            "common_confirmed_minutes": int(len(common_minutes)),
            "all_common_ohlcv_and_confirm_equal": same_daily_monthly,
            "daily_missing_target_hour_minutes_ms": daily_missing,
            "monthly_missing_target_hour_minutes_ms": monthly_missing,
        },
        "hour_checks": checks,
        "trade_id_boundary_checks": bridges,
        "minute_price_evidence_path": str(minute_evidence_path.relative_to(ROOT)),
        "output_series_path": str(output_series.relative_to(ROOT)),
        "reader": {
            "pandas": "pd.read_csv(path, index_col='time', float_precision='round_trip')",
            "time_index": "UTC Unix milliseconds at H1 open",
            "price_columns": OHLC,
            "admission_column": "eligible_for_admission",
        },
        "cvc_source_receipt": source_receipt,
        "cvc_month_receipt": month_receipt,
        "no_interpolation": True,
        "scope_limit": "Price OHLC is proved; the one missing trade's quantity and volume are not reconstructed.",
    }
    _atomic_json(EXP / "source_check_v2.json", result)
    v1_receipt = json.loads((EXP / "source_check.json").read_text())
    summary = {
        "instrument": inst,
        "target_hour_utc": cfg["target_hour_utc"],
        "target_hour_ms": target_ms,
        "v1_full_trade_id_admission": bool(v1_receipt.get("verified", False)),
        "v2_price_only_admission": bool(price_proven),
        "target_h1_ohlc": result["new_h1_row"],
        "volume_proven": False,
        "output_series_path": str(output_series.relative_to(ROOT)),
        "source_check_v1": str((EXP / "source_check.json").relative_to(ROOT)),
        "source_check_v2": str((EXP / "source_check_v2.json").relative_to(ROOT)),
        "admission_column": "eligible_for_admission",
        "series_validation": {
            "frozen_h1_rows": int(len(frozen)),
            "output_h1_rows": int(len(persisted)),
            "added_rows": int(len(persisted) - len(frozen)),
            "added_times_ms": sorted(map(int, persisted.index.difference(frozen.index))),
            "existing_rows_unchanged": bool(np.array_equal(
                persisted.loc[frozen.index, OHLC].to_numpy(float), frozen.loc[:, OHLC].to_numpy(float)
            )),
        },
        "remaining_unknowns": ["missing trade record size", "minute and hourly total volume"],
    }
    _atomic_json(EXP / "summary.json", summary)
    return result


def build() -> dict:
    cfg = json.loads(CFG.read_text())
    inst = cfg["instrument"]
    target_ms = int(pd.Timestamp(cfg["target_hour_utc"]).value // 1_000_000)
    focus_start = target_ms - HOUR_MS
    focus_end = target_ms + 2 * HOUR_MS
    instrument_cache = CACHE / "raw" / inst
    month = cfg["month"]
    trade_name = f"{inst}-trades-{month}.zip"
    trade_path = instrument_cache / trade_name
    trade_url = f"{cfg['official_trade_archive_base']}/{month.replace('-', '')}/{trade_name}"
    trade_receipt = _download_trade_archive(trade_url, trade_path)
    trades = _read_trade_rows(trade_path, trade_receipt["zip_member"], inst)
    trade_bars = _trade_hour_bars(trades, focus_start, focus_end)

    source_series = ROOT / cfg["source_h1_series"]
    source_receipt_path = source_series.parent / "source.json"
    source_months_path = source_series.parent / "months.json"
    source_receipt = json.loads(source_receipt_path.read_text())
    month_receipt = next(row for row in json.loads(source_months_path.read_text()) if row["month"] == month)
    if month_receipt.get("status") != "native_archive":
        raise ValueError("CVC source receipt does not identify the target month as an official native archive")

    minute_archive = ROOT / cfg["source_native_minute_archive"]
    minute_bars, minute_info = _minute_hour_rows(minute_archive, inst, focus_start, focus_end)
    original = pd.read_csv(source_series, index_col="time", float_precision="round_trip")
    if target_ms in original.index:
        raise ValueError("The target hour already exists in the frozen source series")
    if target_ms - HOUR_MS not in original.index or target_ms + HOUR_MS not in original.index:
        raise ValueError("The neighboring native H1 reference hours are not both present")

    checks = []
    for hour_ms in [target_ms - HOUR_MS, target_ms, target_ms + HOUR_MS]:
        exists_trade = hour_ms in trade_bars.index
        exists_minute = hour_ms in minute_bars.index
        row = trade_bars.loc[hour_ms] if exists_trade else None
        minute = minute_bars.loc[hour_ms] if exists_minute else None
        complete_minute = bool(minute is not None and int(minute.minutes) == 60)
        if hour_ms != target_ms:
            original_row = original.loc[hour_ms]
            native_match = bool(exists_trade and _close_enough(row, original_row))
        else:
            native_match = False
        partial_compatible = False
        if minute is not None and exists_trade:
            if int(minute.minutes) == 0:
                partial_compatible = False
            else:
                partial_compatible = bool(
                    np.isclose(float(row.open), float(minute.open), rtol=1e-9, atol=1e-13)
                    and np.isclose(float(row.close), float(minute.close), rtol=1e-9, atol=1e-13)
                    and float(row.high) >= float(minute.high) - 1e-13
                    and float(row.low) <= float(minute.low) + 1e-13
                )
        checks.append({
            "hour_ms": int(hour_ms),
            "hour_utc": pd.to_datetime(hour_ms, unit="ms", utc=True).isoformat(),
            "trade_hour_present": bool(exists_trade),
            "trade_prints": int(row.prints) if exists_trade else 0,
            "trade_first_id": int(row.first_id) if exists_trade else None,
            "trade_last_id": int(row.last_id) if exists_trade else None,
            "trade_first_ms": int(row.first_trade_ms) if exists_trade else None,
            "trade_last_ms": int(row.last_trade_ms) if exists_trade else None,
            "trade_ids_contiguous_within_hour": bool(row.ids_contiguous) if exists_trade else False,
            "trade_id_span_matches_print_count": bool(row.id_count_matches) if exists_trade else False,
            **{col: float(row[col]) if exists_trade else None for col in OHLC},
            "trade_ohlc": {col: float(row[col]) for col in OHLC} if exists_trade else None,
            "confirmed_native_minutes": int(minute.minutes) if exists_minute else 0,
            "complete_60_confirmed_minutes": complete_minute,
            "trade_matches_frozen_h1": native_match,
            "trade_compatible_with_available_minutes": partial_compatible,
        })

    pair_bridges = []
    for previous, current in zip(checks[:-1], checks[1:]):
        pair_bridges.append({
            "from_hour_ms": previous["hour_ms"],
            "to_hour_ms": current["hour_ms"],
            "consecutive_trade_ids_across_hour_boundary": bool(
                previous["trade_hour_present"] and current["trade_hour_present"]
                and current["trade_first_id"] == previous["trade_last_id"] + 1
            ),
        })

    target_row = trade_bars.loc[target_ms] if target_ms in trade_bars.index else None
    actual_missing_minute_ms = int(pd.Timestamp(cfg["expected_missing_minute_utc"]).value // 1_000_000)
    prints_in_missing_minute = int(
        trades.created_time.ge(actual_missing_minute_ms)
        .mul(trades.created_time.lt(actual_missing_minute_ms + MINUTE_MS)).sum()
    )
    target_bar_proven = bool(
        target_row is not None
        and checks[0]["confirmed_native_minutes"] == 60
        and checks[1]["confirmed_native_minutes"] == 59
        and checks[2]["confirmed_native_minutes"] == 60
        and minute_info["missing_confirmed_minutes_by_hour"].get(str(target_ms)) == [actual_missing_minute_ms]
        and all(check["trade_ids_contiguous_within_hour"] and check["trade_id_span_matches_print_count"]
                for check in checks)
        and all(bridge["consecutive_trade_ids_across_hour_boundary"] for bridge in pair_bridges)
        and checks[0]["trade_matches_frozen_h1"]
        and checks[2]["trade_matches_frozen_h1"]
        and checks[1]["trade_compatible_with_available_minutes"]
    )
    hour_check_table = pd.DataFrame(checks)
    hour_check_table["eligible_for_admission"] = (
        hour_check_table.hour_ms.eq(target_ms) & target_bar_proven
    )
    check_path = EXP / "hour_checks.csv.gz"
    check_temp = check_path.with_suffix(check_path.suffix + ".part")
    hour_check_table.to_csv(check_temp, index=False, float_format="%.15g", compression="gzip")
    check_temp.replace(check_path)

    series_dir = CACHE / "series" / inst
    series_dir.mkdir(parents=True, exist_ok=True)
    output_series = series_dir / "bars1h.csv.gz"
    if target_bar_proven:
        repaired = pd.concat([original, target_row[OHLC].to_frame().T]).sort_index()
        repaired.index = repaired.index.astype("int64")
        repaired.index.name = "time"
        temp = output_series.with_suffix(".csv.gz.part")
        repaired.to_csv(temp, float_format="%.15g", compression="gzip")
        temp.replace(output_series)
    else:
        # Keep an explicit copy of the unchanged source; no unsupported row is filled.
        temp = output_series.with_suffix(".csv.gz.part")
        original.to_csv(temp, float_format="%.15g", compression="gzip")
        temp.replace(output_series)

    target_out = {
        "instrument": inst,
        "target_hour_utc": cfg["target_hour_utc"],
        "target_hour_ms": target_ms,
        "verified": target_bar_proven,
        "new_h1_row": ({"time": target_ms, **{col: float(target_row[col]) for col in OHLC}}
                        if target_bar_proven else None),
        "target_minute_archive_has_prints_in_missing_minute": prints_in_missing_minute,
        "official_trade_archive": trade_receipt,
        "cvc_source_receipt_path": str(source_receipt_path.relative_to(ROOT)),
        "cvc_source_receipt": source_receipt,
        "cvc_month_receipt": month_receipt,
        "native_minute_archive_path": str(minute_archive.relative_to(ROOT)),
        "native_minute_focus": minute_info,
        "hour_checks": checks,
        "trade_id_boundary_checks": pair_bridges,
        "output_series_path": str(output_series.relative_to(ROOT)),
        "reader": {
            "pandas": "pd.read_csv(path, index_col='time', float_precision='round_trip')",
            "time_index": "UTC Unix milliseconds at H1 open",
            "price_columns": OHLC,
            "apply_only_added_hour": target_ms,
        },
        "no_interpolation": True,
        "interpretation": (
            "Target is inserted only if both neighboring official H1 bars match exact native trade OHLC, "
            "the three-hour trade IDs are consecutive within and across hours, and target trades are "
            "compatible with the available confirmed native minute candles."
        ),
    }
    _atomic_json(EXP / "source_check.json", target_out)
    return target_out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-only", action="store_true", help="Print the existing source check without fetching")
    parser.add_argument("--v2", action="store_true", help="Run exact-price-bound reconstruction and preserve v1")
    args = parser.parse_args()
    EXP.mkdir(parents=True, exist_ok=True)
    receipt_name = "source_check_v2.json" if args.v2 else "source_check.json"
    if args.summary_only:
        receipt = EXP / receipt_name
        if not receipt.exists():
            raise FileNotFoundError(receipt)
        result = json.loads(receipt.read_text())
    else:
        result = build_v2() if args.v2 else build()
    print(json.dumps({
        "verified": result["verified"],
        "eligible_for_admission": result.get("eligible_for_admission", result["verified"]),
        "target_hour_utc": result["target_hour_utc"],
        "new_h1_row": result["new_h1_row"],
        "target_missing_minute_trade_prints": result.get(
            "target_minute_archive_has_prints_in_missing_minute",
            result.get("price_reconstruction_logic", {}).get("known_03_11_trade_count", 0),
        ),
        "output_series_path": result["output_series_path"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
