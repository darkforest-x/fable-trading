"""Build a read-only, source-authenticated V1/V8 asset ranking ledger.

This study never replays signals, costs, stops, or exits.  It normalizes the
frozen V1 common-execution long ledger and frozen V8 same-entry ledger, then
summarizes closed trades by asset.  The ranking uses additive trade R rather
than a portfolio/account return: rows for one asset can occur on several
venues and timeframes and therefore are not independent exposures.

The V1 input is specifically ``v1_common_execution_long``.  It is long-only;
V8 is emitted as separate ``v8_long`` and ``v8_both`` report views.  Features
available to a ranking row are frozen trade fields only, including the entry
risk fraction and the corresponding 0.2% round-trip cost expressed in R.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

EXP = Path("experiments/active/exp-spike-v1-v8-asset-ranking-20260914-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
HOLDOUT = EXP / "holdout_receipt.json"
TEST = Path("tests/evaluation/test_spike_v1_v8_asset_ranking.py")
V1_ROOT = Path("experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3")
V8_ROOT = Path("experiments/active/exp-spike-v8-noise-filter-20260913-v1/replay_v1")
V8_EVIDENCE = Path("experiments/active/exp-spike-v8-entry-process-20260913-v1/results/full_v1/same_entry_evidence.csv.gz")
V1_POST_COMMON = Path("experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/post_final_v4/common_execution_trades.csv.gz")
ROUND_TRIP_COST = 0.002
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
END = pd.Timestamp("2026-09-10T00:00:00Z")
NORMALIZED_COLUMNS = [
    "system_source", "stream_key", "venue", "symbol", "asset", "timeframe_min", "side",
    "signal_bar_open", "signal_confirm_time", "entry_time", "exit_time", "exit_reason",
    "net_r", "net_return", "mfe_r", "censored", "closed", "executed", "period",
    "scoring_closed", "risk_fraction_at_entry", "risk_fraction_source", "cost_r",
]


def sha256(path: Path) -> str:
    """Return a SHA-256 for a frozen file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean(paths: Iterable[Path]) -> bool:
    """Require all source definition files to be committed and unmodified."""
    root = Path.cwd().resolve()
    for path in paths:
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return False
        if subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], capture_output=True).returncode:
            return False
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)]).returncode:
            return False
    return True


def _utc(value: pd.Series) -> pd.Series:
    return pd.to_datetime(value, utc=True, errors="coerce")


def _risk_fields(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach exact entry risk and the unchanged 0.2% cost in R where known."""
    blank = pd.Series(np.nan, index=frame.index, dtype=float)
    risk = pd.to_numeric(frame.get("initial_risk_frac", blank), errors="coerce")
    net_r = pd.to_numeric(frame.get("net_r", blank), errors="coerce")
    net_return = pd.to_numeric(frame.get("net_return", blank), errors="coerce")
    source = np.where(risk.gt(0), "frozen_trade_initial_risk_frac", "missing")
    fallback = risk.isna() & net_r.ne(0) & np.isfinite(net_r) & np.isfinite(net_return)
    risk = risk.where(~fallback, net_return / net_r)
    source = np.where(fallback, "derived_net_return_div_net_r", source)
    frame["risk_fraction_at_entry"] = risk.where(risk.gt(0))
    frame["risk_fraction_source"] = source
    frame["cost_r"] = ROUND_TRIP_COST / frame["risk_fraction_at_entry"]
    return frame


def score_flags(frame: pd.DataFrame) -> pd.DataFrame:
    """Assign confirmation-clock periods and non-crossing scoring eligibility."""
    frame = frame.copy()
    frame["signal_confirm_time"] = _utc(frame["signal_confirm_time"])
    frame["exit_time"] = _utc(frame["exit_time"])
    frame["closed"] = frame["closed"].fillna(False).astype(bool)
    frame["period"] = np.where(frame.signal_confirm_time < SPLIT, "development", "validation")
    frame["scoring_closed"] = (
        frame.closed
        & frame.exit_time.notna()
        & np.where(frame.period.eq("development"), frame.exit_time < SPLIT, frame.exit_time < END)
    )
    return frame


def normalize_v1(root: Path = V1_ROOT) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read only frozen ``v1_common_execution_long`` trades and normalize them."""
    rows: list[pd.DataFrame] = []
    audit: list[dict[str, object]] = []
    for path in sorted((root / "streams").glob("*/trades.csv.gz")):
        raw = pd.read_csv(path)
        selected = raw.loc[raw["variant"].eq("v1_common_execution_long")].copy()
        audit.append({"system_source": "v1_common_execution_long", "stream_key": path.parent.name,
                      "path": str(path), "sha256": sha256(path), "expected_sha256": pd.NA,
                      "verification": "not_receipt_bound_in_v1_common_completion",
                      "all_rows": len(raw), "selected_rows": len(selected)})
        if selected.empty:
            continue
        selected["system_source"] = "v1_common_execution_long"
        selected["stream_key"] = path.parent.name
        selected["signal_confirm_time"] = _utc(selected["signal_bar_open"]) + pd.to_timedelta(selected["timeframe_min"], unit="min")
        selected["executed"] = True
        selected["closed"] = ~selected["censored"].fillna(True).astype(bool)
        selected = score_flags(selected)
        rows.append(_risk_fields(selected))
    if not rows:
        raise ValueError("no v1_common_execution_long frozen trades found")
    out = pd.concat(rows, ignore_index=True)
    return out.loc[:, NORMALIZED_COLUMNS], pd.DataFrame(audit)


def verify_v1_post_consistency(root: Path = V1_ROOT, post_path: Path = V1_POST_COMMON) -> dict[str, object]:
    """Compare V1 stream trades to the old post aggregate without treating it as a hash receipt."""
    parts = []
    fields = ["venue", "symbol", "timeframe_min", "signal_bar_open", "side", "entry_price", "initial_stop", "initial_risk", "mfe_r", "exit_time", "exit_price", "exit_reason", "net_return", "net_r", "censored"]
    for path in sorted((root / "streams").glob("*/trades.csv.gz")):
        raw = pd.read_csv(path)
        parts.append(raw.loc[raw.variant.eq("v1_common_execution_long"), fields])
    stream = pd.concat(parts, ignore_index=True)
    post = pd.read_csv(post_path)
    post = post.loc[post.variant.eq("v1_common_execution_long"), fields]
    keys = ["venue", "symbol", "timeframe_min", "signal_bar_open", "side"]
    if stream.duplicated(keys).any() or post.duplicated(keys).any():
        raise ValueError("V1 post consistency identity is nonunique")
    merged = stream.merge(post, on=keys, how="outer", suffixes=("_stream", "_post"), indicator=True, validate="one_to_one")
    numeric = ["entry_price", "initial_stop", "initial_risk", "mfe_r", "exit_price", "net_return", "net_r"]
    mismatch = merged._merge.ne("both")
    for field in numeric:
        left, right = merged[f"{field}_stream"], merged[f"{field}_post"]
        mismatch |= ~np.isclose(left, right, rtol=0, atol=1e-12, equal_nan=True)
    for field in ("exit_time", "exit_reason", "censored"):
        mismatch |= merged[f"{field}_stream"].astype(str).ne(merged[f"{field}_post"].astype(str))
    return {
        "post_path": str(post_path), "post_sha256": sha256(post_path),
        "post_manifest_binds_content_sha256": False,
        "stream_rows": len(stream), "post_rows": len(post), "identity_matched_rows": int(merged._merge.eq("both").sum()),
        "mismatch_rows": int(mismatch.sum()), "passed": bool(not mismatch.any()),
        "identity_keys": keys, "compared_fields": numeric + ["exit_time", "exit_reason", "censored"],
    }


def normalize_v8(evidence_path: Path = V8_EVIDENCE, replay_root: Path = V8_ROOT) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize frozen V8 outcomes and exact V8 entry-risk fields by trade id."""
    evidence = pd.read_csv(evidence_path)
    required = {"trade_id", "executed", "closed", "signal_confirm_time", "period", "net_r", "net_return"}
    if missing := required - set(evidence.columns):
        raise ValueError("V8 evidence missing " + ", ".join(sorted(missing)))
    evidence["executed"] = evidence.executed.fillna(False).astype(bool)
    evidence["closed"] = evidence.closed.fillna(False).astype(bool)
    events = evidence.loc[evidence.executed].copy()
    if events.trade_id.isna().any() or events.trade_id.duplicated().any():
        raise ValueError("executed V8 events require unique non-null trade_id")
    details: list[pd.DataFrame] = []
    audit: list[dict[str, object]] = [{"system_source": "v8", "stream_key": "same_entry_evidence", "path": str(evidence_path), "sha256": sha256(evidence_path), "expected_sha256": pd.NA, "verification": "top_level_config_sha256", "all_rows": len(evidence), "selected_rows": len(events)}]
    for path in sorted((replay_root / "streams").glob("*.trades.csv.gz")):
        stream_key = path.name.removesuffix(".trades.csv.gz")
        receipt = replay_root / "streams" / f"{stream_key}.json"
        if not receipt.exists():
            raise ValueError(f"V8 stream receipt missing: {receipt}")
        expected = json.loads(receipt.read_text()).get("files", {}).get(path.name)
        actual = sha256(path)
        if not expected or actual != expected:
            raise ValueError(f"V8 stream trade hash mismatch: {path}")
        raw = pd.read_csv(path)
        selected = raw.loc[raw.arm.eq("v8"), ["trade_id", "initial_risk_frac"]].copy()
        audit.append({"system_source": "v8", "stream_key": stream_key, "path": str(path), "sha256": actual,
                      "expected_sha256": expected, "verification": "stream_receipt_files",
                      "all_rows": len(raw), "selected_rows": len(selected)})
        details.append(selected)
    risks = pd.concat(details, ignore_index=True)
    if risks.trade_id.duplicated().any():
        raise ValueError("V8 replay V8-arm trade_id must be unique")
    events = events.merge(risks, on="trade_id", how="left", validate="one_to_one")
    if events.initial_risk_frac.isna().any():
        raise ValueError("executed V8 event lacks frozen replay risk fraction")
    events["system_source"] = "v8"
    events = score_flags(events)
    return _risk_fields(events).loc[:, NORMALIZED_COLUMNS], pd.DataFrame(audit)


def _pf(values: pd.Series) -> float:
    wins = values.loc[values > 0].sum()
    losses = -values.loc[values < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / losses)


def summarize_groups(frame: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    """Summarize closed trade R and nominal returns without account compounding."""
    records: list[dict[str, object]] = []
    for keys, group in frame.groupby(group_columns, dropna=False, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        r = pd.to_numeric(group.net_r, errors="coerce")
        ret = pd.to_numeric(group.net_return, errors="coerce")
        positive_r = r.clip(lower=0)
        record = dict(zip(group_columns, keys))
        record.update({
            "n_closed": int(len(group)), "n_wins": int((r > 0).sum()), "win_rate": float((r > 0).mean()),
            "sum_net_r": float(r.sum()), "mean_net_r": float(r.mean()), "median_net_r": float(r.median()), "pf_net_r": _pf(r),
            "sum_net_return": float(ret.sum()), "mean_net_return": float(ret.mean()), "median_net_return": float(ret.median()), "pf_net_return": _pf(ret),
            "realized_ge10r": int((r >= 10).sum()), "realized_ge10r_rate": float((r >= 10).mean()),
            "top1_net_r": float(r.max()), "top1_positive_r_share": float(r.max() / positive_r.sum()) if positive_r.sum() > 0 else np.nan,
            "top1_abs_net_r_share": float(r.abs().max() / r.abs().sum()) if r.abs().sum() > 0 else np.nan,
            "mean_risk_fraction_at_entry": float(group.risk_fraction_at_entry.mean()), "median_risk_fraction_at_entry": float(group.risk_fraction_at_entry.median()),
            "mean_cost_r": float(group.cost_r.mean()), "median_cost_r": float(group.cost_r.median()),
        })
        records.append(record)
    return pd.DataFrame(records)


def ranking_views(ledger: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    """Return predeclared full/dev/validation closed cohorts for each ranking view."""
    views = {
        "v1_common_execution_long": ledger.system_source.eq("v1_common_execution_long"),
        "v8_long": ledger.system_source.eq("v8") & ledger.side.eq(1),
        "v8_both": ledger.system_source.eq("v8"),
    }
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for view, view_mask in views.items():
        candidate = ledger.loc[view_mask]
        out[(view, "full_closed")] = candidate.loc[candidate.closed]
        for period in ("development", "validation"):
            out[(view, period)] = candidate.loc[candidate.scoring_closed & candidate.period.eq(period)]
    return out


def build_rankings(ledger: pd.DataFrame, min_closed: int) -> dict[str, pd.DataFrame]:
    """Build full and split rankings, retaining low-sample rows separately."""
    asset: list[pd.DataFrame] = []
    venue_asset: list[pd.DataFrame] = []
    asset_tf_side: list[pd.DataFrame] = []
    coverage: list[dict[str, object]] = []
    for (view, scope), cohort in ranking_views(ledger).items():
        coverage.append({"view": view, "scope": scope, "n_closed": len(cohort), "n_assets": cohort.asset.nunique()})
        for target, cols in ((asset, ["asset"]), (venue_asset, ["venue", "asset"]), (asset_tf_side, ["asset", "timeframe_min", "side"])):
            table = summarize_groups(cohort, cols)
            if not table.empty:
                table.insert(0, "scope", scope)
                table.insert(0, "view", view)
                target.append(table)
    all_asset = pd.concat(asset, ignore_index=True)
    main = all_asset.loc[all_asset.n_closed >= min_closed].copy()
    main = main.sort_values(["view", "scope", "sum_net_r", "mean_net_r", "n_closed", "asset"], ascending=[True, True, False, False, False, True]).reset_index(drop=True)
    main["rank_sum_net_r"] = main.groupby(["view", "scope"]).cumcount() + 1
    low = all_asset.loc[all_asset.n_closed < min_closed].sort_values(["view", "scope", "n_closed", "asset"], ascending=[True, True, False, True])
    return {
        "asset_ranking_all": all_asset.sort_values(["view", "scope", "sum_net_r", "asset"], ascending=[True, True, False, True]),
        "asset_ranking_main_min30": main,
        "asset_ranking_low_sample": low,
        "venue_asset_ranking_all": pd.concat(venue_asset, ignore_index=True),
        "asset_timeframe_side_ranking_all": pd.concat(asset_tf_side, ignore_index=True),
        "ranking_coverage": pd.DataFrame(coverage),
    }


def _require_hash(path: Path, expected: str, label: str) -> str:
    """Verify a fixed frozen input before its contents are consumed."""
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def run(output: Path, official: bool) -> None:
    """Create the complete frozen-ledger ranking artifact without any replay."""
    if official and not _clean([Path(__file__), CONFIG, PLAN, HOLDOUT, TEST]):
        raise RuntimeError("official run requires committed, clean builder/config/plan/receipt/test")
    config = json.loads(CONFIG.read_text())
    _require_hash(V1_ROOT / "manifest.json", config["v1_common_manifest_sha256"], "V1 common manifest")
    _require_hash(V8_ROOT / "manifest.json", config["v8_replay_manifest_sha256"], "V8 replay manifest")
    _require_hash(V8_EVIDENCE, config["v8_same_entry_evidence_sha256"], "V8 same-entry evidence")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    output.mkdir(parents=True, exist_ok=False)
    v1, v1_audit = normalize_v1()
    v1_post_consistency = verify_v1_post_consistency()
    if not v1_post_consistency["passed"]:
        raise ValueError("V1 stream trade ledger disagrees with post_final_v4 common aggregate")
    _write_json(output / "v1_post_final_consistency.json", v1_post_consistency)
    v8, v8_audit = normalize_v8()
    if len(v1) != int(config["expected_v1_common_entries"]) or int(v1.closed.sum()) != int(config["expected_v1_common_closed"]):
        raise ValueError("V1 common frozen entry/closed counts do not match the fixed source contract")
    if len(v1_audit) != int(config["expected_streams"]) or len(v8_audit) != int(config["expected_streams"]) + 1:
        raise ValueError("frozen stream coverage does not match the fixed source contract")
    ledger = pd.concat([v1, v8], ignore_index=True)
    if ledger.duplicated(["system_source", "stream_key", "signal_bar_open", "side"], keep=False).any():
        raise ValueError("normalized ledger identity is unexpectedly duplicated")
    ledger.to_csv(output / "normalized_ledger.csv.gz", index=False, compression="gzip")
    audit = pd.concat([v1_audit, v8_audit], ignore_index=True)
    audit.to_csv(output / "input_trade_file_hashes.csv", index=False)
    for name, table in build_rankings(ledger, int(config["main_ranking_min_closed_trades"])).items():
        table.to_csv(output / f"{name}.csv", index=False)
    summary = {
        "official": official, "config_sha256": sha256(CONFIG), "plan_sha256": sha256(PLAN), "holdout_receipt_sha256": sha256(HOLDOUT),
        "v1_manifest_sha256": sha256(V1_ROOT / "manifest.json"), "v8_replay_manifest_sha256": sha256(V8_ROOT / "manifest.json"),
        "v8_same_entry_evidence_sha256": sha256(V8_EVIDENCE), "v1_rows": len(v1), "v1_closed": int(v1.closed.sum()),
        "v8_executed_rows": len(v8), "v8_closed": int(v8.closed.sum()), "v8_scoring_closed": int(v8.scoring_closed.sum()),
        "v1_post_final_consistency": v1_post_consistency,
        "input_files": len(audit), "input_files_aggregate_sha256": hashlib.sha256("\n".join(audit.sha256.astype(str)).encode()).hexdigest(),
        "normalized_columns": NORMALIZED_COLUMNS,
        "normalized_ledger_sha256": sha256(output / "normalized_ledger.csv.gz"),
        "normalized_ledger_bytes": (output / "normalized_ledger.csv.gz").stat().st_size,
    }
    _write_json(output / "normalized_ledger_manifest.json", summary)
    print(json.dumps(summary, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official", action="store_true")
    args = parser.parse_args()
    run(args.output, args.official)


if __name__ == "__main__":
    main()
