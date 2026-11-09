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
    args = parser.parse_args()
    EXP.mkdir(parents=True, exist_ok=True)
    if args.summary_only:
        receipt = EXP / "source_check.json"
        if not receipt.exists():
            raise FileNotFoundError(receipt)
        result = json.loads(receipt.read_text())
    else:
        result = build()
    print(json.dumps({
        "verified": result["verified"],
        "target_hour_utc": result["target_hour_utc"],
        "new_h1_row": result["new_h1_row"],
        "target_missing_minute_trade_prints": result["target_minute_archive_has_prints_in_missing_minute"],
        "output_series_path": result["output_series_path"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
