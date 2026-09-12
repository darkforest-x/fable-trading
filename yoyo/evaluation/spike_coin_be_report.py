#!/usr/bin/env python3
"""Describe pre-existing SPIKE exit ledgers by market without rerunning a strategy.

The module accepts only the receipt-bound output of the 2026-09-12 exit-policy
study.  It slices fixed, owner-named coins into independent market accounts and
same-entry baseline/break-even comparisons.  It never reads current prices,
changes thresholds, or builds a pooled portfolio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXP = ROOT / "experiments/active/exp-spike-exit-policy-20260912-v1"
FIXED_ASSETS = ("BTC", "ETH", "SOL", "ZEC", "PEPE", "WIF", "TAO", "SOPH", "USELESS", "BICO", "DOGE", "SUI")
ASSET_ALIASES = {"PEPE": ("PEPE", "1000PEPE")}
COHORTS = ("v1_common_long", "v6_both", "v7_both")
POLICIES = ("baseline", "be1_price", "be1_cost")


def sha256(path: Path) -> str:
    """Return a content hash, including for compressed receipt artifacts."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def numeric_cap(value: object) -> float | None:
    """Normalize a mixed CSV cap field without accepting stress rows as 1x."""
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def verify_inputs(source: Path = SOURCE_EXP) -> dict[str, object]:
    """Verify the manifest chain before consuming any existing outcome rows."""
    config = json.loads((source / "config.json").read_text())
    engine = json.loads((source / "engine_results/full_v1/engine_manifest.json").read_text())
    post = json.loads((source / "post_full_v1/post_manifest.json").read_text())
    raw_manifest = Path(config["raw"]) / "manifest.json"
    if sha256(raw_manifest) != config["raw_manifest_sha256"]:
        raise ValueError("raw manifest SHA-256 mismatch")
    if sha256(source / "config.json") != engine["config_sha256"] or sha256(source / "config.json") != post["config_sha256"]:
        raise ValueError("config SHA-256 mismatch")
    if engine["upstream_manifest_sha256"] != config["raw_manifest_sha256"]:
        raise ValueError("engine points to a different frozen replay")
    if not (engine.get("complete") and post.get("complete")):
        raise ValueError("source manifest is incomplete")
    for name, expected in post["outputs"].items():
        if sha256(source / "post_full_v1" / name) != expected:
            raise ValueError(f"post output SHA-256 mismatch: {name}")
    summary = pd.read_csv(source / "engine_results/full_v1/engine_stream_summary.csv")
    if len(summary) != config["expected_streams"] or len(summary) != engine["completed_streams"]:
        raise ValueError("stream count does not match manifest")
    return {"config": config, "engine": engine, "post": post, "stream_summary": summary}


def event_metrics(rows: pd.DataFrame) -> pd.DataFrame:
    """Add defined closed-event metrics while retaining zero-denominator cells."""
    result = rows.copy()
    result["net_win_rate"] = result["wins"].div(result["closed"]).where(result["closed"].gt(0))
    result["net_pf"] = result["positive_return_sum"].div(result["negative_return_sum"].abs()).where(result["negative_return_sum"].ne(0))
    result["mean_net_r"] = result["net_r_sum"].div(result["closed"]).where(result["closed"].gt(0))
    return result


def _read_stream_trades(engine: Path, keys: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades, fills = [], []
    for key in sorted(keys):
        base = engine / "streams" / key
        completion = json.loads(base.with_suffix(".completion.json").read_text())
        expected = completion.get("output_sha256", {})
        for suffix in (".trades.csv.gz", ".fills.csv.gz"):
            path = base.with_suffix(suffix)
            if sha256(path) != expected.get(path.name):
                raise ValueError(f"per-stream output SHA-256 mismatch: {path.name}")
        trade = pd.read_csv(base.with_suffix(".trades.csv.gz"))
        trade = trade.loc[trade.cohort.isin(COHORTS) & trade.policy.isin(POLICIES)].copy()
        if len(trade):
            trades.append(trade)
            wanted = set(trade.trade_id)
            fill = pd.read_csv(base.with_suffix(".fills.csv.gz"))
            fills.append(fill.loc[fill.trade_id.isin(wanted)].copy())
    return (pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(),
            pd.concat(fills, ignore_index=True) if fills else pd.DataFrame())


def read_selected_accounts(path: Path, keys: set[str]) -> pd.DataFrame:
    """Filter the 2.5m-row account ledger in chunks to keep research memory bounded."""
    selected = []
    for chunk in pd.read_csv(path, chunksize=100_000):
        chunk["notional_cap_numeric"] = pd.to_numeric(chunk.notional_cap, errors="coerce")
        mask = (chunk.stream_key.isin(keys) & chunk.cohort.isin(COHORTS) & chunk.policy.isin(POLICIES)
                & chunk.risk_fraction.eq(.01) & chunk.notional_cap_numeric.eq(1.0))
        if mask.any(): selected.append(chunk.loc[mask].copy())
    return pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()


def same_entry_pairs(trades: pd.DataFrame) -> pd.DataFrame:
    """Pair policy paths only on their common frozen entry, never by outcome rank."""
    keys = ["stream_key", "venue", "symbol", "asset", "timeframe_min", "cohort", "signal_bar_open", "entry_time", "side"]
    baseline = trades.loc[trades.policy.eq("baseline")].copy()
    baseline = baseline.rename(columns={column: f"baseline_{column}" for column in ["trade_id", "net_r", "net_return", "mfe_r", "exit_time", "exit_price", "exit_reason", "censored"]})
    pieces = []
    for policy in ("be1_price", "be1_cost"):
        other = trades.loc[trades.policy.eq(policy)].copy()
        other = other.rename(columns={column: f"{policy}_{column}" for column in ["trade_id", "net_r", "net_return", "mfe_r", "exit_time", "exit_price", "exit_reason", "censored"]})
        joined = baseline.merge(other, on=keys, how="outer", validate="one_to_one", indicator=True)
        joined.insert(0, "comparison_policy", policy)
        joined["same_entry"] = joined["_merge"].eq("both")
        joined["net_r_delta"] = joined[f"{policy}_net_r"] - joined["baseline_net_r"]
        # Price break-even exits still pay the precommitted round-trip fee.  Keep
        # a strict net-nonloss field separate from the broader loss-reduction
        # fact so a chart caption never calls fee-bearing price BE "net BE".
        joined["be_reduced_loss"] = joined.same_entry & joined.baseline_net_r.lt(0) & joined.net_r_delta.gt(0)
        joined["be_rescued_to_net_nonloss"] = joined.be_reduced_loss & joined[f"{policy}_net_r"].ge(0)
        joined["be_cut_trend"] = joined.same_entry & joined.net_r_delta.lt(0) & joined.baseline_mfe_r.ge(3)
        pieces.append(joined.drop(columns="_merge"))
    return pd.concat(pieces, ignore_index=True)


def choose_cases(pairs: pd.DataFrame, limit: int = 12) -> pd.DataFrame:
    """Choose a balanced, fixed-asset explanatory set rather than a winner reel."""
    scored = pairs.loc[pairs.same_entry & pairs.comparison_policy.eq("be1_price")].copy()
    scored["asset_group"] = scored.asset.replace({alias: group for group, aliases in ASSET_ALIASES.items() for alias in aliases})
    entry = pd.to_datetime(scored.entry_time, utc=True)
    scored["case_period"] = entry.ge("2025-09-10T00:00:00Z").map({True: "validation", False: "development"})
    scored["both_ge_10r"] = scored.baseline_net_r.ge(10) & scored.be1_price_net_r.ge(10)
    scored["be_failure"] = scored.baseline_net_r.lt(0) & scored.net_r_delta.le(0)
    named = ["BTC", "ETH", "SOL", "ZEC", "PEPE", "WIF", "TAO"]
    examples = ["SOPH", "USELESS", "BICO", "DOGE", "SUI"]
    selected: list[pd.Series] = []
    requested_kinds = {
        "BTC": "be_reduced_loss", "ETH": "be_failure", "SOL": "be_reduced_loss", "ZEC": "both_ge_10r",
        "PEPE": "be_failure", "WIF": "be_early_trend_exit", "TAO": "be_early_trend_exit",
        "SOPH": "be_failure", "USELESS": "both_ge_10r", "BICO": "both_ge_10r", "DOGE": "be_early_trend_exit", "SUI": "be_failure",
    }
    predicates = {
        "both_ge_10r": lambda frame: frame.both_ge_10r,
        "be_reduced_loss": lambda frame: frame.be_reduced_loss,
        "be_early_trend_exit": lambda frame: frame.be_cut_trend,
        "be_failure": lambda frame: frame.be_failure,
    }
    used: set[str] = set()
    def add(row: pd.Series, kind: str) -> bool:
        if str(row.baseline_trade_id) in used or len(selected) >= limit: return False
        copy = row.copy(); copy["case_kind"] = kind; selected.append(copy); used.add(str(copy.baseline_trade_id)); return True
    def choose(asset: str, kind: str) -> None:
        candidates = scored.loc[scored.asset_group.eq(asset) & predicates[kind](scored)].copy()
        # 1H validation is preferred only among cases that satisfy the fixed
        # explanatory category; it is not a return-ranking selection rule.
        candidates["period_rank"] = candidates.case_period.ne("validation").astype(int)
        candidates["timeframe_rank"] = candidates.timeframe_min.ne(60).astype(int)
        candidates["magnitude"] = candidates.net_r_delta.abs()
        candidates = candidates.sort_values(["period_rank", "timeframe_rank", "magnitude"], ascending=[True, True, False])
        if len(candidates): add(candidates.iloc[0], kind)
    for asset in named + examples:
        choose(asset, requested_kinds[asset])
    # A missing requested category is explicit, but a matching other asset may
    # fill the display quota only after every fixed asset was attempted.
    for kind, predicate in predicates.items():
        if any(row.case_kind == kind for row in selected): continue
        candidates = scored.loc[predicate(scored)].assign(magnitude=lambda x: x.net_r_delta.abs()).sort_values("magnitude", ascending=False)
        for _, row in candidates.iterrows():
            if add(row, kind): break
    result = pd.DataFrame(selected).reset_index(drop=True)
    if len(result): result["selection_scope"] = "posthoc_explanatory_case_not_success_rate_estimate"
    if len(result): result.insert(0, "case_id", [f"case-{index:02d}" for index in range(1, len(result) + 1)])
    return result


def build(source: Path, output: Path, assets: Iterable[str] = FIXED_ASSETS) -> dict[str, object]:
    """Write only descriptive coin-level slices derived from completed evidence."""
    verified = verify_inputs(source)
    config, summary = verified["config"], verified["stream_summary"]
    selected_assets = tuple(dict.fromkeys(str(asset).upper() for asset in assets))
    actual_assets = tuple(dict.fromkeys(alias for asset in selected_assets for alias in ASSET_ALIASES.get(asset, (asset,))))
    group_map = {alias: group for group, aliases in ASSET_ALIASES.items() for alias in aliases}
    stream_rows = summary.loc[summary.asset.isin(actual_assets)].copy()
    stream_rows["asset_group"] = stream_rows.asset.map(group_map).fillna(stream_rows.asset)
    stream_rows.to_csv(output / "statistics/selected_streams.csv", index=False)
    keys = set(stream_rows.stream_key)
    events = pd.read_csv(source / "post_full_v1/stream_event_rows.csv.gz")
    events = events.loc[events.stream_key.isin(keys) & events.cohort.isin(COHORTS) & events.policy.isin(POLICIES)].copy()
    events["asset_group"] = events.asset.map(group_map).fillna(events.asset)
    events = event_metrics(events)
    events.to_csv(output / "statistics/coin_event_detail_all_venues.csv", index=False)
    okx = events.loc[events.venue.eq("okx")].copy()
    okx.to_csv(output / "statistics/coin_event_detail_okx.csv", index=False)
    accounts = read_selected_accounts(source / "post_full_v1/independent_account_rows.csv.gz", keys)
    accounts["asset_group"] = accounts.asset.map(group_map).fillna(accounts.asset)
    accounts.to_csv(output / "statistics/coin_independent_accounts_all_venues.csv", index=False)
    accounts.loc[accounts.venue.eq("okx")].to_csv(output / "statistics/coin_independent_accounts_okx.csv", index=False)
    trades, fills = _read_stream_trades(source / "engine_results/full_v1", keys)
    trades["asset_group"] = trades.asset.map(group_map).fillna(trades.asset)
    fills["asset_group"] = fills.asset.map(group_map).fillna(fills.asset)
    trades.to_csv(output / "statistics/coin_trade_ledger.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    fills.to_csv(output / "statistics/coin_fill_ledger.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    pairs = same_entry_pairs(trades)
    pairs.to_csv(output / "statistics/same_entry_baseline_be_pairs.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    cases = choose_cases(pairs)
    clean_cases = cases.astype(object).where(pd.notna(cases), None).to_dict("records")
    (output / "case_manifest.json").write_text(json.dumps({"schema_version": 1, "source": str(source), "assets": selected_assets,
        "cases": clean_cases}, indent=2, default=str, allow_nan=False) + "\n")
    missing = sorted(set(selected_assets) - set(stream_rows.asset_group))
    receipt = {"source_config_sha256": sha256(source / "config.json"), "raw_manifest_sha256": config["raw_manifest_sha256"],
               "post_manifest_sha256": sha256(source / "post_full_v1/post_manifest.json"), "requested_assets": selected_assets, "actual_assets": actual_assets,
               "missing_assets": missing, "selected_streams": int(len(stream_rows)), "event_rows": int(len(events)),
               "account_rows": int(len(accounts)), "trade_rows": int(len(trades)), "fill_rows": int(len(fills)),
               "pair_rows": int(len(pairs)), "case_rows": int(len(cases))}
    (output / "data_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_EXP)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", nargs="*", default=list(FIXED_ASSETS))
    args = parser.parse_args()
    (args.output / "statistics").mkdir(parents=True, exist_ok=True)
    print(json.dumps(build(args.source, args.output, args.assets), indent=2))


if __name__ == "__main__":
    main()
