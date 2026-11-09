"""Run the preregistered repeated matched-placebo controls for the Binance FVG filter.

The candidate labels, original score, Binance native exit paths, delivery rules,
NAV/10 admission order, and hourly 20% name cap are reused from the frozen
first-touch FVG cap-filter study.  Only the sampled 12H event identities vary.
Each draw matches the outside-gap cohort exactly within entry timestamp, side,
and availability of the frozen score.  Seed 1029 is read from the completed
cap-filter artifact and is never simulated again.
"""
from __future__ import annotations

import gc
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_fvg_cap_filter as previous


wallet = previous.wallet
admin = wallet.admin
archive = admin.archive
EXP = previous.EXP.parent / "exp-cloud-close-fvg-random-controls-20261109-v1"
CONFIG_PATH = EXP / "config.json"
PRIOR_EXP = previous.EXP
PERIODS = ("all", "dev", "2025", "2026")
MATCH_KEYS = ("entry_ms", "side", "has_score")
METRIC_FIELDS = (
    "final_usdt", "multiple", "dd_mark_pct", "dd_bound_pct", "trades",
    "win_pct", "payoff", "payoff_usdt", "median_net_pct",
)


def _read_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    seeds = config["randomization"]["seeds"]
    if seeds != list(range(1029, 1068)):
        raise ValueError("Frozen randomization must contain seeds 1029 through 1067")
    if config["randomization"]["new_seeds"] != list(range(1030, 1068)):
        raise ValueError("Only seeds 1030 through 1067 may be newly simulated")
    if config["randomization"]["reused_seed"] != 1029:
        raise ValueError("Seed 1029 must reuse the completed first placebo")
    return config


def _read_candidates() -> tuple[pd.DataFrame, pd.Series, dict[str, Any]]:
    """Load the already frozen known decisions and score; do not reclassify FVGs."""
    source_path = wallet.rank.scores.EXP / "candidate_order.csv.gz"
    source = pd.read_csv(source_path, usecols=["event_id", "entry_ms", "score"])
    if source.event_id.duplicated().any():
        raise ValueError("Original scored candidate file has duplicate event IDs")
    scores = source.set_index("event_id").score

    observed_path = PRIOR_EXP / "observed_candidates.csv.gz"
    observed = pd.read_csv(
        observed_path,
        dtype={"event_id": str, "outside_gap": "boolean", "has_score": "boolean", "placebo": "boolean"},
    ).set_index("event_id", verify_integrity=True)
    for name in ("outside_gap", "has_score", "placebo"):
        if observed[name].isna().any():
            raise ValueError(f"Frozen observed candidates contain missing {name}")
        observed[name] = observed[name].astype(bool)

    if set(observed.index) - set(scores.index):
        raise ValueError("A known FVG candidate lacks its original frozen score row")
    score_present = scores.reindex(observed.index).notna()
    if not score_present.equals(observed.has_score):
        raise ValueError("Frozen score-availability strata differ from original score file")
    if not observed.outside_gap.isin([True, False]).all():
        raise ValueError("Unknown FVG labels entered the known candidate panel")

    prior = json.loads((PRIOR_EXP / "summary.json").read_text(encoding="utf-8"))
    expected = {
        "known_candidates": int(prior["covered_candidates"]),
        "outside_gap_candidates": int(prior["confirmed_candidates"]),
        "unknown_candidates": int(prior["unknown_candidates"]),
        "original_candidates": int(prior["original_candidates"]),
    }
    if (len(observed), int(observed.outside_gap.sum()), len(source)) != (
        expected["known_candidates"], expected["outside_gap_candidates"], expected["original_candidates"]
    ):
        raise ValueError("Known/outside/unknown source counts differ from frozen cap-filter receipt")
    if expected["known_candidates"] != 22_890 or expected["outside_gap_candidates"] != 19_176:
        raise ValueError("This study is frozen to 22,890 known and 19,176 outside-gap events")

    reference = prior["results"]["confirmed"]["metrics"]
    if not np.isclose(reference["all"]["final_usdt"], 11_620.786336040052, rtol=0, atol=1e-8):
        raise ValueError("Original outside-gap target no longer matches the preregistered 11,620.79U")
    meta = {
        **expected,
        "score_rows": len(source),
        "scored_known_candidates": int(observed.has_score.sum()),
        "unscored_known_candidates": int((~observed.has_score).sum()),
        "movable_strata": int(
            observed.groupby(list(MATCH_KEYS), sort=True).outside_gap.agg(["sum", "size"])
            .pipe(lambda x: ((x["sum"] > 0) & (x["sum"] < x["size"])).sum())
        ),
        "reference_outside_metrics": reference,
        "seed_1029_metrics": prior["results"]["placebo"]["metrics"],
    }
    return observed, scores, meta


def _sample_matched(observed: pd.DataFrame, seed: int) -> pd.Series:
    """Draw the original outside count in every exact 12H/side/score stratum."""
    sample = pd.Series(False, index=observed.index, dtype=bool, name="placebo")
    rng = np.random.default_rng(seed)
    for _, part in observed.sort_index().groupby(list(MATCH_KEYS), sort=True):
        count = int(part.outside_gap.sum())
        if count:
            sample.loc[rng.choice(part.index.to_numpy(), size=count, replace=False)] = True
    return sample


def _check_matched(observed: pd.DataFrame, sample: pd.Series, seed: int) -> None:
    if not sample.index.equals(observed.index) or int(sample.sum()) != int(observed.outside_gap.sum()):
        raise ValueError(f"Seed {seed} changed the total matched cohort size")
    target = observed.groupby(list(MATCH_KEYS), sort=True).outside_gap.sum().astype("int64")
    actual = observed.assign(_matched_sample=sample).groupby(list(MATCH_KEYS), sort=True)[
        "_matched_sample"
    ].sum().astype("int64")
    if not target.equals(actual):
        raise ValueError(f"Seed {seed} changed one or more exact matching strata")


def _settlement_inputs() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, float]], dict[str, str]]:
    """Read only the already archived official delivery specs and 1m index marks."""
    sources = [
        (admin.EXP, "settlements"),
        (wallet.rank.scores.oi.EXP, "other_funded_settlements"),
        (wallet.rank.scores.EXP, "additional_settlements"),
        (wallet.rank.EXP, "additional_settlements"),
        (PRIOR_EXP, "additional_settlements"),
        (wallet.vwap.EXP, "additional_settlements"),
        (wallet.timing.EXP, "additional_settlements"),
        (previous.fvg.EXP, "additional_settlements"),
    ]
    specs: dict[str, dict[str, Any]] = {}
    marks: dict[str, dict[str, float]] = {}
    mark_sources: dict[str, str] = {}
    for directory, key in sources:
        config_path = Path(directory) / "config.json"
        if not config_path.is_file():
            raise FileNotFoundError(f"Registered settlement configuration missing: {config_path}")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for symbol, details in config.get(key, {}).items():
            if symbol in specs:
                old = specs[symbol]
                if (old.get("utc"), old.get("minutes")) != (details.get("utc"), details.get("minutes")):
                    raise ValueError(f"Conflicting official delivery definitions for {symbol}")
            specs[symbol] = details

        mark_path = Path(directory) / "settlement_index_1m.csv"
        if not mark_path.is_file():
            if config.get(key):
                raise FileNotFoundError(f"Official settlement marks missing: {mark_path}")
            continue
        frame = pd.read_csv(mark_path)
        required = {"symbol", "low", "close", "high"}
        if not required.issubset(frame.columns):
            raise ValueError(f"Settlement mark schema changed in {mark_path}")
        loaded = frame.groupby("symbol")[["low", "close", "high"]].mean().to_dict("index")
        for symbol, prices in loaded.items():
            marks[symbol] = {key: float(value) for key, value in prices.items()}
            mark_sources[symbol] = str(mark_path)

    for symbol in specs:
        if symbol not in marks:
            raise ValueError(f"Configured official delivery has no saved 1m index mark: {symbol}")
    return specs, marks, mark_sources


def _load_native_rows(specs: dict[str, dict[str, Any]], scores: pd.Series,
                      observed: pd.DataFrame, extra: set[str]) -> pd.DataFrame:
    paths = archive.pool(extra)
    with archive.market_quotes(extra):
        _, selected = admin.study.tenth.trend.inputs("Binance", paths[~paths.is_control])
        rows = admin.adjusted_legs(selected, extra, specs)
    if rows.is_control.any():
        raise ValueError("Random controls must be matched random confirmation events, not later native controls")
    if rows.duplicated(["event_id", "tp_r"]).any() or not rows.groupby("event_id").size().eq(2).all():
        raise ValueError("Each scored event must retain exactly the native runner and 2R paths")
    candidates = rows.loc[rows.tp_r.eq(0)].set_index("event_id", verify_integrity=True)
    if set(candidates.index) != set(scores.index):
        raise ValueError("The capped-filter candidate identity set changed")
    if set(observed.index) - set(candidates.index):
        raise ValueError("A known frozen label lacks a native Binance path")
    return rows


def _period_rows(seed: int, metrics: dict[str, dict[str, float]],
                 sampled: int, funded: int, status: str) -> list[dict[str, Any]]:
    if set(metrics) != set(PERIODS):
        raise ValueError(f"Seed {seed} did not return the complete four-period account metrics")
    result = []
    for period in PERIODS:
        values = metrics[period]
        result.append({
            "seed": seed,
            "status": status,
            "period": period,
            "sampled_events": sampled,
            "funded_events": funded,
            **{field: float(values[field]) if field != "trades" else int(values[field])
               for field in METRIC_FIELDS},
        })
    return result


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False, compression="gzip" if path.name.endswith(".gz") else None)
    os.replace(temporary, path)


def _atomic_json(value: dict[str, Any], path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_checkpoint() -> pd.DataFrame:
    path = EXP / "seed_metrics.csv"
    if not path.is_file():
        return pd.DataFrame()
    existing = pd.read_csv(path)
    if existing.duplicated(["seed", "period"]).any():
        raise ValueError("Seed metric checkpoint has duplicate seed-period rows")
    valid = set(range(1029, 1068))
    if not set(existing.seed.astype(int)).issubset(valid):
        raise ValueError("Seed metric checkpoint contains a non-preregistered seed")
    for _, group in existing.groupby("seed"):
        if set(group.period) != set(PERIODS):
            raise ValueError("Partial seed metrics cannot be resumed safely")
    return existing


def _save_checkpoint(existing: pd.DataFrame, rows: list[dict[str, Any]]) -> pd.DataFrame:
    added = pd.DataFrame(rows)
    combined = pd.concat([existing, added], ignore_index=True) if len(existing) else added
    if combined.duplicated(["seed", "period"]).any():
        raise ValueError("Seed metric checkpoint would repeat an existing seed")
    combined = combined.sort_values(["seed", "period"], key=lambda s: s.map(
        {name: i for i, name in enumerate(PERIODS)} if s.name == "period" else s
    )).reset_index(drop=True)
    _atomic_csv(combined, EXP / "seed_metrics.csv")
    return combined


def _distribution(metrics: pd.DataFrame, reference: dict[str, dict[str, float]]) -> pd.DataFrame:
    rows = []
    for period in PERIODS:
        sample = metrics[metrics.period.eq(period)]
        if sample.seed.nunique() != 39:
            raise ValueError(f"Distribution is missing one or more seeds for {period}")
        for field in ("final_usdt", "multiple", "dd_bound_pct", "dd_mark_pct", "trades",
                      "win_pct", "payoff_usdt"):
            values = sample[field].astype(float)
            target = float(reference[period][field])
            greater = int((values > target).sum())
            greater_equal = int((values >= target).sum())
            rows.append({
                "period": period,
                "metric": field,
                "outside_gap_reference": target,
                "control_mean": float(values.mean()),
                "control_median": float(values.median()),
                "control_min": float(values.min()),
                "control_p05": float(values.quantile(.05)),
                "control_p95": float(values.quantile(.95)),
                "control_max": float(values.max()),
                "controls_above_reference": greater,
                "controls_at_or_above_reference": greater_equal,
                "reference_rank_desc": 1 + greater,
                "reference_percentile_ascending": 100 * float((values <= target).sum()) / len(values),
                "empirical_upper_tail_p": (1 + greater_equal) / (len(values) + 1),
            })
    return pd.DataFrame(rows)


def main() -> None:
    if (EXP / "summary.json").exists():
        raise FileExistsError(f"Completed repeated FVG random controls already exist: {EXP}")
    EXP.mkdir(parents=True, exist_ok=True)
    config = _read_config()
    observed, scores, counts = _read_candidates()
    expected_seed = _sample_matched(observed, 1029)
    if not expected_seed.equals(observed.placebo.rename("placebo")):
        raise ValueError("Recreated seed 1029 differs from the already completed first placebo")
    for seed in config["randomization"]["seeds"]:
        _check_matched(observed, expected_seed if seed == 1029 else _sample_matched(observed, seed), seed)

    specs, marks, mark_sources = _settlement_inputs()
    extra = set(json.loads((archive.EXP / "summary.json").read_text(encoding="utf-8"))["archived_additions"])
    extra.update(json.loads((admin.study.EXP / "summary.json").read_text(encoding="utf-8"))["new_archive_cohorts"]["priced"])
    rows = _load_native_rows(specs, scores, observed, extra)
    score_reference = scores

    sample_rosters: list[pd.DataFrame] = []
    funded_rosters: list[pd.DataFrame] = []
    delivery_rosters: list[pd.DataFrame] = []
    admitted_by_seed: dict[int, pd.DataFrame] = {}
    previous_funded = pd.read_csv(PRIOR_EXP / "funded_placebo.csv.gz", usecols=["event_id"])
    previous_funded_ids = set(previous_funded.event_id.astype(str))

    for seed in config["randomization"]["seeds"]:
        sampled = observed.placebo if seed == 1029 else _sample_matched(observed, seed)
        _check_matched(observed, sampled, seed)
        sample_ids = sampled.index[sampled].astype(str)
        sample_rosters.append(observed.loc[sample_ids, ["entry_ms", "side", "has_score"]]
                              .reset_index().assign(seed=seed))
        seed_rows = rows[rows.event_id.astype(str).isin(set(sample_ids))].copy()
        ranked, decisions = wallet.rank.scores.rerank(seed_rows, score_reference)
        if set(decisions.event_id.astype(str)) != set(sample_ids) or decisions.event_id.duplicated().any():
            raise ValueError(f"Seed {seed} ranking lost or duplicated a sampled candidate")
        paid = admin.admitted(ranked)
        funded_ids = set(paid.event_id.astype(str))
        if not funded_ids.issubset(set(sample_ids)) or paid.duplicated(["event_id", "tp_r"]).any():
            raise ValueError(f"Seed {seed} admission changed candidate identities")
        if not paid.groupby("event_id").size().eq(2).all():
            raise ValueError(f"Seed {seed} admission dropped one of the two native paths")
        if seed == 1029 and funded_ids != previous_funded_ids:
            raise ValueError("Reconstructed seed 1029 admission differs from completed funded placebo")
        admitted_by_seed[seed] = paid
        funded_rosters.append(paid.loc[paid.tp_r.eq(0),
            ["event_id", "parent_id", "symbol", "side", "entry_ms"]].assign(seed=seed))
        deliveries = paid.loc[paid.delivery,
            ["event_id", "symbol", "side", "tp_r", "exit_bar_ms", "exit_phase",
             "exit_fee_rate", "delivery_ambiguous"]].copy()
        if len(deliveries):
            delivery_rosters.append(deliveries.assign(seed=seed))

    sample_table = pd.concat(sample_rosters, ignore_index=True)
    funded_table = pd.concat(funded_rosters, ignore_index=True)
    deliveries_table = (pd.concat(delivery_rosters, ignore_index=True) if delivery_rosters
                        else pd.DataFrame(columns=["event_id", "symbol", "side", "tp_r", "exit_bar_ms",
                                                   "exit_phase", "exit_fee_rate", "delivery_ambiguous", "seed"]))
    _atomic_csv(sample_table, EXP / "sampled_rosters.csv.gz")
    _atomic_csv(funded_table, EXP / "funded_rosters.csv.gz")
    _atomic_csv(deliveries_table, EXP / "selected_deliveries.csv.gz")

    missing_marks = sorted(set(deliveries_table.symbol.astype(str) + "USDT") - set(marks))
    ambiguous = deliveries_table[deliveries_table.delivery_ambiguous.astype(bool)]
    if missing_marks or len(ambiguous):
        raise ValueError(
            "All-seed delivery preflight failed before any new VectorBT account: "
            f"missing_official_index_marks={missing_marks}; "
            f"ambiguous_midhour_delivery_legs={len(ambiguous)}"
        )

    checkpoint = _read_checkpoint()
    seed1029_rows = _period_rows(
        1029, counts["seed_1029_metrics"], int(observed.outside_gap.sum()), len(previous_funded_ids),
        "reused_existing_seed_1029",
    )
    has_seed_1029 = bool(len(checkpoint) and checkpoint.seed.astype(int).eq(1029).any())
    if not has_seed_1029:
        checkpoint = _save_checkpoint(checkpoint, seed1029_rows)
    else:
        cached = checkpoint.loc[checkpoint.seed.astype(int).eq(1029)].sort_values("period")
        expected = pd.DataFrame(seed1029_rows).sort_values("period")
        if set(cached.period) != set(PERIODS):
            raise ValueError("Existing seed 1029 checkpoint is incomplete")
        for field in METRIC_FIELDS:
            if not np.allclose(cached[field].to_numpy(dtype=float), expected[field].to_numpy(dtype=float),
                               rtol=0, atol=1e-9):
                raise ValueError("Seed 1029 checkpoint no longer matches its frozen prior result")

    complete = set(checkpoint.seed.astype(int)) if len(checkpoint) else set()
    for seed in config["randomization"]["new_seeds"]:
        if seed in complete:
            continue
        paid = admitted_by_seed[seed]
        with archive.market_quotes(extra):
            ready = admin.resolve_settlements(paid.copy(), marks, "proxy")
            data = archive.native.prepare(ready)
            metrics, positions, equity, trims = wallet.hourly.tested.trim_account(
                data, archive.native.quotes, .9, .20, review_hours=1
            )
        if set(positions.index.astype(str)) != set(paid.event_id.astype(str).unique()):
            raise ValueError(f"Seed {seed} VectorBT position roster differs from admitted parents")
        if not np.isclose(1000 + positions.cash_pnl.sum(), equity.nav.iloc[-1], rtol=1e-8, atol=.001):
            raise ValueError(f"Seed {seed} funded positions fail VectorBT NAV reconciliation")
        period_rows = _period_rows(
            seed, metrics, int(observed.outside_gap.sum()), len(positions), "vectorbt_from_order_func"
        )
        checkpoint = _save_checkpoint(checkpoint, period_rows)
        complete.add(seed)
        del ready, data, metrics, positions, equity, trims
        gc.collect()

    reference = counts["reference_outside_metrics"]
    distribution = _distribution(checkpoint, reference)
    _atomic_csv(distribution, EXP / "distribution_ranks.csv")
    settlement_audit = {
        "delivery_specs": specs,
        "selected_delivery_legs": int(len(deliveries_table)),
        "selected_delivery_legs_by_symbol": deliveries_table.groupby("symbol").size().astype(int).to_dict()
        if len(deliveries_table) else {},
        "official_mark_source_by_symbol": {
            ticker.removesuffix("USDT"): mark_sources[ticker]
            for ticker in sorted(set(deliveries_table.symbol.astype(str) + "USDT")) if ticker in mark_sources
        },
        "missing_official_index_marks": missing_marks,
        "ambiguous_midhour_delivery_legs": int(len(ambiguous)),
    }
    _atomic_json(settlement_audit, EXP / "settlement_audit.json")
    summary = {
        "candidate_counts": counts,
        "randomization": {
            "seeds": config["randomization"]["seeds"],
            "new_vectorbt_seeds": config["randomization"]["new_seeds"],
            "reused_seed": 1029,
            "rng": config["randomization"]["rng"],
            "matching_keys": list(MATCH_KEYS),
            "sampled_events_per_seed": int(observed.outside_gap.sum()),
            "strata_match_verified_for_all_seeds": True,
        },
        "wallet": config["wallet"],
        "reference_outside_gap": reference,
        "seed_1029_reused_placebo": counts["seed_1029_metrics"],
        "distribution_file": "distribution_ranks.csv",
        "seed_metrics_file": "seed_metrics.csv",
        "settlement_audit_file": "settlement_audit.json",
    }
    _atomic_json(summary, EXP / "summary.json")
    print(json.dumps(summary, indent=2, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
