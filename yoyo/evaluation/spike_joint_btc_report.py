"""Pre-specified BTC entry-gate comparison on receipt-bound SPIKE joint replays.

Source: owner 2026-09-22 and the committed experiment PROJECT_PLAN. Outcomes
are only reporting labels. Earlier selection excludes exits at/after SPLIT;
all variants share monthly bootstrap draws and calendar-week sign flips so
cross-asset dependence is not treated as thousands of independent trades.
This is exposed historical evidence, not account performance or a blind test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

EXP = Path("experiments/active/exp-spike-joint-btc-gate-20260922-v1")
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
START = pd.Timestamp("2024-09-10T00:00:00Z")
END = pd.Timestamp("2026-05-01T00:00:00Z")
GATES = ("none", "same_sma60", "same_sma120", "same_sma240", "h1_sma60", "h1_sma120", "h1_sma240")
EXITS = ("price", "rsi7")
SOURCE = Path("experiments/active/exp-spike-v112-support-20260919-v1/results/run_v1")
PERIODS = ("full", "earlier", "later")
SEED, REPS, PSEED, PREPS, FAMILY = 91509, 2000, 92201, 10000, 24


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def booleans(series: pd.Series) -> pd.Series:
    value = series.astype(str).str.lower().map({"true": True, "false": False})
    if value.isna().any():
        raise ValueError(f"Unknown persisted boolean {series.name}")
    return value.astype(bool)


def add_dates(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for col in ("signal_bar_open", "entry_time", "exit_time", "control_exit_time"):
        if col in frame:
            frame[col] = pd.to_datetime(frame[col], utc=True, format="mixed")
    frame["signal_close"] = frame.signal_bar_open + pd.to_timedelta(frame.timeframe.map({"15m": 15, "1h": 60}), unit="m")
    frame["month"] = frame.signal_close.dt.strftime("%Y-%m")
    # Monday-anchored UTC calendar weeks, including the two partial end weeks.
    frame["week"] = (frame.signal_close.dt.normalize() - pd.to_timedelta(frame.signal_close.dt.dayofweek, unit="D")).dt.strftime("%Y-%m-%d")
    if "net_return" in frame:
        frame["net_bp"] = frame.net_return * 1e4
        frame["gross_bp"] = frame.gross_return * 1e4
    return frame


def period_rows(t: pd.DataFrame, period: str) -> pd.DataFrame:
    closed = t.loc[t.status.eq("closed")]
    if period == "earlier":
        return closed.loc[closed.signal_close.lt(SPLIT) & closed.exit_time.lt(SPLIT)]
    if period == "later":
        return closed.loc[closed.signal_close.ge(SPLIT)]
    if period != "full":
        raise ValueError(period)
    return closed


def sign_flip(values, *, seed=PSEED, reps=PREPS) -> float:
    """One-sided calendar-block test; zero/one block cannot establish evidence."""
    v = np.asarray(values, float)
    if not np.isfinite(v).all() or len(v) < 2:
        return float("nan")
    observed = float(v.sum())
    if len(v) <= 16:
        ids = np.arange(2 ** len(v), dtype=np.uint64)
        signs = (((ids[:, None] >> np.arange(len(v), dtype=np.uint64)) & 1).astype(float) * 2 - 1)
        simulated = (signs * v).sum(axis=1)
        return float(np.mean(simulated >= observed - 1e-12))
    rng = np.random.default_rng(seed)
    exceed = 0
    for n in range(0, reps, 500):
        signs = rng.choice([-1., 1.], (min(500, reps - n), len(v)))
        exceed += int(((signs * v).sum(axis=1) >= observed - 1e-12).sum())
    return (exceed + 1) / (reps + 1)


def holm(values, family: int = FAMILY) -> np.ndarray:
    """Step-down adjusted p; missing tests count conservatively in the family."""
    p = np.asarray(values, float)
    valid = np.flatnonzero(np.isfinite(p))
    if family < len(p):
        raise ValueError("family smaller than reported tests")
    out = np.full(len(p), np.nan)
    order = valid[np.argsort(p[valid], kind="stable")]
    running = 0.
    for rank, i in enumerate(order):
        running = max(running, min(1., (family - rank) * p[i]))
        out[i] = running
    return out


def drawdown(t: pd.DataFrame) -> float:
    """Realized equal-risk event curve, coalescing simultaneous exits first."""
    sums = t.groupby("exit_time").net_r.sum().sort_index().to_numpy(float)
    curve = np.r_[0., sums.cumsum()]
    return float(np.max(np.maximum.accumulate(curve) - curve))


def metrics(t: pd.DataFrame, period: str) -> dict:
    p = period_rows(t, period)
    matched = p.matched.copy()
    if period == "earlier":
        matched &= p.control_exit_time.lt(SPLIT)
    q = p.loc[matched]
    delta_r = q.net_r - q.control_net_r
    delta_bp = q.net_bp - q.control_net_return * 1e4
    weekly_r = delta_r.groupby(q.week).sum()
    weekly_bp = delta_bp.groupby(q.week).sum()
    plus, minus = p.net_r.clip(lower=0).sum(), -p.net_r.clip(upper=0).sum()
    return {"closed": len(p), "wins": int(p.net_r.gt(0).sum()), "win_rate": p.net_r.gt(0).mean(),
            "mean_net_r": p.net_r.mean(), "sum_net_r": p.net_r.sum(), "mean_gross_r": p.gross_r.mean(),
            "mean_net_bp": p.net_bp.mean(), "mean_gross_bp": p.gross_bp.mean(),
            "pf_r": plus / minus if minus > 0 else np.nan, "realized_dd_r": drawdown(p),
            "gt5r": int(p.net_r.gt(5).sum()), "gt10r": int(p.net_r.gt(10).sum()),
            "gt10r_rate": p.net_r.gt(10).mean(), "matched": len(q),
            "match_coverage": len(q) / len(p) if len(p) else np.nan,
            "paired_actual_r": q.net_r.mean(), "random_r": q.control_net_r.mean(),
            "excess_r": delta_r.mean(), "random_bp": q.control_net_return.mean() * 1e4,
            "excess_bp": delta_bp.mean(), "random_p_r": sign_flip(weekly_r), "random_p_bp": sign_flip(weekly_bp),
            "random_weeks": len(weekly_r),
            "cross_split_closed": int((t.status.eq("closed") & t.signal_close.lt(SPLIT) & t.exit_time.ge(SPLIT)).sum()),
            "censored_total": int(t.status.ne("closed").sum())}


def differences(a: pd.DataFrame, b: pd.DataFrame, column: str, *, months: list[str] | None = None) -> dict:
    """Candidate minus baseline with each arm's own denominator in each draw."""
    aa = a.groupby("month")[column].agg(["sum", "count"])
    bb = b.groupby("month")[column].agg(["sum", "count"])
    months = sorted(set(aa.index) | set(bb.index)) if months is None else months
    if not months:
        return {"metric": column, "delta_mean": np.nan, "ci_low": np.nan, "ci_high": np.nan,
                "delta_sum": 0., "sum_ci_low": np.nan, "sum_ci_high": np.nan,
                "months": 0, "weeks": 0, "p": np.nan, "valid_reps": 0,
                "empty_baseline_months": 0, "empty_filtered_months": 0}
    missing_a, missing_b = len(set(months) - set(aa.index)), len(set(months) - set(bb.index))
    aa, bb = aa.reindex(months, fill_value=0), bb.reindex(months, fill_value=0)
    draws = np.random.default_rng(SEED).integers(0, len(months), (REPS, len(months)))
    an, bn = aa["count"].to_numpy()[draws].sum(1), bb["count"].to_numpy()[draws].sum(1)
    asum, bsum = aa["sum"].to_numpy()[draws].sum(1), bb["sum"].to_numpy()[draws].sum(1)
    valid = (an > 0) & (bn > 0)
    low, high = (np.quantile((bsum / np.maximum(bn, 1) - asum / np.maximum(an, 1))[valid], [.025, .975])
                 if len(months) >= 2 and valid.any() else (np.nan, np.nan))
    slo, shi = np.quantile(bsum - asum, [.025, .975])
    aw, bw = a.groupby("week")[column].sum(), b.groupby("week")[column].sum()
    weeks = sorted(set(aw.index) | set(bw.index))
    contrib = (bw.reindex(weeks, fill_value=0) / len(b) - aw.reindex(weeks, fill_value=0) / len(a)) if len(a) and len(b) else np.array([np.nan])
    return {"metric": column, "delta_mean": b[column].mean() - a[column].mean(),
            "ci_low": float(low), "ci_high": float(high), "months": len(months), "valid_reps": int(valid.sum()),
            "empty_baseline_months": missing_a, "empty_filtered_months": missing_b,
            "delta_sum": b[column].sum() - a[column].sum(), "sum_ci_low": float(slo), "sum_ci_high": float(shi),
            "weeks": len(weeks), "p": sign_flip(contrib)}


def attribution(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Record lost winners and new serial fills; shared exits must be unchanged."""
    j = a.merge(b, on="trade_key", how="outer", suffixes=("_a", "_b"), indicator=True, validate="one_to_one")
    shared, lost, new = (j._merge.eq(k) for k in ("both", "left_only", "right_only"))
    for col in ("net_r", "net_bp"):
        if not np.allclose(j.loc[shared, col + "_a"], j.loc[shared, col + "_b"], rtol=1e-10, atol=1e-10):
            raise ValueError("An entry-only gate changed a shared event's exit")
    removed, added = j.loc[lost], j.loc[new]
    delta = -removed.net_r_a.sum() + added.net_r_b.sum()
    assert np.isclose(delta, b.net_r.sum() - a.net_r.sum(), atol=1e-8)
    return {"baseline_n": len(a), "filtered_n": len(b), "retained": int(shared.sum()),
            "removed": len(removed), "added": len(added), "removed_winners": int(removed.net_r_a.gt(0).sum()),
            "removed_losers": int(removed.net_r_a.lt(0).sum()), "avoided_loss_r": -removed.net_r_a.clip(upper=0).sum(),
            "forgone_profit_r": removed.net_r_a.clip(lower=0).sum(), "added_r": added.net_r_b.sum(),
            "net_delta_r": delta, "baseline_gt10r": int(a.net_r.gt(10).sum()),
            "retained_gt10r": int((shared & j.net_r_a.gt(10)).sum()),
            "lost_gt10r": int(removed.net_r_a.gt(10).sum()), "added_gt10r": int(added.net_r_b.gt(10).sum())}


def select_earlier(summary: pd.DataFrame) -> list[dict]:
    """Choose only with mature earlier outcomes; later columns never enter."""
    rows = []
    for exit_rule in EXITS:
        p = summary.loc[summary.exit_rule.eq(exit_rule) & summary.period.eq("earlier") & summary.timeframe.eq("pooled")
                        & summary.gate.ne("none")].copy()
        eligible = []
        for row in p.itertuples():
            counts = summary.loc[summary.exit_rule.eq(exit_rule) & summary.period.eq("earlier")
                                 & summary.gate.eq(row.gate) & summary.timeframe.isin(["15m", "1h"]), "closed"]
            if row.closed >= 100 and len(counts) == 2 and counts.ge(30).all():
                eligible.append(row.gate)
        p = p.loc[p.gate.isin(eligible)].sort_values(["mean_net_r", "gate"], ascending=[False, True])
        top = None if p.empty else p.iloc[0]
        recommend = "none" if top is None or top.mean_net_r <= 0 or top.mean_net_bp <= 0 else str(top.gate)
        rows.append({"exit_rule": exit_rule, "exploratory_top": None if top is None else str(top.gate),
                     "selected_gate": recommend, "criterion": "earlier mature pooled mean net R, positive R and bp",
                     "earlier_score_r": None if top is None else float(top.mean_net_r),
                     "earlier_score_bp": None if top is None else float(top.mean_net_bp)})
    return rows


def decision_flags(gate: str, selected_gate: str, checks: dict) -> dict:
    """Only the frozen earlier selection can pass as the selected policy."""
    selected = gate == selected_gate and gate != "none"
    return {"selected_from_earlier": selected, "descriptive_conditions_met": all(checks.values()),
            "passed": selected and all(checks.values())}


def validate_window(frame: pd.DataFrame) -> None:
    if not frame.signal_close.ge(START).all() or not frame.signal_close.lt(END).all():
        raise ValueError("event outside frozen signal-close window")
    if "control_signal_bar_open" in frame:
        known = frame.control_signal_bar_open.notna()
        stamp = pd.to_datetime(frame.loc[known, "control_signal_bar_open"], utc=True, format="mixed")
        close = stamp + pd.to_timedelta(frame.loc[known, "timeframe"].map({"15m": 15, "1h": 60}), unit="m")
        if not close.ge(START).all() or not close.lt(END).all():
            raise ValueError("control outside frozen signal-close window")


def validate_parent_symbol(symbol: str, directory: Path, tables: dict) -> None:
    """Independently compare parent facts; receipt booleans are not evidence."""
    from yoyo.evaluation.spike_v10_4_increment_report import PARITY
    from yoyo.evaluation.spike_v112_support_report import compare
    from yoyo.evaluation.spike_v112_execution_report import control_contract
    parent = SOURCE / "streams" / symbol
    old_s = pd.read_csv(parent / "decisions.csv.gz")
    new_s = tables["statuses"].query("exit_rule == 'price' and gate == 'none'")
    def candidate_keys(frame, status_column):
        return {(str(r.timeframe), int(r.signal_i), pd.Timestamp(r.signal_bar_open), str(getattr(r, status_column))) for r in frame.itertuples()}
    if len(old_s) != len(new_s) or candidate_keys(old_s, "box_any_status") != candidate_keys(new_s, "status"):
        raise ValueError("parent candidate identity/timestamp/status mismatch")
    expected_keys = {f"binance_um:{symbol}:{r.timeframe}:box_any:{int(r.signal_i)}" for r in old_s.itertuples()}
    if set(new_s.trade_key) != expected_keys:
        raise ValueError("parent candidate trade-key mismatch")
    old_t = pd.read_csv(parent / "trades.csv.gz").query("arm == 'box_any'")
    new_t = tables["trades"].query("exit_rule == 'price' and gate == 'none'")
    fields = [*PARITY, "initial_risk_frac", "gross_return", "net_return", "status"]
    if not compare(old_t, new_t, fields, "independent_parent")["passed"]:
        raise ValueError("parent baseline trade economics mismatch")
    old_c = pd.read_csv(parent / "controls.csv.gz").query("arm == 'box_any'")
    new_c = tables["controls"].query("exit_rule == 'price' and gate == 'none'")
    if len(old_c) or len(new_c):
        control_contract(old_c, new_c)


def load(run: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    manifest = json.loads((run / "manifest.json").read_text())
    identity = json.loads((run / "identity.json").read_text())
    assert manifest["complete"] and manifest["completed"] == manifest["symbols"] == 638 and not manifest["errors"]
    ih = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    assert ih == manifest["run_identity"]
    assert {p.name for p in (run / "streams").iterdir()} == set(identity["inputs"])
    assert digest(SOURCE / "manifest.json") == identity["source_manifest_sha256"]
    assert digest(SOURCE / "identity.json") == identity["source_identity_sha256"]
    assert identity["inputs"] == identity["source_identity"]["inputs"]
    for key in ("btc_input", "exchange_info"):
        assert digest(Path(identity[key]["path"])) == identity[key]["sha256"]
    parts = {name: [] for name in ("trades", "controls", "statuses")}
    receipt_hashes = {}
    for symbol in sorted(identity["inputs"]):
        directory = run / "streams" / symbol
        r = json.loads((directory / "completion.json").read_text())
        assert r["run_identity"] == ih and r["input_sha256"] == identity["inputs"][symbol]
        assert r["baseline_parity"]["passed"] and r["control_parity"]["passed"] and r["candidate_status_parity"]
        for name, sha in r["files"].items():
            assert digest(directory / name) == sha
        assert digest(SOURCE / "streams" / symbol / "completion.json") == identity["source_receipts"][symbol]
        parent_receipt = json.loads((SOURCE / "streams" / symbol / "completion.json").read_text())
        for name, sha in parent_receipt["files"].items():
            assert digest(SOURCE / "streams" / symbol / name) == sha
        symbol_tables = {}
        for name in parts:
            assert f"{name}.csv.gz" in r["files"]
            table = pd.read_csv(directory / f"{name}.csv.gz")
            symbol_tables[name] = table
            if len(table):
                parts[name].append(table)
        validate_parent_symbol(symbol, directory, symbol_tables)
        receipt_hashes[symbol] = digest(directory / "completion.json")
    tables = {k: pd.concat(v, ignore_index=True) for k, v in parts.items()}
    t, c = tables["trades"], tables["controls"]
    keys = ["exit_rule", "gate", "trade_key"]
    assert not t.duplicated(keys).any() and not c.duplicated(keys).any()
    assert set(map(tuple, t[keys].values)) == set(map(tuple, c[keys].values))
    keep = [col for col in c.columns if col not in t.columns or col in keys]
    t = add_dates(t.merge(c[keep], on=keys, validate="one_to_one"))
    t["matched"] = booleans(t.matched)
    s = add_dates(tables["statuses"])
    validate_window(t)
    validate_window(s)
    base_status = s.loc[s.exit_rule.eq("price") & s.gate.eq("none")]
    key_fields = ["trade_key", "timeframe", "signal_i", "signal_bar_open"]
    expected_candidates = set(map(tuple, base_status[key_fields].values))
    assert len(base_status) == len(expected_candidates) == 9301
    for _, group in s.groupby(["exit_rule", "gate"]):
        assert len(group) == 9301 and set(map(tuple, group[key_fields].values)) == expected_candidates
    assert len(t.loc[t.exit_rule.eq("price") & t.gate.eq("none")]) == 9287
    # Exactly equal 1h gates, including decisions and occupied/censored status.
    for exit_rule in EXITS:
        for length in (60, 120, 240):
            a = s.loc[s.timeframe.eq("1h") & s.exit_rule.eq(exit_rule) & s.gate.eq(f"same_sma{length}")].set_index("trade_key")
            b = s.loc[s.timeframe.eq("1h") & s.exit_rule.eq(exit_rule) & s.gate.eq(f"h1_sma{length}")].set_index("trade_key")
            pd.testing.assert_series_equal(a.status.sort_index(), b.status.sort_index())
    return t, s, {"run_identity": ih, "manifest_sha256": digest(run / "manifest.json"), "receipts": receipt_hashes}


def markdown(frame: pd.DataFrame) -> str:
    def cell(v):
        if pd.isna(v):
            return "—"
        if isinstance(v, (float, np.floating)):
            return f"{v:.4f}"
        return str(v)
    return "\n".join(["| " + " | ".join(frame.columns) + " |", "| " + " | ".join(["---"] * len(frame.columns)) + " |",
                      *["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False, name=None)]])


def main(run: Path, out: Path) -> None:
    from yoyo.evaluation.spike_v8_six_filters import _committed
    assert _committed((Path(__file__), Path("tests/evaluation/test_spike_joint_btc_report.py"), EXP / "PROJECT_PLAN.md", EXP / "config.json"))
    assert not out.exists(), "Keep previous statistics immutable; use a new output directory."
    config = json.loads((EXP / "config.json").read_text())
    expected = {"gates": list(GATES), "exit_rules": list(EXITS), "bootstrap_seed": SEED, "bootstrap_reps": REPS,
                "permutation_seed": PSEED, "permutation_reps": PREPS, "comparison_family": FAMILY}
    for key, value in expected.items():
        assert config[key] == value, key
    for key, value in (("start", START), ("split", SPLIT), ("end_exclusive", END)):
        assert pd.Timestamp(config[key]) == value, key
    t, s, evidence = load(run)
    rows, diffs, attrs = [], [], []
    for tf in ("15m", "1h", "pooled"):
        tf_rows = t if tf == "pooled" else t.loc[t.timeframe.eq(tf)]
        for ex in EXITS:
            for gate in GATES:
                part = tf_rows.loc[tf_rows.exit_rule.eq(ex) & tf_rows.gate.eq(gate)]
                base = tf_rows.loc[tf_rows.exit_rule.eq(ex) & tf_rows.gate.eq("none")]
                for period in PERIODS:
                    key = {"timeframe": tf, "exit_rule": ex, "gate": gate, "period": period}
                    rows.append({**key, **metrics(part, period)})
                    if gate != "none":
                        a, b = period_rows(base, period), period_rows(part, period)
                        for column in ("net_r", "net_bp"):
                            lo, hi = (START, SPLIT) if period == "earlier" else (SPLIT, END) if period == "later" else (START, END)
                            months = pd.date_range(lo.replace(day=1), (hi - pd.Timedelta(nanoseconds=1)).replace(day=1).normalize(), freq="MS").strftime("%Y-%m").tolist()
                            diffs.append({**key, **differences(a, b, column, months=months)})
                        attrs.append({**key, **attribution(a, b)})
    summary, delta = pd.DataFrame(rows), pd.DataFrame(diffs)
    for period in PERIODS:
        for metric in ("net_r", "net_bp"):
            mask = delta.period.eq(period) & delta.metric.eq(metric) & delta.timeframe.ne("pooled")
            delta.loc[mask, "p_holm"] = holm(delta.loc[mask, "p"], FAMILY)
        for endpoint in ("r", "bp"):
            mask = summary.period.eq(period) & summary.gate.ne("none") & summary.timeframe.ne("pooled")
            summary.loc[mask, f"random_p_{endpoint}_holm"] = holm(summary.loc[mask, f"random_p_{endpoint}"], FAMILY)
    selected = select_earlier(summary)
    selected_by_exit = {r["exit_rule"]: r["selected_gate"] for r in selected}
    decisions = []
    for row in summary.loc[summary.period.eq("later") & summary.gate.ne("none") & summary.timeframe.ne("pooled")].itertuples():
        dr = delta.loc[delta.period.eq("later") & delta.timeframe.eq(row.timeframe) & delta.exit_rule.eq(row.exit_rule)
                       & delta.gate.eq(row.gate) & delta.metric.eq("net_r")].iloc[0]
        db = delta.loc[delta.period.eq("later") & delta.timeframe.eq(row.timeframe) & delta.exit_rule.eq(row.exit_rule)
                       & delta.gate.eq(row.gate) & delta.metric.eq("net_bp")].iloc[0]
        checks = {"later_r_positive": row.mean_net_r > 0, "later_bp_positive": row.mean_net_bp > 0,
                  "r_delta_ci_positive": dr.ci_low > 0, "bp_delta_ci_positive": db.ci_low > 0,
                  "excess_r_positive": row.excess_r > 0, "excess_bp_positive": row.excess_bp > 0,
                  "random_r_holm_lt_001": row.random_p_r_holm < .01,
                  "random_bp_holm_lt_001": row.random_p_bp_holm < .01,
                  "quality_r_holm_lt_001": dr.p_holm < .01, "quality_bp_holm_lt_001": db.p_holm < .01}
        decisions.append({"timeframe": row.timeframe, "exit_rule": row.exit_rule, "gate": row.gate,
                          **checks, **decision_flags(row.gate, selected_by_exit[row.exit_rule], checks)})
    out.mkdir(parents=True)
    summary.to_csv(out / "metrics.csv", index=False)
    delta.to_csv(out / "differences.csv", index=False)
    pd.DataFrame(attrs).to_csv(out / "attribution.csv", index=False)
    pd.DataFrame(decisions).to_csv(out / "research_gates.csv", index=False)
    s.groupby(["timeframe", "exit_rule", "gate", "status"]).size().rename("n").reset_index().to_csv(out / "status_counts.csv", index=False)
    monthly = t.loc[t.status.eq("closed")].groupby(["timeframe", "exit_rule", "gate", "month"]).agg(closed=("net_r", "size"), net_r=("net_r", "sum"), mean_r=("net_r", "mean"), net_bp=("net_bp", "mean"))
    monthly.to_csv(out / "monthly.csv")
    (out / "selection.json").write_text(json.dumps(selected, indent=2) + "\n")
    columns = ["timeframe", "exit_rule", "gate", "period", "closed", "win_rate", "mean_net_r", "sum_net_r", "mean_net_bp", "pf_r", "realized_dd_r", "random_r", "excess_r", "random_p_r_holm"]
    (out / "tables.md").write_text(markdown(summary[columns]) + "\n")
    evidence.update(source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    reporter_sha256=digest(Path(__file__)), selection=selected,
                    files={p.name: digest(p) for p in out.iterdir() if p.is_file()}, all_research_gates_passed=int(sum(r["passed"] for r in decisions)),
                    training_eligible=False, production_eligible=False)
    (out / "manifest.json").write_text(json.dumps(evidence, indent=2, default=lambda x: bool(x) if isinstance(x, np.bool_) else str(x)) + "\n")
    print(json.dumps({"out": str(out), "selection": selected, "gates_passed": sum(r["passed"] for r in decisions)}, default=str))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    main(args.run, args.out)
