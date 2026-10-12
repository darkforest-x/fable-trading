"""Owner-requested detailed HTML backtest for the five-year V12.10 ETH 15m replay.

Reads results_v1 written by build.py (ledgers, controls, summary.json, bars_15m.csv.gz), derives the
report tables (years and months in Beijing time, side, exit reason, hold time, R buckets, what the
VIDYA gate removed, top-winner dependence, 1%-risk compounding equity) and embeds them with every
trade's candle window into report_template.html, producing report_v1/index.html (self-contained,
no network). Long trades are drawn with merged candles so a window never exceeds MAX_CANDLES.

Usage (after build.py):
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1210-eth15m-5y-20261012-v1/build_report.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RES = HERE / "results_v1"
OUTDIR = HERE / "report_v1"
BJ = "Asia/Shanghai"
PRE, POST, MAX_CANDLES = 48, 16, 260
REASONS = {"initial_stop": "初始止损", "trailing_stop": "跟踪止损", "opposite_v6_next_open": "反向信号离场",
           "boundary_mark": "仍持仓（数据结束）"}
R_BUCKETS = [(-math.inf, -1, "≤ −1R"), (-1, 0, "−1 ~ 0R"), (0, 1, "0 ~ 1R"), (1, 2, "1 ~ 2R"), (2, 5, "2 ~ 5R"),
             (5, 10, "5 ~ 10R"), (10, math.inf, "≥ 10R")]


def fnum(x, d=4):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), d)


def stats(r: np.ndarray) -> dict:
    r = np.asarray(r, float)
    if not len(r):
        return {"trades": 0}
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    streak = best = 0
    for v in r:
        streak = streak + 1 if v < 0 else 0
        best = max(best, streak)
    return {"trades": int(len(r)), "win_rate": fnum((r > 0).mean() * 100, 1), "avg_r": fnum(r.mean(), 3),
            "sum_r": fnum(r.sum(), 1), "pf": fnum(pos / neg, 2) if neg > 0 else None, "max_dd_r": fnum((cum - peak).min(), 1),
            "max_losing_streak": int(best), "avg_win": fnum(r[r > 0].mean(), 2) if (r > 0).any() else None,
            "avg_loss": fnum(r[r <= 0].mean(), 2) if (r <= 0).any() else None}


def equity_1pct(r: np.ndarray) -> dict:
    eq = np.cumprod(1 + 0.01 * np.asarray(r, float))
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return {"ret_pct": fnum((eq[-1] - 1) * 100, 1), "max_dd_pct": fnum((eq / peak - 1).min() * 100, 1)}


def load(name: str) -> pd.DataFrame:
    df = pd.read_csv(RES / f"{name}.csv.gz")
    for col in ("signal_close", "signal_bar_open", "entry_time", "exit_time"):
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=True)
    return df


def closed(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[~df.censored.astype(bool)].sort_values(["exit_time", "trade_key"]).reset_index(drop=True)


def with_controls(trades: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    m = controls.set_index("trade_key").control_net_r if len(controls) else pd.Series(dtype=float)
    out = trades.copy()
    out["control_net_r"] = out.trade_key.map(m)
    return out


def window(bars: pd.DataFrame, t: pd.Series) -> tuple[list, int]:
    """Candle rows around one trade; merged k-to-1 when the window is long."""
    ms = bars.ts.to_numpy()
    si = int(np.searchsorted(ms, int(t.signal_bar_open.value // 1_000_000)))
    xi = int(np.searchsorted(ms, int(t.exit_time.value // 1_000_000)))
    a, b = max(0, si - PRE), min(len(bars), xi + POST + 1)
    sub = bars.iloc[a:b]
    k = max(1, math.ceil(len(sub) / MAX_CANDLES))
    rows = []
    for s in range(0, len(sub), k):
        g = sub.iloc[s:s + k]
        last = g.iloc[-1]
        rows.append([int(g.ts.iloc[0]), round(float(g.open.iloc[0]), 2), round(float(g.high.max()), 2), round(float(g.low.min()), 2),
                     round(float(last.close), 2), fnum(last.vidya_line, 2), int(bool(last.vidya_up)),
                     fnum(last.rope_high, 2), fnum(last.rope_low, 2)])
    return rows, k


def main():
    summary = json.loads((RES / "summary.json").read_text())
    cfg = summary["config"]
    start, split, end = (pd.Timestamp(cfg[k]) for k in ("start", "split", "end"))
    bars = pd.read_csv(RES / "bars_15m.csv.gz")
    gate = with_controls(closed(load("okx_vidya_gate_trades")), load("okx_vidya_gate_controls"))
    base = with_controls(closed(load("okx_baseline_trades")), load("okx_baseline_controls"))
    open_now = load("okx_vidya_gate_trades")
    open_now = open_now.loc[open_now.censored.astype(bool)]
    for df in (gate, base):
        df["bj"] = df.signal_close.dt.tz_convert(BJ)
        df["year"] = df.bj.dt.year
        df["month"] = df.bj.dt.strftime("%Y-%m")
        df["period"] = np.where(df.signal_close < split, "before", "after")
    r = gate.net_r.to_numpy(float)

    def block(df):
        out: dict = {"all": stats(df.net_r.to_numpy()), **equity_1pct(df.net_r.to_numpy())} if len(df) else {"all": {"trades": 0}}
        ctl = df.control_net_r.to_numpy(float)
        ok = np.isfinite(ctl)
        out["control_avg_r"] = fnum(ctl[ok].mean(), 3) if ok.any() else None
        out["excess_r"] = fnum((df.net_r.to_numpy()[ok] - ctl[ok]).mean(), 3) if ok.any() else None
        return out

    years = sorted(set(gate.year) | set(base.year))
    yearly = [{"year": int(y), "gate": block(gate[gate.year == y]), "base": block(base[base.year == y])} for y in years]
    months = pd.period_range(start.tz_convert(BJ).strftime("%Y-%m"), end.tz_convert(BJ).strftime("%Y-%m"), freq="M").astype(str)
    monthly = [{"m": m, "n": int((gate.month == m).sum()), "sum_r": fnum(gate.net_r[gate.month == m].sum(), 2)} for m in months]
    hold_h = (gate.exit_time - gate.entry_time).dt.total_seconds().to_numpy() / 3600
    groups = {
        "side": [{"label": lbl, **stats(gate.net_r[gate.side == s].to_numpy())} for s, lbl in ((1, "做多"), (-1, "做空"))],
        "reason": [{"label": REASONS.get(k, k), **stats(gate.net_r[gate.exit_reason == k].to_numpy())}
                   for k in ("initial_stop", "trailing_stop", "opposite_v6_next_open") if (gate.exit_reason == k).any()],
        "hold": [{"label": lbl, **stats(gate.net_r[(hold_h >= lo) & (hold_h < hi)].to_numpy())}
                 for lbl, lo, hi in (("< 4 小时", 0, 4), ("4–12 小时", 4, 12), ("12–48 小时", 12, 48), ("≥ 48 小时", 48, 1e9))],
        "buckets": [{"label": lbl, "n": int(((r >= lo) & (r < hi)).sum()), "sum_r": fnum(r[(r >= lo) & (r < hi)].sum(), 1)} for lo, hi, lbl in R_BUCKETS],
    }
    srt = np.sort(r)[::-1]
    dependence = {"best5_sum": fnum(srt[:5].sum(), 1), "without_best5": fnum(srt[5:].sum(), 1),
                  "without_best10": fnum(srt[10:].sum(), 1), "ge5r": int((r >= 5).sum()), "ge10r": int((r >= 10).sum()),
                  "max_r": fnum(srt[0], 2), "median_hold_h": fnum(np.median(hold_h), 1), "median_risk_pct": fnum(gate.initial_risk_frac.median() * 100, 2),
                  "cost_r": fnum((gate.gross_r - gate.net_r).mean(), 3), "gross_avg_r": fnum(gate.gross_r.mean(), 3)}
    removed = base.loc[~base.signal_close.isin(set(gate.signal_close))]
    gate_effect = {"removed": stats(removed.net_r.to_numpy()), "removed_long": stats(removed.net_r[removed.side == 1].to_numpy()),
                   "removed_short": stats(removed.net_r[removed.side == -1].to_numpy()),
                   "new_only": stats(gate.loc[~gate.signal_close.isin(set(base.signal_close))].net_r.to_numpy())}
    periods = {v: {p: block(df[df.period == p]) for p in ("before", "after")} | {"full": block(df)} for v, df in (("gate", gate), ("base", base))}

    daily = (bars.assign(t=pd.to_datetime(bars.ts, unit="ms", utc=True)).set_index("t")
             .loc[lambda d: (d.index >= start - pd.Timedelta(days=3)) & (d.index < end)]
             .resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
    overview = [[int(t.value // 1_000_000), *(round(float(x), 2) for x in row)] for t, row in zip(daily.index, daily.to_numpy())]

    rows, cum, eq = [], 0.0, 1.0
    for n, t in enumerate(gate.itertuples(index=False), 1):
        cum += t.net_r
        eq *= 1 + 0.01 * t.net_r
        candles, k = window(bars, pd.Series(t._asdict()))
        rows.append({"n": n, "side": int(t.side), "signal_ms": int(t.signal_close.value // 1_000_000), "entry_ms": int(t.entry_time.value // 1_000_000),
                     "exit_ms": int(t.exit_time.value // 1_000_000), "entry": fnum(t.entry_price, 2), "stop": fnum(t.initial_stop, 2),
                     "exit": fnum(t.exit_price, 2), "reason": REASONS.get(t.exit_reason, t.exit_reason), "reason_key": t.exit_reason,
                     "r": fnum(t.net_r, 3), "r_gross": fnum(t.gross_r, 3), "risk_pct": fnum(t.initial_risk_frac * 100, 2),
                     "mfe": fnum(t.mfe_known_r, 2), "peak": fnum(t.close_peak_r, 2), "armed": bool(t.trail_armed),
                     "control_r": fnum(t.control_net_r, 3), "cum_r": fnum(cum, 2), "equity": fnum(eq * 100, 2),
                     "hold_h": fnum((t.exit_time - t.entry_time).total_seconds() / 3600, 2), "period": t.period, "k": k, "candles": candles})
    c = summary["cases"]
    payload = {"meta": {"start": cfg["start"], "end": cfg["end"], "split": cfg["split"], "generated_at": summary["generated_at"],
                        "git_head": summary["git_head"], "parity": summary["parity"], "data": summary["data"],
                        "okx": {k: c["okx"][k] for k in ("gate_counts", "incomplete_15m_buckets", "data_gap_bars", "first_bar", "last_bar")},
                        "open_at_end": [{"side": int(x.side), "signal_ms": int(x.signal_close.value // 1_000_000), "entry": fnum(x.entry_price, 2),
                                         "stop": fnum(x.initial_stop, 2)} for x in open_now.itertuples()]},
               "metric": {"okx": c["okx"], "binance": c["binance"]}, "periods": periods, "yearly": yearly, "monthly": monthly,
               "groups": groups, "dependence": dependence, "gate_effect": gate_effect, "overview": overview, "trades": rows,
               "base_curve": [[int(t.exit_time.value // 1_000_000), fnum(v, 2)] for t, v in zip(base.itertuples(), np.cumsum(base.net_r.to_numpy()))]}
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    OUTDIR.mkdir(exist_ok=True)
    html = (HERE / "report_template.html").read_text().replace("__DATA__", blob)
    (OUTDIR / "index.html").write_text(html)
    print("wrote", OUTDIR / "index.html", f"{len(html) / 1e6:.2f} MB", "trades", len(rows))


if __name__ == "__main__":
    main()
