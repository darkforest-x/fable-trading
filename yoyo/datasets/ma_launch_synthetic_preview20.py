"""Render twenty explicitly synthetic MA-release examples for style review.

Owner specifically requested program-generated candles on 2026-09-21. The
price-series generator creates OHLC first; all six SMA/EMA curves are then
calculated from its close values. No real tickers, venues, returns, dates,
trained weights, or production thresholds are used. These positive-only
illustrations do not establish a usable training distribution or model edge.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from yoyo.datasets.ma_launch_preview20 import digest, dump, gallery, overview, render
from yoyo.datasets.ma_launch_synthetic_series import generate_case
from yoyo.datasets.ma_rope_filter import add_six_mas

ROOT=Path(__file__).resolve().parents[2]
EXPERIMENT="exp-ma-launch-synthetic-preview20-20260921-v1"
DIRECTORY=ROOT/"experiments/active"/EXPERIMENT


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output-name",default="preview_v1")
    parser.add_argument("--plan-name",default="plan.json")
    args=parser.parse_args();out=DIRECTORY/args.output_name
    plan_path=DIRECTORY/args.plan_name
    paths=["yoyo/datasets/ma_launch_synthetic_series.py","yoyo/datasets/ma_launch_synthetic_preview20.py",
           "yoyo/datasets/ma_launch_preview20.py",str(plan_path.relative_to(ROOT))]
    dirty=subprocess.check_output(["git","status","--porcelain","--",*paths],cwd=ROOT,text=True)
    if dirty:raise RuntimeError("Commit exact generator, renderer and plan before generation")
    if out.exists():raise RuntimeError("Refuse to overwrite an existing preview")
    for folder in [out,out/"full",out/"early",out/"ohlc"]:folder.mkdir()
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    rows=[]
    for case_id in range(1,21):
        raw,meta=generate_case(case_id);frame=add_six_mas(raw)
        row={**meta,"id":f"{case_id:02d}","symbol":f"SYNTH_{case_id:02d}","timeframe":"SIMULATED",
             "source_type":"synthetic","venue":None,"onset_open_ms":int(frame.ts.iloc[meta["onset_i"]]),
             "onset_time_local":meta["label_zh"]+" · 程序合成，非真实行情",
             "training_eligible":False,"production_eligible":False,"owner_confirmed":False}
        row["full_image"]=f"full/{case_id:02d}.png";row["early_image"]=f"early/{case_id:02d}.png"
        row["full_view"]=render(frame,row,out/row["full_image"])
        row["early_view"]=render(frame,row,out/row["early_image"],True)
        row["ohlc_csv"]=f"ohlc/{case_id:02d}.csv";frame.to_csv(out/row["ohlc_csv"],index=False)
        row["ohlc_sha256"]=digest(out/row["ohlc_csv"])
        names=[f"{kind}{period}" for period in (20,60,120) for kind in ("sma","ema")]
        before=frame.loc[row["onset_i"]-1,names]
        row["prelaunch_six_ma_spread_pct"]=float((before.max()-before.min())/frame.close.iloc[row["onset_i"]-1]*100)
        rows.append(row);print(f"Rendered synthetic {case_id}/20: {meta['family']}",flush=True)
    dump(out/"manifest.json",rows);overview(rows,out);gallery(rows,out)
    path=out/"index.html";markup=path.read_text()
    markup=markup.replace("均线收拢启动 · 20 张真实行情预览","均线收拢启动 · 20 张程序合成样本")
    markup=markup.replace("20 个真实 OKX 历史事件。红圈是建议关注的启动区域，等待你确认形态；全部保留较长左侧背景。",
                          "20 张程序合成样本。先生成 K 线，再计算 SMA/EMA 20、60、120；红圈标出设定的启动区域。不是实际币种或历史行情。")
    markup=markup.replace("完整走势含启动后的 40 根，仅供形态预览；启动视图截到建议启动根之后第 3 根，价格轴也只按当时数据计算。历史筛选使用了后续走势，因此这些图尚不是独立测试集或已批准金标。",
                          "这是五种结构、每种四例的合成正例预览。T0 为设定启动根；完整图展示合成后续走势，早期图只到 T+3，价格轴也按前缀计算。均线由合成收盘价计算。样式确认后仍需真实行情验证及反例，当前不具备训练资格。")
    markup=markup.replace("完整历史走势，包含后文","完整合成走势").replace("仅展示当时可见的启动附近","合成前缀：只到 T+3")
    path.write_text(markup)
    dump(out/"receipt.json",{"experiment_id":EXPERIMENT,"builder_commit":commit,
          "generated_at":datetime.now(timezone.utc).isoformat(),"synthetic_events":20,
          "price_source":"procedural_simulation_only","real_market_rows":0,
          "generator_sha256":digest(ROOT/paths[0]),"renderer_sha256":digest(ROOT/paths[2]),
          "plan_sha256":digest(plan_path),"plan_path":str(plan_path.relative_to(ROOT)),
          "manifest_sha256":digest(out/"manifest.json"),
          "training_eligible":False,"production_eligible":False})
    print(out/"index.html")


if __name__=="__main__":main()
