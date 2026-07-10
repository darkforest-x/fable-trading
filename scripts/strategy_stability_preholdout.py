"""No-tuning pre-holdout walk-forward stability audit for frozen candidates.

Evaluates only predeclared existing strategy artifacts on chronological folds
whose source rows all end strictly before HOLDOUT_START (2026-05-04 UTC).

Hard rules:
- never reads or scores judgment holdout (>= 2026-05-04);
- never searches parameters or re-picks thresholds from fold results;
- score gate is fixed ex ante as train-only score q90 (same family as
  src.backtest.run); top-decile metrics are also reported with the fixed
  10% cut rule (not a searched cutoff);
- all results are historical candidate evidence, not final profitability.

Usage:
    python3 -m scripts.strategy_stability_preholdout
    python3 -m scripts.strategy_stability_preholdout --n-folds 4 --write-report
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.run import SCORE_QUANTILE, simulate, window_metrics
from src.data.bars import bar_to_timedelta, purge_window
from src.data.funding import funding_costs_for_trades
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import HOLDOUT_START, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "strategy_stability_preholdout.json"
OUT_REPORT = PROJECT_DIR / "analysis" / "strategy_stability_preholdout.md"
SWAP_MAKER_COST = 0.0006  # fixed; not tuned
MIN_TRAIN = 80
MIN_TEST = 30


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    path: Path
    bar: str
    horizon_bars: int
    role: str
    notes: str


CANDIDATES: tuple[CandidateSpec, ...] = (
    CandidateSpec(
        name="tp5_sl2_long_swap",
        path=PROJECT_DIR / "data" / "ma206" / "swap_tp5_sl2_ma206.csv",
        bar="15m",
        horizon_bars=72,
        role="frozen_champion",
        notes="Frozen ACTIVE long TP5/SL2 on expanded SWAP universe.",
    ),
    CandidateSpec(
        name="h1_scaled_25_t3",
        path=PROJECT_DIR / "data" / "sweep_exits_swap" / "scaled_25_t3.csv",
        bar="15m",
        horizon_bars=72,
        role="challenger",
        notes="H1 scaled take-profit (half @2.5xATR + trail 3xATR); discovery-tier only.",
    ),
    CandidateSpec(
        name="h8_30m_h48",
        path=PROJECT_DIR / "data" / "mtf_sweep" / "h8_30m_h48.csv",
        bar="30m",
        horizon_bars=48,
        role="challenger",
        notes="H8 30m TP5/SL2 pool (horizon 48 bars); discovery-tier only.",
    ),
    CandidateSpec(
        name="h10_short_tp5_sl2",
        path=PROJECT_DIR / "data" / "short_replication" / "swap_short_tp5_sl2.csv",
        bar="15m",
        horizon_bars=72,
        role="challenger",
        notes="H10 short-side mirror TP5/SL2; discovery-tier only.",
    ),
)


class HoldoutLeakError(RuntimeError):
    """Raised when any input or fold timestamp reaches the holdout boundary."""


def _display_path(path: Path) -> str:
    # Do not resolve() — data/ may be a symlink outside the repo.
    try:
        return str(path.relative_to(PROJECT_DIR))
    except ValueError:
        text = str(path)
        prefix = str(PROJECT_DIR) + "/"
        return text[len(prefix) :] if text.startswith(prefix) else text


def _ensure_utc(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, utc=True)
    return ts


def load_preholdout(spec: CandidateSpec) -> pd.DataFrame:
    """Load a predeclared artifact and keep only rows fully before holdout.

    Source rows are time-filtered before any model training or metric fold.
    Labels/features already on disk are treated as frozen inputs; this audit
    does not rebuild candidates or search labels.
    """
    if not spec.path.exists():
        raise FileNotFoundError(f"missing candidate artifact: {spec.path}")
    frame = pd.read_csv(spec.path, parse_dates=["signal_time"])
    frame["signal_time"] = _ensure_utc(frame["signal_time"])
    frame = frame.sort_values("signal_time").reset_index(drop=True)

    purge = purge_window(spec.horizon_bars, spec.bar)
    cutoff = HOLDOUT_START - purge
    pre = frame[frame["signal_time"] < cutoff].copy().reset_index(drop=True)
    assert_no_holdout(pre["signal_time"], context=f"load:{spec.name}")
    missing = [c for c in FEATURE_COLUMNS if c not in pre.columns]
    if missing:
        raise ValueError(f"{spec.name}: missing feature columns {missing[:8]}")
    required = {"label", "realized_ret", "maker_filled", "exit_offset", "symbol", "source", "outcome"}
    miss_req = required - set(pre.columns)
    if miss_req:
        raise ValueError(f"{spec.name}: missing required columns {sorted(miss_req)}")
    return pre


def assert_no_holdout(times: pd.Series, *, context: str) -> None:
    """Abort if any timestamp reaches HOLDOUT_START (inclusive)."""
    if times.empty:
        return
    ts = _ensure_utc(times)
    if (ts >= HOLDOUT_START).any():
        bad = ts[ts >= HOLDOUT_START].iloc[0]
        raise HoldoutLeakError(
            f"{context}: timestamp {bad} reaches holdout boundary {HOLDOUT_START}; abort"
        )


def chronological_folds(frame: pd.DataFrame, n_folds: int) -> list[pd.DataFrame]:
    """Split sorted pre-holdout rows into n chronological equal-count folds."""
    if n_folds < 4:
        raise ValueError("acceptance requires at least 4 chronological folds")
    if len(frame) < n_folds * MIN_TEST:
        raise ValueError(
            f"need at least {n_folds * MIN_TEST} pre-holdout rows for {n_folds} folds; got {len(frame)}"
        )
    edges = np.linspace(0, len(frame), n_folds + 1, dtype=int)
    folds: list[pd.DataFrame] = []
    for i in range(n_folds):
        part = frame.iloc[edges[i] : edges[i + 1]].copy().reset_index(drop=True)
        if part.empty:
            raise ValueError(f"fold {i} is empty")
        assert_no_holdout(part["signal_time"], context=f"fold_{i}")
        folds.append(part)
    return folds


def _bar_delta(bar: str) -> pd.Timedelta:
    return bar_to_timedelta(bar)


def _top_decile_metrics(y: np.ndarray, prob: np.ndarray, rets: np.ndarray, filled: np.ndarray) -> dict:
    k = max(1, len(prob) // 10)
    top_idx = np.argsort(prob)[-k:]
    top_rets = rets[top_idx]
    top_filled = filled[top_idx]
    gross = float(top_rets.mean())
    return {
        "n": int(k),
        "gross_per_trade": round(gross, 5),
        "net_per_trade_maker": round(gross - SWAP_MAKER_COST, 5),
        "win_rate": round(float((top_rets > 0).mean()), 4),
        "maker_fill_rate": round(float(top_filled.mean()), 4) if len(top_filled) else 0.0,
    }


def _funding_coverage(
    fold_df: pd.DataFrame,
    *,
    bar: str,
    selected_index: np.ndarray | None = None,
) -> dict:
    subset = fold_df if selected_index is None else fold_df.iloc[selected_index]
    if subset.empty:
        return {"funding_available_rate": 0.0, "n_with_funding": 0}
    # funding_costs_for_trades uses 15m bar default; adjust exit via exit_offset * bar
    costs = funding_costs_for_trades(subset, bar=_bar_delta(bar))
    available = np.isfinite(costs.to_numpy())
    return {
        "funding_available_rate": round(float(available.mean()), 4),
        "n_with_funding": int(available.sum()),
    }


def evaluate_fold(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    bar: str,
    fold_id: int,
) -> dict:
    assert_no_holdout(train["signal_time"], context=f"train_fold_{fold_id}")
    assert_no_holdout(test["signal_time"], context=f"test_fold_{fold_id}")
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        return {
            "fold": fold_id,
            "status": "skipped_small",
            "n_train": int(len(train)),
            "n_test": int(len(test)),
        }

    # Internal train/val for early stopping only — still entirely pre-holdout.
    split_i = max(MIN_TRAIN // 2, int(len(train) * 0.8))
    if split_i >= len(train) - 5:
        split_i = len(train) - max(5, len(train) // 10)
    tr, va = train.iloc[:split_i], train.iloc[split_i:]
    model = train_model(tr, va)
    test_prob = model.predict(test[list(FEATURE_COLUMNS)], num_iteration=model.best_iteration)
    train_prob = model.predict(train[list(FEATURE_COLUMNS)], num_iteration=model.best_iteration)
    threshold = float(np.quantile(train_prob, SCORE_QUANTILE))

    y = test["label"].to_numpy()
    rets = test["realized_ret"].to_numpy(dtype=float)
    filled = test["maker_filled"].to_numpy(dtype=bool)
    top = _top_decile_metrics(y, test_prob, rets, filled)
    k = top["n"]
    top_idx = np.argsort(test_prob)[-k:]
    fund_top = _funding_coverage(test, bar=bar, selected_index=top_idx)

    bar_delta = _bar_delta(bar)
    sig = test.copy()
    sig["score"] = test_prob
    sig["entry_time"] = sig["signal_time"] + bar_delta
    sig["exit_time"] = sig["entry_time"] + sig["exit_offset"].astype(int) * bar_delta
    sig = sig.sort_values(["entry_time", "score"], ascending=[True, False])
    # Maker rule: unfilled signals are missed, not losses (same family as maker_val_sim).
    maker_pool = sig[sig["maker_filled"]]
    trades = simulate(maker_pool, threshold)
    port = window_metrics(trades, SWAP_MAKER_COST)
    if not trades.empty:
        # funding helper expects signal_time + exit_offset; rebuild from trades
        rebuilt = trades.copy()
        rebuilt["signal_time"] = rebuilt["entry_time"] - bar_delta
        rebuilt["exit_offset"] = (
            (rebuilt["exit_time"] - rebuilt["entry_time"]) / bar_delta
        ).round().astype(int)
        costs = funding_costs_for_trades(rebuilt, bar=bar_delta)
        fund_rate = float(np.isfinite(costs.to_numpy()).mean()) if len(costs) else 0.0
    else:
        fund_rate = 0.0

    return {
        "fold": fold_id,
        "status": "ok",
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "test_start": str(test["signal_time"].min()),
        "test_end": str(test["signal_time"].max()),
        "train_score_q90": round(threshold, 5),
        "top_decile": top,
        "top_decile_funding": fund_top,
        "portfolio_maker_filled": {
            "n_trades": port.get("n_trades", 0),
            "profit_factor": port.get("profit_factor", 0.0),
            "net_per_trade": port.get("mean_net_per_trade", 0.0),
            "win_rate": port.get("win_rate", 0.0),
            "max_drawdown_pct": port.get("max_drawdown_pct", 0.0),
            "net_total_units": port.get("net_total_units", 0.0),
            "real_funding_coverage": round(fund_rate, 4),
        },
        "maker_fill_rate_test": round(float(filled.mean()), 4),
    }


def walk_forward(spec: CandidateSpec, n_folds: int) -> dict:
    pre = load_preholdout(spec)
    folds = chronological_folds(pre, n_folds)
    fold_results: list[dict] = []
    # Expanding train: folds 0..i-1 train, fold i test; need at least fold 0 as train seed
    for i in range(1, n_folds):
        train = pd.concat(folds[:i], ignore_index=True)
        purge = purge_window(spec.horizon_bars, spec.bar)
        test = folds[i]
        # Drop train rows whose barrier window overlaps the test period.
        test_start = test["signal_time"].min()
        train = train[train["signal_time"] < test_start - purge].reset_index(drop=True)
        fold_results.append(evaluate_fold(train, test, bar=spec.bar, fold_id=i))

    ok = [f for f in fold_results if f.get("status") == "ok"]
    summary: dict = {
        "candidate": spec.name,
        "role": spec.role,
        "notes": spec.notes,
        "path": _display_path(spec.path),
        "bar": spec.bar,
        "horizon_bars": spec.horizon_bars,
        "n_preholdout": int(len(pre)),
        "preholdout_start": str(pre["signal_time"].min()) if len(pre) else None,
        "preholdout_end": str(pre["signal_time"].max()) if len(pre) else None,
        "holdout_start": str(HOLDOUT_START),
        "n_folds_requested": n_folds,
        "n_test_folds": len(fold_results),
        "n_ok_folds": len(ok),
        "folds": fold_results,
        "evidence_label": "historical_candidate_evidence_preholdout_only",
        "not_final_profitability_proof": True,
    }
    if ok:
        nets = [f["top_decile"]["net_per_trade_maker"] for f in ok]
        pfs = [f["portfolio_maker_filled"]["profit_factor"] for f in ok if f["portfolio_maker_filled"]["n_trades"]]
        summary["aggregate"] = {
            "top_decile_net_maker_mean": round(float(np.mean(nets)), 5),
            "top_decile_net_maker_min": round(float(np.min(nets)), 5),
            "top_decile_net_maker_max": round(float(np.max(nets)), 5),
            "top_decile_positive_fold_share": round(float(np.mean([n > 0 for n in nets])), 4),
            "portfolio_pf_mean": round(float(np.mean(pfs)), 3) if pfs else None,
            "portfolio_trades_total": int(sum(f["portfolio_maker_filled"]["n_trades"] for f in ok)),
        }
    return summary


def render_report(results: list[dict]) -> str:
    lines = [
        "# Pre-holdout strategy stability audit",
        "",
        "> **Evidence class: historical candidate evidence only.**",
        "> All folds use `signal_time < 2026-05-04` (minus barrier purge).",
        "> This is **not** final profitability proof, **not** a holdout evaluation,",
        "> and **not** a parameter search. No threshold/TP/SL/cost was retuned.",
        "",
        "## Scope",
        "",
        "- Predeclared candidates: frozen TP5/SL2 long SWAP, H1 scaled, H8 30m h48, H10 short.",
        "- Walk-forward expanding train; test folds are chronological.",
        "- Score gate: train-only score q90 (fixed rule from `src.backtest.run`).",
        "- Cost: SWAP maker round-trip **0.06%** (fixed; not searched).",
        "- Portfolio sim: maker-filled only, max 10 concurrent, one position per symbol.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python3 -m scripts.strategy_stability_preholdout --n-folds 4 --write-report",
        "PYTHONPATH=. python3 -m pytest tests/test_strategy_stability_preholdout.py -q",
        "```",
        "",
        "## Results by candidate",
        "",
    ]
    for res in results:
        if res.get("status") == "unsupported":
            lines += [
                f"### {res['candidate']} — **unsupported**",
                "",
                f"- Reason: {res.get('reason')}",
                "",
            ]
            continue
        agg = res.get("aggregate") or {}
        lines += [
            f"### {res['candidate']} ({res['role']})",
            "",
            f"- Artifact: `{res['path']}`",
            f"- Notes: {res['notes']}",
            f"- Pre-holdout n={res['n_preholdout']} "
            f"({res['preholdout_start']} → {res['preholdout_end']})",
            f"- OK test folds: {res['n_ok_folds']}/{res['n_test_folds']}",
            "",
            "| fold | test window | n_test | top-decile n | top gross | top net@maker | top win | top fill | top fund cov | port trades | PF | net/trade | win | maxDD | port fund cov |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for f in res["folds"]:
            if f.get("status") != "ok":
                lines.append(
                    f"| {f['fold']} | skipped | {f.get('n_test', 0)} |  |  |  |  |  |  |  |  |  |  |  |  |"
                )
                continue
            td = f["top_decile"]
            pf = f["portfolio_maker_filled"]
            lines.append(
                f"| {f['fold']} | {f['test_start'][:10]}→{f['test_end'][:10]} | {f['n_test']} | "
                f"{td['n']} | {td['gross_per_trade']:.5f} | {td['net_per_trade_maker']:.5f} | "
                f"{td['win_rate']:.3f} | {td['maker_fill_rate']:.3f} | "
                f"{f['top_decile_funding']['funding_available_rate']:.3f} | "
                f"{pf['n_trades']} | {pf['profit_factor']} | {pf['net_per_trade']} | "
                f"{pf['win_rate']} | {pf['max_drawdown_pct']} | {pf['real_funding_coverage']:.3f} |"
            )
        lines += [
            "",
            f"- Aggregate top-decile net@maker mean/min/max: "
            f"{agg.get('top_decile_net_maker_mean')}/"
            f"{agg.get('top_decile_net_maker_min')}/"
            f"{agg.get('top_decile_net_maker_max')}",
            f"- Share of folds with positive top-decile net@maker: "
            f"{agg.get('top_decile_positive_fold_share')}",
            f"- Portfolio PF mean / total trades: "
            f"{agg.get('portfolio_pf_mean')} / {agg.get('portfolio_trades_total')}",
            "",
        ]

    lines += [
        "## Interpretation",
        "",
        "- Positive fold-level top-decile net after fixed maker cost is **candidate evidence** "
        "that ranking still separates outcomes out-of-sample within the pre-holdout era.",
        "- Fold-to-fold variance (min vs max) is the stability signal; a single strong fold "
        "is not enough to claim robustness.",
        "- Portfolio PF uses concurrent-slot constraints and maker-fill filtering; trade counts "
        "will be lower than raw top-decile counts.",
        "- Real-funding coverage is reported where OKX funding history exists; uncovered trades "
        "must not be treated as zero funding.",
        "",
        "## Risk and honesty",
        "",
        "- **Not a live profit guarantee.** Future return is unproven.",
        "- **Holdout (≥2026-05-04) was not read** for scoring or summary.",
        "- Consumed trading-validation windows were not re-tuned.",
        "- Prebuilt candidate CSVs may themselves contain post-holdout rows on disk; this audit "
        "filters them out before training and aborts if any fold timestamp leaks.",
        "- H1/H8/H10 remain challengers; ACTIVE frozen TP5/SL2 is unchanged by this report.",
        "- Short-side funding helper currently uses long funding cost convention; short funding "
        "coverage is informational and may need a dedicated short funding path for production.",
        "",
        "## Next options (owner-gated if changing live state)",
        "",
        "1. Keep collecting prospective forward/shadow trades (Todo 4) without promoting challengers.",
        "2. Owner decision only: any ACTIVE/threshold/cost change.",
        "3. E2.1b / SAHI path remains independent of this judgment-layer stability audit.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-folds", type=int, default=4)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json-out", type=Path, default=OUT_JSON)
    parser.add_argument("--report-out", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    results: list[dict] = []
    for spec in CANDIDATES:
        try:
            print(f"== {spec.name} ==")
            results.append(walk_forward(spec, args.n_folds))
        except FileNotFoundError as exc:
            results.append(
                {
                    "candidate": spec.name,
                    "role": spec.role,
                    "status": "unsupported",
                    "reason": str(exc),
                    "not_final_profitability_proof": True,
                }
            )
            print(f"unsupported: {exc}")

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "evidence_label": "historical_candidate_evidence_preholdout_only",
        "holdout_start": str(HOLDOUT_START),
        "cost_maker_rt": SWAP_MAKER_COST,
        "score_quantile": SCORE_QUANTILE,
        "n_folds": args.n_folds,
        "candidates": results,
    }
    args.json_out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"wrote {args.json_out}")

    if args.write_report:
        args.report_out.write_text(render_report(results))
        print(f"wrote {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
