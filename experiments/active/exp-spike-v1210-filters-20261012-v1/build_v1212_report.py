"""Owner-requested detailed HTML backtest for SPIKE V12.12 on OKX ETHUSDT.P 15m, five years (2026-10-13).

V12.12 = V12.10 + filter F1 (15m, trade only on the side of SMA(close, 800)). Its ETH trades are the F1 variant
of this experiment's build.py (results_v1/trades.csv.gz, case eth_okx), whose V12.10 arm equals the five-year
study's ledger trade for trade. The comparison line is V12.10 on the same data. Random controls are the
F1-and-VIDYA-state matched draws of build.py. Table derivations and candle windows reuse the five-year report
builder (exp-spike-v1210-eth15m-5y-20261012-v1/build_report.py); the page is report_v1212_template.html.

Honesty notes carried into the page: F1's threshold was found on this ETH data, so the ETH numbers are
in-sample; the out-of-sample evidence is the 20-alt pool reported in results_v1/summary.json.

Usage (after build.py):
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1210-filters-20261012-v1/build_v1212_report.py
Output: report_v1212/index.html
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
RES = HERE / "results_v1"
OUTDIR = HERE / "report_v1212"
ETH5Y = ROOT / "experiments/active/exp-spike-v1210-eth15m-5y-20261012-v1"
NEW, OLD = "F1_trend8d", "V12.10"

_spec = importlib.util.spec_from_file_location("v1210_5y_report", ETH5Y / "build_report.py")
assert _spec is not None and _spec.loader is not None
rep = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rep
_spec.loader.exec_module(rep)


def ledger(trades: pd.DataFrame, controls: pd.DataFrame, variant: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closed trades with matched control R, trades still open at the end) for one ETH OKX variant."""
    t = trades.loc[(trades.case == "eth_okx") & (trades.variant == variant)].copy()
    for col in ("signal_close", "signal_bar_open", "entry_time", "exit_time"):
        t[col] = pd.to_datetime(t[col], utc=True)
    c = controls.loc[(controls.case == "eth_okx") & (controls.variant == variant)]
    t["control_net_r"] = t.trade_key.map(c.set_index("trade_key").control_net_r)
    return rep.closed(t), t.loc[t.censored.astype(bool)]


def main():
    summary = json.loads((RES / "summary.json").read_text())
    cfg = summary["config"]["cases"]["eth_okx"]
    start, end = (pd.Timestamp(x) for x in cfg["window"])
    split = pd.Timestamp(cfg["split"])
    trades, controls = pd.read_csv(RES / "trades.csv.gz"), pd.read_csv(RES / "controls.csv.gz")
    gate, open_now = ledger(trades, controls, NEW)
    base, _ = ledger(trades, controls, OLD)
    bars = pd.read_csv(ETH5Y / "results_v1/bars_15m.csv.gz")
    for df in (gate, base):
        df["bj"] = df.signal_close.dt.tz_convert(rep.BJ)
        df["year"] = df.bj.dt.year
        df["month"] = df.bj.dt.strftime("%Y-%m")
        df["period"] = np.where(df.signal_close < split, "before", "after")
    r = gate.net_r.to_numpy(float)

    def block(df):
        out: dict = {"all": rep.stats(df.net_r.to_numpy()), **rep.equity_1pct(df.net_r.to_numpy())} if len(df) else {"all": {"trades": 0}}
        ctl = df.control_net_r.to_numpy(float)
        ok = np.isfinite(ctl)
        out["control_avg_r"] = rep.fnum(ctl[ok].mean(), 3) if ok.any() else None
        out["excess_r"] = rep.fnum((df.net_r.to_numpy()[ok] - ctl[ok]).mean(), 3) if ok.any() else None
        return out

    years = sorted(set(gate.year) | set(base.year))
    yearly = [{"year": int(y), "gate": block(gate[gate.year == y]), "base": block(base[base.year == y])} for y in years]
    months = pd.period_range(start.tz_convert(rep.BJ).strftime("%Y-%m"), end.tz_convert(rep.BJ).strftime("%Y-%m"), freq="M").astype(str)
    monthly = [{"m": m, "n": int((gate.month == m).sum()), "sum_r": rep.fnum(gate.net_r[gate.month == m].sum(), 2)} for m in months]
    hold_h = (gate.exit_time - gate.entry_time).dt.total_seconds().to_numpy() / 3600
    groups = {
        "side": [{"label": lbl, **rep.stats(gate.net_r[gate.side == s].to_numpy())} for s, lbl in ((1, "做多"), (-1, "做空"))],
        "reason": [{"label": rep.REASONS.get(k, k), **rep.stats(gate.net_r[gate.exit_reason == k].to_numpy())}
                   for k in ("initial_stop", "trailing_stop", "opposite_v6_next_open") if (gate.exit_reason == k).any()],
        "hold": [{"label": lbl, **rep.stats(gate.net_r[(hold_h >= lo) & (hold_h < hi)].to_numpy())}
                 for lbl, lo, hi in (("< 4 小时", 0, 4), ("4–12 小时", 4, 12), ("12–48 小时", 12, 48), ("≥ 48 小时", 48, 1e9))],
        "buckets": [{"label": lbl, "n": int(((r >= lo) & (r < hi)).sum()), "sum_r": rep.fnum(r[(r >= lo) & (r < hi)].sum(), 1)} for lo, hi, lbl in rep.R_BUCKETS],
    }
    srt = np.sort(r)[::-1]
    dependence = {"best5_sum": rep.fnum(srt[:5].sum(), 1), "without_best5": rep.fnum(srt[5:].sum(), 1),
                  "without_best10": rep.fnum(srt[10:].sum(), 1), "ge5r": int((r >= 5).sum()), "ge10r": int((r >= 10).sum()),
                  "max_r": rep.fnum(srt[0], 2), "median_hold_h": rep.fnum(np.median(hold_h), 1),
                  "median_risk_pct": rep.fnum(gate.initial_risk_frac.median() * 100, 2),
                  "cost_r": rep.fnum((gate.gross_r - gate.net_r).mean(), 3), "gross_avg_r": rep.fnum(gate.gross_r.mean(), 3)}
    removed = base.loc[~base.signal_close.isin(set(gate.signal_close))]
    gate_effect = {"removed": rep.stats(removed.net_r.to_numpy()), "removed_long": rep.stats(removed.net_r[removed.side == 1].to_numpy()),
                   "removed_short": rep.stats(removed.net_r[removed.side == -1].to_numpy()),
                   "new_only": rep.stats(gate.loc[~gate.signal_close.isin(set(base.signal_close))].net_r.to_numpy())}
    periods = {v: {p: block(df[df.period == p]) for p in ("before", "after")} | {"full": block(df)} for v, df in (("gate", gate), ("base", base))}

    daily = (bars.assign(t=pd.to_datetime(bars.ts, unit="ms", utc=True)).set_index("t")
             .loc[lambda d: (d.index >= start - pd.Timedelta(days=3)) & (d.index < end)]
             .resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
    overview = [[int(t.value // 1_000_000), *(round(float(x), 2) for x in row)] for t, row in zip(daily.index, daily.to_numpy())]
    rows, cum, eq = [], 0.0, 1.0
    for n, t in enumerate(gate.itertuples(index=False), 1):
        cum += t.net_r
        eq *= 1 + 0.01 * t.net_r
        candles, k = rep.window(bars, pd.Series(t._asdict()))
        rows.append({"n": n, "side": int(t.side), "signal_ms": int(t.signal_close.value // 1_000_000), "entry_ms": int(t.entry_time.value // 1_000_000),
                     "exit_ms": int(t.exit_time.value // 1_000_000), "entry": rep.fnum(t.entry_price, 2), "stop": rep.fnum(t.initial_stop, 2),
                     "exit": rep.fnum(t.exit_price, 2), "reason": rep.REASONS.get(t.exit_reason, t.exit_reason), "reason_key": t.exit_reason,
                     "r": rep.fnum(t.net_r, 3), "r_gross": rep.fnum(t.gross_r, 3), "risk_pct": rep.fnum(t.initial_risk_frac * 100, 2),
                     "mfe": rep.fnum(t.mfe_known_r, 2), "peak": rep.fnum(t.close_peak_r, 2), "armed": bool(t.trail_armed),
                     "control_r": rep.fnum(t.control_net_r, 3), "cum_r": rep.fnum(cum, 2), "equity": rep.fnum(eq * 100, 2),
                     "hold_h": rep.fnum((t.exit_time - t.entry_time).total_seconds() / 3600, 2), "period": t.period, "k": k, "candles": candles})
    eth, alts = summary["eth"], summary["alts_pooled"]
    five = json.loads((ETH5Y / "results_v1/summary.json").read_text())
    okx_counts = {"raw_candidates": eth["eth_okx"][OLD]["candidates"], "admitted_candidates": eth["eth_okx"][NEW]["candidates"],
                  "rejected_opposite": eth["eth_okx"][OLD]["candidates"] - eth["eth_okx"][NEW]["candidates"], "rejected_unready": 0}
    payload = {
        "meta": {"start": cfg["window"][0], "end": cfg["window"][1], "split": cfg["split"], "generated_at": summary["generated_at"],
                 "git_head": summary["git_head"], "parity": summary["parity"], "data": five["data"],
                 "okx": {"gate_counts": okx_counts, **{k: five["cases"]["okx"][k] for k in ("incomplete_15m_buckets", "data_gap_bars", "first_bar", "last_bar")}},
                 "open_at_end": [{"side": int(x.side), "signal_ms": int(x.signal_close.value // 1_000_000), "entry": rep.fnum(x.entry_price, 2),
                                  "stop": rep.fnum(x.initial_stop, 2)} for x in open_now.itertuples()]},
        "metric": {"okx": {"vidya_gate": eth["eth_okx"][NEW], "baseline": eth["eth_okx"][OLD], "end": cfg["window"][1]},
                   "binance": {"vidya_gate": eth["eth_binance"][NEW], "baseline": eth["eth_binance"][OLD],
                               "end": summary["config"]["cases"]["eth_binance"]["window"][1]}},
        "alts": {v: {k: alts[v]["periods"][k] for k in ("full", "before_split", "from_split")} for v in (OLD, NEW)},
        "alts_split": summary["config"]["cases"]["alts"]["split"],
        "periods": periods, "yearly": yearly, "monthly": monthly, "groups": groups, "dependence": dependence, "gate_effect": gate_effect,
        "overview": overview, "trades": rows,
        "base_curve": [[int(t.exit_time.value // 1_000_000), rep.fnum(v, 2)] for t, v in zip(base.itertuples(), np.cumsum(base.net_r.to_numpy()))]}
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    OUTDIR.mkdir(exist_ok=True)
    (OUTDIR / "index.html").write_text((HERE / "report_v1212_template.html").read_text().replace("__DATA__", blob))
    print("wrote", OUTDIR / "index.html", "trades", len(rows), "open", len(open_now))


if __name__ == "__main__":
    main()
