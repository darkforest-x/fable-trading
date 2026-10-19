"""Configuration H deep dive (VIDYA trend-line retest, 2R) as one self-contained HTML file.

Owner request 2026-10-12: explain H's logic and write a detailed backtest analysis in HTML with
candle charts. Basis is the report's (and the monitor history view's): entry at the confirming
close, 0.05% per side fees plus 0.01% slippage on market fills (entry and stop), 1% risk per trade
compounding, ETHUSDT perpetual 15m from Binance USD-M archives 2022-01-01 to 2026-10-10 UTC.
Rules come from yoyo/monitor/vidya_retest.py (the code the live paper worker runs); the script
aborts unless H reproduces the report (53 trades, mean +0.38R).

The page embeds every number and candle it draws (no network, no build step).

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-vidya-report-nine-configs-20261011-v1/build_h_analysis.py
Output: H_analysis.html next to this file.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.monitor.vidya_retest import Params, State, Strategy, indicator

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[2] / "data/vidya_retest"
H = Params(length=10, delta_threshold=50.0, k_atr=1.0, rr=2.0, exit_mode="fixed", tol_atr=0.0)
FEE, SLIP, WARMUP = 0.0005, 0.0001, 1000
SPLIT_MS = int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
BJ = timezone(timedelta(hours=8))
RNG = np.random.default_rng(20261012)
BAR = 900_000


def load(sym):
    df = pd.read_csv(DATA / f"binance_um_{sym}USDT_15m.csv", parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    df["ms"] = (df["time"].astype("int64") // 1_000_000).astype("int64")
    return df


def run(df, p, fill="close", cost=2 * FEE, slip=SLIP):
    bars = {k: df[c].to_numpy(float) for k, c in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))}
    ind = indicator(bars["o"], bars["h"], bars["l"], bars["c"], df["volume"].to_numpy(float),
                    length=p.length, momentum=p.momentum, distance=p.distance)
    strat, st, out = Strategy(p, cost=cost, fill=fill, slip=slip), State(), []
    for i in range(WARMUP, len(df)):
        for ev in strat.on_bar(i, bars, ind, st):
            if ev["kind"] == "exit":
                out.append(ev)
    return out, ind, bars


def stats(trades):
    r = np.array([t["r"] for t in trades], float)
    if not len(r):
        return {"trades": 0}
    eq = np.cumprod(1 + 0.01 * r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"trades": int(len(r)), "win_rate": round(float((r > 0).mean() * 100), 1), "avg_r": round(float(r.mean()), 3),
            "total_r": round(float(r.sum()), 1), "pf": round(float(gains / losses), 2) if losses > 0 else None,
            "ret_pct": round(float((eq[-1] - 1) * 100), 1), "max_dd_pct": round(float((eq / peak - 1).min() * 100), 1)}


def random_control(df, trades, ind, bars, draws=20, resamples=2000):
    """Same month, same trend side, same stop distance (%), same 2R target and costs; entry at a random close."""
    up, h, l, c = ind["up"], bars["h"], bars["l"], bars["c"]
    month = df["time"].dt.strftime("%Y-%m").to_numpy()
    per_trade = []
    for t in trades:
        i, long = t["signal_index"], t["side"] == "long"
        pool = np.where((month == month[i]) & (up == long))[0]
        pool = pool[(pool != i) & (pool >= WARMUP) & (pool < len(df) - 2)]
        res = []
        for j in RNG.choice(pool, size=min(draws, len(pool)), replace=False) if len(pool) else []:
            e = c[j] * (1 + SLIP if long else 1 - SLIP)
            risk = e * t["risk"] / t["entry"]
            stop, target = (e - risk, e + 2 * risk) if long else (e + risk, e - 2 * risk)
            px = None
            for k in range(j + 1, len(df)):
                if (l[k] <= stop) if long else (h[k] >= stop):
                    px = stop * (1 - SLIP if long else 1 + SLIP)
                elif (h[k] >= target) if long else (l[k] <= target):
                    px = target
                if px is not None:
                    break
            px = c[-1] if px is None else px
            gross = (px - e) if long else (e - px)
            res.append((gross - FEE * (e + px)) / risk)
        per_trade.append(res)
    flat = np.concatenate([np.asarray(x) for x in per_trade if x])
    real = float(np.mean([t["r"] for t in trades]))
    means = np.array([np.mean([RNG.choice(x) for x in per_trade if x]) for _ in range(resamples)])
    return {"random_avg_r": round(float(flat.mean()), 3), "random_win_rate": round(float((flat > 0).mean() * 100), 1),
            "excess_r": round(real - float(flat.mean()), 3), "p_value": round(float((means >= real).mean()), 4),
            "draws": int(len(flat))}


def trade_rows(df, trades, ind, bars):
    rows, eq = [], 1.0
    for n, t in enumerate(trades, 1):
        eq *= 1 + 0.01 * t["r"]
        si, ti, xi = t["signal_index"], t["touch_index"], t["index"]
        a, b = max(0, ti - 56), min(len(df), xi + 28)
        win = [[int(df.ms[k]), round(bars["o"][k], 2), round(bars["h"][k], 2), round(bars["l"][k], 2), round(bars["c"][k], 2),
                (round(float(ind["line"][k]), 2) if np.isfinite(ind["line"][k]) else None), bool(ind["up"][k]),
                round(float(ind["delta"][k]), 1)] for k in range(a, b)]
        rows.append({"n": n, "side": t["side"], "touch_ms": int(df.ms[ti]), "signal_ms": int(df.ms[si]), "exit_ms": int(df.ms[xi]),
                     "pending": bool(ti != si), "entry": round(t["entry"], 2), "stop": round(t["stop"], 2),
                     "target": round(t["target"], 2), "exit": round(t["price"], 2), "level": round(t["level"], 2),
                     "delta": round(t["delta"], 1), "risk_pct": round(t["risk"] / t["entry"] * 100, 2),
                     "reason": {"target": "止盈", "stop": "止损"}[t["reason"]], "bars": int(xi - si),
                     "r": round(t["r"], 3), "r_gross": round(t["r_gross"], 3), "equity": round(eq * 100, 2),
                     "atr": round(float(ind["atr14"][si]), 2), "candles": win})
    return rows


def main():
    eth = load("ETH")
    trades, ind, bars = run(eth, H)
    s = stats(trades)
    if (s["trades"], round(s["avg_r"], 2)) != (53, 0.38):
        raise SystemExit(f"H does not reproduce the report: {s}")
    rows = trade_rows(eth, trades, ind, bars)
    entry_ms = np.array([r["signal_ms"] for r in rows])
    bj = [datetime.fromtimestamp(ms / 1000, BJ) for ms in entry_ms]
    years = np.array([d.year for d in bj])
    ym = np.array([d.strftime("%Y-%m") for d in bj])
    rs = np.array([r["r"] for r in rows])
    yearly = [{"year": int(y), **stats([t for t, yy in zip(trades, years) if yy == y])} for y in sorted(set(years))]
    months = [f"{y}-{m:02d}" for y in range(bj[0].year, 2027) for m in range(1, 13) if ym[0] <= f"{y}-{m:02d}" <= "2026-10"]
    monthly = [{"m": m, "n": int((ym == m).sum()), "sum_r": round(float(rs[ym == m].sum()), 2)} for m in months]

    def group(label, mask):
        sub = [t for t, k in zip(trades, mask) if k]
        return {"label": label, **stats(sub)}
    side = [group("做多", [r["side"] == "long" for r in rows]), group("做空", [r["side"] == "short" for r in rows])]
    confirm = [group("碰线 K 线当根确认", [not r["pending"] for r in rows]), group("等下一根确认", [r["pending"] for r in rows])]
    ad = np.abs([r["delta"] for r in rows])
    delta_b = [group(lbl, (ad >= lo) & (ad < hi)) for lbl, lo, hi in [("50–60%", 50, 60), ("60–75%", 60, 75), ("75–100%", 75, 100), ("100% 以上", 100, 1e9)]]
    rp = np.array([r["risk_pct"] for r in rows])
    stop_b = [group(lbl, (rp >= lo) & (rp < hi)) for lbl, lo, hi in [("< 1%", 0, 1), ("1–1.5%", 1, 1.5), ("1.5–2.5%", 1.5, 2.5), ("≥ 2.5%", 2.5, 99)]]
    hold = np.array([r["bars"] for r in rows]) * 0.25
    hold_b = [group(lbl, (hold >= lo) & (hold < hi)) for lbl, lo, hi in [("< 4 小时", 0, 4), ("4–12 小时", 4, 12), ("12–24 小时", 12, 24), ("≥ 24 小时", 24, 1e9)]]
    split = {"before": stats([t for t, ms in zip(trades, entry_ms) if ms < SPLIT_MS]), "after": stats([t for t, ms in zip(trades, entry_ms) if ms >= SPLIT_MS])}

    def sweep(name, values, make, fmt):
        out = []
        for v in values:
            tr, _, _ = run(eth, make(v))
            out.append({"value": fmt(v), "current": make(v) == H, **stats(tr)})
        print("sweep", name, [(o["value"], o["trades"], o["avg_r"]) for o in out])
        return out
    sweeps = {
        "delta": sweep("delta", [0, 10, 20, 30, 40, 50, 60, 70], lambda v: replace(H, delta_threshold=float(v)), lambda v: f"{v}%"),
        "stop": sweep("stop", [0.3, 0.5, 1.0, 1.5, 2.0], lambda v: replace(H, k_atr=v), lambda v: f"{v} ATR"),
        "rr": sweep("rr", [1.0, 1.5, 2.0, 2.5, 3.0], lambda v: replace(H, rr=v), lambda v: f"{v}R"),
        "length": sweep("length", [8, 9, 10, 11, 12, 13, 14], lambda v: replace(H, length=v), lambda v: f"L{v}"),
        "tol": sweep("tol", [0.0, 0.1, 0.2, 0.3], lambda v: replace(H, tol_atr=v), lambda v: f"{v} ATR"),
    }
    nxt, _, _ = run(eth, H, fill="next_open", cost=0.002, slip=0.0)
    basis = [{"label": "报告口径：确认收盘价入场，每边 0.05% + 0.01% 滑点", **s},
             {"label": "本仓标准口径：下一根开盘成交，20bp 往返", **stats(nxt)}]
    control = random_control(eth, trades, ind, bars)
    coins = [{"coin": "ETH", **s, "split": split}]
    for sym in ("BTC", "SOL"):
        df = load(sym)
        tr, _, _ = run(df, H)
        ms = df["ms"].to_numpy()
        coins.append({"coin": sym, **stats(tr), "split": {"before": stats([t for t in tr if ms[t["signal_index"]] < SPLIT_MS]),
                                                          "after": stats([t for t in tr if ms[t["signal_index"]] >= SPLIT_MS])}})
    daily = (eth.set_index("time").resample("1D", label="left", closed="left")
             .agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
    day_ms = (daily.index.asi8 // 1_000_000).tolist()
    overview = [[int(t), *(round(float(x), 2) for x in r)] for t, r in zip(day_ms, daily[["open", "high", "low", "close"]].to_numpy())]
    payload = {"summary": s, "split": split, "yearly": yearly, "monthly": monthly, "side": side, "confirm": confirm,
               "delta_buckets": delta_b, "stop_buckets": stop_b, "hold_buckets": hold_b, "sweeps": sweeps, "basis": basis,
               "control": control, "coins": coins, "overview": overview, "trades": rows,
               "extra": {"avg_win": round(float(rs[rs > 0].mean()), 3), "avg_loss": round(float(rs[rs <= 0].mean()), 3),
                         "avg_risk_pct": round(float(np.mean(rp)), 2), "median_hold_h": round(float(np.median(hold)), 2),
                         "max_hold_h": round(float(hold.max()), 2), "pending_share": round(float(np.mean([r["pending"] for r in rows]) * 100), 1),
                         "first": str(eth.time.iloc[0])[:10], "last": str(eth.time.iloc[-1])[:10], "bars": int(len(eth))}}
    html = (HERE / "h_analysis_template.html").read_text()
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (HERE / "H_analysis.html").write_text(html.replace("__DATA__", blob))
    print("wrote", HERE / "H_analysis.html", f"{len(blob) / 1e6:.2f} MB data", "control", control)


if __name__ == "__main__":
    main()
