"""Build matched, outcome-blind 5m tapes for the owner-image touch cohort.

The cohort is keyed from the prior owner source map and the frozen 50,986-row
Binance touch-card archive. Controls match month and side exactly, then prefer
the same asset class, the same within-month first-touch/retouch status, the
same touch-hour wick/reclaim labels, and the nearest time segment. No fields
containing post-touch outcomes are read when controls are selected.

Each usable tape is float64 [open_ms, open, high, low, close] on a continuous
5m UTC grid from known_ms - 5 days through known_ms + 48 hours (exclusive).
The touch-hour close is known_ms; no bar opening at or after known_ms is part
of the warmup. The 5m row ending at known_ms must reproduce the touching H1.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / "experiments/active/exp-month-firstweek-intraday-retest-20261112-v1"
WICKS = ROOT / "experiments/active/exp-alt-isolated-12h-firstweek-wicks-20261111-v1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OLD))
sys.path.insert(0, str(WICKS))

import bars as local_bars  # noqa: E402
from census12 import read_raw  # noqa: E402


SOURCE_MAP = ROOT / "experiments/active/exp-month-firstweek-all-replay-20261111-v1/manual/selected_source_map.csv"
BINANCE_CROSSWALK = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/owner_reference/原图15张_OKX与币安逐张对照.csv"
CARDS = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1/cards.csv.gz"
OUT = HERE / "cohort.csv"
TAPE_DIR = ROOT / "data/research/month-firstweek-owner-yolo-20261113-v1/tapes"

FIVE_MS = 5 * 60_000
HOUR_MS = 60 * 60_000
DAY_MS = 24 * HOUR_MS
BEIJING_OFFSET_MS = 8 * HOUR_MS
WARMUP_MS = 5 * DAY_MS
FORWARD_MS = 48 * HOUR_MS
EXPECTED_BARS = (WARMUP_MS + FORWARD_MS) // FIVE_MS
EMPTY = np.empty((0, 5), dtype=np.float64)
CARD_COLUMNS = ["id", "symbol", "month", "side", "underlying", "beijing_touch", "touch_ms", "rail"]
CSV_COLUMNS = [
    "case_id", "pair_id", "role", "source_photo_id", "candidate_type",
    "symbol", "month", "side", "touch_ms", "known_ms", "rail",
    "direction_close", "wick_reclaim", "close_reclaim", "coverage",
    "asset_class", "tape_path", "touch_beijing", "month_touch_ordinal",
    "h1_open", "h1_high", "h1_low", "h1_close", "wick_ratio_pct",
    "match_rank", "match_rule", "match_rejections", "reason",
]

OLD_EVENT_TIMES = {
    # The 9/27 SSV row is a retouch: use the first Binance touch of that
    # Beijing calendar day, not the original 9/19 monthly first touch.
    "photo_09_retouch": "touch_beijing",
}
ALT_EVENTS = [
    dict(case_id="photo_02_alt", source_photo_id="photo_02", symbol="OPUSDT",
         month="2026-08", side="long", touch_beijing="2026-08-19 11:00",
         candidate_type="supplementary_candidate_not_new_photo"),
    dict(case_id="photo_03_alt", source_photo_id="photo_03", symbol="NEARUSDT",
         month="2025-07", side="short", touch_beijing="2025-07-28 08:00",
         candidate_type="supplementary_candidate_not_new_photo"),
    dict(case_id="photo_04_alt", source_photo_id="photo_04", symbol="ZKUSDT",
         month="2024-10", side="short", touch_beijing="2024-10-22 13:00",
         candidate_type="supplementary_candidate_not_new_photo"),
    dict(case_id="photo_05_alt", source_photo_id="photo_05", symbol="JELLYJELLYUSDT",
         month="2026-05", side="short", touch_beijing="2026-05-22 15:00",
         candidate_type="supplementary_candidate_not_new_photo"),
]


def _utc_ms(beijing_text: str) -> int:
    value = pd.Timestamp(str(beijing_text), tz="Asia/Shanghai")
    return int(value.tz_convert("UTC").value // 1_000_000)


def _beijing_text(ms: int) -> str:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")


def _month_bounds(month: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-\d{2}", str(month)):
        raise ValueError(f"invalid NY month {month!r}")
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    return int(start.value // 1_000_000), int(end.value // 1_000_000)


def _side(value: str) -> str:
    mapping = {"upper": "short", "lower": "long", "上": "short", "下": "long",
               "上轨": "short", "下轨": "long", "long": "long", "short": "short"}
    try:
        return mapping[str(value)]
    except KeyError as exc:
        raise ValueError(f"unknown rail side {value!r}") from exc


def _symbol_from_contract(value: str) -> str:
    value = str(value).strip().upper()
    return value if value.endswith("USDT") else f"{value}USDT"


def _read_cards() -> pd.DataFrame:
    if not CARDS.is_file():
        raise FileNotFoundError(f"frozen Binance touch cards missing: {CARDS}")
    cards = pd.read_csv(CARDS, usecols=CARD_COLUMNS, dtype={
        "id": "string", "symbol": "string", "month": "string", "side": "string",
        "underlying": "string", "beijing_touch": "string",
    }, float_precision="round_trip")
    if len(cards) != 50_986 or cards.id.nunique() != 50_986:
        raise ValueError(f"expected 50,986 unique touch cards; got {len(cards)} rows")
    cards["touch_ms"] = pd.to_numeric(cards.touch_ms, errors="raise").astype("int64")
    cards["rail"] = pd.to_numeric(cards.rail, errors="raise").astype("float64")
    if cards.duplicated(["symbol", "month", "side", "touch_ms"]).any():
        raise ValueError("frozen cards contain duplicate symbol/month/side/touch rows")
    # A daily first-touch card index is an event ledger; the rank below is
    # derived only from its touch timestamps, not from any later outcome field.
    cards = cards.sort_values(["symbol", "month", "side", "touch_ms", "id"], kind="stable").copy()
    cards["month_touch_ordinal"] = cards.groupby(["symbol", "month", "side"], sort=False).cumcount() + 1
    return cards


def _owner_specs() -> list[dict]:
    if not SOURCE_MAP.is_file() or not BINANCE_CROSSWALK.is_file():
        raise FileNotFoundError("owner source map or Binance crosswalk is missing")
    source = pd.read_csv(SOURCE_MAP, encoding="utf-8-sig", dtype=str).fillna("")
    crosswalk = pd.read_csv(BINANCE_CROSSWALK, dtype=str).fillna("")
    class_by_symbol = {
        _symbol_from_contract(row["合约"]): row["币安标的类型"]
        for row in crosswalk.to_dict("records")
    }
    specs: list[dict] = []
    for row in source.to_dict("records"):
        case_id = row["case_id"]
        ticker = _symbol_from_contract(row["okx_instrument"].replace("-USDT-SWAP", ""))
        side = _side(row["side"])
        spec = dict(case_id=case_id, pair_id=case_id, role="owner",
                    source_photo_id=case_id.split("_retouch")[0],
                    candidate_type="original_selected_touch", symbol=ticker,
                    month=row["month_ny"], side=side,
                    asset_class=class_by_symbol.get(ticker, ""),
                    touch_beijing="", touch_ms=None, rail=None,
                    source_ref="selected_source_map.csv")
        if case_id == "photo_16" or not row.get("binance_first_touch_beijing", ""):
            spec["coverage"] = "missing_binance_touch"
            spec["reason"] = "RAM has no saved Binance counterpart/touch time; kept as an explicit missing owner row."
        else:
            time_field = OLD_EVENT_TIMES.get(case_id, "binance_first_touch_beijing")
            text = row[time_field]
            spec["touch_beijing"] = text
            spec["touch_ms"] = _utc_ms(text)
            spec["candidate_type"] = ("original_selected_retouch" if case_id.endswith("_retouch")
                                       else "original_selected_touch")
        specs.append(spec)

    for alt in ALT_EVENTS:
        specs.append(dict(**alt, pair_id=alt["case_id"], role="owner",
                           touch_ms=_utc_ms(alt["touch_beijing"]), rail=None,
                           asset_class="", source_ref="all_replay_v1/cards.csv.gz"))
    if len(specs) != 20 or len({x["case_id"] for x in specs}) != 20:
        raise ValueError(f"expected 16 original touches plus 4 supplementary candidates; got {len(specs)}")
    return specs


def _exact_card(cards: pd.DataFrame, spec: dict) -> dict | None:
    if spec.get("touch_ms") is None:
        return None
    rows = cards.loc[
        cards.symbol.eq(spec["symbol"]) & cards.month.eq(spec["month"])
        & cards.side.eq(spec["side"]) & cards.touch_ms.eq(int(spec["touch_ms"]))
    ]
    if len(rows) != 1:
        spec["coverage"] = "source_card_not_found"
        spec["reason"] = f"Expected one all_replay card at saved Binance touch time; found {len(rows)}."
        return None
    return rows.iloc[0].to_dict()


@lru_cache(maxsize=256)
def _read_hourly(symbol: str) -> np.ndarray:
    rows = np.asarray(read_raw(symbol), dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] < 5:
        raise ValueError(f"{symbol}: registered Binance hourly source has shape {rows.shape}")
    rows = rows[:, :5]
    if len(rows) and (not np.isfinite(rows).all() or (np.diff(rows[:, 0]) <= 0).any()
                      or (rows[:, 0] % HOUR_MS != 0).any()):
        raise ValueError(f"{symbol}: invalid registered Binance H1 timestamps/values")
    return rows


def _h1_features(event: dict) -> dict:
    symbol = str(event["symbol"])
    touch_ms = int(event["touch_ms"])
    rail = float(event["rail"])
    side = str(event["side"])
    hourly = _read_hourly(symbol)
    pos = int(np.searchsorted(hourly[:, 0], touch_ms))
    if pos >= len(hourly) or int(hourly[pos, 0]) != touch_ms:
        raise ValueError(f"{symbol} {touch_ms}: no exact Binance H1 touch bar in registered source")
    opening, high, low, close = map(float, hourly[pos, 1:5])
    eps = abs(rail) * 1e-12
    if not (low <= rail + eps and high >= rail - eps):
        raise ValueError(f"{symbol} {touch_ms}: saved card does not match H1 true touch")
    if high < max(opening, close, low) or low > min(opening, close, high):
        raise ValueError(f"{symbol} {touch_ms}: invalid H1 OHLC range")
    sign = 1 if side == "long" else -1
    close_reclaim = bool(sign * (close - rail) > eps)
    wick = min(opening, close) - low if sign == 1 else high - max(opening, close)
    body = abs(opening - close)
    wick_ratio = wick / (high - low) if high > low else 0.0
    body_inside = (min(opening, close) > rail + eps if sign == 1
                   else max(opening, close) < rail - eps)
    pure_wick_through = (low <= rail + eps if sign == 1 else high >= rail - eps)
    wick_reclaim = bool(body_inside and pure_wick_through
                        and wick_ratio >= 0.5 - 1e-12 and wick + eps >= 2 * body)

    # Verify owner retouches (especially SSV 9/27) are the first same-rail
    # Binance H1 touch in that Beijing calendar day.
    day_start = ((touch_ms + BEIJING_OFFSET_MS) // DAY_MS) * DAY_MS - BEIJING_OFFSET_MS
    a, b = np.searchsorted(hourly[:, 0], [day_start, day_start + DAY_MS])
    day = hourly[a:b]
    hits = np.flatnonzero((day[:, 3] <= rail + eps) & (day[:, 2] >= rail - eps))
    first_day_touch = int(day[hits[0], 0]) if len(hits) else None
    if first_day_touch != touch_ms:
        raise ValueError(f"{symbol} {touch_ms}: not the first Binance rail touch of its Beijing day")
    return dict(h1_open=opening, h1_high=high, h1_low=low, h1_close=close,
                direction_close=close, wick_reclaim=wick_reclaim,
                close_reclaim=close_reclaim, wick_ratio_pct=100 * wick_ratio,
                month_touch_ordinal=int(event["month_touch_ordinal"]),
                touch_beijing=_beijing_text(touch_ms),
                first_day_touch_ms=first_day_touch)


def _official_archive(symbol: str, month: str, temp_root: Path) -> tuple[Path | None, str]:
    ticker = _symbol_from_contract(symbol)
    archive_dir = temp_root / ticker
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{ticker}-5m-{month}.zip"
    if path.is_file():
        return path, "cached_download"
    url = ("https://data.binance.vision/data/futures/um/monthly/klines/"
           f"{ticker}/5m/{ticker}-5m-{month}.zip")
    try:
        request = Request(url, headers={"User-Agent": "fable-trading-research/1.0"})
        with urlopen(request, timeout=45) as response:
            payload = response.read()
        if not payload:
            return None, "empty_official_archive"
        path.write_bytes(payload)
        if not zipfile.is_zipfile(path):
            path.unlink(missing_ok=True)
            return None, "official_archive_not_zip"
        return path, "downloaded_official_archive"
    except HTTPError as exc:
        if exc.code == 404:
            return None, "official_archive_404_not_listed_or_not_archived"
        return None, f"official_archive_http_{exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return None, f"official_archive_unavailable:{type(exc).__name__}:{exc}"


def _month_rows(symbol: str, month: str, needed_start: int, needed_end: int,
                temp_root: Path, archive_attempts: dict[tuple[str, str], str]) -> tuple[np.ndarray, str]:
    rows = local_bars.load_bars(symbol, month)
    month_start, month_end = _month_bounds(month)
    lo, hi = max(needed_start, month_start), min(needed_end, month_end)
    expected = np.arange(lo, hi, FIVE_MS, dtype=np.int64)
    present = rows[:, 0].astype(np.int64, copy=False) if len(rows) else np.empty(0, dtype=np.int64)
    if not np.array_equal(present[np.searchsorted(present, expected, side="left")]
                          if len(present) and np.all(np.searchsorted(present, expected, side="left") < len(present))
                          else np.empty(0, dtype=np.int64), expected):
        key = (_symbol_from_contract(symbol), month)
        if key not in archive_attempts:
            archive, status = _official_archive(symbol, month, temp_root)
            archive_attempts[key] = status
            if archive is not None:
                rows = local_bars.load_bars(symbol, month)
        # Recompute after optional public archive supplementation.
        present = rows[:, 0].astype(np.int64, copy=False) if len(rows) else np.empty(0, dtype=np.int64)
        ix = np.searchsorted(present, expected, side="left")
        complete = len(ix) == len(expected) and np.all(ix < len(present)) and np.array_equal(present[ix], expected)
    else:
        complete = True
    if key := (_symbol_from_contract(symbol), month):
        source_note = archive_attempts.get(key, "registered_local_source")
    return rows, source_note if complete else source_note


def _load_tape(event: dict, temp_root: Path,
               archive_attempts: dict[tuple[str, str], str]) -> tuple[np.ndarray, str, str]:
    touch = int(event["touch_ms"])
    known = touch + HOUR_MS
    start, end = known - WARMUP_MS, known + FORWARD_MS
    if touch % FIVE_MS or known % HOUR_MS:
        return EMPTY.copy(), "invalid_time_alignment", "touch/known timestamp is not on an H1/5m boundary"
    pieces = []
    first = pd.Timestamp(start, unit="ms", tz="UTC").strftime("%Y-%m")
    last = pd.Timestamp(end - 1, unit="ms", tz="UTC").strftime("%Y-%m")
    try:
        for period in pd.period_range(first, last, freq="M"):
            rows, _ = _month_rows(str(event["symbol"]), str(period), start, end,
                                  temp_root, archive_attempts)
            lo, hi = rows[:, 0].searchsorted([start, end]) if len(rows) else (0, 0)
            if hi > lo:
                pieces.append(rows[lo:hi])
    except (OSError, ValueError, EOFError) as exc:
        return EMPTY.copy(), "invalid_5m_source", f"{type(exc).__name__}: {exc}"
    tape = np.concatenate(pieces, axis=0) if pieces else EMPTY.copy()
    expected = np.arange(start, end, FIVE_MS, dtype=np.int64)
    if len(tape) != EXPECTED_BARS or not np.array_equal(tape[:, 0].astype(np.int64, copy=False), expected):
        if not len(tape):
            coverage = "missing_5m_data"
        elif int(tape[0, 0]) > start:
            coverage = "insufficient_5d_warmup"
        elif int(tape[-1, 0]) + FIVE_MS < end:
            coverage = "insufficient_48h_followup"
        else:
            coverage = "gapped_5m"
        return EMPTY.copy(), coverage, f"expected {EXPECTED_BARS} continuous 5m bars; found {len(tape)}"
    if not np.isfinite(tape).all() or not np.equal(tape[:, 0], np.floor(tape[:, 0])).all():
        return EMPTY.copy(), "invalid_5m_source", "non-finite values or non-integral timestamps"
    if ((tape[:, 1:] <= 0).any()
            or (tape[:, 2] < np.maximum.reduce((tape[:, 1], tape[:, 3], tape[:, 4]))).any()
            or (tape[:, 3] > np.minimum.reduce((tape[:, 1], tape[:, 2], tape[:, 4]))).any()):
        return EMPTY.copy(), "invalid_5m_source", "invalid OHLC range in 5m source"
    close_row = int(np.searchsorted(tape[:, 0], known - FIVE_MS))
    if (close_row >= len(tape) or int(tape[close_row, 0]) != known - FIVE_MS
            or not np.isclose(tape[close_row, 4], float(event["direction_close"]),
                              rtol=1e-8, atol=max(1e-12, abs(float(event["direction_close"])) * 1e-12))):
        return EMPTY.copy(), "source_disagrees_on_h1_close", "5m close at known_ms differs from touching H1 close"
    touch_start = int(event["touch_ms"])
    touch_indices = np.searchsorted(tape[:, 0], touch_start + np.arange(12, dtype=np.int64) * FIVE_MS)
    if (len(touch_indices) != 12 or np.any(touch_indices >= len(tape))
            or not np.array_equal(tape[touch_indices, 0].astype(np.int64),
                                  touch_start + np.arange(12, dtype=np.int64) * FIVE_MS)):
        return EMPTY.copy(), "missing_touch_hour", "5m source lacks a full 12-bar touching H1"
    touching = tape[touch_indices]
    h1 = np.array([touching[0, 1], touching[:, 2].max(), touching[:, 3].min(), touching[-1, 4]])
    expected_h1 = np.array([event["h1_open"], event["h1_high"], event["h1_low"], event["h1_close"]])
    if not np.allclose(h1, expected_h1, rtol=1e-8, atol=np.maximum(1e-12, np.abs(expected_h1) * 1e-12)):
        return EMPTY.copy(), "source_disagrees_on_touch_h1", "12 5m bars do not reproduce registered Binance H1 OHLC"
    if not np.array_equal(tape[:WARMUP_MS // FIVE_MS, 0].astype(np.int64),
                          np.arange(start, known, FIVE_MS, dtype=np.int64)):
        return EMPTY.copy(), "invalid_warmup_boundary", "warmup must end exactly before known_ms"
    return tape.astype(np.float64, copy=False), "complete", ""


def _match_key(owner: dict, candidate: dict) -> tuple:
    owner_day = pd.Timestamp(int(owner["touch_ms"]), unit="ms", tz="UTC").tz_convert("Asia/Shanghai")
    cand_day = pd.Timestamp(int(candidate["touch_ms"]), unit="ms", tz="UTC").tz_convert("Asia/Shanghai")
    owner_hour, cand_hour = owner_day.hour, cand_day.hour
    hour_gap = min(abs(owner_hour - cand_hour), 24 - abs(owner_hour - cand_hour))
    owner_segment, cand_segment = (owner_day.day - 1) // 7, (cand_day.day - 1) // 7
    return (
        int(str(candidate["asset_class"]) != str(owner["asset_class"])),
        int((int(candidate["month_touch_ordinal"]) == 1)
            != (int(owner["month_touch_ordinal"]) == 1)),
        int(bool(candidate["wick_reclaim"]) != bool(owner["wick_reclaim"])),
        int(bool(candidate["close_reclaim"]) != bool(owner["close_reclaim"])),
        abs(owner_segment - cand_segment),
        abs(owner_day.day - cand_day.day),
        hour_gap,
        abs(int(owner["touch_ms"]) - int(candidate["touch_ms"])),
        str(candidate["symbol"]), str(candidate["card_id"]),
    )


def _candidate_pool(cards: pd.DataFrame, owner: dict, owner_symbols: set[str]) -> list[dict]:
    subset = cards.loc[cards.month.eq(owner["month"]) & cards.side.eq(owner["side"])]
    result = []
    for row in subset.to_dict("records"):
        symbol = str(row["symbol"])
        if symbol in owner_symbols or symbol == owner["symbol"]:
            continue
        try:
            h1 = _h1_features(dict(symbol=symbol, touch_ms=int(row["touch_ms"]),
                                    rail=float(row["rail"]), side=str(row["side"]),
                                    month_touch_ordinal=int(row["month_touch_ordinal"])))
        except (OSError, ValueError, IndexError):
            continue
        result.append(dict(card_id=str(row["id"]), symbol=symbol, month=str(row["month"]),
                           side=str(row["side"]), asset_class=str(row["underlying"]),
                           touch_ms=int(row["touch_ms"]), rail=float(row["rail"]),
                           source_photo_id="", candidate_type="ordinary_daily_first_touch",
                           **h1))
    return result


def _record(event: dict, *, tape_path: str = "", coverage: str = "complete",
            reason: str = "", match_rank: int | str = "", match_rule: str = "",
            match_rejections: str = "") -> dict:
    known = int(event["touch_ms"]) + HOUR_MS if event.get("touch_ms") is not None else None
    return dict(
        case_id=event["case_id"], pair_id=event["pair_id"], role=event["role"],
        source_photo_id=event.get("source_photo_id", ""), candidate_type=event.get("candidate_type", ""),
        symbol=event.get("symbol", ""), month=event.get("month", ""), side=event.get("side", ""),
        touch_ms=event.get("touch_ms"), known_ms=known, rail=event.get("rail"),
        direction_close=event.get("direction_close"), wick_reclaim=event.get("wick_reclaim"),
        close_reclaim=event.get("close_reclaim"), coverage=coverage,
        asset_class=event.get("asset_class", ""), tape_path=tape_path,
        touch_beijing=event.get("touch_beijing", ""),
        month_touch_ordinal=event.get("month_touch_ordinal"),
        h1_open=event.get("h1_open"), h1_high=event.get("h1_high"),
        h1_low=event.get("h1_low"), h1_close=event.get("h1_close"),
        wick_ratio_pct=event.get("wick_ratio_pct"), match_rank=match_rank,
        match_rule=match_rule, match_rejections=match_rejections, reason=reason,
    )


def _write_tape(case_id: str, tape: np.ndarray) -> str:
    if not re.fullmatch(r"(?:photo|control)_\d{2}(?:_retouch|_alt)?", case_id):
        raise ValueError(f"unsafe tape case id {case_id!r}")
    TAPE_DIR.mkdir(parents=True, exist_ok=True)
    destination = TAPE_DIR / f"{case_id}.npy"
    temporary = TAPE_DIR / f".{case_id}.tmp.npy"
    with temporary.open("wb") as stream:
        np.save(stream, tape, allow_pickle=False)
        stream.flush()
    temporary.replace(destination)
    return str(destination.relative_to(ROOT))


def _prepare_event(spec: dict, card: dict) -> tuple[dict, np.ndarray, str, str]:
    event = dict(spec)
    event["card_id"] = str(card["id"])
    if int(card["touch_ms"]) != int(spec["touch_ms"]):
        raise ValueError(f"{spec['case_id']}: frozen card time disagrees with source map")
    event["rail"] = float(card["rail"])
    event["asset_class"] = str(card["underlying"])
    if spec.get("asset_class") and str(spec["asset_class"]) != event["asset_class"]:
        raise ValueError(f"{spec['case_id']}: crosswalk category differs from Binance card")
    event["month_touch_ordinal"] = int(card["month_touch_ordinal"])
    event.update(_h1_features(event))
    if event.get("coverage") == "missing_binance_touch":
        return event, EMPTY.copy(), "missing_binance_touch", str(event.get("reason", ""))
    return event, EMPTY.copy(), "", ""


def _write_cohort(records: list[dict]) -> None:
    frame = pd.DataFrame(records, columns=CSV_COLUMNS)
    tmp = OUT.with_name(f".{OUT.name}.tmp")
    frame.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(OUT)


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"refusing to replace an existing cohort ledger: {OUT}")
    cards = _read_cards()
    specs = _owner_specs()
    owner_symbols = {str(row["symbol"]) for row in specs}
    temp_context = tempfile.TemporaryDirectory(prefix="month-firstweek-binance-monthly-")
    temp_root = Path(temp_context.name)
    local_bars._MONTH_ZIP_ROOTS = tuple(local_bars._MONTH_ZIP_ROOTS) + (temp_root,)
    archive_attempts: dict[tuple[str, str], str] = {}

    owner_events: list[dict] = []
    owner_tapes: dict[str, np.ndarray] = {}
    records: dict[str, dict] = {}
    availability: dict[str, tuple[str, str]] = {}
    for spec in specs:
        if spec.get("touch_ms") is None:
            records[spec["case_id"]] = _record(spec, coverage=spec.get("coverage", "missing_binance_touch"),
                                                reason=spec.get("reason", ""))
            availability[spec["case_id"]] = (spec.get("coverage", "missing_binance_touch"), spec.get("reason", ""))
            continue
        card = _exact_card(cards, spec)
        if card is None:
            records[spec["case_id"]] = _record(spec, coverage=spec.get("coverage", "source_card_not_found"),
                                                reason=spec.get("reason", "source card not found"))
            availability[spec["case_id"]] = (spec.get("coverage", "source_card_not_found"), spec.get("reason", ""))
            continue
        try:
            event, _, _, _ = _prepare_event(spec, card)
        except (OSError, ValueError, IndexError) as exc:
            spec["coverage"] = "invalid_registered_h1"
            spec["reason"] = f"{type(exc).__name__}: {exc}"
            records[spec["case_id"]] = _record(spec, coverage=spec["coverage"], reason=spec["reason"])
            availability[spec["case_id"]] = (spec["coverage"], spec["reason"])
            continue
        tape, coverage, reason = _load_tape(event, temp_root, archive_attempts)
        availability[event["case_id"]] = (coverage, reason)
        event["coverage"] = coverage
        event["reason"] = reason
        if coverage == "complete":
            owner_events.append(event)
            owner_tapes[event["case_id"]] = tape
        else:
            records[event["case_id"]] = _record(event, coverage=coverage, reason=reason)

    # Candidate event cards carry only identity, touch time, side, month,
    # asset category and rail. No first-close, excursion, outcome or strategy
    # label is read. Hourly source OHLC is used only at the touch H1.
    eligible_owner_ids = {e["case_id"] for e in owner_events}
    scored_pools: dict[str, list[dict]] = {}
    for owner in owner_events:
        candidates = _candidate_pool(cards, owner, owner_symbols)
        candidates.sort(key=lambda candidate: _match_key(owner, candidate))
        scored_pools[owner["case_id"]] = candidates

    # Assign the most constrained matching problems first. Controls are unique
    # by symbol across the cohort and can never share a symbol with an owner.
    def pool_tightness(owner: dict) -> tuple:
        candidates = scored_pools[owner["case_id"]]
        ideal = sum(_match_key(owner, item)[:4] == (0, 0, 0, 0) for item in candidates)
        return ideal, len(candidates), owner["case_id"]

    owner_events.sort(key=pool_tightness)
    used_control_symbols: set[str] = set()
    control_tapes: dict[str, np.ndarray] = {}
    control_records: dict[str, dict] = {}
    for owner in owner_events:
        case_id = str(owner["case_id"])
        control_id = case_id.replace("photo_", "control_", 1)
        failures: list[str] = []
        selected = None
        selected_tape = None
        selected_reason = ""
        selected_rank = 0
        for rank, candidate in enumerate(scored_pools[case_id], start=1):
            if candidate["symbol"] in used_control_symbols:
                continue
            tape, coverage, reason = _load_tape(candidate, temp_root, archive_attempts)
            if coverage != "complete":
                failures.append(f"{candidate['card_id']}:{coverage}")
                continue
            selected, selected_tape, selected_rank = candidate, tape, rank
            break
        if selected is None:
            why = ("No complete, unique different-symbol touch card remained after outcome-blind matching. "
                   + ";".join(failures[:30]))
            placeholder = dict(case_id=control_id, pair_id=case_id, role="control",
                               source_photo_id=owner.get("source_photo_id", ""),
                               candidate_type="ordinary_daily_first_touch", symbol="",
                               month=owner["month"], side=owner["side"], touch_ms=None,
                               rail=None, direction_close=None, wick_reclaim=None,
                               close_reclaim=None, asset_class="", touch_beijing="",
                               month_touch_ordinal=None)
            control_records[case_id] = _record(placeholder, coverage="no_eligible_control", reason=why,
                                               match_rejections=";".join(failures[:30]))
            continue
        used_control_symbols.add(str(selected["symbol"]))
        selected = dict(selected, case_id=control_id, pair_id=case_id, role="control",
                        source_photo_id=owner.get("source_photo_id", ""),
                        candidate_type="ordinary_daily_first_touch", coverage="complete", reason="")
        key = _match_key(owner, selected)
        rule = ("same_month_side;score=(asset_class,month_first_or_retouch,wick_reclaim,close_reclaim,"
                "month_week,day,hour,absolute_time); unique_symbol; coverage_only_replacement")
        control_records[case_id] = _record(selected, tape_path="", coverage="complete",
                                           match_rank=selected_rank, match_rule=rule,
                                           match_rejections=";".join(failures[:30]))
        control_tapes[case_id] = selected_tape

    # All binary tape files are written before cohort.csv is atomically
    # replaced, so downstream readers never see a ledger pointing to files
    # that have not landed yet.
    for event in owner_events:
        records[event["case_id"]] = _record(event, tape_path=_write_tape(event["case_id"], owner_tapes[event["case_id"]]),
                                             coverage="complete")
    for owner in owner_events:
        case_id = owner["case_id"]
        ctrl = control_records.get(case_id)
        if ctrl is None:
            continue
        if ctrl["coverage"] == "complete":
            control_id = str(ctrl["case_id"])
            tape = control_tapes[case_id]
            ctrl_event = dict(case_id=control_id, pair_id=case_id, role="control",
                              source_photo_id=owner.get("source_photo_id", ""),
                              candidate_type="ordinary_daily_first_touch", symbol=ctrl["symbol"],
                              month=ctrl["month"], side=ctrl["side"], touch_ms=int(ctrl["touch_ms"]),
                              rail=float(ctrl["rail"]), direction_close=float(ctrl["direction_close"]),
                              wick_reclaim=bool(ctrl["wick_reclaim"]), close_reclaim=bool(ctrl["close_reclaim"]),
                              asset_class=ctrl["asset_class"], touch_beijing=ctrl["touch_beijing"],
                              month_touch_ordinal=int(ctrl["month_touch_ordinal"]),
                              h1_open=float(ctrl["h1_open"]), h1_high=float(ctrl["h1_high"]),
                              h1_low=float(ctrl["h1_low"]), h1_close=float(ctrl["h1_close"]),
                              wick_ratio_pct=float(ctrl["wick_ratio_pct"]))
            path = _write_tape(control_id, tape)
            ctrl["tape_path"] = path
        records[ctrl["case_id"]] = ctrl

    ordered: list[dict] = []
    for spec in specs:
        owner_row = records.get(spec["case_id"])
        if owner_row is not None:
            ordered.append(owner_row)
            control = control_records.get(spec["case_id"])
            if control is not None:
                ordered.append(control)
    # Cohort publication is the last write. If this fails, already-written
    # tapes remain unreferenced and the waiting detector has no partial ledger.
    _write_cohort(ordered)

    by_role = Counter(row["role"] for row in ordered)
    by_coverage = Counter(row["coverage"] for row in ordered)
    print(f"cohort={OUT} rows={len(ordered)} roles={dict(by_role)} coverage={dict(by_coverage)}")
    print(f"complete_tapes={sum(row['coverage'] == 'complete' for row in ordered)} "
          f"expected_rows_per_tape={EXPECTED_BARS} official_archives={len(archive_attempts)}")
    for key, status in sorted(archive_attempts.items()):
        if status != "registered_local_source":
            print(f"archive {key[0]} {key[1]} {status}")
    temp_context.cleanup()


if __name__ == "__main__":
    main()
