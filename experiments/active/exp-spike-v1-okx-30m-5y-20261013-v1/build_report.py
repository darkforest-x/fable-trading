"""Owner-requested detailed HTML backtest for SPIKE 强劲爆发 V1 on OKX ETH/BTC 30m over five years.

Reads results_v1 written by build.py (ledgers, controls, bars with rope and protection, summary.json),
derives the report tables (periods, Beijing-time years, exit kind, hold time, R buckets, top-winner
dependence, 1%-risk compounding equity) and embeds them with every trade's candle window into
report_template.html, producing report_v1/index.html (self-contained, no network). Long trades are
drawn with merged candles so a window never exceeds MAX_CANDLES.

Usage (after build.py):
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1-okx-30m-5y-20261013-v1/build_report.py
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
PRE, POST, MAX_CANDLES = 60, 16, 260
SYMS = {"ETH_USDT_SWAP": "ETH", "BTC_USDT_SWAP": "BTC"}
REASONS = {"initial_stop": "初始止损", "trailing_stop": "跟踪止损", "entry_gap_through_stop": "开盘跳空过止损",
           "censored": "仍持仓（数据结束）"}
R_BUCKETS = [(-math.inf, -1, "≤ −1R"), (-1, 0, "−1 ~ 0R"), (0, 1, "0 ~ 1R"), (1, 2, "1 ~ 2R"), (2, 5, "2 ~ 5R"),
             (5, 10, "5 ~ 10R"), (10, math.inf, "≥ 10R")]


def fnum(x, d=4):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), d)


def stats(r) -> dict:
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
    eq = np.cumprod(1 + 0.01 * r)
    epk = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return {"trades": int(len(r)), "win_rate": fnum((r > 0).mean() * 100, 1), "avg_r": fnum(r.mean(), 3),
            "sum_r": fnum(r.sum(), 1), "pf": fnum(pos / neg, 2) if neg > 0 else None, "max_dd_r": fnum((cum - peak).min(), 1),
            "max_losing_streak": int(best), "avg_win": fnum(r[r > 0].mean(), 2) if (r > 0).any() else None,
            "avg_loss": fnum(r[r <= 0].mean(), 2) if (r <= 0).any() else None,
            "ret_pct": fnum((eq[-1] - 1) * 100, 1), "max_dd_pct": fnum((eq / epk - 1).min() * 100, 1)}


def load_trades() -> pd.DataFrame:
    parts = []
    for tag in SYMS.values():
        df = pd.read_csv(RES / f"{tag.lower()}_trades.csv.gz")
        df["sym"] = tag
        parts.append(df)
    t = pd.concat(parts, ignore_index=True)
    for col in ("signal_bar_open", "signal_close_time", "entry_time", "exit_time"):
        t[col] = pd.to_datetime(t[col], utc=True)
    return t


def load_bars(tag: str) -> pd.DataFrame:
    return pd.read_csv(RES / f"{tag.lower()}_bars_30m.csv.gz")


def window(bars: pd.DataFrame, t) -> tuple[list, int]:
    """Candle rows around one trade; merged k-to-1 when the window is long."""
    ms = bars.ts.to_numpy()
    si = int(np.searchsorted(ms, int(t.signal_bar_open.value // 1_000_000)))
    xi = int(np.searchsorted(ms, int(t.exit_time.value // 1_000_000)))
    a, b = max(0, si - PRE), min(len(bars), xi + POST + 1)
    sub = bars.iloc[a:b]
    k = max(1, math.ceil(len(sub) / MAX_CANDLES))
    d = 0 if t.entry_price >= 1000 else 2
    rows = []
    for s in range(0, len(sub), k):
        g = sub.iloc[s:s + k]
        last = g.iloc[-1]
        rows.append([int(g.ts.iloc[0]), round(float(g.open.iloc[0]), d + 1), round(float(g.high.max()), d + 1),
                     round(float(g.low.min()), d + 1), round(float(last.close), d + 1), fnum(last.protection, d + 1),
                     fnum(last.rope_high, d + 1), fnum(last.rope_low, d + 1)])
    return rows, k


def main():
    summary = json.loads((RES / "summary.json").read_text())
    cfg = summary["config"]
    start, split, end = (pd.Timestamp(cfg[k]) for k in ("start", "split", "end"))
    allt = load_trades()
    open_now = allt.loc[allt.censored.astype(bool)]
    t = allt.loc[~allt.censored.astype(bool)].sort_values(["exit_time", "entry_time"]).reset_index(drop=True)
    t["bj"] = t.signal_close_time.dt.tz_convert(BJ)
    t["year"] = t.bj.dt.year
    t["period"] = np.where(t.signal_close_time < split, "before", "after")

    def block(df):
        out = stats(df.net_r.to_numpy())
        ok = np.isfinite(df.excess_r.to_numpy(float))
        out["excess_r"] = fnum(df.excess_r[ok].mean(), 3) if ok.any() else None
        out["control_avg_r"] = fnum(df.control_net_r[ok].mean(), 3) if ok.any() else None
        return out

    scopes = {"ETH": t[t.sym == "ETH"], "BTC": t[t.sym == "BTC"], "ALL": t}
    yearly = [{"year": int(y), **{k: block(df[df.year == y]) for k, df in scopes.items()}} for y in range(start.year, end.year + 1)]
    groups = {
        "reason": [{"label": REASONS.get(k, k), **{s: stats(df.net_r[df.exit_kind == k]) for s, df in scopes.items()}}
                   for k in ("initial_stop", "trailing_stop", "entry_gap_through_stop") if (t.exit_kind == k).any()],
        "hold": [{"label": lbl, **{s: stats(df.net_r[((df.exit_time - df.entry_time).dt.total_seconds() / 3600 >= lo)
                                                     & ((df.exit_time - df.entry_time).dt.total_seconds() / 3600 < hi)]) for s, df in scopes.items()}}
                 for lbl, lo, hi in (("< 12 小时", 0, 12), ("12–48 小时", 12, 48), ("2–7 天", 48, 168), ("≥ 7 天", 168, 1e9))],
        "buckets": [{"label": lbl, **{s: {"n": int(((df.net_r >= lo) & (df.net_r < hi)).sum()),
                                          "sum_r": fnum(df.net_r[(df.net_r >= lo) & (df.net_r < hi)].sum(), 1)} for s, df in scopes.items()}}
                    for lo, hi, lbl in R_BUCKETS],
    }

    def dependence(df):
        r = np.sort(df.net_r.to_numpy(float))[::-1]
        h = (df.exit_time - df.entry_time).dt.total_seconds().to_numpy() / 3600
        return {"best1": fnum(r[:1].sum(), 1), "without_best1": fnum(r[1:].sum(), 1), "best3_sum": fnum(r[:3].sum(), 1),
                "without_best3": fnum(r[3:].sum(), 1), "ge5r": int((r >= 5).sum()), "ge10r": int((r >= 10).sum()),
                "max_r": fnum(r[0], 2) if len(r) else None, "median_hold_h": fnum(np.median(h), 1) if len(h) else None,
                "median_risk_pct": fnum(df.risk_fraction_at_entry.median() * 100, 2) if len(df) else None,
                "cost_r": fnum((df.gross_r - df.net_r).mean(), 3) if len(df) else None,
                "gross_avg_r": fnum(df.gross_r.mean(), 3) if len(df) else None}

    bars = {tag: load_bars(tag) for tag in SYMS.values()}
    overview = {}
    for tag, b in bars.items():
        d = (b.assign(t=pd.to_datetime(b.ts, unit="ms", utc=True)).set_index("t")
             .loc[lambda x: (x.index >= start - pd.Timedelta(days=3)) & (x.index < end)]
             .resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
        overview[tag] = [[int(ts.value // 1_000_000), *(round(float(x), 2) for x in row)] for ts, row in zip(d.index, d.to_numpy())]

    rows, cum, eq, symcum = [], 0.0, 1.0, {"ETH": 0.0, "BTC": 0.0}
    for n, x in enumerate(t.itertuples(index=False), 1):
        cum += x.net_r
        eq *= 1 + 0.01 * x.net_r
        symcum[x.sym] += x.net_r
        candles, k = window(bars[x.sym], x)
        rows.append({"n": n, "sym": x.sym, "side": 1, "signal_ms": int(x.signal_close_time.value // 1_000_000),
                     "signal_open_ms": int(x.signal_bar_open.value // 1_000_000), "entry_ms": int(x.entry_time.value // 1_000_000),
                     "exit_ms": int(x.exit_time.value // 1_000_000), "entry": fnum(x.entry_price, 4), "stop": fnum(x.initial_stop, 4),
                     "exit": fnum(x.exit_price, 4), "reason": REASONS.get(x.exit_kind, x.exit_kind), "reason_key": x.exit_kind,
                     "r": fnum(x.net_r, 3), "r_gross": fnum(x.gross_r, 3), "risk_pct": fnum(x.risk_fraction_at_entry * 100, 2),
                     "risk_atr": fnum(x.risk_atr, 2), "mfe": fnum(x.mfe_r, 2), "control_r": fnum(x.control_net_r, 3),
                     "vol_ratio": fnum(x.volume_ratio, 2), "expansion": fnum(x.tr_atr_expansion, 2), "quiet": int(x.density_duration),
                     "cum_r": fnum(cum, 2), "sym_cum_r": fnum(symcum[x.sym], 2), "equity": fnum(eq * 100, 2),
                     "hold_h": fnum((x.exit_time - x.entry_time).total_seconds() / 3600, 2), "period": x.period, "k": k, "candles": candles})
    c = summary["cases"]
    payload = {"meta": {"start": cfg["start"], "end": cfg["end"], "split": cfg["split"], "generated_at": summary["generated_at"],
                        "git_head": summary["git_head"], "parity": summary["parity"],
                        "receipts": {SYMS[s]: c[s]["receipt"] for s in SYMS},
                        "sim_diff": {SYMS[s]: c[s]["sim_vs_ledger_max_abs_diff_r"] for s in SYMS},
                        "open_at_end": [{"sym": x.sym, "signal_ms": int(x.signal_close_time.value // 1_000_000), "entry": fnum(x.entry_price, 4),
                                         "stop": fnum(x.initial_stop, 4)} for x in open_now.itertuples()]},
               "cases": {SYMS[s]: {p: c[s][p] for p in ("full", "before", "after")} for s in SYMS} | {"ALL": summary["combined"]},
               "yearly": yearly, "groups": groups, "dependence": {s: dependence(df) for s, df in scopes.items()},
               "overview": overview, "trades": rows}
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    OUTDIR.mkdir(exist_ok=True)
    html = (HERE / "report_template.html").read_text().replace("__DATA__", blob)
    (OUTDIR / "index.html").write_text(html)
    print("wrote", OUTDIR / "index.html", f"{len(html) / 1e6:.2f} MB", "trades", len(rows))


if __name__ == "__main__":
    main()
