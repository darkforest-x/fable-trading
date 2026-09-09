"""Frozen pre-2026 trend-policy comparison, with no live/exchange access.

Protocol: exp-altseason-donchian-ewmac-20260910-v1/PROJECT_PLAN.md. The
only real-data entry point refuses an uncommitted builder. Features use only
closed data; price outcomes may look forward within their own chronological
fold. Random entry matching sees month, causal volatility bucket, contemporaneous
market regime and decision hour, never outcomes. No parameter is fitted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess

import numpy as np
import pandas as pd

from yoyo.contracts.costs import LEGACY_P0_ROUND_TRIP
from yoyo.evaluation.altseason_trend_data import load_universe, load_bars, market_regime
from yoyo.evaluation.altseason_trend_engine import build_features, evaluate_events, portfolio_from_events, FEE_PER_SIDE, EVENT_COLUMNS
from yoyo.evaluation import altseason_trend_statistics as stats

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-altseason-donchian-ewmac-20260910-v1"
DATA_MANIFEST = ROOT / "data/altcoin_trends_20260909_v1/history_development/manifest.json"
FOLDS = [("development", "2023-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
         ("validation", "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z")]
ARMS = ("D", "E", "D_E")
SEED = 20260910
STEP = pd.Timedelta(hours=4)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_for(*parts) -> int:
    return (SEED + int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)) % 2**32


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, Path, datetime)):
        return str(value)
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def dump_json(path: Path, value) -> None:
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def committed_sources() -> dict:
    paths = [ROOT / f"yoyo/evaluation/altseason_trend_{part}.py"
             for part in ("data", "engine", "statistics", "research", "report")]
    paths += [EXP / "PROJECT_PLAN.md", ROOT / "yoyo/contracts/costs.py", ROOT / "yoyo/contracts/holdout.py"]
    result = {}
    for path in paths:
        rel = str(path.relative_to(ROOT))
        saved = subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT)
        if saved != path.read_bytes():
            raise ValueError("Commit builder before real evaluation: " + rel)
        result[rel] = sha(path)
    return {"generator_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_hashes": result}


def regime_labels(regime: pd.DataFrame) -> pd.Series:
    result = pd.Series("unknown", index=regime.index, dtype=object)
    known = regime.strong.notna()
    result.loc[known & regime.strong.fillna(False)] = "strong"
    result.loc[known & ~regime.strong.fillna(False)] = "other"
    return result


def candidate_indexes(features: pd.DataFrame, first_i: int, last_i: int, family: str):
    """Keep signal and its next-open entry inside the fold; E enters daily."""
    eligible = features.ready.fillna(False).to_numpy(bool).copy()
    eligible[:first_i] = False
    eligible[last_i:] = False
    if family == "E":
        eligible &= features.index.hour == 20
        selected = features.ewmac_signal.to_numpy(bool)
    else:
        selected = features.donchian_signal.to_numpy(bool)
    return np.flatnonzero(eligible & selected), np.flatnonzero(eligible)


def attach(events, features, symbol, fold, arm):
    events = events.copy()
    events["symbol"], events["fold"], events["arm"] = symbol, fold, arm
    if events.empty:
        for col in ("decision_time", "month", "regime", "vol_bucket", "score", "risk_sized_return"):
            events[col] = pd.Series(dtype=object)
        return events
    indexes = events.signal_i.to_numpy(int)
    events["decision_time"] = features.index[indexes] + STEP
    events["month"] = events.decision_time.dt.strftime("%Y-%m")
    events["regime"] = features.regime.iloc[indexes].to_numpy()
    events["vol_bucket"] = features.vol_bucket.iloc[indexes].to_numpy()
    events["score"] = (features.ewmac_forecast if arm == "E" else features.breakout_score).iloc[indexes].to_numpy()
    risk = pd.to_numeric(events.initial_risk_frac, errors="coerce")
    fraction = np.minimum(.01 / risk.where(risk > 0), 1 / (1 + FEE_PER_SIDE))
    events["risk_sized_return"] = fraction * pd.to_numeric(events.net_return, errors="coerce")
    return events


def buy_hold_curve(bars, features, first_i, last_i):
    """Full-cash per-symbol benchmark, first eligible next open to fold close.

    This benchmark deliberately has full cash exposure, unlike the 1%-risk
    strategy. Report its exposure beside returns; never call it equal risk.
    """
    idx = bars.index[first_i:last_i + 1]
    result = pd.DataFrame({"equity": 1., "exposure": 0., "cash": 1., "notional": 0.}, index=idx)
    ready = np.flatnonzero(features.ready.to_numpy(bool) & (np.arange(len(bars)) >= first_i)
                          & (np.arange(len(bars)) < last_i))
    if not len(ready):
        return result
    entry_i = int(ready[0]) + 1
    notional = 1 / (1 + FEE_PER_SIDE)
    quantity = notional / float(bars.open.iloc[entry_i])
    held = bars.index[entry_i:last_i + 1]
    value = quantity * bars.close.loc[held]
    result.loc[held, "equity"] = value
    result.loc[held, "notional"] = value
    result.loc[held, "cash"] = 0.
    result.loc[held, "exposure"] = 1.
    result.loc[idx[-1], "equity"] -= notional * FEE_PER_SIDE
    result.loc[idx[-1], "cash"] = result.loc[idx[-1], "equity"]
    result.loc[idx[-1], ["notional", "exposure"]] = 0.
    return result


def curve_summary(curve):
    equity = curve.equity.to_numpy(float)
    if not len(equity):
        return {"net_pct": 0., "mdd_pct": 0., "mean_exposure": 0., "peak_exposure": 0.}
    peak = np.maximum.accumulate(np.r_[1., equity])[1:]
    return {"net_pct": (equity[-1] - 1) * 100,
            "mdd_pct": float(np.min(equity / peak - 1)) * 100,
            "mean_exposure": float(curve.exposure.mean()), "peak_exposure": float(curve.exposure.max())}


def aggregate_sleeves(frames, symbols, start, end):
    """Fixed denominator, missing/unlisted sleeves remain cash (never dropped)."""
    index = pd.date_range(start, end, freq="4h", inclusive="left")
    equity, notional = [], []
    for symbol in symbols:
        frame = frames.get(symbol)
        if frame is None:
            equity.append(pd.Series(1., index=index)); notional.append(pd.Series(0., index=index))
        else:
            equity.append(frame.equity.reindex(index).ffill().fillna(1.))
            notional.append(frame.notional.reindex(index).fillna(0.))
    eq = pd.concat(equity, axis=1).mean(axis=1)
    nom = pd.concat(notional, axis=1).mean(axis=1)
    return pd.DataFrame({"equity": eq, "notional": nom, "exposure": nom / eq, "cash": eq - nom}, index=index)


def state_contributions(curve, global_regime):
    """Allocate each held bar's PnL using the state known at its OPEN."""
    # Regime row t is a t-close decision. Shift on the complete market timeline
    # before selecting the fold, preserving the first fold bar's known state.
    states = regime_labels(global_regime).shift(1).reindex(curve.index).fillna("unknown")
    changes = curve.equity.diff()
    changes.iloc[0] = curve.equity.iloc[0] - 1
    rows = []
    for state in ("strong", "other", "unknown"):
        mask = states.eq(state)
        rows.append({"state": state, "bars": int(mask.sum()),
                     "pnl_contribution_pp": float(changes.loc[mask].sum() * 100)})
    if not np.isclose(sum(r["pnl_contribution_pp"] for r in rows), (curve.equity.iloc[-1] - 1) * 100):
        raise ArithmeticError("State contributions do not reconcile")
    return rows


def summarize_events(frame, seed):
    result = stats.event_descriptives(frame)
    matched = frame.loc[frame.valid.eq(True) & frame.risk_sized_excess.notna()].copy()
    inference = stats.paired_block_inference(matched.risk_sized_excess.to_numpy(float), matched.month.to_numpy(), seed=seed)
    result.update({"matched_n": len(matched), "matched_symbols": int(matched.symbol.nunique()),
                   "matched_strategy_mean_net_bp": float(matched.net_return.mean() * 10_000) if len(matched) else None,
                   "control_mean_net_bp": float(matched.matched_net_return.mean() * 10_000) if len(matched) else None,
                   "mean_matched_excess_bp": float((matched.net_return - matched.matched_net_return).mean() * 10_000) if len(matched) else None,
                   "block_risk_excess_bp": inference["mean"] * 10_000 if inference["mean"] is not None else None,
                   "block_ci_low_bp": inference["ci_low"] * 10_000 if inference["ci_low"] is not None else None,
                   "block_ci_high_bp": inference["ci_high"] * 10_000 if inference["ci_high"] is not None else None,
                   "p": inference["p"], "n_blocks": inference["n_blocks"]})
    result.update(stats.score_diagnostics(frame, seed=seed))
    return result


def gate_verdicts(summary, portfolio_rows):
    """The research gate requires profitable actual cash-account paths too."""
    portfolios = pd.DataFrame(portfolio_rows)
    verdicts = []
    for arm in ARMS:
        rows = summary.loc[summary.arm.eq(arm) & summary.cohort.eq("all")].set_index("fold")
        actual = portfolios.loc[portfolios.portfolio.eq(arm)].set_index("fold")
        p = rows.loc["validation", "top_p_holm"]
        top_net = rows.loc["validation", "top_mean_net_bp"]
        checks = {"both_folds_positive_event_net": bool(rows.mean_net_bp.gt(0).all()),
                  "both_folds_positive_actual_portfolio_net": bool(actual.net_pct.gt(0).all()),
                  "both_folds_positive_matched_excess": bool(rows.block_risk_excess_bp.gt(0).all()),
                  "validation_top_decile_net_positive": bool(pd.notna(top_net) and top_net > 0),
                  "validation_top_decile_p_holm_lt_001": bool(pd.notna(p) and p < .01),
                  "each_fold_50_matches_10_symbols_6_months": bool((rows.matched_n.ge(50) & rows.matched_symbols.ge(10) & rows.n_blocks.ge(6)).all())}
        verdicts.append({"arm": arm, "research_gate_pass": all(checks.values()), "checks": checks})
    return verdicts


def paired_exit_diagnostic(events):
    """Compare identical Donchian entries, retaining boundary censor counts."""
    key = ["fold", "symbol", "signal_i"]
    cols = key + ["entry_price", "initial_risk_frac", "net_return", "risk_sized_return", "month", "regime", "valid", "censored"]
    base = events.loc[events.arm.eq("D"), cols]
    other = events.loc[events.arm.eq("D_E"), cols]
    pairs = base.merge(other, on=key, suffixes=("_D", "_D_E"), validate="one_to_one")
    both = pairs.valid_D.eq(True) & pairs.valid_D_E.eq(True)
    if both.any() and not np.allclose(pairs.loc[both, "entry_price_D"].to_numpy(float), pairs.loc[both, "entry_price_D_E"].to_numpy(float), equal_nan=False):
        raise ValueError("Exit comparison changed the entry")
    pairs["valid_pair"] = both
    pairs["delta_net_return"] = pairs.net_return_D_E - pairs.net_return_D
    pairs["delta_risk_sized_return"] = pairs.risk_sized_return_D_E - pairs.risk_sized_return_D
    rows = []
    for fold, _, _ in FOLDS:
        for cohort in ("all", "strong"):
            mask = both & pairs.fold.eq(fold)
            if cohort == "strong":
                mask &= pairs.regime_D.eq("strong")
            g = pairs.loc[mask]
            effect = stats.paired_block_inference(g.delta_risk_sized_return, g.month_D, seed_for("exit", fold, cohort))
            rows.append({"fold": fold, "cohort": cohort, "paired_n": len(g),
                         "censored_either": int((g.censored_D.eq(True) | g.censored_D_E.eq(True)).sum()),
                         "mean_delta_net_bp": float(g.delta_net_return.mean() * 10000) if len(g) else None,
                         **effect})
    return pairs, pd.DataFrame(rows)


def run(outdir: Path, manifest_path=DATA_MANIFEST):
    if outdir.exists():
        raise ValueError("Preserve earlier runs; output directory already exists")
    if 2 * FEE_PER_SIDE != LEGACY_P0_ROUND_TRIP:
        raise ValueError("Cost contract drift")
    provenance = committed_sources()
    records = load_universe(manifest_path)
    outdir.mkdir(parents=True)
    dump_json(outdir / "run_started.json", {**provenance, "started_at": datetime.now(timezone.utc), "holdout_consumed": False})
    bars_by, features_by, coverage = {}, {}, []
    for n, record in enumerate(records, 1):
        symbol = record["symbol"]
        bars = load_bars(record)
        features = build_features(bars)
        features["breakout_score"] = (bars.close - features.prior_high20) / features.atr
        bars_by[symbol], features_by[symbol] = bars, features
        ready = features.index[features.ready]
        coverage.append({"symbol": symbol, **bars.attrs["source_receipt"],
                         "ready_rows": int(features.ready.sum()), "first_ready": ready[0] + STEP if len(ready) else None})
        print(f"features {n}/{len(records)} {symbol}: {len(bars)} bars, ready={len(ready)}", flush=True)
    regime = market_regime(features_by)
    regime.to_csv(outdir / "market_regime.csv.gz", index_label="bar_open")
    labels = regime_labels(regime)
    for features in features_by.values():
        features["regime"] = labels.reindex(features.index).fillna("unknown")
    altcoins = sorted(set(bars_by) - {"BTC", "ETH"})
    if len(altcoins) != 52:
        raise ValueError("Fixed altcoin denominator changed")
    all_events, all_ledger, all_maps, portfolio_rows, state_rows, equity_rows = [], [], [], [], [], []
    for fold, start, end in FOLDS:
        sleeves = {key: {} for key in (*ARMS, *("random_" + a for a in ARMS), "buy_hold")}
        for n, symbol in enumerate(altcoins, 1):
            bars, f = bars_by[symbol], features_by[symbol]
            positions = np.flatnonzero((bars.index >= pd.Timestamp(start)) & (bars.index + STEP <= pd.Timestamp(end)))
            if not len(positions):
                continue
            first, last = int(positions[0]), int(positions[-1])
            sleeves["buy_hold"][symbol] = buy_hold_curve(bars, f, first, last)
            maps, candidates = {}, {}
            for family in ("D", "E"):
                chosen, eligible = candidate_indexes(f, first, last, family)
                candidates[family] = chosen
                maps[family] = stats.match_indexes(f, chosen, eligible, seed=seed_for(symbol, fold, family))
                all_maps.extend({"symbol": symbol, "fold": fold, "family": family, "signal_i": int(i),
                                 "control_i": j, "decision_time": bars.index[i] + STEP,
                                 "control_decision_time": bars.index[j] + STEP if j is not None else None}
                                for i, j in maps[family].items())
            for arm in ARMS:
                family = "E" if arm == "E" else "D"
                events = attach(evaluate_events(bars, f, candidates[family], first, last, arm), f, symbol, fold, arm)
                control_indexes = sorted(j for j in maps[family].values() if j is not None)
                control = attach(evaluate_events(bars, f, control_indexes, first, last, arm), f, symbol, fold, arm)
                lookup = control.set_index("signal_i")
                events["control_i"] = events.signal_i.map(maps[family])
                for dest, source in (("matched_net_return", "net_return"), ("matched_risk_sized_return", "risk_sized_return")):
                    values = lookup.loc[lookup.valid.eq(True), source] if len(lookup) else pd.Series(dtype=float)
                    events[dest] = events.control_i.map(values)
                events["risk_sized_excess"] = events.risk_sized_return - events.matched_risk_sized_return
                all_events.append(events)
                for name, table in ((arm, events), ("random_" + arm, control)):
                    curve, ledger = portfolio_from_events(bars, table, first, last)
                    ledger["portfolio"] = name
                    sleeves[name][symbol] = curve
                    all_ledger.append(ledger)
            print(f"{fold} {n}/52 {symbol}: D={len(candidates['D'])}, E={len(candidates['E'])}", flush=True)
        for name, frames in sleeves.items():
            curve = aggregate_sleeves(frames, altcoins, start, end)
            portfolio_rows.append({"fold": fold, "portfolio": name, **curve_summary(curve)})
            state_rows.extend({"fold": fold, "portfolio": name, **r} for r in state_contributions(curve, regime))
            eq = curve.reset_index(names="bar_open")
            eq["fold"], eq["portfolio"] = fold, name
            equity_rows.append(eq)
    metadata = ["symbol", "fold", "arm", "decision_time", "month", "regime", "vol_bucket", "score", "risk_sized_return",
                "control_i", "matched_net_return", "matched_risk_sized_return", "risk_sized_excess"]
    events = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame(columns=EVENT_COLUMNS + metadata)
    ledger = pd.concat(all_ledger, ignore_index=True) if all_ledger else pd.DataFrame(columns=EVENT_COLUMNS + metadata + ["portfolio", "accepted"])
    summaries = []
    for fold, _, _ in FOLDS:
        for arm in ARMS:
            group = events.loc[events.fold.eq(fold) & events.arm.eq(arm)]
            for cohort in ("all", "strong", "other", "unknown"):
                subset = group if cohort == "all" else group.loc[group.regime.eq(cohort)]
                summaries.append({"fold": fold, "arm": arm, "cohort": cohort,
                                  **summarize_events(subset, seed_for(fold, arm, cohort))})
    summary = pd.DataFrame(summaries)
    for _, indexes in summary.groupby(["fold", "cohort"]).groups.items():
        summary.loc[indexes, "p_holm"] = stats.holm(summary.loc[indexes, "p"].tolist(), total_tests=3)
        summary.loc[indexes, "top_p_holm"] = stats.holm(summary.loc[indexes, "top_p"].tolist(), total_tests=3)
    events.to_csv(outdir / "events.csv.gz", index=False)
    ledger.to_csv(outdir / "portfolio_ledger.csv.gz", index=False)
    pd.DataFrame(all_maps).to_csv(outdir / "matched_mapping.csv.gz", index=False)
    pd.concat(equity_rows, ignore_index=True).to_csv(outdir / "portfolio_equity.csv.gz", index=False)
    pd.DataFrame(portfolio_rows).to_csv(outdir / "portfolios.csv", index=False)
    pd.DataFrame(state_rows).to_csv(outdir / "state_contributions.csv", index=False)
    pd.DataFrame(coverage).to_csv(outdir / "coverage.csv", index=False)
    summary.to_csv(outdir / "event_summary.csv", index=False)
    exit_pairs, exit_summary = paired_exit_diagnostic(events)
    exit_pairs.to_csv(outdir / "same_entry_exit_pairs.csv.gz", index=False)
    exit_summary.to_csv(outdir / "same_entry_exit_summary.csv", index=False)
    verdicts = gate_verdicts(summary, portfolio_rows)
    for rel, expected in provenance["source_hashes"].items():
        if sha(ROOT / rel) != expected:
            raise ValueError("Builder changed during evaluation: " + rel)
    artifacts = {p.name: {"sha256": sha(p), "bytes": p.stat().st_size} for p in sorted(outdir.iterdir()) if p.is_file()}
    manifest = {**provenance, "finished_at": datetime.now(timezone.utc), "data_manifest_path": str(manifest_path),
                "data_manifest_sha256": sha(Path(manifest_path)), "source_receipts": coverage,
                "folds": FOLDS, "arms": ARMS, "altcoin_denominator": 52, "seed": SEED,
                "holdout_consumed": False, "holdout_price_rows_materialized": 0, "end_exclusive": "2026-01-01T00:00:00Z",
                "risk_fraction": .01, "round_trip_entry_notional_cost": LEGACY_P0_ROUND_TRIP,
                "funding_included": False, "survivorship_bias": True, "parameter_search": False,
                "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
                "verdicts": verdicts, "artifacts": artifacts}
    dump_json(outdir / "manifest.json", manifest)
    print(json.dumps(clean({"verdicts": verdicts, "portfolios": portfolio_rows}), ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=EXP / "results")
    args = parser.parse_args()
    run(args.out.resolve())


if __name__ == "__main__":
    main()
