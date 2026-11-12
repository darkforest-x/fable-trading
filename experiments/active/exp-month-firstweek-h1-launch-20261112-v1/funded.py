"""Run H1-launch fills through the frozen V1 account and compare with H4 rail-target."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SUMMARY_PATH = ROOT / "experiments/active/exp-month-firstweek-h4-rail-target-20261112-v1/account_summary.py"
DATA = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1"
FILLS = DATA / "h1_launch/fills.csv"
OUTPUT = DATA / "h1_launch/account_168h"
H4_ACCOUNT = DATA / "h4_rail_target/account_168h"

spec = spec_from_file_location("h4_account_summary_helpers", SUMMARY_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Cannot load account helpers from {SUMMARY_PATH}")
HELPERS = module_from_spec(spec)
sys.modules[spec.name] = HELPERS
spec.loader.exec_module(HELPERS)
ACCOUNT = HELPERS.ACCOUNT


def _check_net_identity(summary: pd.DataFrame, orders: pd.DataFrame) -> None:
    funded_status = {"closed", "period_end_valuation", "open_marked_at_cutoff"}
    for metric in summary.itertuples(index=False):
        rows = orders[
            orders["sample"].astype(str).eq(str(metric.sample))
            & orders["account_variant"].astype(str).eq(str(metric.account_variant))
        ]
        funded = rows[rows["account_status"].astype(str).isin(funded_status)]
        if len(funded) != int(metric.funded_trade_count):
            raise AssertionError(f"Funded count mismatch: {metric.sample}/{metric.account_variant}")
        realized = float(pd.to_numeric(funded.get("account_realized_net_u"), errors="coerce").fillna(0).sum())
        if not np.isclose(realized, float(metric.realized_net_pnl_u), rtol=1e-9, atol=1e-6):
            raise AssertionError(f"Realized net PnL mismatch: {metric.sample}/{metric.account_variant}")
        open_rows = funded[funded["account_status"].astype(str).eq("open_marked_at_cutoff")]
        unrealized = float(pd.to_numeric(open_rows.get("account_unrealized_gross_u", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        open_fees = float(pd.to_numeric(open_rows.get("account_entry_fee_u", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        equity = float(metric.initial_equity_u) + realized + unrealized - open_fees
        if not np.isclose(equity, float(metric.final_equity_u), rtol=1e-9, atol=1e-6):
            raise AssertionError(f"1000U + net PnL != final equity: {metric.sample}/{metric.account_variant}")


def run() -> None:
    if not FILLS.is_file():
        raise FileNotFoundError(FILLS)
    fills = ACCOUNT._validate_fills(pd.read_csv(FILLS, float_precision="round_trip"))
    if set(fills["sample"].astype(str)) != {"signal", "random"}:
        raise ValueError("Expected both V1 samples: signal and random")
    groups = sorted(fills["group"].astype(str).unique())
    legacy_groups = sorted(ACCOUNT.MAIN_GROUPS)
    if len(groups) != 2 or len(legacy_groups) != 2 or "source_group" in fills:
        raise ValueError(f"Expected two source groups and a free source_group field; got {groups}")
    group_map = dict(zip(groups, legacy_groups))
    fills["source_group"] = fills["group"].astype(str)
    fills["group"] = fills["group"].astype(str).map(group_map)

    replay = ACCOUNT._replay_module()
    cutoff = int(replay.CUTOFF) // ACCOUNT.HOUR_MS * ACCOUNT.HOUR_MS
    loader = HELPERS.cached_raw_loader(replay.read_raw, fills, cutoff)
    with TemporaryDirectory(prefix="h1-launch-account-") as tmp:
        adapted = Path(tmp) / "fills.csv"
        fills.to_csv(adapted, index=False, float_format="%.17g")
        result = ACCOUNT.run(fills_path=adapted, cutoff_ms=cutoff, raw_loader=loader)

    _check_net_identity(result["summary"], result["orders"])
    result["orders"]["account_group"] = result["orders"]["group"]
    result["orders"]["group"] = result["orders"].pop("source_group")
    h4_summary = pd.read_csv(H4_ACCOUNT / "account_summary.csv", float_precision="round_trip")
    h4_orders = pd.read_csv(H4_ACCOUNT / "account_orders.csv", float_precision="round_trip")
    comparison = HELPERS._comparison_rows(result["summary"], result["orders"], h4_summary, h4_orders)
    comparison.loc[comparison["ledger"].eq("v1"), "ledger"] = "h1_launch"

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, table in (("account_summary.csv", result["summary"]),
                        ("account_orders.csv", result["orders"]),
                        ("account_hourly.csv", result["hourly"]),
                        ("account_comparison.csv", comparison)):
        target = (HERE / name) if name == "account_comparison.csv" else OUTPUT / name
        temp = target.with_name(target.name + ".tmp")
        table.to_csv(temp, index=False)
        temp.replace(target)
    print(f"group mapping: {group_map}")
    print(f"account outputs: {OUTPUT}")


if __name__ == "__main__":
    run()
