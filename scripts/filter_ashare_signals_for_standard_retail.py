"""Build a delivery-only A-share list for a basic Shanghai/Shenzhen account.

This post-processor never loads the detector or candle snapshot.  It only reads
the immutable 31-event ledger from the parent holdout use #8 and removes boards
that require an additional individual-investor trading permission:

* STAR Market: SSE rule 6.2 requires CNY 500k average assets and 24 months.
  https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml
* ChiNext: SZSE says a new individual applicant needs CNY 100k average assets
  and 24 months of securities-trading experience.
  https://investor.szse.cn/knowledge/t20200513_577026.html
* Beijing Stock Exchange: BSE rule 5 requires CNY 500k average assets and
  24 months, subject to its stated STAR-permission exception.
  https://www.bse.cn/jygl_list/200018386.html

The retained population is therefore a conservative *board-permission* view,
not proof that a specific account can place or fill an order.  Risk-warning
names are also excluded.  Suspension, price limits, broker controls, available
cash, lot size, and real-time order-book liquidity are outside this snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARENT_RESULTS = (
    ROOT
    / "experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results"
)
DEFAULT_OUTPUT = PARENT_RESULTS / "standard_retail_mainboard"
EXPECTED_SIGNALS_SHA256 = (
    "8d8db040ee634714257d03097fbd6add2c7699ea55df1d2804d52fb8495ec541"
)
EXPECTED_SUMMARY_SHA256 = (
    "d65578e6b678d4c526f4e6e5fb713c06ca34fcbb358fdb5889e54df411a25e20"
)
EXPECTED_PARENT_EVENTS = 31
EXPECTED_RETAINED_EVENTS = 18
EXPECTED_EXCLUDED_EVENTS = 13
STANDARD_BOARDS = frozenset({"SH_MAIN", "SZ_MAIN"})
OFFICIAL_ACCESS_SOURCES = {
    "STAR": "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml",
    "CHINEXT": "https://investor.szse.cn/knowledge/t20200513_577026.html",
    "BSE": "https://www.bse.cn/jygl_list/200018386.html",
}


class RetailFilterError(RuntimeError):
    """Raised when parent evidence or a derived delivery artifact drifts."""


def sha256_file(path: Path) -> str:
    """Return the byte identity of one local artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_code(value: object) -> str:
    """Normalize an A-share security code without losing leading zeroes."""

    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit():
        raise RetailFilterError(f"non-numeric A-share code: {value!r}")
    return text.zfill(6)


def classify_board(code: object, market: object) -> str:
    """Classify the exchange board from the frozen A-share identity columns."""

    normalized = normalize_code(code)
    market_id = int(market)
    if normalized.startswith(("920", "4", "8")):
        return "BSE"
    if normalized.startswith(("688", "689")):
        return "STAR"
    if normalized.startswith(("300", "301", "302")):
        return "CHINEXT"
    if market_id == 1 and normalized.startswith(("600", "601", "603", "605")):
        return "SH_MAIN"
    if market_id == 0 and normalized.startswith(("000", "001", "002", "003")):
        return "SZ_MAIN"
    return "UNKNOWN"


def restricted_name_reason(name: object) -> str:
    """Return a conservative name-based restriction reason, or an empty string."""

    compact = str(name).upper().replace(" ", "")
    if compact.startswith("PT"):
        return "particular_transfer_name"
    if compact.startswith(("ST", "*ST", "S*ST", "SST")):
        return "risk_warning_name"
    if "退" in compact:
        return "delisting_name"
    return ""


def annotate_access(rows: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic board/access fields without reading market outcomes."""

    required = {"code", "market", "name", "direction", "chart", "chart_sha256"}
    missing = required - set(rows.columns)
    if missing:
        raise RetailFilterError(f"parent signal ledger missing columns: {sorted(missing)}")
    annotated = rows.copy()
    annotated["code"] = annotated["code"].map(normalize_code)
    annotated.insert(0, "original_rank", np.arange(1, len(annotated) + 1))
    annotated["board"] = [
        classify_board(code, market)
        for code, market in zip(annotated["code"], annotated["market"])
    ]
    restrictions = annotated["name"].map(restricted_name_reason)
    annotated["retail_eligible"] = annotated["board"].isin(STANDARD_BOARDS) & restrictions.eq("")
    annotated["retail_exclusion_reason"] = np.where(
        restrictions.ne(""), restrictions, np.where(annotated["board"].isin(STANDARD_BOARDS), "", "extra_board_permission")
    )
    return annotated


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


def render_overview_page(
    chart_paths: Sequence[Path], events: Sequence[Mapping[str, Any]], page_number: int
) -> np.ndarray:
    """Render one deterministic 3x3 standard-retail contact sheet."""

    thumb_w, thumb_h = 620, 426
    sheet = np.full((3 * thumb_h + 82, 3 * thumb_w, 3), 240, dtype=np.uint8)
    _put_text(
        sheet,
        f"A-SHARE 15m | STANDARD RETAIL MAIN-BOARD VIEW | page {page_number}",
        (24, 34),
        scale=0.66,
        thickness=2,
    )
    _put_text(
        sheet,
        "SH/SZ main boards only; board-access filter, NOT validated trade signals",
        (24, 66),
        scale=0.48,
        color=(45, 45, 180),
        thickness=2,
    )
    for slot, (path, event) in enumerate(zip(chart_paths, events)):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise RetailFilterError(f"could not read source chart: {path}")
        thumb = cv2.resize(image, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        row, col = divmod(slot, 3)
        y, x = 82 + row * thumb_h, col * thumb_w
        sheet[y : y + thumb_h, x : x + thumb_w] = thumb
        label = (
            f"orig#{int(event['original_rank']):03d} {event['code']} "
            f"{event['direction']} {float(event['confidence']):.3f}"
        )
        cv2.rectangle(sheet, (x + 4, y + 4), (x + 350, y + 31), (250, 250, 250), -1)
        _put_text(sheet, label, (x + 10, y + 25), scale=0.50, thickness=2)
    return sheet


def build_overviews(
    source_results: Path,
    events: Sequence[Mapping[str, Any]],
    destination: Path,
) -> list[str]:
    """Build paged contact sheets without altering the parent charts."""

    pages: list[str] = []
    page_size = 9
    for page_number, start in enumerate(range(0, len(events), page_size), 1):
        subset = events[start : start + page_size]
        paths = [source_results / str(row["chart"]) for row in subset]
        sheet = render_overview_page(paths, subset, page_number)
        filename = f"overview_page_{page_number:02d}.png"
        if not cv2.imwrite(str(destination / filename), sheet, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            raise RetailFilterError(f"failed to write {filename}")
        pages.append(filename)
    shutil.copyfile(destination / pages[0], destination / "overview.png")
    return pages


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_zip(destination: Path) -> Path:
    """Create a deterministic archive of the filtered delivery files."""

    archive = destination / "standard_retail_mainboard_charts_18.zip"
    members = sorted(
        path
        for path in destination.rglob("*")
        if path.is_file() and path != archive and path.name != "verification.json"
    )
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for path in members:
            info = zipfile.ZipInfo(path.relative_to(destination).as_posix())
            info.date_time = (2026, 9, 2, 11, 30, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            handle.writestr(info, path.read_bytes())
    return archive


def load_parent(source_results: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    signals_path = source_results / "signals.csv"
    summary_path = source_results / "summary.json"
    if sha256_file(signals_path) != EXPECTED_SIGNALS_SHA256:
        raise RetailFilterError("parent signals.csv SHA drifted")
    if sha256_file(summary_path) != EXPECTED_SUMMARY_SHA256:
        raise RetailFilterError("parent summary.json SHA drifted")
    rows = pd.read_csv(signals_path, dtype={"code": "string"})
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if len(rows) != EXPECTED_PARENT_EVENTS or int(summary["semantic_events"]) != EXPECTED_PARENT_EVENTS:
        raise RetailFilterError("parent event count drifted")
    return rows, summary


def build(source_results: Path, output: Path) -> dict[str, Any]:
    """Create a non-destructive standard-retail derivative of the parent ledger."""

    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    rows, parent_summary = load_parent(source_results)
    annotated = annotate_access(rows)
    retained = annotated.loc[annotated["retail_eligible"]].copy().reset_index(drop=True)
    excluded = annotated.loc[~annotated["retail_eligible"]].copy().reset_index(drop=True)
    if len(retained) != EXPECTED_RETAINED_EVENTS or len(excluded) != EXPECTED_EXCLUDED_EVENTS:
        raise RetailFilterError(
            f"frozen filter count drifted: retained={len(retained)} excluded={len(excluded)}"
        )
    building = output.with_name(output.name + ".building")
    if building.exists():
        raise FileExistsError(f"stale building directory: {building}")
    chart_dir = building / "charts"
    chart_dir.mkdir(parents=True)
    retained_records = retained.to_dict(orient="records")
    for row in retained_records:
        source = source_results / str(row["chart"])
        if sha256_file(source) != str(row["chart_sha256"]):
            raise RetailFilterError(f"source chart SHA drifted: {row['event_id']}")
        destination = chart_dir / source.name
        shutil.copyfile(source, destination)
        if sha256_file(destination) != str(row["chart_sha256"]):
            raise RetailFilterError(f"copied chart SHA drifted: {row['event_id']}")
        row["filtered_chart"] = f"charts/{destination.name}"
    retained = pd.DataFrame(retained_records)
    retained.to_csv(building / "signals.csv", index=False)
    excluded.to_csv(building / "excluded.csv", index=False)
    pages = build_overviews(source_results, retained_records, building)
    board_counts = Counter(str(row["board"]) for row in retained_records)
    side_counts = Counter(str(row["direction"]) for row in retained_records)
    excluded_board_counts = Counter(str(row["board"]) for row in excluded.to_dict(orient="records"))
    summary = {
        "protocol": "ashare_standard_retail_mainboard_delivery_filter_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parent_experiment_id": parent_summary["experiment_id"],
        "parent_holdout_consumption_number": 8,
        "additional_holdout_consumption": False,
        "parent_signals_sha256": EXPECTED_SIGNALS_SHA256,
        "parent_summary_sha256": EXPECTED_SUMMARY_SHA256,
        "scope": "basic Shanghai/Shenzhen A-share account; no STAR, ChiNext or BSE permission assumed",
        "standard_boards": sorted(STANDARD_BOARDS),
        "official_access_sources": OFFICIAL_ACCESS_SOURCES,
        "source_events": len(annotated),
        "retained_events": len(retained),
        "excluded_events": len(excluded),
        "retained_long": int(side_counts["LONG"]),
        "retained_short": int(side_counts["SHORT"]),
        "retained_by_board": dict(sorted(board_counts.items())),
        "excluded_by_board": dict(sorted(excluded_board_counts.items())),
        "overview_pages": pages,
        "model_inference": False,
        "network_reads": 0,
        "threshold_or_weight_changed": False,
        "parent_results_changed": False,
        "production_eligible": False,
        "tradability_proven": False,
        "warnings": [
            "Board permission is not proof that a specific account may trade the security.",
            "Suspension, price limits, broker controls, cash, lot size and order-book fillability were not checked.",
            "SHORT remains a morphology class, not an executable cash-stock short order.",
        ],
    }
    write_json(building / "summary.json", summary)
    archive = build_zip(building)
    summary["chart_pack"] = archive.name
    summary["chart_pack_sha256"] = sha256_file(archive)
    write_json(building / "summary.json", summary)
    os.replace(building, output)
    return summary


def verify(source_results: Path, output: Path) -> dict[str, Any]:
    """Recompute classification, copied-chart and overview identities."""

    rows, _ = load_parent(source_results)
    expected = annotate_access(rows)
    expected_retained = expected.loc[expected["retail_eligible"]].reset_index(drop=True)
    saved = pd.read_csv(output / "signals.csv", dtype={"code": "string"})
    saved_excluded = pd.read_csv(output / "excluded.csv", dtype={"code": "string"})
    if saved["event_id"].tolist() != expected_retained["event_id"].tolist():
        raise RetailFilterError("retained event identities drifted")
    if len(saved) != EXPECTED_RETAINED_EVENTS or len(saved_excluded) != EXPECTED_EXCLUDED_EVENTS:
        raise RetailFilterError("saved filter counts drifted")
    records = saved.to_dict(orient="records")
    chart_checks = 0
    for row in records:
        source = source_results / str(row["chart"])
        copy = output / str(row["filtered_chart"])
        expected_hash = str(row["chart_sha256"])
        if sha256_file(source) != expected_hash or sha256_file(copy) != expected_hash:
            raise RetailFilterError(f"filtered chart identity failed: {row['event_id']}")
        chart_checks += 1
    overview_checks = 0
    for page_number, start in enumerate(range(0, len(records), 9), 1):
        subset = records[start : start + 9]
        paths = [source_results / str(row["chart"]) for row in subset]
        expected_image = render_overview_page(paths, subset, page_number)
        saved_image = cv2.imread(str(output / f"overview_page_{page_number:02d}.png"))
        if saved_image is None or not np.array_equal(saved_image, expected_image):
            raise RetailFilterError(f"overview pixels drifted: page {page_number}")
        overview_checks += 1
    archive = output / "standard_retail_mainboard_charts_18.zip"
    with zipfile.ZipFile(archive) as handle:
        bad = handle.testzip()
        if bad is not None:
            raise RetailFilterError(f"chart pack CRC failed: {bad}")
    receipt = {
        "protocol": "ashare_standard_retail_mainboard_delivery_filter_verify_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "retained_events": len(saved),
        "excluded_events": len(saved_excluded),
        "chart_sha_checks": chart_checks,
        "overview_pixel_checks": overview_checks,
        "chart_pack_sha256": sha256_file(archive),
        "model_inference": 0,
        "network_reads": 0,
        "passed": True,
    }
    write_json(output / "verification.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--build", action="store_true")
    phase.add_argument("--verify", action="store_true")
    parser.add_argument("--source-results", type=Path, default=PARENT_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.source_results, args.out) if args.build else verify(args.source_results, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
