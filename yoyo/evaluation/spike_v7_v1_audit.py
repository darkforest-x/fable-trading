"""Post-outcome concentration/accounting audit of the fixed V7/V1 replay.

Consumes closed event ledgers only, never signal features. RAVE and USDC were
identified after examining the extreme outcome examples; exclusions below are
explicit sensitivity diagnostics, not a new selected strategy or a tuned pool.
The original full-pool benchmark is retained unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v7_v1_report import bools, event_metrics


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(raw: Path, post: Path, output: Path) -> None:
    manifest = json.loads((raw / "manifest.json").read_text())
    done = json.loads((post / "post_manifest.json").read_text())
    assert done["complete"] and done["raw_manifest_sha256"] == digest(raw / "manifest.json")
    source = post / "common_execution_trades.csv.gz"
    trades = pd.read_csv(source)
    closed = trades.loc[~bools(trades.censored)]
    computed = (closed.side * (closed.exit_price - closed.entry_price)
                - .002 * closed.entry_price) / closed.initial_risk
    errors = np.abs(computed.to_numpy() - closed.net_r.to_numpy())
    assert np.isfinite(errors).all() and errors.max() < 1e-7
    native_path = Path(manifest["historical_v1_native"]["path"])
    assert digest(native_path) == manifest["historical_v1_native"]["sha256"]
    native = pd.read_csv(native_path)
    native = native.loc[native.timeframe_min.isin([30, 60, 240])].copy()
    native["variant"] = "historical_v1_native"
    selected = trades.loc[trades.variant.isin(["v1_common_execution_long", "v7_bb_long", "v7_bb_both"])]
    rows = []
    for (variant, minutes), part in pd.concat([selected, native], ignore_index=True).groupby(["variant", "timeframe_min"]):
        full = event_metrics(part)
        for name, subset in (("original_full_pool", part),
                             ("post_hoc_without_RAVE", part.loc[~part.symbol.str.contains("RAVE")]),
                             ("post_hoc_without_USDC", part.loc[~part.symbol.str.contains("USDC")])):
            stats = event_metrics(subset)
            # Native V1 has no MFE field; concatenation must not imply zero MFE.
            stats.pop("mfe_ge_10r", None)
            rows.append({"variant": variant, "timeframe_min": minutes, "diagnostic": name,
                         **stats, "excluded_positive_return_share":
                         1-stats["positive_net_return_sum"]/full["positive_net_return_sum"]})
    output.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(rows).to_csv(output / "post_hoc_sensitivity.csv", index=False)
    closed.loc[closed.net_return.le(-1)].to_csv(output / "unmodelled_insolvency_events.csv", index=False)
    examples = pd.concat([closed.loc[closed.variant.eq("v1_common_execution_long")].nlargest(2, "net_r"),
                          closed.loc[closed.variant.eq("v7_bb_both")].nsmallest(2, "net_r")])
    examples = examples.copy()
    examples["cost_in_r"] = .002 * examples.entry_price / examples.initial_risk
    examples.to_csv(output / "extreme_example_arithmetic.csv", index=False)
    receipt = {"source_ledger_sha256": digest(source), "audit_code_sha256": digest(Path(__file__)),
               "closed_events_checked": len(closed), "maximum_net_r_arithmetic_error": float(errors.max()),
               "post_hoc_diagnostic_only": True, "no_benchmark_rows_removed": True,
               "outputs": {f.name: digest(f) for f in output.glob("*.csv")}}
    (output / "audit_manifest.json").write_text(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("post", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.raw, args.post, args.output)
