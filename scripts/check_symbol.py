#!/usr/bin/env python3
"""One-shot live probe for a single symbol: fresh klines -> YOLO tip detect ->
frozen v11 score -> freshness verdict. Human-readable Chinese output.

Read-only by design: fetches fresh bars from OKX into MEMORY only (never
touches data/kline_fetched), never writes forward_log, never places orders,
and renders temp PNGs inside a TemporaryDirectory that is removed on exit.
Fully independent from the VPS mainline pipeline.

Pipeline pieces reused (no re-implementation):
  - klines:   src.data.loader (local merged history) + src.data.fetch_okx's
              throttled `_request` for the in-memory incremental tail
  - detect:   src.judgment.yolo_candidates.scan_series_with_yolo (live/tip
              mode, models/owner_best.pt) -- the exact mainline entry point
  - score:    src.judgment.frozen.latest_artifact(default_config()) (v11
              freeze, threshold_val_q90) + sizing_tiers.tier_for_score
  - fresh:    ExecutorConfig.max_signal_age_min (30min gate, same value as
              TG filter and dashboard FRESH_DETECT_MIN)

Import-order constraint (docs/learnings/lightgbm-import-before-ultralytics-
predict-segfaults.md): lightgbm must NOT be imported before the first
ultralytics predict in this process, so frozen/lightgbm imports live inside
`_score_candidates`, called only after detection finished. OMP/MKL single
thread is also forced (docs/learnings/duplicate-libomp-segfault-needs-omp-
threads-1.md).

Usage (local machine with OKX network):

    PYTHONPATH=. OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python \
        scripts/check_symbol.py BTC_USDT_SWAP

    # lenient symbol forms all resolve to the SWAP series:
    #   btc / BTC / btc_usdt / BTC-USDT-SWAP / btcusdt
    # optional: --mode tip (single tip window; default live = mainline schedule)
"""
from __future__ import annotations

import os

# Belt and braces: dual-libomp (torch + lightgbm) needs single-thread OMP.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import argparse
import sys
import tempfile
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.fetch_okx import API, PAGE_LIMIT, _request  # noqa: E402
from src.data.loader import OHLCV_COLUMNS, list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.execution.config import ExecutorConfig  # noqa: E402
from src.judgment.candidates import WARMUP_BARS, add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    STRIDE,
    WINDOW,
    _resolve_predict_device,
    load_yolo_model,
    right_edge_to_bar,
)

BAR_MIN = 15
# Mirrors forward_scan.LIVE_TAIL_BARS; not imported because forward_scan pulls
# lightgbm (via forward_types) at import time -- see docstring.
TAIL_BARS = 2000
# Same 30min value as TG filter and dashboard FRESH_DETECT_MIN (三门同值).
FRESH_GATE_MIN = float(ExecutorConfig().max_signal_age_min)


def normalize_symbol(raw: str) -> str:
    """Lenient parse: btc / BTC_USDT / BTC-USDT-SWAP / btcusdt -> BTC_USDT_SWAP."""
    sym = raw.strip().upper().replace("-", "_").replace("/", "_")
    if "_" not in sym:  # btcusdtswap / btcusdt / btc
        for suffix in ("USDTSWAP", "USDT"):
            if sym.endswith(suffix) and len(sym) > len(suffix):
                sym = sym[: -len(suffix)]
                break
    if sym.endswith("_USDT_SWAP"):
        return sym
    if sym.endswith("_USDT"):
        return f"{sym}_SWAP"
    return f"{sym}_USDT_SWAP"


def load_local_bars(symbol: str) -> pd.DataFrame:
    """Merged local 15m history for the symbol (old cache + kline_fetched)."""
    paths = list_series(bar="15m").get(("okx", symbol))
    if not paths:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    return load_series(paths)


def fetch_bars_memory(symbol: str, *, after_ts: int | None, min_bars: int) -> pd.DataFrame:
    """Page confirmed 15m candles backwards from now, in memory only.

    Stops once `after_ts` (newest local bar, ms) is reached, or `min_bars`
    rows are collected when there is no local history. Never writes to disk.
    """
    inst_id = symbol.replace("_", "-")
    rows: dict[int, list[float]] = {}
    after: int | None = None
    while True:
        url = f"{API}?instId={inst_id}&bar=15m&limit={PAGE_LIMIT}"
        if after is not None:
            url += f"&after={after}"
        payload = _request(url)
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX API 错误: {payload.get('msg')} (instId={inst_id})")
        page = payload.get("data") or []
        if not page:
            break
        for r in page:  # [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]
            ts = int(r[0])
            if len(r) > 8 and r[8] == "0":
                continue  # unconfirmed candle
            if after_ts is not None and ts <= after_ts:
                continue
            rows[ts] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        oldest = int(page[-1][0])
        if after_ts is not None and oldest <= after_ts:
            break
        if after_ts is None and len(rows) >= min_bars:
            break
        after = oldest
    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    frame = pd.DataFrame(
        [[ts, *vals] for ts, vals in sorted(rows.items())],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    frame["open_time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    return frame[OHLCV_COLUMNS]


def harvest_confidences(frame: pd.DataFrame, model, mode: str, tmpdir: Path) -> dict[int, float]:
    """Max YOLO confidence per signal bar, display only.

    Re-renders the same window schedule as scan_series_with_yolo (live/tip)
    because the mainline function returns bare indices without confidences.
    The authoritative fire/no-fire verdict stays with scan_series_with_yolo.
    """
    from src.detection.data import add_mas
    from src.detection.render import render_chart

    enriched_ma = add_mas(frame)
    last_start = len(frame) - WINDOW
    first_start = WARMUP_BARS
    if last_start < first_start:
        return {}
    if mode == "tip":
        starts = [last_start]
    else:  # live schedule: tip + 2 back + coarse stride walk, max 6 windows
        starts_set = {last_start - back for back in (0, 1, 2) if last_start - back >= first_start}
        s = last_start - STRIDE
        while s >= first_start and len(starts_set) < 6:
            starts_set.add(s)
            s -= STRIDE
        starts = sorted(starts_set, reverse=True)
    rendered = []
    for k, start in enumerate(starts):
        png = tmpdir / f"conf_{k}.png"
        try:
            _, tf = render_chart(enriched_ma.iloc[start : start + WINDOW], out_path=png)
        except Exception:  # noqa: BLE001 -- conf is decorative, never fatal
            continue
        rendered.append((start, tf, png))
    if not rendered:
        return {}
    results = model.predict(
        [str(p) for _, _, p in rendered],
        conf=DEFAULT_CONF,
        verbose=False,
        device=_resolve_predict_device(),
    )
    conf_by_bar: dict[int, float] = {}
    for (start, tf, _), res in zip(rendered, results):
        if res.boxes is None:
            continue
        xywhn = res.boxes.xywhn.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        for b, c in zip(xywhn, confs):
            cx, _, w, _ = map(float, b[:4])
            signal_i = start + right_edge_to_bar(cx, w, tf, n_bars=WINDOW)
            if 0 <= signal_i < len(frame):
                conf_by_bar[signal_i] = max(conf_by_bar.get(signal_i, 0.0), float(c))
    return conf_by_bar


def _score_candidates(frame: pd.DataFrame, indices: list[int]):
    """Frozen v11 scoring. lightgbm imported HERE, after YOLO predict ran."""
    import lightgbm as lgb  # noqa: PLC0415 -- dual-libomp import order

    from src.judgment.frozen import default_config, latest_artifact

    artifact = latest_artifact(default_config())
    if artifact is None:
        raise RuntimeError("找不到 ACTIVE 冻结 artifact (frozen_tp5_sl2_swap_yolo_v11_reg_*)")
    enriched = add_indicators(frame)
    scores: list[float] = []
    feature_rows = pd.DataFrame()
    if indices:
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, indices)
        booster = lgb.Booster(model_file=str(artifact.model_path))
        scores = [
            float(s)
            for s in booster.predict(
                feature_rows[FEATURE_COLUMNS], num_iteration=artifact.best_iteration
            )
        ]
    return artifact, enriched, feature_rows, scores


def main() -> int:
    parser = argparse.ArgumentParser(description="单币一键盘口检测(只读, 不写账本不下单)")
    parser.add_argument("symbol", help="币种, 宽容格式: btc / BTC_USDT / BTC_USDT_SWAP")
    parser.add_argument(
        "--mode", choices=("live", "tip"), default="live",
        help="YOLO 窗口调度: live=主线前向调度(默认, ≤6 窗), tip=仅最右窗",
    )
    args = parser.parse_args()
    symbol = normalize_symbol(args.symbol)
    now = pd.Timestamp.now(tz="UTC")

    print(f"=== 一键盘口检测: {symbol} (mode={args.mode}) ===")

    # --- klines: local history + in-memory incremental tail ---
    local = load_local_bars(symbol)
    after_ts = int(local["open_time"].max().timestamp() * 1000) if not local.empty else None
    fetch_note = ""
    try:
        fresh = fetch_bars_memory(symbol, after_ts=after_ts, min_bars=TAIL_BARS + 200)
    except Exception as exc:  # noqa: BLE001 -- degrade to local bars, but say so
        fresh = pd.DataFrame(columns=OHLCV_COLUMNS)
        fetch_note = f" [警告: OKX 增量拉取失败({exc}), 仅用本地缓存]"
    frame = pd.concat([local, fresh], ignore_index=True)
    frame = (
        frame.drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
        .tail(TAIL_BARS)
        .reset_index(drop=True)
    )
    if len(frame) < WARMUP_BARS + WINDOW + 2:
        print(f"数据不足: 本地+增量仅 {len(frame)} bars (需 ≥{WARMUP_BARS + WINDOW + 2}), 无法检测")
        return 1
    print(f"数据: 本地 {len(local)} bars, OKX 增量 {len(fresh)} bars (内存合并, 不落盘){fetch_note}")

    last_open = pd.Timestamp(frame["open_time"].iloc[-1])
    data_age_min = (now - (last_open + pd.Timedelta(minutes=BAR_MIN))).total_seconds() / 60
    stale = data_age_min > BAR_MIN
    print(
        f"最新已收 bar: {last_open} (收盘距今 {data_age_min:.1f} min)"
        + (" [警告: 数据疑似滞后]" if stale else " [数据新鲜]")
    )
    if is_stockish(symbol):
        print("注意: 该币属 stockish(代币化股票), 主线裁决不计入 crypto 口径")

    # --- detect: mainline entry point + display-only confidence pass ---
    from src.judgment.yolo_candidates import scan_series_with_yolo

    model = load_yolo_model()
    with tempfile.TemporaryDirectory(prefix="check_symbol_") as td:
        tmpdir = Path(td)
        indices = scan_series_with_yolo(
            frame, model, mode=args.mode, tmp_png=tmpdir / "win.png"
        )
        conf_by_bar = harvest_confidences(frame, model, args.mode, tmpdir)

    # --- score + freshness (lightgbm only from here on) ---
    artifact, enriched, feature_rows, scores = _score_candidates(frame, indices)
    print(
        f"检测器: models/owner_best.pt | 判断层: {artifact.relative_model_path} "
        f"(阈值 {artifact.threshold:.5f})"
    )

    if not indices:
        print("YOLO: 当前盘口无信号 (live 调度窗口内无检出)")
    else:
        print(f"YOLO 检出 {len(indices)} 个信号:")
        for n, signal_i in enumerate(indices, 1):
            signal_time = pd.Timestamp(enriched["open_time"].iloc[signal_i])
            bars_back = len(frame) - 1 - signal_i
            conf = conf_by_bar.get(signal_i)
            conf_txt = f"conf {conf:.2f}" if conf is not None else "conf n/a"
            score = scores[n - 1]
            passed = score >= artifact.threshold
            if artifact.sizing_tiers is not None:
                tier, mult = artifact.sizing_tiers.tier_for_score(score, artifact.threshold)
                tier_txt = f"tier {tier} ({mult:g}x)" if passed else "不进 tier"
            else:
                tier_txt = "tier n/a (sidecar 无 sizing_tiers)"
            atr_pct = float(feature_rows["atr_pct"].iloc[n - 1])
            atr_ok = pd.notna(atr_pct) and atr_pct >= ATR_PCT_MIN
            age_min = (now - signal_time).total_seconds() / 60
            fresh_ok = age_min <= FRESH_GATE_MIN
            tradeable = passed and atr_ok and fresh_ok
            print(f"  [{n}] 信号 bar {signal_time} (距最新 bar {bars_back} 根, {conf_txt})")
            print(
                f"      判断分 {score:.5f} vs 阈值 {artifact.threshold:.5f} → "
                f"{'过' if passed else '不过'}, {tier_txt}"
            )
            print(
                f"      atr_pct {atr_pct:.4f} vs 下限 {ATR_PCT_MIN} → {'过' if atr_ok else '不过'}"
            )
            print(
                f"      信号年龄 {age_min:.1f} min vs 新鲜度门 {FRESH_GATE_MIN:.0f} min → "
                f"{'新鲜' if fresh_ok else '超龄'};"
                f" 若进主线管道{'可开单' if tradeable else '不会开单'}"
            )

    print("免责: 本地即时探测, 不写账本、不下单、不动主线数据, 与 VPS 主线管道互不影响。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
