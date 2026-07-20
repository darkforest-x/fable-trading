"""Weight-centric position sizing experiment (offline, val window only).

Owner-approved 2026-07-20: single-variable experiment answering "does mapping
score -> continuous position size beat the current binary all-in rule on
capital utilisation and net return?" Everything else is held identical to the
mainline stage-3 backtest (src/backtest/run.py): frozen artifact
frozen_tp5_sl2_swap_yolo_v11_reg_20260718 scores, val-q90 entry threshold
(0.02022), TP5/SL2 label replay, per-symbol single position, account capital
= 10 units, cost sweep {0.2%, 0.3%}.

Scope guards:
- dataset: data/judgment_yolo_swap_v11.csv (frozen artifact's own pool);
- evaluation: entries strictly before 2026-05-04 (holdout NEVER touched,
  accept window NEVER simulated); primary window = val
  (signal_time >= 2026-03-12), train window reported as in-sample context;
- no production defaults changed; this script is the only entry point.

Sizing variants (the ONLY variable):
- baseline: w = 1 for every eligible trade (current binary rule);
- tiered:   val-score quantile bands q90-q95 / q95-q99 / q99+ -> w = 1 / 1.5 / 2
            (bands fixed from val scores, same convention as the q90 threshold);
- calib:    isotonic calibration score -> P(label=1) fitted on the TRAIN window
            (never on val/holdout), then w = (p - p_min) / scale capped at 2,
            where p_min = calibrated p at the entry threshold and scale is set
            so the mean weight over train-window eligible candidates is 1.

Capacity: sum of open weights <= 10 (same 10-unit account as MAX_CONCURRENT=10
with w=1). Same-bar contention resolved by score descending; a candidate that
does not fit the remaining capacity is skipped (with w=1 this reduces to the
mainline break-when-full behaviour).

Usage:
    PYTHONPATH=. .venv/bin/python scripts/weight_centric_backtest.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from src.judgment.frozen import default_config, latest_artifact, score_with_artifact
from src.judgment.train import HOLDOUT_START, load_splits

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"
BAR = pd.Timedelta(minutes=15)
CAPITAL = 10.0  # units; == MAX_CONCURRENT * 1 unit in the mainline backtest
COSTS = (0.002, 0.003)  # round-trip, owner assumptions, do not tune
WEIGHT_CAP = 2.0


def build_scored_pool() -> tuple[pd.DataFrame, float, pd.DataFrame, pd.DataFrame]:
    """Score the v11 pool with the frozen artifact; return (pre-holdout signals,
    threshold, train split, val split). Val/train splits carry frozen scores too."""
    artifact = latest_artifact(default_config())
    if artifact is None:
        raise SystemExit("no frozen artifact for the default (v11) config")
    signals, threshold = score_with_artifact(artifact)

    # Hard scope guard: never simulate entries at/after the holdout boundary.
    signals = signals[signals["entry_time"] < HOLDOUT_START].copy()

    train, val, _ = load_splits(artifact.dataset_path)
    score_map = signals.set_index(["source", "symbol", "signal_time"])["score"]
    for split in (train, val):
        split["score"] = score_map.loc[
            list(zip(split["source"], split["symbol"], split["signal_time"]))
        ].to_numpy()
    return signals, threshold, train, val


def tiered_weights(scores: np.ndarray, q90: float, q95: float, q99: float) -> np.ndarray:
    w = np.where(scores >= q99, 2.0, np.where(scores >= q95, 1.5, 1.0))
    return np.where(scores >= q90, w, 0.0)


def fit_calibration(train: pd.DataFrame, threshold: float):
    """Isotonic score -> P(TP) on the train window only. Returns (iso, p_min,
    scale). Train scores are in-sample for the model (disclosed in report)."""
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train["score"].to_numpy(), train["label"].to_numpy())
    p_min = float(iso.predict([threshold])[0])
    eligible = train[train["score"] >= threshold]
    p_train = iso.predict(eligible["score"].to_numpy())
    scale = float(np.mean(p_train - p_min))
    if scale <= 0:
        raise SystemExit("calibration degenerate: mean(p - p_min) <= 0 on train")
    return iso, p_min, scale


def calib_weights(scores: np.ndarray, iso, p_min: float, scale: float) -> np.ndarray:
    p = iso.predict(scores)
    return np.clip((p - p_min) / scale, 0.0, WEIGHT_CAP)


def simulate(signals: pd.DataFrame, threshold: float, weights: np.ndarray) -> pd.DataFrame:
    """Mainline event loop with continuous weights: per-symbol single position,
    sum of open weights <= CAPITAL, score-descending contention, skip-if-no-fit."""
    sig = signals.copy()
    sig["w"] = weights
    eligible = sig[(sig["score"] >= threshold) & (sig["w"] > 0)]
    taken: list[dict] = []
    open_positions: list[tuple[pd.Timestamp, str, float]] = []  # (exit_time, key, w)
    for entry_time, group in eligible.groupby("entry_time", sort=True):
        open_positions = [p for p in open_positions if p[0] > entry_time]
        held = {key for _, key, _ in open_positions}
        used = sum(w for _, _, w in open_positions)
        for row in group.itertuples():
            key = f"{row.source}:{row.symbol}"
            if key in held or used + row.w > CAPITAL + 1e-9:
                continue
            open_positions.append((row.exit_time, key, row.w))
            held.add(key)
            used += row.w
            taken.append({
                "source": row.source, "symbol": row.symbol,
                "entry_time": entry_time, "exit_time": row.exit_time,
                "score": row.score, "w": row.w, "outcome": row.outcome,
                "gross_ret": row.realized_ret,
            })
    return pd.DataFrame(taken)


def utilisation(trades: pd.DataFrame, win_start: pd.Timestamp, win_end: pd.Timestamp) -> float:
    """Time-weighted mean open exposure / CAPITAL over [win_start, win_end)."""
    t = trades[(trades["entry_time"] >= win_start) | (trades["exit_time"] > win_start)]
    events: list[tuple[pd.Timestamp, float]] = []
    for row in t.itertuples():
        events.append((max(row.entry_time, win_start), row.w))
        events.append((min(row.exit_time, win_end), -row.w))
    if not events:
        return 0.0
    ev = pd.DataFrame(events, columns=["time", "dw"]).groupby("time")["dw"].sum().sort_index()
    times = ev.index.to_list() + [win_end]
    exposure, area, prev = 0.0, 0.0, win_start
    for time, dw in ev.items():
        area += exposure * (time - prev).total_seconds()
        exposure += dw
        prev = time
    area += exposure * (win_end - prev).total_seconds()
    return area / (win_end - win_start).total_seconds() / CAPITAL


def window_metrics(trades: pd.DataFrame, cost_rt: float,
                   win_start: pd.Timestamp, win_end: pd.Timestamp) -> dict:
    """Same conventions as src/backtest/run.py::window_metrics, with notional
    weights: net per trade = w * (gross - cost); equity marked at exits."""
    t = trades[(trades["entry_time"] >= win_start) & (trades["entry_time"] < win_end)]
    if t.empty:
        return {"n_trades": 0}
    t = t.sort_values("exit_time")
    net = t["w"].to_numpy() * (t["gross_ret"].to_numpy() - cost_rt)
    equity = CAPITAL + np.cumsum(net)
    peak = np.maximum.accumulate(np.concatenate([[CAPITAL], equity]))[1:]
    wins, losses = net[net > 0].sum(), net[net < 0].sum()
    days = (win_end - win_start).total_seconds() / 86400
    return {
        "n_trades": int(len(t)),
        "net_total_units": round(float(net.sum()), 4),
        "net_return_on_capital": round(float(net.sum() / CAPITAL), 4),
        "mean_net_per_trade_unitized": round(float(net.mean()), 5),
        "win_rate": round(float((net > 0).mean()), 4),
        "profit_factor": round(float(wins / -losses), 3) if losses < 0 else float("inf"),
        "max_drawdown_pct": round(float(((peak - equity) / peak).max()), 4),
        "utilisation": round(utilisation(t, win_start, win_end), 4),
        "turnover_units": round(float(t["w"].sum()), 2),
        "turnover_units_per_day": round(float(t["w"].sum() / days), 3),
        "mean_weight": round(float(t["w"].mean()), 3),
    }


def monthly_net(trades: pd.DataFrame, cost_rt: float,
                win_start: pd.Timestamp, win_end: pd.Timestamp) -> dict:
    t = trades[(trades["entry_time"] >= win_start) & (trades["entry_time"] < win_end)].copy()
    if t.empty:
        return {}
    t["net"] = t["w"] * (t["gross_ret"] - cost_rt)
    t["month"] = t["entry_time"].dt.strftime("%Y-%m")
    grouped = t.groupby("month")
    return {
        month: {"n": int(len(g)), "net_units": round(float(g["net"].sum()), 4)}
        for month, g in grouped
    }


def main() -> int:
    signals, threshold, train, val = build_scored_pool()
    val_scores = val["score"].to_numpy()
    q90 = float(np.quantile(val_scores, 0.90))
    q95 = float(np.quantile(val_scores, 0.95))
    q99 = float(np.quantile(val_scores, 0.99))
    assert abs(q90 - threshold) < 1e-9, "val q90 must reproduce the frozen threshold"

    iso, p_min, scale = fit_calibration(train, threshold)

    val_start = val["signal_time"].min() + BAR  # first possible val entry bar
    win_end = HOLDOUT_START
    train_start = signals["entry_time"].min()

    scores = signals["score"].to_numpy()
    variants = {
        "baseline_binary": np.where(scores >= threshold, 1.0, 0.0),
        "tiered_q90_95_99": tiered_weights(scores, threshold, q95, q99),
        "calib_isotonic": calib_weights(scores, iso, p_min, scale),
    }

    results: dict = {
        "dataset": "data/judgment_yolo_swap_v11.csv",
        "frozen_artifact": "frozen_tp5_sl2_swap_yolo_v11_reg_20260718",
        "threshold_val_q90": round(threshold, 5),
        "tier_bounds_val": {"q90": round(q90, 5), "q95": round(q95, 5), "q99": round(q99, 5)},
        "calibration": {
            "fit_on": "train window (in-sample scores, disclosed)",
            "p_min_at_threshold": round(p_min, 4),
            "scale_mean_p_minus_pmin": round(scale, 4),
        },
        "windows": {
            "val": [str(val_start), str(win_end)],
            "train_insample": [str(train_start), str(val_start)],
            "holdout": "NOT simulated (entries >= 2026-05-04 excluded before the event loop)",
        },
        "capital_units": CAPITAL,
        "variants": {},
    }

    for name, weights in variants.items():
        trades = simulate(signals, threshold, weights)
        trades.to_csv(OUTPUT_DIR / f"p_weight_centric_{name}_trades.csv", index=False)
        results["variants"][name] = {
            "val": {f"cost_{c:.3f}": window_metrics(trades, c, val_start, win_end) for c in COSTS},
            "train_insample": {
                f"cost_{c:.3f}": window_metrics(trades, c, train_start, val_start) for c in COSTS
            },
            "val_monthly_net_cost_0.003": monthly_net(trades, 0.003, val_start, win_end),
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "p_weight_centric_val.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
