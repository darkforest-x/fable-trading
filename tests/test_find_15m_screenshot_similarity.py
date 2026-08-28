"""Contract tests for screenshot-derived pre-holdout similarity retrieval."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from scripts.find_15m_screenshot_similarity import (
    HOLDOUT_MS,
    ROOT,
    extract_query_contract,
    multivariate_dtw_distance,
    normalize_window,
    read_preholdout_csv,
    score_features,
)


PREREG = (
    ROOT
    / "experiments/active/exp-15m-right-edge-screenshot-similarity-v1"
    / "preregistration.json"
)
REFERENCE = PREREG.parent / "reference/owner_reference.png"


def load_prereg() -> dict:
    """Load the committed frozen configuration."""

    return json.loads(PREREG.read_text(encoding="utf-8"))


def test_reference_extracts_frozen_right_edge_contract() -> None:
    """The copied Owner screenshot must retain its exact candle geometry."""

    prereg = load_prereg()
    query = extract_query_contract(REFERENCE, prereg["reference"])

    assert query["reference_sha256"] == prereg["reference"]["sha256"]
    assert query["color_sequence"] == "GRRGGRRGGRRGRG"
    assert len(query["candles"]) == 14
    assert query["volume_trusted"] == [True] * 12 + [False, False]

    ohlc = np.asarray(query["pseudo_ohlc"], dtype=float)
    # The right edge contains two red breakdown bars; the second has a lower
    # wick longer than its body, which is the distinctive screenshot feature.
    assert ohlc[9, 3] < ohlc[9, 0]
    assert ohlc[10, 3] < ohlc[10, 0]
    lower_wick = min(ohlc[10, 0], ohlc[10, 3]) - ohlc[10, 2]
    body = abs(ohlc[10, 3] - ohlc[10, 0])
    assert lower_wick > body


def test_identity_score_and_dtw_are_zero() -> None:
    """The frozen scoring surface must map a query to itself exactly."""

    prereg = load_prereg()
    query = extract_query_contract(REFERENCE, prereg["reference"])
    ohlc = np.asarray(query["pseudo_ohlc"], dtype=float)
    volume = np.asarray(query["volume_pixels"], dtype=float)
    trusted = np.asarray(query["volume_trusted"], dtype=bool)
    price, log_volume, _, _ = normalize_window(
        ohlc,
        volume,
        prelude_bars=prereg["similarity"]["prelude_bars"],
    )

    score = score_features(
        price,
        log_volume,
        reference_price=price,
        reference_volume=log_volume,
        config=prereg["similarity"],
        volume_trusted=trusted,
        include_dtw=True,
    )

    assert score["lock_distance"] == 0.0
    assert score["price_dtw_distance"] == 0.0
    assert score["distance"] == 0.0
    assert multivariate_dtw_distance(price, price, radius=1) == 0.0


def test_reader_stops_before_parsing_holdout_ohlcv(tmp_path: Path) -> None:
    """An invalid boundary-row OHLCV value must remain unparsed and harmless."""

    path = tmp_path / "okx_TEST_USDT_SWAP_15m_3.csv"
    path.write_text(
        "ts,open,high,low,close,volume,open_time\n"
        f"{HOLDOUT_MS - 1800000},10,11,9,10.5,100,pre1\n"
        f"{HOLDOUT_MS - 900000},10.5,11,10,10.8,120,pre2\n"
        f"{HOLDOUT_MS},THIS,MUST,NOT,BE,PARSED,holdout\n",
        encoding="utf-8",
    )

    series = read_preholdout_csv(path)

    assert len(series.times) == 2
    assert int(series.times.max()) < HOLDOUT_MS
    assert series.source_audit["holdout_ohlcv_rows_read"] == 0
    assert series.source_audit["stopped_at_boundary"] is True
    assert series.source_audit["boundary_timestamp_metadata_seen"].startswith(
        "2026-05-04T00:00:00"
    )
