#!/usr/bin/env python3
"""3-day holdout smoke: W10 classifier → frozen SHORT barriers.

Owner authorized this window on 2026-08-13. It is holdout
(≥2026-05-04). Canonical ``data/kline_fetched`` was last written 2026-08-05
and is not used. The just-pulled 3-day files are the disposable snapshot
under ``analysis/output/yoyo_r3a_v3gold_ft_r1_holdout_losers3d_20260813/``.

This is not an acceptance test and does not promote anything.

At each 15m decision bar the model sees only the causal W10 ending on that
bar (slots 0–4 pre-context, 5–8 core, 9 confirm). SIGNAL → next-open short
with frozen TP5/SL2/72. NO_SIGNAL → no trade. No boxes, no future candles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
YOYO_REPO = Path.home() / "yoyo-trading"
for module_path in (ROOT, YOYO_REPO):
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))

from yoyo.contracts.costs import SWAP_MAKER, SWAP_TAKER  # noqa: E402
from yoyo.contracts.outcomes import (  # noqa: E402
    ATR_PCT_MIN,
    HORIZON_BARS,
    OutcomeContractError,
    resolve_barrier_outcome,
)
from yoyo.data.indicators import MIN_GAP_BARS, add_indicators  # noqa: E402
from yoyo.datasets.legacy_gold_migration.renderer import SpanError, render_w10  # noqa: E402
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas  # noqa: E402

# Same class the 3060 trainer pickled into best.pt. Must live on __main__.
class WhiteLetterbox:
    """Pad with renderer white, keep aspect, no crop."""

    def __init__(self, size: int, fill: int = 255):
        self.size = int(size)
        self.fill = fill

    def __call__(self, img: Image.Image) -> Image.Image:
        w, h = img.size
        scale = self.size / max(w, h)
        nw = max(1, round(w * scale))
        nh = max(1, round(h * scale))
        img = img.resize((nw, nh), Image.BILINEAR)
        canvas = Image.new("RGB", (self.size, self.size), (self.fill, self.fill, self.fill))
        if img.mode != "RGB":
            img = img.convert("RGB")
        canvas.paste(img, ((self.size - nw) // 2, (self.size - nh) // 2))
        return canvas


PROTOCOL = "fixed_w10_core4_confirm1_v1_cls_holdout3d_20260813"
EXPECTED_WEIGHTS_SHA256 = "18bcb5988e6dd36bdf2fc8a1a22d3ad66ab78b777a1d02c88080c937e98d0541"
SNAPSHOT_DIR = (
    ROOT / "analysis/output/yoyo_r3a_v3gold_ft_r1_holdout_losers3d_20260813/kline_snapshot"
)
DEFAULT_WEIGHTS = ROOT / "analysis/output/fixed_w10_cls_holdout3d_20260813/best.pt"
DEFAULT_OUT = ROOT / "analysis/output/fixed_w10_cls_holdout3d_20260813"
YOYO_REPORT = YOYO_REPO / "reports/fixed_w10_core4_confirm1_v1/holdout3d_backtest.md"

SCAN_START = pd.Timestamp("2026-08-10T00:00:00Z")
SCAN_END = pd.Timestamp("2026-08-13T12:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
WINDOW_BARS = 10
WARMUP_BARS = 240
Y_PAD_FRAC = 0.05
IMGSZ = 960
THRESHOLD = 0.50
TP_ATR = 5.0
SL_ATR = 2.0
BAR_MINUTES = 15
HOLDOUT_USE_NUMBER = 1
CONFIG_ID = "fixed_w10_core4_confirm1_v1_cls"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def full_frame_transforms(size: int):
    import torchvision.transforms as transforms

    return transforms.Compose([WhiteLetterbox(size), transforms.ToTensor()])


def pick_device(explicit: str | None) -> str:
    import torch

    if explicit:
        return explicit
    if torch.cuda.is_available():
        return "cuda:0"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    raise SystemExit("no CUDA/MPS; refuse CPU grind. Run on the 3060.")


def load_snapshot(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    frame = raw.copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["open_time", "open", "high", "low", "close"])
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    if frame.empty:
        raise ValueError(f"empty snapshot: {path}")
    if (frame["open_time"] < HOLDOUT_START).any():
        raise ValueError(f"{path.name} contains pre-holdout rows; refusing mixed snapshot")
    latest = pd.Timestamp(frame["open_time"].iloc[-1])
    if latest > SCAN_END:
        frame = frame.loc[frame["open_time"] <= SCAN_END].reset_index(drop=True)
    return add_indicators(add_mas(frame))


def decision_indices(frame: pd.DataFrame) -> list[int]:
    times = pd.to_datetime(frame["open_time"], utc=True)
    out: list[int] = []
    for index in range(WINDOW_BARS - 1, len(frame)):
        stamp = times.iloc[index]
        if stamp < SCAN_START or stamp > SCAN_END:
            continue
        if index < WARMUP_BARS:
            continue
        window = frame.iloc[index - WINDOW_BARS + 1 : index + 1]
        if window[list(ALL_MA_COLS)].isna().any().any():
            continue
        out.append(index)
    return out


def bgr_to_tensor(image_bgr: np.ndarray, transform) -> "torch.Tensor":
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    return transform(Image.fromarray(rgb))


def load_classifier(weights: Path, device: str):
    import __main__ as main_mod
    from ultralytics import YOLO

    main_mod.WhiteLetterbox = WhiteLetterbox
    model = YOLO(str(weights), task="classify")
    names = {int(k): str(v) for k, v in dict(model.names).items()}
    signal_ids = [i for i, name in names.items() if name == "SIGNAL"]
    if len(signal_ids) != 1:
        raise SystemExit(f"expected one SIGNAL class, got {names}")
    model.model.to(device).eval()
    return model, names, signal_ids[0]


def classify_symbol(
    model,
    transform,
    frame: pd.DataFrame,
    symbol: str,
    indices: list[int],
    *,
    device: str,
    signal_idx: int,
    batch: int,
) -> list[dict[str, Any]]:
    import torch

    rows: list[dict[str, Any]] = []
    skipped_span = 0
    for start in range(0, len(indices), batch):
        chunk = indices[start : start + batch]
        tensors = []
        kept: list[int] = []
        for decision_i in chunk:
            window = frame.iloc[decision_i - WINDOW_BARS + 1 : decision_i + 1]
            if len(window) != WINDOW_BARS:
                continue
            try:
                image, _tf = render_w10(window, y_pad_frac=Y_PAD_FRAC, overlay=False)
            except (SpanError, ValueError):
                skipped_span += 1
                continue
            tensors.append(bgr_to_tensor(image, transform))
            kept.append(decision_i)
        if not tensors:
            continue
        batch_tensor = torch.stack(tensors).to(device)
        with torch.no_grad():
            logits = model.model(batch_tensor)
            if isinstance(logits, (list, tuple)):
                logits = logits[0]
            probs = torch.softmax(logits.float(), dim=1).detach().cpu().numpy()
        for decision_i, probability in zip(kept, probs):
            p_signal = float(probability[signal_idx])
            pred = "SIGNAL" if p_signal >= THRESHOLD else "NO_SIGNAL"
            rows.append(
                {
                    "symbol": symbol,
                    "decision_i": int(decision_i),
                    "decision_time": pd.Timestamp(frame["open_time"].iloc[decision_i]).isoformat(),
                    "window_start_i": int(decision_i - WINDOW_BARS + 1),
                    "window_end_i": int(decision_i),
                    "p_signal": p_signal,
                    "pred": pred,
                }
            )
    return rows, skipped_span


def outcome_for(frame: pd.DataFrame, decision_i: int) -> dict[str, Any]:
    entry_i = decision_i + 1
    if entry_i >= len(frame):
        return {"status": "open", "outcome": "no_entry", "entry_i": entry_i}
    atr = float(frame["atr14"].iloc[decision_i])
    atr_pct = float(frame["atr_pct"].iloc[decision_i])
    if not np.isfinite(atr) or atr <= 0:
        return {"status": "skip", "outcome": "bad_atr", "entry_i": entry_i}
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return {"status": "skip", "outcome": "atr_floor", "entry_i": entry_i, "atr_pct": atr_pct}
    entry = float(frame["open"].iloc[entry_i])
    try:
        resolution = resolve_barrier_outcome(
            frame,
            side="short",
            entry_i=entry_i,
            entry_price=entry,
            atr=atr,
            tp_atr_mult=TP_ATR,
            sl_atr_mult=SL_ATR,
            horizon_bars=HORIZON_BARS,
            same_bar_policy="conservative_sl",
            gap_policy="barrier_price",
            return_convention="linear_short",
            allow_partial=True,
            bar_duration=pd.Timedelta(minutes=BAR_MINUTES),
        )
    except OutcomeContractError as exc:
        return {"status": "skip", "outcome": f"contract:{exc}", "entry_i": entry_i}
    gross = resolution.gross_ret
    return {
        "status": resolution.status,
        "outcome": resolution.outcome or "running",
        "entry_i": entry_i,
        "entry_time": pd.Timestamp(frame["open_time"].iloc[entry_i]).isoformat(),
        "entry_price": entry,
        "atr14": atr,
        "atr_pct": atr_pct,
        "exit_offset": resolution.exit_offset,
        "exit_time": resolution.exit_time,
        "exit_price": resolution.exit_price,
        "gross_ret": gross,
        "net_taker": None if gross is None else gross - SWAP_TAKER,
        "net_maker": None if gross is None else gross - SWAP_MAKER,
    }


def gap_dedup(rows: list[dict[str, Any]], gap_bars: int = MIN_GAP_BARS) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: (row["symbol"], int(row["decision_i"])))
    keep: list[dict[str, Any]] = []
    last: dict[str, int] = {}
    for row in ordered:
        symbol = str(row["symbol"])
        decision_i = int(row["decision_i"])
        prev = last.get(symbol)
        if prev is not None and decision_i - prev < gap_bars:
            continue
        keep.append(row)
        last[symbol] = decision_i
    return keep


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [row for row in trades if row.get("status") == "closed" and row.get("net_maker") is not None]
    if not closed:
        return {
            "n_closed": 0,
            "n_open": sum(1 for row in trades if row.get("status") == "open"),
            "n_skip": sum(1 for row in trades if row.get("status") == "skip"),
        }
    maker = np.array([float(row["net_maker"]) for row in closed], dtype=float)
    taker = np.array([float(row["net_taker"]) for row in closed], dtype=float)
    gross = np.array([float(row["gross_ret"]) for row in closed], dtype=float)
    outcomes = {}
    for row in closed:
        outcomes[str(row["outcome"])] = outcomes.get(str(row["outcome"]), 0) + 1
    return {
        "n_closed": int(len(closed)),
        "n_open": sum(1 for row in trades if row.get("status") == "open"),
        "n_skip": sum(1 for row in trades if row.get("status") == "skip"),
        "symbols": int(len({row["symbol"] for row in closed})),
        "mean_gross_bp": float(gross.mean() * 1e4),
        "mean_net_taker_bp": float(taker.mean() * 1e4),
        "mean_net_maker_bp": float(maker.mean() * 1e4),
        "total_net_taker": float(taker.sum()),
        "total_net_maker": float(maker.sum()),
        "win_rate_net_maker": float((maker > 0).mean()),
        "win_rate_net_taker": float((taker > 0).mean()),
        "outcomes": outcomes,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def md_report(summary: dict[str, Any]) -> str:
    stats = summary["deduped_closed"]
    raw = summary["raw_closed"]
    return f"""# W10 分类 3 天 holdout 试跑

**仅 3 天 holdout 试跑，不能当验收。未 promote。**

## 怎么回测

每个 15m 决策 bar 只渲染当时及以前的 10 根（槽 0–4 前文、5–8 核、第 9 根 confirm），
`render_w10 overlay=False`，白边 letterbox 到 960，无 CenterCrop、无 ImageNet normalize。
模型输出 SIGNAL 则按冻结 SHORT 合同下一根开盘做空，TP5×ATR14 / SL2×ATR14 / 72 根，
同 bar 双触判 SL；NO_SIGNAL 不做。同币信号间隔 {MIN_GAP_BARS} 根。

## 数据

| 项 | 值 |
|---|---|
| 快照 | `{summary["snapshot_dir"]}` |
| 来源 | 2026-08-13 为 R3A losers3d 拉取的 disposable OKX 15m，未写 canonical `data/kline_fetched` |
| 币 | {summary["n_symbols"]} 个有数据的 USDT-SWAP（{', '.join(summary["symbols"])}） |
| 决策区间 UTC | {summary["scan_start"]} → {summary["scan_end"]} |
| 快照最晚 bar | {summary["latest_bar"]} |
| holdout 起点 | {HOLDOUT_START.isoformat()} |
| 本配置消耗 | **第 {HOLDOUT_USE_NUMBER} 次**（`{CONFIG_ID}`；全局 HANDOFF 最后编号为第 12 次误耗，此后另有按配置记账） |
| 权重 SHA256 | `{summary["weights_sha256"]}` |
| 推理设备 | {summary["device"]} |
| 阈值 | p(SIGNAL) ≥ {THRESHOLD:.2f}（冻结，未扫阈值） |

canonical `data/kline_fetched` 的 SWAP 15m mtime 停在 2026-08-05，**没有**这 3 天，所以没用它。

## 结果（费用后）

主口径：同币 {MIN_GAP_BARS} 根去重后的**已平仓**做空。

| 口径 | 信号条数 | 成交/已平仓 | maker 净盈亏 | taker 净盈亏 | maker 胜率 |
|---|---:|---:|---:|---:|---:|
| 去重后 | {summary["n_signal_dedup"]} | {stats.get("n_closed", 0)} | {stats.get("total_net_maker")} | {stats.get("total_net_taker")} | {stats.get("win_rate_net_maker")} |
| 未去重 | {summary["n_signal_raw"]} | {raw.get("n_closed", 0)} | {raw.get("total_net_maker")} | {raw.get("total_net_taker")} | {raw.get("win_rate_net_maker")} |

去重后未平仓 {stats.get("n_open", 0)}，ATR/合同跳过 {stats.get("n_skip", 0)}。
出场分布：`{json.dumps(stats.get("outcomes", {}), ensure_ascii=False)}`。

扫描窗口 {summary["n_windows"]} 个；平均 p(SIGNAL) 不是本报告重点。

## 结论

样本很小（3 天、{summary["n_symbols"]} 币、且这批币来自另一只 YOLO 的 losers 快照，不是全市场）。
**不能当验收，不能 promote。**
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--max-symbols", type=int, default=0)
    args = parser.parse_args()

    weights = Path(args.weights)
    digest = sha256_file(weights)
    if digest != EXPECTED_WEIGHTS_SHA256:
        raise SystemExit(f"weights sha mismatch: {digest}")
    snapshot_dir = Path(args.snapshot_dir)
    csvs = sorted(snapshot_dir.glob("*_USDT_SWAP.csv"))
    if not csvs:
        raise SystemExit(f"no snapshot csv in {snapshot_dir}")
    if args.max_symbols:
        csvs = csvs[: args.max_symbols]

    device = pick_device(args.device)
    started = time.time()
    model, names, signal_idx = load_classifier(weights, device)
    transform = full_frame_transforms(IMGSZ)

    frames: dict[str, pd.DataFrame] = {}
    predictions: list[dict[str, Any]] = []
    skipped_span = 0
    latest_bar = None
    for path in csvs:
        symbol = path.stem
        frame = load_snapshot(path)
        frames[symbol] = frame
        end = pd.Timestamp(frame["open_time"].iloc[-1])
        latest_bar = end if latest_bar is None else max(latest_bar, end)
        indices = decision_indices(frame)
        rows, span_n = classify_symbol(
            model,
            transform,
            frame,
            symbol,
            indices,
            device=device,
            signal_idx=signal_idx,
            batch=int(args.batch),
        )
        predictions.extend(rows)
        skipped_span += span_n
        print(json.dumps({"symbol": symbol, "windows": len(indices), "rows": len(rows)}), flush=True)

    raw_signal = [row for row in predictions if row["pred"] == "SIGNAL"]
    deduped = gap_dedup(raw_signal)
    raw_trades = []
    for row in raw_signal:
        trade = {**row, **outcome_for(frames[row["symbol"]], int(row["decision_i"]))}
        raw_trades.append(trade)
    dedup_trades = []
    for row in deduped:
        trade = {**row, **outcome_for(frames[row["symbol"]], int(row["decision_i"]))}
        dedup_trades.append(trade)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "predictions.jsonl", predictions)
    write_jsonl(out_dir / "signals_raw.jsonl", raw_trades)
    write_jsonl(out_dir / "signals_dedup.jsonl", dedup_trades)

    summary = {
        "protocol": PROTOCOL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_scope": "holdout",
        "holdout_use_number": HOLDOUT_USE_NUMBER,
        "config_id": CONFIG_ID,
        "owner_authorized_in_conversation": True,
        "production_eligible": False,
        "promoted": False,
        "orders_placed": False,
        "not_acceptance": True,
        "snapshot_dir": str(snapshot_dir),
        "weights": str(weights),
        "weights_sha256": digest,
        "device": device,
        "class_names": names,
        "threshold": THRESHOLD,
        "imgsz": IMGSZ,
        "transform": "white_letterbox_full_frame",
        "window_bars": WINDOW_BARS,
        "core_slots": [5, 6, 7, 8],
        "confirm_slot": 9,
        "entry": "next_open",
        "tp_atr": TP_ATR,
        "sl_atr": SL_ATR,
        "horizon_bars": HORIZON_BARS,
        "min_gap_bars": MIN_GAP_BARS,
        "scan_start": SCAN_START.isoformat(),
        "scan_end": SCAN_END.isoformat(),
        "latest_bar": None if latest_bar is None else latest_bar.isoformat(),
        "n_symbols": len(frames),
        "symbols": sorted(frames),
        "n_windows": len(predictions),
        "skipped_span": skipped_span,
        "n_signal_raw": len(raw_signal),
        "n_signal_dedup": len(deduped),
        "raw_closed": summarize(raw_trades),
        "deduped_closed": summarize(dedup_trades),
        "wall_seconds": round(time.time() - started, 3),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = md_report(summary)
    (out_dir / "holdout3d_backtest.md").write_text(report, encoding="utf-8")
    YOYO_REPORT.parent.mkdir(parents=True, exist_ok=True)
    YOYO_REPORT.write_text(report, encoding="utf-8")
    (YOYO_REPORT.with_suffix(".json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
