#!/usr/bin/env python3
"""Index the owner's selected chart sources and link existing replay evidence.

This builder does not rescan the market or reconstruct existing 12H replays.
It joins the preserved 15-image source ledger to its existing OKX/Binance and
509-signal reconciliation tables, adds the separately named IOST and PEPE
references, and records the existing ETH manual-arrow ledger as a distinct
source. All generated files stay beside this script.
"""
from __future__ import annotations

import csv
import json
import math
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
ALT = ROOT / "data/research/alt-isolated-12h-wicks-20261111"
SOURCE_IMAGES = ALT / "owner_reference/原图15张_首次触轨先后顺序.csv"
OKX_HOURS = ALT / "owner_reference/okx_hours.csv.gz"
VENUE_COMPARE = ALT / "expanded12/owner_reference/原图15张_OKX与币安逐张对照.csv"
SIGNAL_COMPARE = ALT / "results12/原图15张_与509条及入场信号逐张对照.csv"
BLIND_REVEAL = ROOT / "experiments/active/exp-month-firstweek-blind-review-20261111-v1/revealed.csv"
IOST_REFERENCE = ROOT / "experiments/active/exp-alt-isolated-12h-firstweek-wicks-20261111-v1/reference_okx_iost.csv"
IOST_H1 = ROOT / "experiments/active/exp-cloud-allmarket-screen-20261108-v1/inputs/IOST/bars1h.csv.gz"
PEPE_15M = ROOT / "data/kline_deep/okx_PEPE_USDT_SWAP_15m_111988.csv"
ETH_ARROWS = ROOT / "data/chart_annotations/eth-month-first-week-20261110/arrow_ledger.csv"
ETH_TOUCHES = ROOT / "data/chart_annotations/eth-month-first-week-20261110/all_39_first_touches.csv"
ETH_H1 = ROOT / "experiments/active/exp-month-first-week-touches-20261102-v1/inputs_ETH_1h.csv.gz"
ETH_EXISTING_EVENTS = ROOT / "experiments/active/exp-month-first-week-touches-20261102-v1/events.csv"


def read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig")


def as_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def bjt_ms(value: str) -> int:
    ts = pd.Timestamp(value, tz="Asia/Shanghai").tz_convert("UTC")
    return int(ts.value // 1_000_000)


def ms_utc(ms: int | float | str) -> str:
    return pd.to_datetime(int(float(ms)), unit="ms", utc=True).isoformat()


def clean_symbol(value: str) -> str:
    return value.upper().removesuffix("USDT").removesuffix("-USDT-SWAP")


def side_from_blind(value: str) -> str:
    return "lower" if value.lower() == "long" else "upper"


def photo_name(value: str) -> str:
    return f"2026-11-11 {value}.jpg"


def photo_rows() -> tuple[list[dict], dict, dict]:
    raw = read_csv(SOURCE_IMAGES)
    pair = read_csv(VENUE_COMPARE)
    signal = read_csv(SIGNAL_COMPARE)
    blind = read_csv(BLIND_REVEAL)
    pair_by_photo = {as_text(r["图片"]): r for r in pair.to_dict("records")}
    signal_by_photo = {as_text(r["原图"]): r for r in signal.to_dict("records")}
    blind_by_key: dict[tuple[str, str, str], list[dict]] = {}
    for r in blind.to_dict("records"):
        key = (clean_symbol(as_text(r.get("symbol"))), as_text(r.get("month")), side_from_blind(as_text(r.get("side"))))
        if as_text(r.get("group")) == "hand_selected":
            blind_by_key.setdefault(key, []).append(r)

    out: list[dict] = []
    photo_occurrence: dict[str, int] = {}
    for ordinal, r in enumerate(raw.to_dict("records"), start=1):
        image_time = as_text(r["原图文件时间"])
        image = photo_name(image_time)
        photo_occurrence[image] = photo_occurrence.get(image, 0) + 1
        symbol = clean_symbol(as_text(r["OKX合约简称"]))
        month = as_text(r["纽约区间月"])
        side = "upper" if as_text(r["轨道"]) == "上" else "lower"
        touch = bjt_ms(as_text(r["触轨小时（北京）"]))
        pair_row = pair_by_photo.get(image, {})
        signal_row = signal_by_photo.get(image, {})
        blind_matches = blind_by_key.get((symbol, month, side), [])
        # Selection table has one SSV photo representing both its first touch and
        # a separately selected Sep 27 retouch. The latter remains a distinct row.
        repeated = photo_occurrence[image] > 1
        case_id = f"photo_{ordinal:02d}" + ("_retouch" if repeated else "")
        okx_open_text = as_text(pair_row.get("OKX_K线开盘（北京）"))
        first_open = okx_open_text.split(",")[0].strip() if okx_open_text else ""
        out.append({
            "case_id": case_id,
            "source_group": "owner_15_image" if not repeated else "owner_15_image_same_photo_second_touch",
            "image_ref": image,
            "image_bytes_in_workspace": False,
            "okx_instrument": f"{symbol}-USDT-SWAP",
            "month_ny": month,
            "side": side,
            "touch_beijing": as_text(r["触轨小时（北京）"]),
            "touch_utc": ms_utc(touch),
            "touch_ms": touch,
            "rail_price_okx": as_text(r["轨价"]),
            "source_selection_note": as_text(r["起算轮次"]),
            "blind_review_match": ";".join(as_text(x.get("blind_id")) for x in blind_matches),
            "blind_review_call": ";".join(as_text(x.get("call")) for x in blind_matches),
            "blind_review_reason": " | ".join(as_text(x.get("首碰盲判依据")) for x in blind_matches),
            "okx_binance_pair_status": "paired" if as_text(pair_row.get("币安_轨价")) else "OKX_only_no_Binance_counterpart_in_saved_table",
            "okx_first_12h_open_beijing": first_open,
            "binance_rail_price": as_text(pair_row.get("币安_轨价")),
            "binance_first_touch_beijing": as_text(pair_row.get("币安_首次触轨小时（北京）")),
            "existing_signal_reconciliation": as_text(signal_row.get("核对说明")),
            "existing_signal_case_status": as_text(signal_row.get("币安月度首触池判定")),
            "existing_source_table": str(SOURCE_IMAGES.relative_to(ROOT)),
            "existing_venue_pair_table": str(VENUE_COMPARE.relative_to(ROOT)),
            "existing_signal_table": str(SIGNAL_COMPARE.relative_to(ROOT)),
            "existing_h1_source": str(OKX_HOURS.relative_to(ROOT)),
        })
    return out, pair_by_photo, blind_by_key


def reference_iost() -> dict:
    ref = read_csv(IOST_REFERENCE)
    hits = ref[(ref["symbol"] == "IOST-USDT-SWAP") & (ref["month"] == "2026-10") & (ref["side"] == "lower")]
    if len(hits) != 1:
        raise AssertionError(f"Expected one IOST October reference row, found {len(hits)}")
    r = hits.iloc[0].to_dict()
    h1 = read_csv(IOST_H1)
    h1["time"] = pd.to_numeric(h1["time"], errors="raise").astype("int64")
    start = int(r["open_ms"])
    block = h1[(h1.time >= start) & (h1.time < start + 12 * 3_600_000)].sort_values("time")
    expected = list(range(start, start + 12 * 3_600_000, 3_600_000))
    if block.time.tolist() != expected:
        raise AssertionError(f"IOST source does not have a complete H1 block at {start}")
    aggregate = {
        "open": float(block.iloc[0]["open"]),
        "high": float(block.high.max()),
        "low": float(block.low.min()),
        "close": float(block.iloc[-1]["close"]),
    }
    source_bar = {k: float(r[k]) for k in aggregate}
    if any(abs(aggregate[k] - source_bar[k]) > max(abs(source_bar[k]) * 1e-10, 1e-12) for k in aggregate):
        raise AssertionError({"iost_h1_aggregate": aggregate, "reference_12h": source_bar})
    return {
        "case_id": "named_iost_okx_2026_10",
        "source_group": "named_user_image_outside_the_15",
        "image_ref": "user conversation: IOST OKX 12H lower-1.618, October 2026",
        "image_bytes_in_workspace": False,
        "instrument": as_text(r["symbol"]),
        "venue": as_text(r["exchange"]),
        "month_ny": as_text(r["month"]),
        "side": as_text(r["side"]),
        "rail_price": float(r["level"]),
        "touch_ms": int(r["touch_ms"]),
        "touch_utc": ms_utc(r["touch_ms"]),
        "bar_open_ms": start,
        "bar_close_ms": int(r["close_ms"]),
        "bar_ohlc": aggregate,
        "twelve_hour_rebuild_matches_reference": True,
        "overlaps_15_original_images": False,
        "reference_row_path": str(IOST_REFERENCE.relative_to(ROOT)),
        "hourly_source_path": str(IOST_H1.relative_to(ROOT)),
    }


def bounds_ny(month: str) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    first = pd.Timestamp(month + "-01")
    monday = first + pd.Timedelta(days=(-first.weekday()) % 7)
    ny = "America/New_York"
    return tuple(x.tz_localize(ny).tz_convert("UTC") for x in (monday, monday + pd.Timedelta(days=7), first + pd.offsets.MonthBegin(1)))


def reference_pepe(blind_by_key: dict) -> dict:
    raw = read_csv(PEPE_15M)
    raw["ts"] = pd.to_numeric(raw["ts"], errors="raise").astype("int64")
    raw = raw.sort_values("ts").drop_duplicates("ts", keep="last")
    month = "2025-06"
    start, ready, expiry = bounds_ny(month)
    start_ms, ready_ms, expiry_ms = (int(x.value // 1_000_000) for x in (start, ready, expiry))
    anchor = raw[(raw.ts >= start_ms) & (raw.ts < ready_ms)]
    expected = list(range(start_ms, ready_ms, 15 * 60_000))
    if anchor.ts.tolist() != expected:
        raise AssertionError("PEPE OKX NY first-week 15m anchor is incomplete")
    high, low = float(anchor.high.max()), float(anchor.low.min())
    lower = low - 0.618 * (high - low)
    active = raw[(raw.ts >= ready_ms) & (raw.ts < expiry_ms)]
    touched = active[active.low <= lower]
    if touched.empty:
        raise AssertionError("No June 2025 OKX PEPE lower-rail touch in the saved source")
    first = touched.iloc[0]
    touch_ms = int(first.ts)
    bar_start = touch_ms // (12 * 3_600_000) * (12 * 3_600_000)
    block = raw[(raw.ts >= bar_start) & (raw.ts < bar_start + 12 * 3_600_000)]
    expected_12h = list(range(bar_start, bar_start + 12 * 3_600_000, 15 * 60_000))
    if block.ts.tolist() != expected_12h:
        raise AssertionError("PEPE OKX 12H trigger candle is incomplete")
    o, h, l, c = float(block.iloc[0]["open"]), float(block.high.max()), float(block.low.min()), float(block.iloc[-1]["close"])
    wick = min(o, c) - l
    wick_pct = 100 * wick / (h - l) if h > l else 0.0
    binance_key = ("1000PEPE", month, "lower")
    matches = blind_by_key.get(binance_key, [])
    binance_rail = float(matches[0]["rail"]) if matches else None
    return {
        "case_id": "named_pepe_okx_2025_06",
        "source_group": "separate_PEPE_underlying_reference",
        "image_ref": "same underlying as the separate June 2025 1000PEPE Binance hand-selected case; different OKX contract",
        "image_bytes_in_workspace": False,
        "instrument": "PEPE-USDT-SWAP",
        "venue": "OKX",
        "month_ny": month,
        "side": "lower",
        "rail_price": lower,
        "anchor_high": high,
        "anchor_low": low,
        "touch_ms_15m_open": touch_ms,
        "touch_utc": ms_utc(touch_ms),
        "touch_beijing": pd.to_datetime(touch_ms, unit="ms", utc=True).tz_convert("Asia/Shanghai").isoformat(),
        "twelve_hour_bar_open_ms_utc": bar_start,
        "twelve_hour_bar_close_ms_utc": bar_start + 12 * 3_600_000,
        "twelve_hour_ohlc": {"open": o, "high": h, "low": l, "close": c},
        "touch_wick_share_pct": wick_pct,
        "binance_hand_selected_contract": "1000PEPEUSDT" if matches else "",
        "binance_rail_price_original_units": binance_rail,
        "binance_rail_converted_to_pepe_units": binance_rail / 1000 if binance_rail else None,
        "rail_gap_vs_binance_percent_after_unit_conversion": (lower / (binance_rail / 1000) - 1) * 100 if binance_rail else None,
        "overlaps_15_original_images": False,
        "source_path": str(PEPE_15M.relative_to(ROOT)),
        "source_bar_minutes": 15,
        "anchor_rule": "complete NY first Monday 00:00 to next Monday 00:00, June 2025",
    }


def flat_reference(row: dict) -> dict:
    return {
        "case_id": row["case_id"],
        "source_group": row["source_group"],
        "venue": row["venue"],
        "instrument": row["instrument"],
        "month_ny": row["month_ny"],
        "side": row["side"],
        "rail_price": row["rail_price"],
        "touch_utc": row["touch_utc"],
        "image_bytes_in_workspace": row["image_bytes_in_workspace"],
        "overlaps_15_original_images": row["overlaps_15_original_images"],
        "source_path": row.get("reference_row_path", row.get("source_path", "")),
        "hourly_source_path": row.get("hourly_source_path", ""),
        "detail_json": json.dumps(row, ensure_ascii=False, allow_nan=False),
    }


def main() -> None:
    cases, pair_by_photo, blind_by_key = photo_rows()
    unique_photos = {r["image_ref"] for r in cases}
    if len(unique_photos) != 15:
        raise AssertionError(f"Expected 15 unique source images, found {len(unique_photos)}")
    if len(cases) != 16:
        raise AssertionError(f"Expected 16 selected touch rows including the SSV retouch, found {len(cases)}")
    cases_df = pd.DataFrame(cases)
    cases_df.to_csv(OUT / "selected_source_map.csv", index=False, encoding="utf-8-sig")

    iost = reference_iost()
    pepe = reference_pepe(blind_by_key)
    pd.DataFrame([flat_reference(iost), flat_reference(pepe)]).to_csv(
        OUT / "named_reference_checks.csv", index=False, encoding="utf-8-sig"
    )
    (OUT / "named_reference_checks.json").write_text(json.dumps({"iost": iost, "pepe": pepe}, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    arrows = read_csv(ETH_ARROWS)
    all_touches = read_csv(ETH_TOUCHES)
    eth_events = read_csv(ETH_EXISTING_EVENTS)
    has_manual = all_touches["has_manual_arrow"].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
    eth_notes = {
        "source_group": "ETH manual arrows, separate from the 15 screenshot set",
        "manual_arrow_rows": len(arrows),
        "touch_ledger_rows": len(all_touches),
        "manual_arrow_rows_marked_in_touch_ledger": int(has_manual.sum()),
        "existing_hourly_source_rows": len(read_csv(ETH_H1)),
        "existing_hourly_event_rows": int((eth_events["symbol"] == "ETH").sum()),
        "manual_arrow_ledger": str(ETH_ARROWS.relative_to(ROOT)),
        "full_first_touch_ledger": str(ETH_TOUCHES.relative_to(ROOT)),
        "existing_hourly_input": str(ETH_H1.relative_to(ROOT)),
        "existing_per_event_summary": str((ROOT / "experiments/active/exp-month-first-week-touches-20261102-v1/events.csv").relative_to(ROOT)),
        "reuse_note": "The arrow ledger and existing hourly inputs/outcomes are linked; no duplicate ETH replay was generated.",
    }
    image_names = sorted(unique_photos)
    source_receipt = {
        "generated_by_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip(),
        "selected_unique_images": len(unique_photos),
        "selected_touch_rows": len(cases),
        "ssv_same_image_multiple_touches": 1,
        "original_image_bytes_found_in_repository": False,
        "image_file_references": image_names,
        "original_image_bytes_note": "The source ledger preserves photo timestamps and sample metadata; original JPG bytes were not found in the workspace.",
        "owner_15_venue_pairs_with_binance_counterpart": int(cases_df.drop_duplicates("image_ref")["okx_binance_pair_status"].eq("paired").sum()),
        "owner_15_okx_only_unique_images": int(cases_df.drop_duplicates("image_ref")["okx_binance_pair_status"].ne("paired").sum()),
        "blind_hand_selected_unique_keys_matched": int(cases_df.loc[cases_df["blind_review_match"].ne("")].drop_duplicates(["okx_instrument", "month_ny", "side"]).shape[0]),
        "named_references": ["IOST-USDT-SWAP 2026-10 OKX 12H lower", "PEPE-USDT-SWAP 2025-06 OKX lower, compared with 1000PEPE Binance sample after unit normalization"],
        "iost_reference": iost,
        "pepe_reference": pepe,
        "eth_arrow_source": eth_notes,
        "input_sources": [str(p.relative_to(ROOT)) for p in [SOURCE_IMAGES, OKX_HOURS, VENUE_COMPARE, SIGNAL_COMPARE, BLIND_REVEAL, IOST_REFERENCE, IOST_H1, PEPE_15M, ETH_ARROWS, ETH_TOUCHES, ETH_H1, ETH_EXISTING_EVENTS]],
        "source_bytes_mtime": {str(p.relative_to(ROOT)): {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in [SOURCE_IMAGES, OKX_HOURS, VENUE_COMPARE, SIGNAL_COMPARE, BLIND_REVEAL, IOST_REFERENCE, IOST_H1, PEPE_15M, ETH_ARROWS, ETH_TOUCHES, ETH_H1, ETH_EXISTING_EVENTS]},
    }
    (OUT / "source_receipt.json").write_text(json.dumps(source_receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"unique_images": len(unique_photos), "selected_touch_rows": len(cases), "paired_binance_images": source_receipt["owner_15_venue_pairs_with_binance_counterpart"], "okx_only_unique_images": source_receipt["owner_15_okx_only_unique_images"], "blind_hand_selected_unique_keys_matched": source_receipt["blind_hand_selected_unique_keys_matched"], "iost_12h_rebuild_matches": iost["twelve_hour_rebuild_matches_reference"], "pepe_okx_touch_bjt": pepe["touch_beijing"], "eth_manual_arrow_rows_linked": eth_notes["manual_arrow_rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
