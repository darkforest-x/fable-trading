"""Re-express frozen SPIKE matched-random comparisons in scale-invariant bp.

Source: owner approval on 2026-09-22 ("先做第1步") after the low-timeframe
replay showed R-denominated excess over random inflated by narrower random
stops.  Plan: ``experiments/active/exp-spike-bp-random-audit-20260922-v1/``.

Read-only: frozen trades/controls of the V10.4 multi-timeframe joint study,
the V9 full backtest and the V12.6 15m H1-recheck study.  No market data,
replay, redraw or edit of any source ledger.  Each matched, closed pair keeps
target and control net R and net return; control initial risk is recovered
as ``control_net_return / control_net_r``.  Because both sides pay the same
20bp, the net bp excess equals the gross bp excess.  R excess decomposes as
gross-R excess minus the (target - control) cost-R gap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-bp-random-audit-20260922-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
TEST = Path("tests/evaluation/test_spike_bp_random_audit.py")
COLUMNS = ["study", "arm", "timeframe_min", "side", "symbol", "entry_time", "target_net_r", "target_net_return",
           "target_risk", "control_net_r", "control_net_return", "control_risk"]


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _risk(net_return: pd.Series, net_r: pd.Series) -> pd.Series:
    r = net_r.astype(float)
    return (net_return.astype(float) / r).where(r.ne(0) & r.notna())


def load_v104(src: dict) -> pd.DataFrame:
    t = pd.read_csv(src["trades"])
    c = pd.read_csv(src["controls"])
    m = t.merge(c[["trade_key", "arm", "matched", "control_net_r", "control_net_return"]], on=["trade_key", "arm"])
    m = m[m.matched.astype(bool) & ~m.censored.astype(bool)]
    return pd.DataFrame({"study": "v104", "arm": m.arm, "timeframe_min": m.timeframe_min, "side": 1,
                         "symbol": m.symbol, "entry_time": m.entry_time, "target_net_r": m.net_r,
                         "target_net_return": m.net_return, "target_risk": m.initial_risk_frac,
                         "control_net_r": m.control_net_r, "control_net_return": m.control_net_return,
                         "control_risk": _risk(m.control_net_return, m.control_net_r)})


def load_v9(src: dict) -> pd.DataFrame:
    c = pd.read_csv(src["controls"])
    c = c[c.matched.astype(bool) & ~c.target_censored.astype(bool) & ~c.control_censored.astype(bool)]
    return pd.DataFrame({"study": "v9", "arm": c.arm, "timeframe_min": c.timeframe_min, "side": c.side,
                         "symbol": c.symbol, "entry_time": c.entry_time, "target_net_r": c.target_net_r,
                         "target_net_return": c.target_net_return,
                         "target_risk": _risk(c.target_net_return, c.target_net_r),
                         "control_net_r": c.control_net_r, "control_net_return": c.control_net_return,
                         "control_risk": _risk(c.control_net_return, c.control_net_r)})


def load_v126(src: dict) -> pd.DataFrame:
    run = Path(src["run"])
    manifest = json.loads((run / "manifest.json").read_text())
    if not manifest["complete"]:
        raise ValueError("V12.6 run incomplete")
    parts = []
    for symbol in manifest["symbols"]:
        d = run / "streams" / symbol
        receipt = json.loads((d / "receipt.json").read_text())
        for name in ("trades.csv.gz", "controls.csv.gz"):
            if digest(d / name) != receipt["files"][name]:
                raise ValueError(f"artifact changed: {d / name}")
        t = pd.read_csv(d / "trades.csv.gz")
        if t.empty:
            continue
        c = pd.read_csv(d / "controls.csv.gz")
        parts.append(t.merge(c[["trade_key", "arm", "matched", "control_net_r", "control_net_return"]],
                             on=["trade_key", "arm"]))
    m = pd.concat(parts, ignore_index=True)
    m = m[m.matched.astype(bool) & ~m.censored.astype(bool)]
    return pd.DataFrame({"study": "v126", "arm": m.arm, "timeframe_min": 15, "side": 1, "symbol": m.symbol,
                         "entry_time": m.entry_time, "target_net_r": m.net_r, "target_net_return": m.net_return,
                         "target_risk": m.initial_risk_frac, "control_net_r": m.control_net_r,
                         "control_net_return": m.control_net_return,
                         "control_risk": _risk(m.control_net_return, m.control_net_r)})


def block_inference(x: pd.Series, months: pd.Series, rng: np.random.Generator, reps: int, flips: int):
    """UTC-month block bootstrap 95% interval of the mean and one-sided sign-flip p."""
    frame = pd.DataFrame({"x": x.to_numpy(float), "m": months.to_numpy()})
    grouped = frame.groupby("m").x
    sums, counts = grouped.sum().to_numpy(), grouped.size().to_numpy()
    if len(sums) < 2:
        return np.nan, np.nan, np.nan
    draws = rng.integers(0, len(sums), size=(reps, len(sums)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    signs = rng.choice([-1.0, 1.0], size=(flips, len(sums)))
    p = float(((signs * sums).sum(axis=1) >= sums.sum()).mean())
    return float(np.quantile(means, .025)), float(np.quantile(means, .975)), p


def summarize(pairs: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    cost = float(cfg["round_trip_cost"])
    split = pd.Timestamp(cfg["split"])
    rng = np.random.default_rng(int(cfg["seed"]))
    p = pairs.copy()
    p["entry_time"] = pd.to_datetime(p.entry_time, utc=True)
    p["month"] = p.entry_time.dt.strftime("%Y-%m")
    p["period"] = np.where(p.entry_time < split, "earlier", "later")
    p["excess_r"] = p.target_net_r - p.control_net_r
    p["excess_bp"] = (p.target_net_return - p.control_net_return) * 1e4
    p["target_cost_r"] = cost / p.target_risk
    p["control_cost_r"] = cost / p.control_risk
    sides = [("all", None), ("long", 1), ("short", -1)]
    rows = []
    for (study, arm, tf), g in p.groupby(["study", "arm", "timeframe_min"]):
        for side_name, side in sides:
            gs = g if side is None else g[g.side == side]
            if side is not None and (len(gs) == 0 or gs.side.nunique() == g.side.nunique()):
                continue
            for period in ("full", "earlier", "later"):
                x = gs if period == "full" else gs[gs.period == period]
                if len(x) == 0:
                    continue
                r_lo, r_hi, r_p = block_inference(x.excess_r, x.month, rng, cfg["bootstrap"], cfg["flips"])
                b_lo, b_hi, b_p = block_inference(x.excess_bp, x.month, rng, cfg["bootstrap"], cfg["flips"])
                both = x.target_cost_r.notna() & x.control_cost_r.notna()
                rows.append({"study": study, "arm": arm, "timeframe_min": tf, "side": side_name, "period": period,
                             "pairs": len(x), "months": int(x.month.nunique()),
                             "target_net_r": x.target_net_r.mean(), "control_net_r": x.control_net_r.mean(),
                             "excess_r": x.excess_r.mean(), "excess_r_ci_low": r_lo, "excess_r_ci_high": r_hi,
                             "excess_r_p": r_p,
                             "target_net_bp": x.target_net_return.mean() * 1e4,
                             "control_net_bp": x.control_net_return.mean() * 1e4,
                             "excess_bp": x.excess_bp.mean(), "excess_bp_ci_low": b_lo, "excess_bp_ci_high": b_hi,
                             "excess_bp_p": b_p,
                             "target_risk_bp": x.target_risk.median() * 1e4,
                             "control_risk_bp": x.control_risk.median() * 1e4,
                             "target_cost_r": x.target_cost_r[both].mean(), "control_cost_r": x.control_cost_r[both].mean(),
                             "cost_gap_contribution_r": (x.control_cost_r[both] - x.target_cost_r[both]).mean()})
    return pd.DataFrame(rows)


def verdicts(table: pd.DataFrame) -> pd.DataFrame:
    """Pre-registered bp_confirmed / r_only / not_significant per claim cell."""
    key = ["study", "arm", "timeframe_min", "side"]
    full = table[table.period == "full"].set_index(key)
    later = table[table.period == "later"].set_index(key)
    out = []
    for idx, row in full.iterrows():
        later_bp = later.excess_bp.get(idx, np.nan)
        bp_ok = row.excess_bp_ci_low > 0 and row.excess_bp_p < 0.01 and later_bp > 0
        r_sig = row.excess_r_p < 0.01
        out.append({**dict(zip(key, idx)), "pairs": row.pairs, "excess_r": row.excess_r, "excess_r_p": row.excess_r_p,
                    "excess_bp": row.excess_bp, "excess_bp_ci_low": row.excess_bp_ci_low,
                    "excess_bp_p": row.excess_bp_p, "later_excess_bp": later_bp,
                    "verdict": "bp_confirmed" if bp_ok else "r_only" if r_sig else "not_significant"})
    return pd.DataFrame(out)


def run(output: Path) -> None:
    cfg = json.loads(CONFIG.read_text())
    code = (Path(__file__), CONFIG, PLAN, TEST)
    if not _committed(code):
        raise ValueError("commit audit code/plan/config/tests before generating results")
    src = cfg["sources"]
    inputs = {"v104": {k: digest(Path(v)) for k, v in src["v104"].items()},
              "v9": {k: digest(Path(v)) for k, v in src["v9"].items()},
              "v126_manifest": digest(Path(src["v126"]["run"]) / "manifest.json")}
    pairs = pd.concat([load_v104(src["v104"]), load_v9(src["v9"]), load_v126(src["v126"])], ignore_index=True)
    table = summarize(pairs, cfg)
    verdict = verdicts(table)
    output.mkdir(parents=True, exist_ok=False)
    table.to_csv(output / "summary.csv", index=False)
    verdict.to_csv(output / "verdicts.csv", index=False)
    receipt = {"config": cfg, "inputs": inputs, "pairs": int(len(pairs)),
               "pairs_by_study": pairs.groupby("study").size().to_dict(),
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "code": {str(p): digest(p) for p in code},
               "files": {n: digest(output / n) for n in ("summary.csv", "verdicts.csv")}}
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({"pairs": receipt["pairs"], "by_study": receipt["pairs_by_study"],
                      "verdicts": verdict.verdict.value_counts().to_dict()}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
