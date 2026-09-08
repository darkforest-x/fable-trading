"""Independent scalar audit of saved IMACD/YOLO follow-through labels.

Source: exp-imacd-yolo-followthrough-20260908-v1/PROJECT_PLAN.md. Reads only
saved outcomes, controls, contexts and prior decision/metadata ledgers; never
imports this experiment's observe/summarize, reads a raw market file, runs a
model, or changes an outcome. Commit this source before writing the review.

At closed anchor j the entry is open[j+1], endpoint close[j+H], and excursions
use exactly high/low[j+1:j+H]. Only labels look beyond j. ATR normalization
uses saved atr[j]. Pools are rebuilt from the anchor-close UTC month, the
causal last-240 ATR/close percentile (including j), and no saved release in
j-9..j. The event/anchor-kind SHA seed draws three unique controls once for
all horizons. A case lacking H labels emits no control records; otherwise
all selected controls are retained, with incomplete labels never replaced.

ISO-week p-values use math.fsum scalar enumeration, avoiding the runner's
NumPy matrix multiplication and its observed runtime warnings. This verifies
arithmetic, not the exchangeability assumptions: adjacent weeks share labels,
controls are reused and prior data were exposed. Immediate imacd/confirmation
anchors have identical case paths but different random controls because the
frozen seed includes anchor_kind; their excess difference is not a wait effect.
Saved ATR/MD/release values are audited as source evidence, not independently
recomputed from raw history. No account PnL or execution performance is inferred.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-imacd-yolo-followthrough-20260908-v1"
DATA = ROOT / "data/imacd_yolo_followthrough_20260908_v1"
PRIOR = ROOT / "experiments/active/exp-imacd-yolo-expanded-20260908-v1"
OLD_DATA = ROOT / "data/imacd_yolo_expanded_20260908_v1"
BOUNDS = {
    "pre_holdout": (pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-05-04", tz="UTC")),
    "holdout_review": (pd.Timestamp("2026-05-04", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC")),
}
HORIZONS = (6, 24, 72)
SEED, COST = 20260908, 20.0
OBS_FIELDS = ["anchor_i", "entry_i", "end_i", "atr_anchor", "complete", "observed_bars",
              "entry_at", "exit_at", "entry_price", "exit_price", "gross_bp", "net_bp",
              "mfe_bp", "mae_bp", "mfe_atr", "mae_atr"]
METRIC_FIELDS = ["entry_price", "exit_price", "gross_bp", "net_bp", "mfe_bp", "mae_bp", "mfe_atr", "mae_atr"]
KEY = ["event_id", "anchor_kind", "horizon"]
GROUP_KEY = ["fold", "timeframe_min", "anchor_kind", "mode", "horizon"]


def same(a, b):
    if pd.isna(a) or pd.isna(b):
        return bool(pd.isna(a) and pd.isna(b))
    if isinstance(a, (str, bool, np.bool_)) or isinstance(b, (str, bool, np.bool_)):
        return bool(a == b)
    return bool(math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-8))


def scalar_observe(ctx, anchor, side, horizon, end, minutes):
    """Recompute one saved label using scalar extrema and exact index clocks."""
    anchor, side, horizon = int(anchor), int(side), int(horizon)
    entry_i, end_i = anchor + 1, anchor + horizon
    atr = float(ctx.at[anchor, "atr"])
    result = dict(anchor_i=anchor, entry_i=entry_i, end_i=end_i, atr_anchor=atr,
                  complete=False, observed_bars=0, entry_at=None, exit_at=None,
                  **{field: None for field in METRIC_FIELDS})
    indexes = [i for i in range(entry_i, end_i + 1) if i in ctx.index
               and ctx.at[i, "_available"] <= end]
    result["observed_bars"] = len(indexes)
    if len(indexes) != horizon:
        return result
    entry, exit_price = float(ctx.at[entry_i, "open"]), float(ctx.at[end_i, "close"])
    highest = max(float(ctx.at[i, "high"]) for i in indexes)
    lowest = min(float(ctx.at[i, "low"]) for i in indexes)
    favorable = max(0.0, highest - entry) if side == 1 else max(0.0, entry - lowest)
    adverse = max(0.0, entry - lowest) if side == 1 else max(0.0, highest - entry)
    gross = side * (exit_price / entry - 1.0) * 10000
    result.update(complete=True, entry_at=ctx.at[entry_i, "_open_time"].isoformat(),
                  exit_at=ctx.at[end_i, "_available"].isoformat(), entry_price=entry,
                  exit_price=exit_price, gross_bp=gross, net_bp=gross - COST,
                  mfe_bp=favorable / entry * 10000, mae_bp=adverse / entry * 10000,
                  mfe_atr=favorable / atr if math.isfinite(atr) and atr > 0 else None,
                  mae_atr=adverse / atr if math.isfinite(atr) and atr > 0 else None)
    return result


def observation_errors(row, expected):
    failures = []
    for name in OBS_FIELDS:
        actual, wanted = getattr(row, name), expected[name]
        equal = (pd.Timestamp(actual) == pd.Timestamp(wanted) if wanted is not None
                 and name in ("entry_at", "exit_at") else same(actual, wanted))
        if not equal:
            failures.append(name)
    return failures


def control_ids(value):
    if pd.isna(value) or value == "":
        return []
    return [int(float(part)) for part in str(value).split(";")]


def scalar_weekly_null(values, clocks):
    """Independent scalar sign enumeration; no BLAS, dot, @ or matrix multiply."""
    weeks = {}
    for value, clock in zip(values, clocks):
        if pd.isna(value) or pd.isna(clock):
            continue
        if not math.isfinite(float(value)):
            raise ValueError("nonfinite matched excess")
        iso = pd.Timestamp(clock).isocalendar()
        weeks.setdefault(f"{iso.year}-{iso.week}", []).append(float(value))
    totals = [math.fsum(weeks[key]) for key in sorted(weeks)]
    if not totals:
        return dict(p=None, blocks=0, patterns=0, weekly_totals={})
    observed, n = math.fsum(totals), len(totals)
    if n <= 18:
        exceed = sum(math.fsum(sign * total for sign, total in zip(signs, totals))
                     >= observed - 1e-10 for signs in itertools.product((-1.0, 1.0), repeat=n))
        p, patterns = exceed / (2 ** n), 2 ** n
    else:
        rng = np.random.default_rng(SEED)
        signs = rng.choice((-1.0, 1.0), size=(10000, n))
        exceed = sum(math.fsum(float(s) * total for s, total in zip(row, totals))
                     >= observed - 1e-10 for row in signs)
        p, patterns = (1 + exceed) / 10001, 10000
    return dict(p=float(p), blocks=n, patterns=patterns, observed_sum=observed,
                weekly_totals=dict(zip(sorted(weeks), totals)))


def scalar_summary(frame):
    """Reconstruct every reported group using saved complete/matched denominators."""
    q = frame[frame.complete.eq(True)]
    matched = q[q.control_n.eq(3)]
    def mean(name, group=q):
        vals = [float(value) for value in group[name] if pd.notna(value)]
        return math.fsum(vals) / len(vals) if vals else None
    def median(name):
        vals = sorted(float(value) for value in q[name] if pd.notna(value))
        n = len(vals)
        return (vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2) if n else None
    return dict(total=len(frame), complete=len(q), censored=len(frame) - len(q),
                symbols=int(q.symbol.nunique()), gross_mean_bp=mean("gross_bp"), net_mean_bp=mean("net_bp"),
                gross_median_bp=median("gross_bp"),
                direction_rate_pct=100 * int(q.gross_bp.gt(0).sum()) / len(q) if len(q) else None,
                net_positive_rate_pct=100 * int(q.net_bp.gt(0).sum()) / len(q) if len(q) else None,
                mfe_median_bp=median("mfe_bp"), mae_median_bp=median("mae_bp"),
                mfe_median_atr=median("mfe_atr"), mae_median_atr=median("mae_atr"),
                matched=len(matched), matched_case_net_bp=mean("net_bp", matched),
                control_net_bp=mean("control_mean_net_bp", matched), excess_mean_bp=mean("matched_excess_bp", matched))


def verify_saved(data_dir: Path = DATA, results_dir: Path = EXP / "results") -> dict:
    """Read saved evidence and independently verify labels, matching and p-values."""
    data_dir, results_dir = Path(data_dir), Path(results_dir)
    summary = json.loads((results_dir / "summary.json").read_text())
    prior_path, decision_path = PRIOR / "results/summary.json", OLD_DATA / "decisions.csv"
    prior = json.loads(prior_path.read_text())
    universe_path = PRIOR / "universe.json"
    universe = json.loads(universe_path.read_text())
    symbols = [record["symbol"] for record in universe["sources"]]
    checks = {}
    def check(name, value): checks[name] = bool(value)
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (prior_path, decision_path, universe_path):
        if digest(path) != summary["source_inputs"][str(path.relative_to(ROOT))]:
            raise ValueError(f"changed input ledger: {path.name}")
    if digest(decision_path) != prior["files"][str(decision_path.relative_to(ROOT))]:
        raise ValueError("prior decision hash mismatch")
    decisions = pd.read_csv(decision_path)
    check("frozen_population_and_protocol", (
        len(decisions) == 1341 and not decisions.event_id.duplicated().any()
        and int(decisions.status.eq("confirmed").sum()) == 425
        and len(symbols) == len(set(symbols)) == 54
        and summary["rules"]["horizons"] == list(HORIZONS)
        and summary["rules"]["primary_horizon"] == 24 and summary["rules"]["cost_bp"] == COST
        and summary["holdout_consumptions"] == {"60": 3, "240": 3}
        and summary["model_inference_runs"] == 0
        and summary["training_eligible"] is False and summary["production_eligible"] is False
    ))
    expected_files = {data_dir / "outcomes.csv", data_dir / "controls.csv"} | {
        data_dir / "contexts" / f"{symbol}_{minutes}.csv.gz" for symbol in symbols for minutes in (60, 240)
    }
    files = {(ROOT / name).resolve(): expected for name, expected in summary["files"].items()}
    check("saved_file_manifest", set(files) == {path.resolve() for path in expected_files})
    if not checks["saved_file_manifest"]:
        raise ValueError("unexpected saved evidence paths; refuse raw-file reads")
    check("all_saved_file_hashes", all(digest(path) == expected for path, expected in files.items()))
    outcomes, controls = pd.read_csv(data_dir / "outcomes.csv"), pd.read_csv(data_dir / "controls.csv")
    expected_keys = {(row.event_id, kind, horizon) for row in decisions.itertuples()
                     for kind in (("imacd", "confirmation") if row.status == "confirmed" else ("imacd",))
                     for horizon in HORIZONS}
    actual_keys = set(map(tuple, outcomes[KEY].itertuples(index=False, name=None)))
    check("outcome_identity_coverage", len(outcomes) == len(actual_keys) == len(expected_keys) and actual_keys == expected_keys)
    check("control_identity_uniqueness", not controls.duplicated(KEY + ["control_anchor_i"]).any())
    check("boolean_fields", outcomes.complete.isin([True, False]).all() and controls.complete.isin([True, False]).all())
    contexts, pools, coverage_fail, bin_fail = {}, {}, [], []
    for symbol in symbols:
        for minutes in (60, 240):
            ctx = pd.read_csv(data_dir / "contexts" / f"{symbol}_{minutes}.csv.gz")
            ctx["_open_time"] = pd.to_datetime(ctx.open_time, utc=True)
            ctx["_available"] = ctx._open_time + pd.Timedelta(minutes=minutes)
            ctx = ctx.set_index("bar_i", verify_integrity=True)
            key = f"{symbol}_{minutes}"
            info = prior["inputs"][f"{key}_holdout_review"]
            first = pd.Timestamp(info["parsed_first"])
            index_values = ctx.index.to_numpy(int)
            values = ctx[["open", "high", "low", "close", "volume"]].to_numpy(float)
            check(key + "/context_grid_and_ohlc", (
                np.array_equal(index_values, np.arange(index_values[0], int(info["parsed_rows"])))
                and (ctx._open_time == first + pd.to_timedelta(index_values * minutes, unit="min")).all()
                and ctx._available.iloc[-1] == BOUNDS["holdout_review"][1]
                and np.isfinite(values).all() and (values[:, :4] > 0).all() and (values[:, 4] >= 0).all()
                and ctx.high.ge(ctx[["open", "close", "low"]].max(axis=1)).all()
                and ctx.low.le(ctx[["open", "close", "high"]].min(axis=1)).all()
                and ctx.release_side.isin([-1, 0, 1]).all() and ctx.ready.isin([True, False]).all()
            ))
            volatility = (ctx.atr / ctx.close).to_numpy(float)
            releases = ctx.release_side.to_numpy(int)
            for offset, anchor in enumerate(index_values):
                available = ctx._available.iloc[offset]
                if available < BOUNDS["pre_holdout"][0]:
                    continue
                values240 = volatility[offset - 239:offset + 1] if offset >= 239 else np.array([])
                if len(values240) != 240:
                    coverage_fail.append((key, int(anchor)))
                    continue
                if np.isfinite(values240).all():
                    current = float(values240[-1])
                    rank = (int((values240 < current).sum()) + (int((values240 == current).sum()) + 1) / 2) / 240
                    expected_bin = min(5, max(1, math.ceil(rank * 5)))
                else:
                    expected_bin = -1
                if int(ctx.volbin.iloc[offset]) != expected_bin:
                    bin_fail.append((key, int(anchor), int(ctx.volbin.iloc[offset]), expected_bin))
                recent = any(int(value) != 0 for value in releases[max(0, offset - 9):offset + 1])
                if expected_bin <= 0 or recent:
                    continue
                for fold, (start, end) in BOUNDS.items():
                    if start <= available < end:
                        pool_key = (symbol, minutes, fold, available.strftime("%Y-%m"), expected_bin)
                        pools.setdefault(pool_key, []).append(int(anchor))
            contexts[(symbol, minutes)] = ctx
    check("context_covers_all_control_quintile_prefixes", not coverage_fail)
    check("causal_scalar_quintiles", not bin_fail)
    outcome_lookup = {tuple(row[name] for name in KEY): row for row in outcomes.to_dict("records")}
    old_lookup = decisions.set_index("event_id")
    control_lookup = {key: group for key, group in controls.groupby(KEY, sort=False)}
    failures = {name: [] for name in ("frozen_fields", "anchors_modes", "case_labels", "seed_and_controls",
        "control_labels", "control_identity", "matching_and_missing", "same_exit", "immediate_same_path")}
    def note(name, identity, value):
        if not value: failures[name].append(identity)
    seen_control_keys = set()
    for row in outcomes.itertuples(index=False):
        identity = (row.event_id, row.anchor_kind, int(row.horizon))
        old = old_lookup.loc[row.event_id]
        note("frozen_fields", identity, all(same(getattr(row, name), old[name]) for name in decisions.columns if name != "event_id"))
        mode = ("immediate" if old.delay_bars == 0 else "delayed") if old.status == "confirmed" else (
            "censored" if old.status == "censored_end" else "unconfirmed")
        anchor = int(old.signal_i if row.anchor_kind == "imacd" else old.confirmation_i)
        ctx = contexts[(row.symbol, int(row.timeframe_min))]
        start, end = BOUNDS[row.fold]
        available = ctx.at[anchor, "_available"]
        note("anchors_modes", identity, (
            row.mode == mode and row.anchor_i == anchor and start <= available < end
            and ctx.at[int(old.signal_i), "_open_time"] == pd.Timestamp(old.signal_open_at)
            and int(ctx.at[int(old.signal_i), "release_side"]) == row.side
            and (row.anchor_kind != "confirmation" or available == pd.Timestamp(old.confirmation_available_at))
        ))
        expected = scalar_observe(ctx, anchor, row.side, row.horizon, end, row.timeframe_min)
        errors = observation_errors(row, expected)
        if errors: failures["case_labels"].append(dict(identity=identity, fields=errors))
        pool = pools.get((row.symbol, int(row.timeframe_min), row.fold,
                          available.strftime("%Y-%m"), int(ctx.at[anchor, "volbin"])), [])
        seed = int.from_bytes(hashlib.sha256(f"{SEED}|{row.event_id}|{row.anchor_kind}".encode()).digest()[:8], "big")
        chosen = [int(value) for value in np.random.default_rng(seed).choice(pool, size=min(3, len(pool)), replace=False)] if pool else []
        actual_chosen = control_ids(row.control_anchor_ids)
        note("seed_and_controls", identity, actual_chosen == chosen and row.control_selected_n == len(chosen)
             and len(chosen) == len(set(chosen)))
        group = control_lookup.get(identity, controls.iloc[:0])
        expected_control_ids = chosen if expected["complete"] else []
        note("control_identity", identity, group.control_anchor_i.tolist() == expected_control_ids)
        net_values = []
        for control in group.itertuples(index=False):
            control_identity = (*identity, int(control.control_anchor_i))
            seen_control_keys.add(control_identity)
            c = int(control.control_anchor_i)
            note("control_identity", control_identity, (
                control.symbol == row.symbol and control.timeframe_min == row.timeframe_min
                and control.fold == row.fold and control.side == row.side and control.mode == row.mode
                and control.case_anchor_i == anchor and control.anchor_i == c
                and control.volbin == int(ctx.at[c, "volbin"])
                and ctx.at[c, "_available"].strftime("%Y-%m") == available.strftime("%Y-%m")
            ))
            co = scalar_observe(ctx, c, row.side, row.horizon, end, row.timeframe_min)
            errors = observation_errors(control, co)
            if errors: failures["control_labels"].append(dict(identity=control_identity, fields=errors))
            if co["complete"]: net_values.append(co["net_bp"])
        expected_mean = math.fsum(net_values) / 3 if len(net_values) == 3 else None
        excess = expected["net_bp"] - expected_mean if expected_mean is not None else None
        note("matching_and_missing", identity, row.control_n == len(net_values)
             and same(row.control_mean_net_bp, expected_mean) and same(row.matched_excess_bp, excess))
        same_exit = None
        if row.anchor_kind == "confirmation" and expected["complete"]:
            original_entry = float(ctx.at[int(old.signal_i) + 1, "open"])
            same_exit = row.side * (expected["exit_price"] / original_entry - 1) * 10000
            displacement = row.side * (expected["entry_price"] / original_entry - 1) * 10000
            note("same_exit", identity, same(
                same_exit - expected["gross_bp"], displacement * expected["exit_price"] / expected["entry_price"]))
        note("same_exit", identity, same(row.same_exit_imacd_gross_bp, same_exit))
        if row.anchor_kind == "confirmation" and mode == "immediate":
            baseline = outcome_lookup[(row.event_id, "imacd", int(row.horizon))]
            note("immediate_same_path", identity, all(same(getattr(row, field), baseline[field]) for field in OBS_FIELDS))
    check("all_controls_link_to_expected_case_horizon", len(seen_control_keys) == len(controls))
    for name, failed in failures.items(): check(name, not failed)
    for key, group in outcomes.groupby(["event_id", "anchor_kind"], sort=False):
        if set(group.horizon) != set(HORIZONS) or len({tuple(control_ids(v)) for v in group.control_anchor_ids}) != 1:
            failures["seed_and_controls"].append(dict(identity=key, problem="control draw changed across horizons"))
    check("controls_frozen_across_horizons", not failures["seed_and_controls"])
    coverage = {(row["symbol"], row["timeframe_min"], row["fold"]): row for row in summary["coverage"]}
    check("all_group_coverage_including_empty", len(summary["coverage"]) == len(coverage) == 216 and all(
        coverage[(symbol, minutes, fold)]["events"] == int((decisions.symbol.eq(symbol)
            & decisions.timeframe_min.eq(minutes) & decisions.fold.eq(fold)).sum())
        and coverage[(symbol, minutes, fold)]["bars"] == prior["inputs"][f"{symbol}_{minutes}_holdout_review"]["parsed_rows"]
        for symbol in symbols for minutes in (60, 240) for fold in BOUNDS))
    observed_rows = {tuple(row[name] for name in GROUP_KEY): row for row in summary["summary_rows"]}
    computed_rows, primary = {}, {}
    for identity, group in outcomes.groupby(GROUP_KEY, sort=True):
        expected = scalar_summary(group)
        computed_rows[identity] = expected
        actual = observed_rows.get(identity, {})
        check("summary/" + "/".join(map(str, identity)), all(name in actual and same(actual[name], value) for name, value in expected.items()))
        if identity[0] == "holdout_review" and identity[2] == "confirmation" and identity[4] == 24:
            matched = group[group.complete.eq(True) & group.control_n.eq(3)]
            null = scalar_weekly_null(matched.matched_excess_bp, matched.entry_at)
            primary[identity] = null
            check("scalar_week_p/" + str(identity), same(actual.get("p"), null["p"]) and actual.get("blocks") == null["blocks"])
        else:
            check("descriptive_only/" + str(identity), pd.isna(actual.get("p")) and pd.isna(actual.get("p_holm")))
    check("summary_group_identity_coverage", len(summary["summary_rows"]) == len(observed_rows)
          and set(observed_rows) == set(computed_rows))
    known = sorted(((key, value) for key, value in primary.items() if value["p"] is not None), key=lambda x: x[1]["p"])
    running = 0.0
    for rank, (identity, null) in enumerate(known):
        running = max(running, min(1.0, (4 - rank) * null["p"]))
        null["p_holm"] = running
        check("holm4/" + str(identity), same(observed_rows[identity]["p_holm"], running))
    check("only_predeclared_primary_groups", all(
        key[0] == "holdout_review" and key[1] in (60, 240) and key[2] == "confirmation"
        and key[3] in ("immediate", "delayed") and key[4] == 24 for key in primary))
    reuse = controls.groupby(["symbol", "timeframe_min", "control_anchor_i"]).size()
    check("control_reuse_accounting", summary["control_reuse"]["unique_symbol_tf_anchors"] == len(reuse)
          and summary["control_reuse"]["anchors_used_more_than_once"] == int(reuse.gt(1).sum()))
    immediate_comparison = []
    for fold in BOUNDS:
        for minutes in (60, 240):
            before = outcomes[outcomes.fold.eq(fold) & outcomes.timeframe_min.eq(minutes)
                              & outcomes["mode"].eq("immediate") & outcomes.horizon.eq(24) & outcomes.anchor_kind.eq("imacd")]
            after = outcomes[outcomes.fold.eq(fold) & outcomes.timeframe_min.eq(minutes)
                             & outcomes["mode"].eq("immediate") & outcomes.horizon.eq(24) & outcomes.anchor_kind.eq("confirmation")]
            joined = before.merge(after, on="event_id", suffixes=("_imacd", "_confirmation"))
            immediate_comparison.append(dict(fold=fold, timeframe_min=minutes, same_anchor_cases=len(joined),
                different_control_draws=int(joined.control_anchor_ids_imacd.ne(joined.control_anchor_ids_confirmation).sum()),
                imacd=scalar_summary(before), confirmation=scalar_summary(after)))
    return dict(all_passed=all(checks.values()), n_checks=len(checks), checks=checks,
                failed_checks=[name for name, value in checks.items() if not value],
                outcomes_checked=len(outcomes), controls_checked=len(controls), contexts_checked=len(contexts),
                label_failures={name: value for name, value in failures.items() if value},
                quintile_failures=bin_fail, context_prefix_failures=coverage_fail,
                scalar_primary_tests=[dict(fold=key[0], timeframe_min=int(key[1]), anchor_kind=key[2], mode=key[3], horizon=int(key[4]), **value)
                                      for key, value in primary.items()],
                immediate_anchor_comparison=immediate_comparison,
                summary_sha256=digest(results_dir / "summary.json"),
                limitations=[
                    "No raw market file, model or original indicator calculation was re-read/recomputed; saved context features are the audit source.",
                    "Scalar math.fsum sign enumeration avoids NumPy matrix multiplication; it does not repair cross-week dependence or establish alpha.",
                    "Immediate imacd/confirmation case paths are identical, but anchor_kind changes control seeds. Different control draws and matching subsets are sampling variability, not a waiting effect or independent model trials.",
                    "Final unconfirmed modes are post-hoc classifications; censored detector states remain separate and no failed path is removed after confirmation.",
                    "MFE/MAE and fixed-horizon net direction changes are path diagnostics and static cost sensitivity, not realized fills, account drawdown or portfolio PnL.",
                ])


def main():
    source = Path(__file__).resolve()
    relative = str(source.relative_to(ROOT))
    subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative],
                   cwd=ROOT, check=True, capture_output=True)
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", relative],
                   cwd=ROOT, check=True, capture_output=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    review = verify_saved()
    review.update(source_commit=commit, source_sha256=source_sha)
    path = EXP / "results/independent_review.json"
    path.write_text(json.dumps(review, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps(dict(path=str(path), n_checks=review["n_checks"], all_passed=review["all_passed"], failed_checks=review["failed_checks"])))
    if not review["all_passed"]: raise SystemExit(1)


if __name__ == "__main__":
    main()
