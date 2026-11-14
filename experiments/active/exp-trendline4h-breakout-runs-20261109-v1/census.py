"""Census of 4H main-trendline V1.2 closing breaks and the runs that followed.

Definitions are frozen in config.json (written before any event was computed).
Descriptive excursion study: peak gain / peak R before a fixed stop, versus
matched random 4H bars (same symbol, +-180 bars, same entry/stop/horizon).
No exit rule, no sizing, no realised P&L; costs only reported as R per event.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trendline_v12  # noqa: E402

ROOT = HERE.parents[2]
CFG = json.loads((HERE / "config.json").read_text())
DATA = ROOT / CFG["data"]["path"]
H = CFG["horizon_bars"]
MIN_RISK = CFG["min_risk_pct"] / 100
FEE = 0.0005  # taker per side, OKX Lv1 read 2026-10-23


def excursion(h: np.ndarray, l: np.ndarray, x: int, entry: float, stop: float, horizon: int) -> dict:
    """Peak high strictly before the first bar (after x) whose low <= stop."""
    end = min(len(h) - 1, x + horizon)
    hs = h[x + 1:end + 1]
    ls = l[x + 1:end + 1]
    hit = np.nonzero(ls <= stop)[0]
    upto = hit[0] if hit.size else len(hs)
    peak = max(entry, hs[:upto].max()) if upto > 0 else entry
    risk = entry - stop
    return {
        "bars_seen": int(len(hs)),
        "stopped": bool(hit.size),
        "stop_bar": int(hit[0] + 1) if hit.size else None,
        "peak_gain_pct": (peak / entry - 1) * 100,
        "peak_r": (peak - entry) / risk,
        "risk_pct": risk / entry * 100,
        "cost_r": 2 * FEE * entry / risk,
    }


def measure(o, h, l, c, atr, x: int, horizon: int) -> dict:
    entry = c[x]
    out = {}
    for name, stop in (("bar_low", l[x]), ("atr1", entry - atr[x])):
        if not np.isfinite(stop) or (entry - stop) / entry < MIN_RISK:
            out[name] = None
        else:
            out[name] = excursion(h, l, x, entry, stop, horizon)
    return out


def main() -> int:
    rng = np.random.default_rng(CFG["control"]["seed"])
    files = sorted(DATA.glob("okx_*_4H_*.csv"))
    events, controls, ties_total, bars_total = [], [], 0, 0
    for f in files:
        sym = f.name[4:].split("_4H_")[0]
        df = pd.read_csv(f)
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        n = len(c)
        bars_total += n
        if n < 200:
            continue
        res = trendline_v12.run(o, h, l, c)
        ties_total += res.pivot_ties
        atr = trendline_v12.pine_atr(h, l, c)
        for e in res.events:
            if e.kind != "break":
                continue
            full = e.x + H <= n - 1
            m = measure(o, h, l, c, atr, e.x, H)
            events.append({
                "symbol": sym, "x": e.x, "ts": int(df.ts.iloc[e.x]), "full_horizon": full,
                "entry": c[e.x], "line": e.level, "touches": e.touches,
                "line_span_bars": e.b_x - e.a_x, "line_age_bars": e.x - e.a_x,
                "drop_pct": (e.a_y / e.b_y - 1) * 100,
                **{f"{k}_{s}": (m[s] or {}).get(k) for s in ("bar_low", "atr1")
                   for k in ("peak_gain_pct", "peak_r", "risk_pct", "stopped", "stop_bar", "cost_r", "bars_seen")},
            })
            if not full:
                continue
            lo, hi = max(100, e.x - 180), min(n - 1 - H, e.x + 180)
            pool = np.array([b for b in range(lo, hi + 1) if b != e.x])
            if pool.size == 0:
                continue
            for b in rng.choice(pool, size=min(CFG["control"]["draws_per_event"], pool.size), replace=False):
                m = measure(o, h, l, c, atr, int(b), H)
                controls.append({
                    "symbol": sym, "event_x": e.x, "x": int(b),
                    **{f"{k}_{s}": (m[s] or {}).get(k) for s in ("bar_low", "atr1")
                       for k in ("peak_gain_pct", "peak_r", "risk_pct")},
                })
    ev = pd.DataFrame(events)
    ct = pd.DataFrame(controls)
    ev["time_utc8"] = pd.to_datetime(ev.ts, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d %H:%M")
    ev.to_csv(HERE / "events.csv", index=False)
    ct.to_csv(HERE / "controls.csv", index=False)

    full = ev[ev.full_horizon]
    summary = {"symbols": len(files), "bars": bars_total, "pivot_tie_only": ties_total,
               "breaks_all": len(ev), "breaks_full_horizon": len(full),
               "symbols_with_break": int(ev.symbol.nunique()), "rates": {}}
    for s in ("bar_low", "atr1"):
        e_ok = full[full[f"peak_r_{s}"].notna()]
        c_ok = ct[ct[f"peak_r_{s}"].notna()]
        rows = {"n_event": len(e_ok), "n_control": len(c_ok),
                "median_risk_pct_event": float(e_ok[f"risk_pct_{s}"].median()),
                "median_risk_pct_control": float(c_ok[f"risk_pct_{s}"].median())}
        for g in CFG["thresholds"]["gain_pct"]:
            rows[f"gain>={g}%"] = [int((e_ok[f"peak_gain_pct_{s}"] >= g).sum()),
                                   round(float((e_ok[f"peak_gain_pct_{s}"] >= g).mean() * 100), 2),
                                   round(float((c_ok[f"peak_gain_pct_{s}"] >= g).mean() * 100), 2)]
        for r in CFG["thresholds"]["peak_r"]:
            rows[f"R>={r}"] = [int((e_ok[f"peak_r_{s}"] >= r).sum()),
                               round(float((e_ok[f"peak_r_{s}"] >= r).mean() * 100), 2),
                               round(float((c_ok[f"peak_r_{s}"] >= r).mean() * 100), 2)]
        rows["median_peak_r"] = [round(float(e_ok[f"peak_r_{s}"].median()), 2), round(float(c_ok[f"peak_r_{s}"].median()), 2)]
        rows["median_peak_gain_pct"] = [round(float(e_ok[f"peak_gain_pct_{s}"].median()), 2),
                                        round(float(c_ok[f"peak_gain_pct_{s}"].median()), 2)]
        summary["rates"][s] = rows
    (HERE / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
