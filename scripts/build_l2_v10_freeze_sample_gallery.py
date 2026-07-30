#!/usr/bin/env python3
"""Render ~200 sample trade charts for the L2 v10 freeze HTML report.

死命令:
  cd /Users/zhangzc/fable-trading && \\
  PYTHONPATH=. python3 scripts/build_l2_v10_freeze_sample_gallery.py && \\
  PYTHONPATH=. python3 scripts/regen_l2_v10_freeze_report.py && \\
  open analysis/output/l2_v10_reg_freeze_20260731/report.html
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.data.loader import list_series, load_series
from src.detection.data import add_mas
from src.detection.render import render_chart
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits
from src.judgment.yolo_candidates import WINDOW

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis/output/l2_v10_reg_freeze_20260731"
SAMP = OUT / "samples"
META = PROJECT / "models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json"
N_SAMPLE = 200


def main() -> int:
    if not META.exists():
        raise SystemExit(f"missing {META}")
    meta = json.loads(META.read_text())
    thr = float(meta["threshold_val_q90"])
    best = int(meta.get("best_iteration") or 1)
    booster = lgb.Booster(model_file=str(PROJECT / meta["model_path"]))
    train, val, _ = load_splits(PROJECT / meta["dataset_path"], horizon_bars=72)
    val = val.copy()
    val["score"] = booster.predict(val[list(FEATURE_COLUMNS)], num_iteration=best if best > 0 else None)
    train = train.copy()
    train["score"] = booster.predict(train[list(FEATURE_COLUMNS)], num_iteration=best if best > 0 else None)

    k = max(1, len(val) // 10)
    val_s = val.sort_values("score", ascending=False)
    top, bot, mid = val_s.head(k), val_s.tail(k), val_s.iloc[k:-k] if len(val_s) > 2 * k else val_s
    parts = [
        top.sample(n=min(100, len(top)), random_state=42).assign(band="top-decile 顶十分位", split="val 验证集"),
        bot.sample(n=min(50, len(bot)), random_state=43).assign(band="bottom-decile 底十分位", split="val 验证集"),
        mid.sample(n=min(50, len(mid)), random_state=44).assign(band="mid 中间分位", split="val 验证集"),
    ]
    samples = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["symbol", "signal_time"])
    if len(samples) < N_SAMPLE:
        need = N_SAMPLE - len(samples)
        keys = set(zip(samples["symbol"], samples["signal_time"].astype(str)))
        tr_s = train.sort_values("score", ascending=False).head(need * 4)
        tr_s = tr_s[~tr_s.apply(lambda r: (r["symbol"], str(r["signal_time"])) in keys, axis=1)]
        samples = pd.concat(
            [samples, tr_s.head(need).assign(band="top-decile 顶十分位(train补)", split="train 训练集")],
            ignore_index=True,
        )
    samples = samples.head(N_SAMPLE).reset_index(drop=True)

    groups = list_series(bar="15m")
    sym_paths: dict[str, list[Path]] = {}
    for (src, sym), paths in groups.items():
        if not str(sym).endswith("_USDT_SWAP"):
            continue
        if src == "okx":
            sym_paths[sym] = list(paths)
        elif sym not in sym_paths:
            sym_paths[sym] = list(paths)

    SAMP.mkdir(parents=True, exist_ok=True)
    for p in SAMP.glob("*.png"):
        p.unlink()

    cards = []
    miss = 0
    frame_cache: dict[str, pd.DataFrame] = {}
    for i, row in samples.iterrows():
        sym = str(row["symbol"])
        st = pd.Timestamp(row["signal_time"])
        st = st.tz_localize("UTC") if st.tzinfo is None else st.tz_convert("UTC")
        if sym not in frame_cache:
            paths = sym_paths.get(sym)
            if not paths:
                miss += 1
                continue
            fr = load_series(paths)
            if fr.empty:
                miss += 1
                continue
            frame_cache[sym] = add_mas(fr)
        fr = frame_cache[sym]
        times = pd.to_datetime(fr["open_time"], utc=True)
        hits = np.flatnonzero(times == st)
        if len(hits) == 0:
            diffs = (times - st).abs()
            si = int(diffs.argmin())
            if diffs.iloc[si] > pd.Timedelta(minutes=20):
                miss += 1
                continue
        else:
            si = int(hits[0])
        if si < WINDOW - 1:
            miss += 1
            continue
        win = fr.iloc[si - WINDOW + 1 : si + 1]
        if len(win) != WINDOW:
            miss += 1
            continue
        ret = float(row["realized_ret"])
        score = float(row["score"])
        tag = "pos" if ret > 0 else ("neg" if ret < 0 else "flat")
        fname = f"{len(cards)+1:03d}_{re.sub(r'[^A-Za-z0-9_]', '', sym)[:24]}_{st.strftime('%Y%m%d_%H%M')}_{tag}.png"
        try:
            render_chart(win, out_path=SAMP / fname)
        except Exception as exc:  # noqa: BLE001
            print("render fail", sym, exc)
            miss += 1
            continue
        cards.append(
            {
                "i": len(cards) + 1,
                "file": f"samples/{fname}",
                "symbol": sym,
                "signal_time": str(st),
                "score": round(score, 6),
                "realized_ret": round(ret, 6),
                "ret_pct": round(100 * ret, 2),
                "passed": bool(score >= thr),
                "band": str(row.get("band", "")),
                "split": str(row.get("split", "")),
                "label": int(row["label"]) if pd.notna(row.get("label")) else None,
            }
        )
        if len(cards) % 50 == 0:
            print(f"rendered {len(cards)}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "samples_manifest.json").write_text(
        json.dumps({"n": len(cards), "miss": miss, "threshold": thr, "cards": cards}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"cards={len(cards)} miss={miss} -> {SAMP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
