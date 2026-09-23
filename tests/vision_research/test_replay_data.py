"""Read-only local replay history contracts using temporary CSV fixtures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.vision_research.replay_data import LocalReplayHistory, SourceError
from yoyo.vision_research.source import SourceError as VisionSourceError


PERIOD_MS = 15 * 60_000
BASE_MS = (1_700_000_100_000 // PERIOD_MS) * PERIOD_MS


def bars(count: int, *, start: int = BASE_MS, offset: float = 0.0):
    result = []
    for index in range(count):
        close = offset + 100 + index * 0.07 + (index % 11) * 0.13
        stamp = start + index * PERIOD_MS
        result.append({
            "ts": stamp,
            "open": close - 0.2,
            "high": close + 0.8,
            "low": close - 0.7,
            "close": close,
            "volume": 10 + index,
            "confirm": 1,
            "open_time": pd.Timestamp(stamp, unit="ms", tz="UTC").isoformat(),
        })
    return result


def write_bars(root: Path, file_symbol: str, timeframe: str, rows: list[dict], *, suffix: str = "") -> Path:
    path = root / f"okx_{file_symbol}_{timeframe}_{len(rows)}{suffix}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def make_source(root: Path, *, now_ms: int = BASE_MS + 100_000_000 * PERIOD_MS):
    return LocalReplayHistory(roots=[root], clock=lambda: now_ms)


def visible_hash(rows):
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def test_catalog_separates_spot_swap_and_timeframes_and_loads_real_csv_schema(tmp_path):
    spot = bars(320, offset=0)
    swap = bars(320, offset=500)
    write_bars(tmp_path, "ETH_USDT", "15m", spot)
    write_bars(tmp_path, "ETH_USDT_SWAP", "15m", swap)
    hourly = []
    hour_base = (BASE_MS // (60 * 60_000)) * (60 * 60_000)
    for index in range(320):
        row = bars(1, start=hour_base + index * 60 * 60_000, offset=900)[0]
        row["ts"] = hour_base + index * 60 * 60_000
        row["open_time"] = pd.Timestamp(row["ts"], unit="ms", tz="UTC").isoformat()
        hourly.append(row)
    write_bars(tmp_path, "ETH_USDT_SWAP", "1H", hourly)

    source = make_source(tmp_path)
    catalog = source.catalog()
    entries = {(item["symbol"], item["timeframe"]) for item in catalog["items"]}
    assert entries == {
        ("ETH-USDT", "15m"),
        ("ETH-USDT-SWAP", "15m"),
        ("ETH-USDT-SWAP", "1H"),
    }
    assert catalog["warning"] == ""

    requested = BASE_MS + 260 * PERIOD_MS + PERIOD_MS + 1
    loaded = source.load("ETH-USDT-SWAP", "15m", requested)
    assert loaded["duration_ms"] == PERIOD_MS
    assert loaded["first_cursor_index"] == 119
    assert loaded["cursor_index"] >= loaded["first_cursor_index"]
    cursor = loaded["rows"][loaded["cursor_index"]]
    assert cursor["t"] == BASE_MS + 260 * PERIOD_MS
    assert cursor["c"] > 500
    assert all(row[key] is not None for row in loaded["rows"] for key in (
        "sma20", "ema20", "sma60", "ema60", "sma120", "ema120"
    ))
    assert loaded["source_label"] == "OKX 本地历史"
    assert loaded["source_receipt"]["ema_seed_open_ms"] == BASE_MS
    assert loaded["source_receipt"]["algorithm_version"] == "okx-close-sma-ema-v1"
    assert loaded["source_receipt"]["files"][0]["path"].endswith("okx_ETH_USDT_SWAP_15m_320.csv")


def test_coverage_returns_only_segments_with_full_replay_warmup_and_reads_selected_market(tmp_path):
    target = bars(239)
    target.extend(bars(80, start=BASE_MS + 240 * PERIOD_MS))
    write_bars(tmp_path, "ETH_USDT_SWAP", "15m", target)
    unrelated = tmp_path / "okx_SOL_USDT_SWAP_15m_300.csv"
    unrelated.write_text("bad,header\n1,2\n", encoding="utf-8")

    result = make_source(tmp_path).coverage("ETH-USDT-SWAP", "15m")
    assert result == {
        "first_close_ms": BASE_MS + PERIOD_MS,
        "last_close_ms": BASE_MS + 320 * PERIOD_MS,
        "segments": [{
            "first_close_ms": BASE_MS + PERIOD_MS,
            "last_close_ms": BASE_MS + 239 * PERIOD_MS,
            "first_replay_close_ms": BASE_MS + 239 * PERIOD_MS,
        }],
        "source_label": "OKX 本地历史",
    }

    with pytest.raises(SourceError) as raised:
        make_source(tmp_path).coverage("SOL-USDT-SWAP", "15m")
    assert isinstance(raised.value, VisionSourceError)


def test_confirm_zero_and_wallclock_future_rows_are_excluded_without_changing_visible_history(tmp_path):
    original = bars(500)
    original[400]["confirm"] = 0
    original[400]["close"] = "not-a-close"  # The unconfirmed row is excluded before OHLC validation.
    now_ms = BASE_MS + 400 * PERIOD_MS
    path = write_bars(tmp_path, "ETH_USDT_SWAP", "15m", original)
    requested = BASE_MS + 300 * PERIOD_MS + PERIOD_MS

    before = make_source(tmp_path, now_ms=now_ms).load("ETH-USDT-SWAP", "15m", requested)
    cursor = before["cursor_index"]
    visible_before = before["rows"][cursor - 119:cursor + 1]
    hash_before = visible_hash(visible_before)
    assert visible_before[-1]["t"] == BASE_MS + 300 * PERIOD_MS
    assert before["rows"][-1]["t"] == BASE_MS + 399 * PERIOD_MS

    changed = bars(500)
    changed[400]["confirm"] = 0
    changed[400]["close"] = "still-unconfirmed"
    for index in range(401, 500):
        changed[index]["close"] += 10_000  # Future rows are outside wall-clock closure.
        changed[index]["high"] += 10_000
    write_bars(tmp_path, "ETH_USDT_SWAP", "15m", changed)

    after = make_source(tmp_path, now_ms=now_ms).load("ETH-USDT-SWAP", "15m", requested)
    cursor_after = after["cursor_index"]
    visible_after = after["rows"][cursor_after - 119:cursor_after + 1]
    assert visible_after == visible_before
    assert visible_hash(visible_after) == hash_before


def test_same_bar_has_same_ema_for_different_requested_times(tmp_path):
    write_bars(tmp_path, "ETH_USDT_SWAP", "15m", bars(500))
    source = make_source(tmp_path)
    bar_close = BASE_MS + 350 * PERIOD_MS + PERIOD_MS

    exact = source.load("ETH-USDT-SWAP", "15m", bar_close)
    between = source.load("ETH-USDT-SWAP", "15m", bar_close + PERIOD_MS // 2)
    exact_row = exact["rows"][exact["cursor_index"]]
    between_row = between["rows"][between["cursor_index"]]
    assert exact_row["t"] == between_row["t"] == BASE_MS + 350 * PERIOD_MS
    assert exact_row["ema120"] == between_row["ema120"]


def test_gap_stops_forward_rows_and_start_inside_gap_is_rejected(tmp_path):
    data = bars(300)
    data.extend(bars(300, start=BASE_MS + 301 * PERIOD_MS))
    write_bars(tmp_path, "ETH_USDT_SWAP", "15m", data)
    source = make_source(tmp_path)

    first_segment_cursor = BASE_MS + 270 * PERIOD_MS + PERIOD_MS
    loaded = source.load("ETH-USDT-SWAP", "15m", first_segment_cursor)
    assert loaded["rows"][-1]["t"] == BASE_MS + 299 * PERIOD_MS
    assert loaded["source_receipt"]["next_gap_open_ms"] == BASE_MS + 301 * PERIOD_MS

    with pytest.raises(SourceError, match="缺口"):
        source.load("ETH-USDT-SWAP", "15m", BASE_MS + 300 * PERIOD_MS + PERIOD_MS)


def test_identical_duplicate_ohlc_is_deduped_but_conflict_is_rejected(tmp_path):
    data = bars(320)
    first = write_bars(tmp_path, "ETH_USDT_SWAP", "15m", data)
    duplicate_path = tmp_path / "okx_ETH_USDT_SWAP_15m_321.csv"
    pd.DataFrame(data).to_csv(duplicate_path, index=False)
    source = make_source(tmp_path)
    requested = BASE_MS + 270 * PERIOD_MS + PERIOD_MS
    assert source.load("ETH-USDT-SWAP", "15m", requested)["rows"]

    changed = [dict(row) for row in data]
    changed[270]["close"] += 1
    changed[270]["high"] += 1
    pd.DataFrame(changed).to_csv(duplicate_path, index=False)
    with pytest.raises(SourceError, match="冲突 OHLC"):
        source.load("ETH-USDT-SWAP", "15m", requested)
    assert first.exists() and duplicate_path.exists()


@pytest.mark.parametrize("mutation, message", [
    ("schema", "缺少必需字段"),
    ("ohlc", "无效 OHLC"),
])
def test_bad_schema_and_bad_ohlc_are_not_silently_dropped(tmp_path, mutation, message):
    data = bars(300)
    path = write_bars(tmp_path, "ETH_USDT_SWAP", "15m", data)
    if mutation == "schema":
        pd.DataFrame(data).drop(columns=["high"]).to_csv(path, index=False)
    else:
        data[250]["high"] = data[250]["low"] - 1
        write_bars(tmp_path, "ETH_USDT_SWAP", "15m", data)
    with pytest.raises(SourceError, match=message):
        make_source(tmp_path).load("ETH-USDT-SWAP", "15m", BASE_MS + 270 * PERIOD_MS + PERIOD_MS)


def test_missing_symbol_path_injection_and_start_outside_coverage_are_clear_errors(tmp_path):
    write_bars(tmp_path, "ETH_USDT_SWAP", "15m", bars(300))
    source = make_source(tmp_path)

    with pytest.raises(SourceError, match="没有找到"):
        source.load("../ETH-USDT-SWAP", "15m", BASE_MS + 270 * PERIOD_MS)
    with pytest.raises(SourceError, match="晚于本地行情覆盖范围"):
        source.load("ETH-USDT-SWAP", "15m", BASE_MS + 301 * PERIOD_MS)
