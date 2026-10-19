"""Nine VIDYA configurations from the 2026-10-11 backtest report, rebuilt for the monitor.

Owner request 2026-10-12: show every configuration of the report (A-I) in the monitor's
VIDYA 回踩 history view with the report's own numbers and the trades drawn on a candle
chart. Basis is the report's: entry at the confirming close, 0.05%/side fees plus 0.01%
slippage on market fills, 1% risk per trade compounding, ETHUSDT perpetual (Binance USD-M
archives) 2022-01-01 to 2026-10-10 UTC, 1h bars resampled from 15m.

  A/B  video rule: pivot-line retest, delta>=30%, stop 0.3 ATR, 2R target, Length 14 / 10
  C/D  same entry, stop 1.5 ATR, exit on trend flip, Length 14 / 10
  E    C without the delta filter
  F/G  A and C on 1h bars
  H/I  trend-line retest (yoyo/monitor/vidya_retest.py), Length 10, delta>=50%, stop 1 ATR,
       2R target / trend-flip exit with 0.2 ATR touch tolerance

Outputs (read by yoyo/monitor/vidya_retest_api.py):
  history_configs.json  per-config statistics, yearly rows and breakdowns
  history_trades.json   every trade, times as UTC epoch ms of the bar open
  data/vidya_retest/candles_ETH_{15m,1h}.npz   OHLC + trend line, trend and delta for
                        Length 10 and 14 (data/ is not in git; rebuilt from the CSV)

Every config is checked against the report's published trade count and mean R; a mismatch
aborts before anything is written.

Usage (data: data/vidya_retest/binance_um_ETHUSDT_15m.csv, columns time,open,high,low,close,volume):
  PYTHONPATH=. .venv/bin/python experiments/active/exp-vidya-report-nine-configs-20261011-v1/build.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
from pivot_retest import backtest as pivot_backtest, series  # noqa: E402
from yoyo.monitor.vidya_retest import Params, backtest as line_backtest, indicator  # noqa: E402

DATA = REPO / "data/vidya_retest"
CSV = DATA / "binance_um_ETHUSDT_15m.csv"
SPLIT = pd.Timestamp("2024-10-01", tz="UTC")
TZ = "Asia/Shanghai"
FIXED = dict(delta_th=30, k_atr=0.3, rr=2.0, exit_mode="fixed")
TREND = dict(delta_th=30, k_atr=1.5, exit_mode="trend")
CONFIGS = [
    ("A", "A 原版 L14", "15分钟 · 原版规则 · Length 14", "视频原版规则：回踩枢轴短线、Delta≥30% 入场，止损在入场K线外侧 0.3 倍 ATR，止盈 2 倍风险。", "15m", 14, "pivot", FIXED),
    ("B", "B 原版 L10", "15分钟 · 原版规则 · Length 10", "同 A，Length 换回默认的 10。", "15m", 10, "pivot", FIXED),
    ("C", "C 趋势离场 L14", "15分钟 · 趋势变色离场 · Length 14", "入场同 A；止损放宽到 1.5 倍 ATR；不设止盈，趋势云变色才离场。", "15m", 14, "pivot", TREND),
    ("D", "D 趋势离场 L10", "15分钟 · 趋势变色离场 · Length 10", "同 C，Length 换成 10。", "15m", 10, "pivot", TREND),
    ("E", "E 无 Delta 过滤", "15分钟 · 趋势变色离场 · 不用 Delta", "同 C，去掉 Delta≥30% 的条件。", "15m", 14, "pivot", dict(TREND, use_delta=False)),
    ("F", "F 1小时 原版", "1小时 · 原版规则 · Length 14", "规则同 A，换成 1 小时图。", "1h", 14, "pivot", FIXED),
    ("G", "G 1小时 趋势离场", "1小时 · 趋势变色离场 · Length 14", "规则同 C，换成 1 小时图。", "1h", 14, "pivot", TREND),
    ("H", "H 趋势线回踩 2R", "15分钟 · 只做趋势线回踩 · Length 10 · 2R", "只做价格回踩 VIDYA 趋势线本身，Delta≥50%，止损在两根 K 线极值外 1 倍 ATR，止盈 2 倍风险。", "15m", 10, "line",
     Params(length=10, delta_threshold=50.0, k_atr=1.0, rr=2.0, exit_mode="fixed", tol_atr=0.0)),
    ("I", "I 趋势线回踩 趋势离场", "15分钟 · 只做趋势线回踩 · Length 10 · 趋势离场", "入场同 H（碰线容差 0.2 ATR），不设止盈，趋势云变色收盘离场。", "15m", 10, "line",
     Params(length=10, delta_threshold=50.0, k_atr=1.0, rr=2.0, exit_mode="trend", tol_atr=0.2)),
]
# Published in the 2026-10-11 report (trades, mean net R rounded to 2 decimals).
REPORT = {"A": (653, -0.36), "B": (650, -0.33), "C": (433, -0.04), "D": (450, 0.03), "E": (1397, -0.09),
          "F": (188, 0.04), "G": (119, 0.17), "H": (53, 0.38), "I": (66, 0.62)}
REASON = {"target": "止盈", "stop": "止损", "trend": "趋势变色"}


def load():
    df = pd.read_csv(CSV, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    h1 = (df.set_index("time").resample("1h", label="left", closed="left")
          .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index())
    return {"15m": df, "1h": h1}


def run(frames, cfg):
    key, short, name, desc, tf, length, kind, kw = cfg
    df = frames[tf]
    if kind == "pivot":
        return pivot_backtest(df, series(df, length), **kw)
    return line_backtest(df, kw, fill="close", cost=0.001, slip=0.0001)


def ms(ts):
    return int(pd.Timestamp(ts).value // 1_000_000)


def summarize(cfg, t):
    key, short, name, desc, tf, length, kind, kw = cfg
    r = t["r"].to_numpy(float)
    eq = np.cumprod(1 + 0.01 * r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    streak = worst = 0
    for x in r:
        streak = streak + 1 if x < 0 else 0
        worst = max(worst, streak)
    entry = pd.to_datetime(t["entry_time"], utc=True)
    ho, rc = t[entry < SPLIT], t[entry >= SPLIT]
    exit_mode = kw.exit_mode if kind == "line" else kw["exit_mode"]
    rr = kw.rr if kind == "line" else kw.get("rr", 2.0)
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return dict(config=key, short=short, name=name, desc=desc, timeframe="15分钟" if tf == "15m" else "1小时",
                tf=tf, length=length, rule="trendline" if kind == "line" else "pivot",
                trades=int(len(r)), wins=int((r > 0).sum()), win_rate=round(float((r > 0).mean() * 100), 1),
                avg_R=round(float(r.mean()), 2), avg_R_gross=round(float(t["r_gross"].mean()), 2),
                cost_R=round(float((t["r_gross"] - t["r"]).mean()), 2), total_R=round(float(r.sum()), 1),
                pf=round(float(gains / losses), 2) if losses > 0 else None,
                ret_pct=round(float((eq[-1] - 1) * 100), 1), max_dd_pct=round(float((eq / peak - 1).min() * 100), 1),
                final_equity=round(float(eq[-1] * 100), 1), avg_risk_pct=round(float(t["risk_pct"].mean()), 2),
                avg_hold_h=round(float(t["bars"].mean() * (0.25 if tf == "15m" else 1.0)), 1),
                avg_win_R=round(float(r[r > 0].mean()), 2), avg_loss_R=round(float(r[r <= 0].mean()), 2),
                best_R=round(float(r.max()), 2), worst_R=round(float(r.min()), 2), max_losing_streak=int(worst),
                long_n=int((t.side == "long").sum()), short_n=int((t.side == "short").sum()),
                breakeven_wr=round(100 / (1 + rr), 1) if exit_mode == "fixed" else None,
                holdout_n=int(len(ho)), holdout_avgR=round(float(ho.r.mean()), 2),
                recent_n=int(len(rc)), recent_avgR=round(float(rc.r.mean()), 2))


def details(cfg, t):
    key, tf = cfg[0], cfg[4]
    bar_h = 0.25 if tf == "15m" else 1.0
    r = t["r"].to_numpy(float)
    eq = np.cumprod(1 + 0.01 * r)
    entry_bj = pd.to_datetime(t["entry_time"], utc=True).dt.tz_convert(TZ)
    yearly = []
    for y, g in t.groupby(entry_bj.dt.year):
        yearly.append(dict(year=int(y), n=int(len(g)), win_rate=round(float((g.r > 0).mean() * 100), 1),
                           avg_R=round(float(g.r.mean()), 2), sum_R=round(float(g.r.sum()), 1),
                           ret_pct=round(float((np.prod(1 + 0.01 * g.r) - 1) * 100), 1)))
    breakdown = []

    def add(dim, label, g, order):
        breakdown.append(dict(dim=dim, bucket=label, ord=order, n=int(len(g)), win_rate=round(float((g.r > 0).mean() * 100), 1),
                              avg_R=round(float(g.r.mean()), 2), avg_R_gross=round(float(g.r_gross.mean()), 2),
                              sum_R=round(float(g.r.sum()), 1)))
    for i, (side, label) in enumerate([("long", "做多"), ("short", "做空")]):
        g = t[t.side == side]
        if len(g):
            add("方向", label, g, i)
    for i, reason in enumerate(["target", "trend", "stop"]):
        g = t[t.reason == reason]
        if len(g):
            add("出场原因", REASON[reason], g, i)
    ad = t["delta"].abs()
    for i, (lo, hi, label) in enumerate([(0, 30, "低于 30%"), (30, 50, "30–50%"), (50, 70, "50–70%"),
                                          (70, 100, "70–100%"), (100, 1e9, "100% 以上")]):
        g = t[(ad >= lo) & (ad < hi)]
        if len(g):
            add("入场 Delta", label, g, i)
    trades = []
    for i, row in enumerate(t.itertuples()):
        target = row.target if row.target is not None and np.isfinite(row.target) else None
        trades.append(dict(tid=f"{key}-{i + 1:04d}", config=key, side="做多" if row.side == "long" else "做空",
                           signal_ms=ms(row.signal_time), entry_ms=ms(row.entry_time), exit_ms=ms(row.exit_time),
                           touch_ms=ms(row.touch_time) if hasattr(row, "touch_time") and row.touch_time is not None else None,
                           entry=round(float(row.entry), 4), stop=round(float(row.stop), 4),
                           target=round(float(target), 4) if target is not None else None, level=round(float(row.level), 4),
                           delta=round(float(row.delta), 1), stop_pct=round(float(row.risk_pct), 2),
                           exit=round(float(row.exit), 4), reason=REASON[row.reason], hold_h=round(float(row.bars * bar_h), 2),
                           R=round(float(row.r), 2), R_gross=round(float(row.r_gross), 2), equity=round(float(eq[i] * 100), 1)))
    return yearly, breakdown, trades


def write_candles(frames):
    for tf, df in frames.items():
        cols = {k: df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume")}
        out = {"t": (pd.to_datetime(df["time"], utc=True).astype("int64") // 1_000_000).to_numpy(np.int64),
               "o": cols["open"], "h": cols["high"], "l": cols["low"], "c": cols["close"]}
        for length in (10, 14):
            ind = indicator(cols["open"], cols["high"], cols["low"], cols["close"], cols["volume"], length=length)
            out[f"line{length}"] = ind["line"].astype(np.float64)
            out[f"up{length}"] = ind["up"].astype(np.bool_)
            out[f"delta{length}"] = ind["delta"].astype(np.float32)
        np.savez_compressed(DATA / f"candles_ETH_{tf}.npz", **out)
        print("candles", tf, len(df))


def main():
    frames = load()
    configs, trades, problems = [], [], []
    for cfg in CONFIGS:
        t = run(frames, cfg).reset_index(drop=True)
        s = summarize(cfg, t)
        want = REPORT[cfg[0]]
        if (s["trades"], s["avg_R"]) != want:
            problems.append(f"{cfg[0]}: got {s['trades']} trades / {s['avg_R']}R, report {want}")
        yearly, breakdown, rows = details(cfg, t)
        configs.append(dict(s, yearly=yearly, breakdown=breakdown))
        trades += rows
        print(cfg[0], s["trades"], s["avg_R"], s["ret_pct"], s["max_dd_pct"])
    if problems:
        raise SystemExit("report mismatch: " + "; ".join(problems))
    meta = {"experiment_id": HERE.name, "symbol": "ETHUSDT 永续（币安 USD-M 历史数据）",
            "first": str(frames["15m"].time.iloc[0]), "last": str(frames["15m"].time.iloc[-1]),
            "bars_15m": int(len(frames["15m"])), "fee_per_side": 0.0005, "slippage": 0.0001, "risk_fraction": 0.01,
            "fill": "confirming_close", "split": "2024-10-01"}
    (HERE / "history_configs.json").write_text(json.dumps({"meta": meta, "configs": configs}, ensure_ascii=False, indent=1))
    (HERE / "history_trades.json").write_text(json.dumps(trades, ensure_ascii=False, separators=(",", ":")))
    write_candles(frames)
    print("wrote", len(configs), "configs and", len(trades), "trades")


if __name__ == "__main__":
    main()
