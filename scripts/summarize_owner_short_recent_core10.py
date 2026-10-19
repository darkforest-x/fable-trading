#!/usr/bin/env python3
"""Summarize two causal Owner-short scans on one frozen recent Core-10 snapshot.

This is a detection-density report, not a trading backtest. It reads only scan
artifacts plus each snapshot's ``open_time`` column to recover exact endpoint
denominators. It never resolves future outcomes, tunes a confidence threshold,
changes ACTIVE, or places orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.compare_owner_short_canary import cross_model_event_overlap, distribution
from src.scout_mtf.rank import CORE_MAJORS


CORE_SYMBOLS = tuple(f"{base}_USDT_SWAP" for base in CORE_MAJORS)
BASELINE_SHA256 = "da278820f2d96a64006d9ff6358b7c98faec52249ec8a6f4fe6bf55254fc65b4"
HARDNEG_SHA256 = "029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537"
LATER_R2_SHA256 = "52cd38fda253f052c3c8eb712d93557c0125dceb336fb4cd58136236dca32afe"
CONTRACT_FIELDS = (
    "protocol",
    "symbols",
    "scanned_symbols",
    "stale_symbols",
    "latest_bar",
    "replay_start_exclusive",
    "hours",
    "window_lengths",
    "confidence",
    "nms_iou",
    "nms_device",
    "nms_implementation",
    "nms_max_time_img",
    "event_gap_bars",
    "bar_endpoints",
    "window_exposures",
    "evaluation_scope",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_contract(
    baseline: dict[str, Any],
    hardneg: dict[str, Any],
    *,
    baseline_holdout_use: int,
    hardneg_holdout_use: int,
) -> dict[str, Any]:
    mismatches = {
        field: {"baseline": baseline.get(field), "hardneg": hardneg.get(field)}
        for field in CONTRACT_FIELDS
        if baseline.get(field) != hardneg.get(field)
    }
    if mismatches:
        raise ValueError(f"scan contract mismatch: {json.dumps(mismatches, ensure_ascii=False)}")
    for name, summary, expected_sha, expected_use in (
        ("baseline", baseline, BASELINE_SHA256, baseline_holdout_use),
        ("hardneg", hardneg, HARDNEG_SHA256, hardneg_holdout_use),
    ):
        if summary.get("weights_sha256") != expected_sha:
            raise ValueError(f"{name} weight SHA drift")
        if summary.get("evaluation_scope") != "holdout":
            raise ValueError(f"{name} is not recorded as holdout")
        if int(summary.get("holdout_use_number", -1)) != expected_use:
            raise ValueError(f"{name} holdout use number drift")
        if summary.get("owner_authorized_in_conversation") is not True:
            raise ValueError(f"{name} lacks owner authorization receipt")
        if summary.get("promoted") or summary.get("orders_placed"):
            raise ValueError(f"{name} scan changed production state")
        if summary.get("stale_symbols"):
            raise ValueError(f"{name} contains stale symbols")
    if tuple(baseline.get("scanned_symbols", ())) != tuple(sorted(CORE_SYMBOLS)):
        raise ValueError("scan universe is not the frozen Core-10 set")
    return {field: baseline.get(field) for field in CONTRACT_FIELDS}


def endpoint_counts(
    snapshot_dir: Path,
    *,
    symbols: tuple[str, ...],
    start_exclusive: object,
    end_inclusive: object,
    window_max: int,
) -> dict[str, int]:
    start = pd.Timestamp(start_exclusive)
    end = pd.Timestamp(end_inclusive)
    counts: dict[str, int] = {}
    for symbol in symbols:
        path = snapshot_dir / f"{symbol}.csv"
        frame = pd.read_csv(path, usecols=["open_time"])
        times = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
        counts[symbol] = sum(
            start < value <= end and index >= 119 + int(window_max)
            for index, value in enumerate(times)
        )
    return counts


def validate_snapshot(
    snapshot_summary: dict[str, Any],
    snapshot_summary_path: Path,
    baseline_summary: dict[str, Any],
    hardneg_summary: dict[str, Any],
) -> Path:
    """Rebind both scans to the exact CSV bytes registered by the fetch."""
    rows = snapshot_summary.get("symbols", [])
    usable = {
        str(row["symbol"]): row
        for row in rows
        if int(row.get("rows", 0)) > 0 and not row.get("error")
    }
    if set(usable) != set(CORE_SYMBOLS):
        raise ValueError("snapshot is not the frozen Core-10 set")
    if int(snapshot_summary.get("requested_symbols", -1)) != len(CORE_SYMBOLS):
        raise ValueError("snapshot requested-symbol count drift")
    if int(snapshot_summary.get("usable_symbols", -1)) != len(CORE_SYMBOLS):
        raise ValueError("snapshot usable-symbol count drift")
    if snapshot_summary.get("canonical_data_written") is not False:
        raise ValueError("scan snapshot unexpectedly wrote canonical data")

    expected_dir = (snapshot_summary_path.parent / "kline_snapshot").resolve()
    baseline_dir = Path(str(baseline_summary["snapshot_dir"])).resolve()
    hardneg_dir = Path(str(hardneg_summary["snapshot_dir"])).resolve()
    if baseline_dir != expected_dir or hardneg_dir != expected_dir:
        raise ValueError("the two scans are not bound to the registered snapshot directory")
    for symbol, row in usable.items():
        path = expected_dir / f"{symbol}.csv"
        if not path.is_file():
            raise ValueError(f"missing snapshot CSV: {symbol}")
        if sha256_file(path) != str(row.get("sha256")):
            raise ValueError(f"snapshot CSV SHA drift: {symbol}")
    return expected_dir


def validate_nms_parity(root: Path) -> dict[str, Any]:
    """Require an artifact-backed parity receipt for the CPU-NMS runtime fix."""
    native_dir = root / "parity_fp32_eth_20260811"
    cpu_dir = root / "parity_cpu_torchvision_nms60_v2_b32_eth_20260811"
    native = read_json(native_dir / "scan_summary.json")
    cpu = read_json(cpu_dir / "scan_summary.json")
    parity_fields = (
        "weights_sha256",
        "snapshot_dir",
        "scanned_symbols",
        "latest_bar",
        "replay_start_exclusive",
        "hours",
        "window_lengths",
        "confidence",
        "nms_iou",
        "event_gap_bars",
        "bar_endpoints",
        "window_exposures",
        "raw_detections",
        "deduplicated_events",
    )
    mismatches = {
        field: {"native": native.get(field), "cpu": cpu.get(field)}
        for field in parity_fields
        if native.get(field) != cpu.get(field)
    }
    if mismatches:
        raise ValueError(f"NMS parity contract mismatch: {mismatches}")
    if native.get("weights_sha256") != BASELINE_SHA256:
        raise ValueError("NMS parity used the wrong baseline weight")
    if (
        cpu.get("nms_device") != "cpu"
        or cpu.get("nms_implementation") != "torchvision"
        or float(cpu.get("nms_max_time_img", 0)) != 60.0
    ):
        raise ValueError("CPU-NMS parity did not disable the truncating time budget")
    hashes: dict[str, str] = {}
    for name in ("raw_detections.jsonl", "events.jsonl"):
        native_sha = sha256_file(native_dir / name)
        cpu_sha = sha256_file(cpu_dir / name)
        if native_sha != cpu_sha:
            raise ValueError(f"CPU-NMS changed {name}")
        hashes[name] = native_sha
    return {
        "status": "byte_identical",
        "snapshot": "ETH 2026-08-09T06:30Z..2026-08-11T06:30Z",
        "window_exposures": native["window_exposures"],
        "raw_detections": native["raw_detections"],
        "deduplicated_events": native["deduplicated_events"],
        "artifact_sha256": hashes,
        "native_dir": str(native_dir),
        "cpu_dir": str(cpu_dir),
    }


def scan_artifact_receipt(directory: Path, expected_events: int) -> dict[str, Any]:
    """Bind the comparison to complete, immutable scan artifact bytes."""
    success = read_json(directory / "_SUCCESS.json")
    if success.get("ok") is not True or int(success.get("events", -1)) != expected_events:
        raise ValueError(f"scan success receipt mismatch: {directory}")
    names = ("scan_summary.json", "raw_detections.jsonl", "events.jsonl", "_SUCCESS.json")
    return {
        "directory": str(directory),
        "sha256": {name: sha256_file(directory / name) for name in names},
    }


def model_metrics(
    summary: dict[str, Any],
    raw: list[dict[str, Any]],
    events: list[dict[str, Any]],
    endpoints: dict[str, int],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    raw_counts = Counter(str(row["symbol"]) for row in raw)
    event_counts = Counter(str(row["symbol"]) for row in events)
    by_symbol_events: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in CORE_SYMBOLS}
    for row in events:
        by_symbol_events[str(row["symbol"])].append(row)
    windows = len(summary["window_lengths"])
    hours = float(summary["hours"])
    total_endpoints = sum(endpoints.values())
    total_exposures = total_endpoints * windows
    if total_endpoints != int(summary["bar_endpoints"]):
        raise ValueError("snapshot endpoint count does not match scan summary")
    if total_exposures != int(summary["window_exposures"]):
        raise ValueError("snapshot exposure count does not match scan summary")
    if len(raw) != int(summary["raw_detections"]):
        raise ValueError("raw detection file/count mismatch")
    if len(events) != int(summary["deduplicated_events"]):
        raise ValueError("event file/count mismatch")
    if any("outcome" in row or "net_taker" in row for row in events):
        raise ValueError("descriptive scan unexpectedly contains future outcomes")

    per_symbol: dict[str, dict[str, Any]] = {}
    for symbol in CORE_SYMBOLS:
        symbol_events = by_symbol_events[symbol]
        n_endpoints = endpoints[symbol]
        n_exposures = n_endpoints * windows
        per_symbol[symbol] = {
            "bar_endpoints": n_endpoints,
            "window_exposures": n_exposures,
            "raw_detections": raw_counts[symbol],
            "raw_per_1000_exposures": raw_counts[symbol] / n_exposures * 1000,
            "events": event_counts[symbol],
            "events_per_1000_endpoints": event_counts[symbol] / n_endpoints * 1000,
            "events_per_day": event_counts[symbol] / hours * 24,
            "first_confidence": distribution(float(row["conf"]) for row in symbol_events),
            "peak_confidence": distribution(float(row["event_conf_max"]) for row in symbol_events),
        }
    # This distribution is over the frozen universe, so zero-event symbols
    # must remain in the denominator rather than disappearing as if unscanned.
    event_values = [event_counts[symbol] for symbol in CORE_SYMBOLS]
    overall = {
        "weights_sha256": summary["weights_sha256"],
        "raw_detections": len(raw),
        "raw_per_1000_exposures": len(raw) / total_exposures * 1000,
        "events": len(events),
        "events_per_1000_endpoints": len(events) / total_endpoints * 1000,
        "events_per_day": len(events) / hours * 24,
        "triggered_symbols": len(event_counts),
        "zero_event_symbols": [symbol for symbol in CORE_SYMBOLS if not event_counts[symbol]],
        "events_per_symbol": distribution(event_values),
        "first_confidence": distribution(float(row["conf"]) for row in events),
        "peak_confidence": distribution(float(row["event_conf_max"]) for row in events),
        "core_width_4_to_7_share": (
            sum(4 <= int(row["predicted_core_bars"]) <= 7 for row in events) / len(events)
            if events
            else None
        ),
        "decision_delay_3_to_5_share": (
            sum(3 <= int(row["decision_delay_bars"]) <= 5 for row in events) / len(events)
            if events
            else None
        ),
        "latest_events": [
            {
                "symbol": str(row["symbol"]),
                "decision_time": str(row["decision_time"]),
                "core_mid_time": str(row["core_mid_time"]),
                "peak_confidence": float(row["event_conf_max"]),
                "predicted_core_bars": int(row["predicted_core_bars"]),
                "decision_delay_bars": int(row["decision_delay_bars"]),
            }
            for row in sorted(
                events,
                key=lambda item: (str(item["decision_time"]), str(item["symbol"])),
                reverse=True,
            )[:10]
        ],
    }
    return overall, per_symbol


def relative_change(new: float, old: float) -> float | None:
    return (new - old) / old if old else None


def build_summary(
    baseline_dir: Path,
    hardneg_dir: Path,
    snapshot_summary_path: Path,
    *,
    baseline_holdout_use: int = 2,
    hardneg_holdout_use: int = 1,
) -> dict[str, Any]:
    baseline_summary = read_json(baseline_dir / "scan_summary.json")
    hardneg_summary = read_json(hardneg_dir / "scan_summary.json")
    contract = validate_contract(
        baseline_summary,
        hardneg_summary,
        baseline_holdout_use=baseline_holdout_use,
        hardneg_holdout_use=hardneg_holdout_use,
    )
    snapshot_summary = read_json(snapshot_summary_path)
    snapshot_dir = validate_snapshot(
        snapshot_summary,
        snapshot_summary_path,
        baseline_summary,
        hardneg_summary,
    )
    nms_parity = validate_nms_parity(snapshot_summary_path.parent)
    endpoints = endpoint_counts(
        snapshot_dir,
        symbols=CORE_SYMBOLS,
        start_exclusive=contract["replay_start_exclusive"],
        end_inclusive=contract["latest_bar"],
        window_max=max(contract["window_lengths"]),
    )
    baseline_raw = read_jsonl(baseline_dir / "raw_detections.jsonl")
    hardneg_raw = read_jsonl(hardneg_dir / "raw_detections.jsonl")
    baseline_events = read_jsonl(baseline_dir / "events.jsonl")
    hardneg_events = read_jsonl(hardneg_dir / "events.jsonl")
    baseline, baseline_per_symbol = model_metrics(
        baseline_summary, baseline_raw, baseline_events, endpoints
    )
    hardneg, hardneg_per_symbol = model_metrics(
        hardneg_summary, hardneg_raw, hardneg_events, endpoints
    )
    scan_artifacts = {
        "baseline": scan_artifact_receipt(baseline_dir, len(baseline_events)),
        "hardneg": scan_artifact_receipt(hardneg_dir, len(hardneg_events)),
    }
    per_symbol: dict[str, Any] = {}
    for symbol in CORE_SYMBOLS:
        overlap = cross_model_event_overlap(
            [row for row in baseline_events if row["symbol"] == symbol],
            [row for row in hardneg_events if row["symbol"] == symbol],
            gap_bars=int(contract["event_gap_bars"]),
        )
        per_symbol[symbol] = {
            "baseline": baseline_per_symbol[symbol],
            "hardneg": hardneg_per_symbol[symbol],
            "hardneg_minus_baseline_events": (
                hardneg_per_symbol[symbol]["events"] - baseline_per_symbol[symbol]["events"]
            ),
            "overlap": overlap,
        }
    overlap = cross_model_event_overlap(
        baseline_events,
        hardneg_events,
        gap_bars=int(contract["event_gap_bars"]),
    )
    self_control = cross_model_event_overlap(
        baseline_events,
        baseline_events,
        gap_bars=int(contract["event_gap_bars"]),
    )
    if self_control["matched_events"] != len(baseline_events):
        raise ValueError("self-comparison null control failed")
    delta_keys = ("raw_detections", "events", "events_per_1000_endpoints", "events_per_day")
    return {
        "protocol": "owner_short_recent15d_core10_comparison_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_scope": "owner-approved post-holdout descriptive inference replay",
        "production_eligible": False,
        "training_eligible": False,
        "threshold_tuned": False,
        "future_outcomes_read": False,
        "contract": contract,
        "holdout_consumption": {
            "baseline": baseline_holdout_use,
            "hardneg": hardneg_holdout_use,
        },
        "snapshot": {
            "path": str(snapshot_summary_path),
            "sha256": sha256_file(snapshot_summary_path),
            "generated_at": snapshot_summary.get("generated_at"),
            "requested_symbols": snapshot_summary.get("requested_symbols"),
            "usable_symbols": snapshot_summary.get("usable_symbols"),
            "canonical_data_written": snapshot_summary.get("canonical_data_written"),
        },
        "runtime_integrity": {
            "discarded_attempts": [
                "native MPS NMS default time limit exceeded; no final artifact written",
                "CPU pure-Torch NMS was complete but too slow; stopped before final artifact and replaced only after byte-identical torchvision parity",
            ],
            "cpu_nms_parity": nms_parity,
        },
        "scan_artifacts": scan_artifacts,
        "model_lineage": {
            "baseline": {
                "task": "short-only L1 detection",
                "train_positive": 1143,
                "train_easy_negative": 1143,
                "train_hard_negative": 0,
                "val_positive": 202,
                "val_easy_negative": 200,
                "frozen_val": {"precision": 0.8467, "recall": 0.9024, "map50": 0.9206},
            },
            "hardneg_r1": {
                "task": "short-only L1 detection",
                "train_positive": 1143,
                "train_easy_negative": 1143,
                "train_hard_negative": 2286,
                "owner_long_as_negative": 916,
                "model_ranked_background_negative": 1370,
                "val_positive": 202,
                "val_easy_negative": 200,
                "frozen_val": {"precision": 0.8626, "recall": 0.7770, "map50": 0.8980},
            },
            "newer_weight_exists": {
                "name": "owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft",
                "sha256": LATER_R2_SHA256,
            },
        },
        "baseline": baseline,
        "hardneg": hardneg,
        "hardneg_minus_baseline": {
            key: {
                "absolute": float(hardneg[key]) - float(baseline[key]),
                "relative": relative_change(float(hardneg[key]), float(baseline[key])),
            }
            for key in delta_keys
        },
        "cross_model_overlap": overlap,
        "self_comparison_null_control": self_control,
        "per_symbol": per_symbol,
    }


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def render_report(summary: dict[str, Any], path: Path) -> None:
    baseline = summary["baseline"]
    hardneg = summary["hardneg"]
    delta = summary["hardneg_minus_baseline"]
    overlap = summary["cross_model_overlap"]
    coin_rows = []
    for symbol in CORE_SYMBOLS:
        row = summary["per_symbol"][symbol]
        coin_rows.append(
            f"| {symbol.replace('_USDT_SWAP', '')} | {row['baseline']['raw_detections']:,} | "
            f"{row['hardneg']['raw_detections']:,} | {row['baseline']['events']:,} | "
            f"{row['hardneg']['events']:,} | {row['hardneg_minus_baseline_events']:+d} | "
            f"{row['overlap']['matched_events']:,} |"
        )
    contract = summary["contract"]
    recent_rows = []
    for model_name, model in (("baseline", baseline), ("hardneg R1", hardneg)):
        for event in model["latest_events"]:
            recent_rows.append(
                f"| {model_name} | {event['symbol'].replace('_USDT_SWAP', '')} | "
                f"{event['decision_time']} | {event['core_mid_time']} | "
                f"{event['peak_confidence']:.3f} | {event['predicted_core_bars']} | "
                f"{event['decision_delay_bars']} |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# 两个Owner空头YOLO最近15天核心10币描述性扫描（2026-08-21）

## 结论先行

- 固定同一份Core-10快照、W12–19、conf=0.25、IoU=0.70与5根事件去重后，baseline产生 **{baseline['events']:,}** 个事件，hard-negative R1产生 **{hardneg['events']:,}** 个事件。
- R1相对baseline事件变化 **{delta['events']['absolute']:+.0f}**（{pct(delta['events']['relative'])}），原始框变化 **{delta['raw_detections']['absolute']:+.0f}**（{pct(delta['raw_detections']['relative'])}）。
- 两模型匹配事件 **{overlap['matched_events']:,}**，baseline-only **{overlap['r1_only_events']:,}**，R1-only **{overlap['r2_only_events']:,}**。
- 本轮只描述检测密度：没有读取未来收益、没有跑TP/SL、没有调阈值、没有promote、没有下单。

## 这两个模型到底是什么

| 模型 | 训练正例 | 训练负例 | 方向 | 状态 |
|---|---:|---:|---|---|
| `owner_lsv2_short_gold_center_v1_ft` | 1,143 | 1,143 easy | 只识别Owner空头形态 | baseline；未通过生产密度门 |
| `owner_lsv2_short_gold_center_hardneg_r1_ft` | 同一批1,143 | 1,143 easy + 2,286 hard | 仍只识别空头；916张Owner多头被当作“不要报空头”的负例 | R1；更克制但也未获生产资格 |

- 两者都不是当前生产模型，ACTIVE没有启用任何一个。
- 严格说它们也不是该支线最新权重：后面还有 `owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft`（SHA `{LATER_R2_SHA256}`）。本轮只比较Owner点名的这两个，不偷偷替换成R2。
- 它们是W12–19紧凑因果图上的L1检测器，不是收益模型；“报框”只表示看起来像空头形态。
- 旧冻结val仅作历史参考：baseline P/R/mAP50=`0.847/0.902/0.921`，R1=`0.863/0.777/0.898`。R1的主要变化是少报而非全面变好；这些val数字不能裁决生产资格。

## 冻结合同

| 项目 | 值 |
|---|---|
| 时间范围UTC | `{contract['replay_start_exclusive']}`之后至`{contract['latest_bar']}` |
| 时间范围CST | `{pd.Timestamp(contract['replay_start_exclusive']).tz_convert('Asia/Shanghai')}`之后至`{pd.Timestamp(contract['latest_bar']).tz_convert('Asia/Shanghai')}` |
| 币种 | `{', '.join(base for base in CORE_MAJORS)}` |
| 周期/窗口 | 15m；W12–19逐bar全扫 |
| conf / NMS IoU | {contract['confidence']} / {contract['nms_iou']} |
| NMS执行 / 实现 / 超时预算 | {contract['nms_device']} / {contract['nms_implementation']} / {contract['nms_max_time_img']}秒每图；禁止批内截断 |
| 去重 | 同币预测核心中点相差≤{contract['event_gap_bars']}根，保留首次跨门决策 |
| bar endpoints / exposures | {contract['bar_endpoints']:,} / {contract['window_exposures']:,} |
| baseline权重 | `{BASELINE_SHA256}` |
| R1权重 | `{HARDNEG_SHA256}` |
| holdout登记 | baseline第{summary['holdout_consumption']['baseline']}次；R1第{summary['holdout_consumption']['hardneg']}次 |

## 总体结果

| 模型 | raw | raw/1000 exposures | 去重events | events/1000 endpoints | events/day | 触发币数 |
|---|---:|---:|---:|---:|---:|---:|
| 1:1 baseline | {baseline['raw_detections']:,} | {baseline['raw_per_1000_exposures']:.3f} | {baseline['events']:,} | {baseline['events_per_1000_endpoints']:.3f} | {baseline['events_per_day']:.2f} | {baseline['triggered_symbols']}/10 |
| 1:3 hardneg R1 | {hardneg['raw_detections']:,} | {hardneg['raw_per_1000_exposures']:.3f} | {hardneg['events']:,} | {hardneg['events_per_1000_endpoints']:.3f} | {hardneg['events_per_day']:.2f} | {hardneg['triggered_symbols']}/10 |

## 逐币结果

| 币 | baseline raw | R1 raw | baseline events | R1 events | event差 | 共同事件 |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(coin_rows)}

## 两模型各自最近10个事件

| 模型 | 币 | decision time UTC | core midpoint UTC | peak conf | 核心根数 | 延迟根数 |
|---|---|---|---|---:|---:|---:|
{chr(10).join(recent_rows)}

完整事件逐条保存在各扫描目录的`events.jsonl`，这里不按事后涨跌筛选，只按decision time倒序展示。

## 零假设与必报指标

- 自比较零对照：baseline事件集与自身比较，{summary['self_comparison_null_control']['matched_events']}/{baseline['events']}全部匹配，baseline-only=0、R1-only=0；证明比较器不会凭空制造差异。
- AUC、置换p、top-decile毛/净收益、胜率、匹配随机交易对照：**N/A**。本轮没有L2排序或未来结果，禁止编造交易指标。
- BTC虽然属于仓库固定Core-10，但不在本模型Owner-short正例母池中；其结果属于跨币泛化描述，不能与有训练正例的币等价解释。

## 运行完整性

- 首次15天native-MPS尝试出现`NMS time limit 3.600s exceeded`；Ultralytics会在超时后跳过该批剩余图片，因此该次运行被中止，未生成最终结果。
- 第二次CPU纯Torch NMS不会漏图，但密集候选下过慢，也在最终artifact写出前中止；没有把两次半成品拼进正式结果。
- 修复只把NMS后处理移到CPU、使用编译版torchvision NMS并把每图预算设为60秒；模型前向、FP32、图片、conf与IoU均不变。
- 在冻结ETH 48小时、1,536个窗口上，native与CPU-NMS均为411个raw / 18个events；`raw_detections.jsonl`与`events.jsonl`逐字节一致。SHA分别为`{summary['runtime_integrity']['cpu_nms_parity']['artifact_sha256']['raw_detections.jsonl']}`、`{summary['runtime_integrity']['cpu_nms_parity']['artifact_sha256']['events.jsonl']}`。

## 风险与诚实声明

- 最近15天全部晚于2026-05-04 holdout边界；本轮由Owner在对话中明确要求运行，登记为baseline第2次、R1第1次holdout描述性读取。
- 这不是独立canary，也不能在看完结果后调conf、删币、回灌难负例，再把同一时间块称为新验收。
- 两个模型都是空头形态检测器；事件数不等于正确空头信号数，更不等于订单或收益。
- `production_eligible=false`、`training_eligible=false`；ACTIVE未改，订单为0。

## 产物

- baseline完整事件：`{summary['scan_artifacts']['baseline']['directory']}/events.jsonl`
- hard-negative R1完整事件：`{summary['scan_artifacts']['hardneg']['directory']}/events.jsonl`
- 机器对比：`analysis/output/owner_short_gold_center_recent15d_core10_20260821/comparison.json`
- 本报告记录上述scan summary、raw、events与`_SUCCESS`的逐文件SHA；任一字节变化都要重新汇总。

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/backtest_owner_short_gold_center_recent.py scan \\
  --snapshot-dir {Path(summary['snapshot']['path']).parent / 'kline_snapshot'} \\
  --out-dir /path/to/baseline \\
  --weights analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_v1_ft/weights/best.pt \\
  --hours 360 --window-min 12 --window-max 19 --conf 0.25 --iou 0.70 \\
  --imgsz 960 --device mps --batch 32 --nms-device cpu \\
  --nms-implementation torchvision --nms-max-time-img 60 \\
  --evaluation-scope holdout --holdout-use-number 2

# R1保持完全相同参数，只替换权重、输出目录，并使用--holdout-use-number 1。

PYTHONPATH=. .venv/bin/python scripts/summarize_owner_short_recent_core10.py \\
  --baseline-dir /path/to/baseline --hardneg-dir /path/to/hardneg \\
  --snapshot-summary {summary['snapshot']['path']} \\
  --out-json analysis/output/owner_short_gold_center_recent15d_core10_20260821/comparison.json \\
  --report {path}

python3 scripts/md_to_html.py {path} --out-dir analysis/html
```
""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--hardneg-dir", type=Path, required=True)
    parser.add_argument("--snapshot-summary", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline-holdout-use", type=int, default=2)
    parser.add_argument("--hardneg-holdout-use", type=int, default=1)
    args = parser.parse_args()
    summary = build_summary(
        args.baseline_dir,
        args.hardneg_dir,
        args.snapshot_summary,
        baseline_holdout_use=args.baseline_holdout_use,
        hardneg_holdout_use=args.hardneg_holdout_use,
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    render_report(summary, args.report)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
