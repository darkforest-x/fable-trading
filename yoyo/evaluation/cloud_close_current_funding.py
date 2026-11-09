"""Prepare the frozen Binance NAV-tenth account for funding attribution.

This is an offline adapter: it joins the completed 680-contract cohort account
to its cached native V1.7 path rows and reconstructs its two child quantities.
Funding settlement windows and cashflow attribution are delegated to the frozen
``cloud_close_funding_ledger`` module. The adapter's preflight and mini modes
never fetch, simulate the account, or rewrite an equity curve.
"""
from __future__ import annotations

import argparse
import inspect
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_cohort as archive
from yoyo.evaluation import cloud_close_archive_complete as complete
from yoyo.evaluation import cloud_close_efficiency_binance_bounds as efficient
from yoyo.evaluation import cloud_close_efficient_trend as trend
from yoyo.evaluation import cloud_close_funding_ledger as ledger
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-current-funding-20261109-v1"
CONFIG_PATH = EXP / "config.json"
CURRENT_OUTPUT = EXP / "current"
ARCHIVE_EXP = complete.EXP
ACCOUNT = ARCHIVE_EXP / "funded_tenth.csv.gz"
ACCOUNT_METRICS = ARCHIVE_EXP / "account_metrics.csv"
ACCOUNT_CONFIG = ARCHIVE_EXP / "config.json"
SOURCE_COVERAGE = ARCHIVE_EXP / "source_coverage.json"
ARCHIVE_COHORT_SUMMARY = archive.EXP / "summary.json"
EXPECTED_TAKER = float(wallet.TAKER)
EXPECTED_MAKER = float(engine.MAKER)
EXPECTED_LEG_FRACTIONS = {float(k): float(v) for k, v in efficient.LEG_FRACTIONS.items()}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_config(config: dict) -> None:
    if config["venue"] != "Binance USD-M":
        raise ValueError("Current funding adapter is restricted to Binance USD-M")
    account = config["account"]
    if account["source_file"] != str(ACCOUNT.relative_to(ROOT)):
        raise ValueError("Current account source path changed")
    if account["policy"] != "tenth" or account["scenario"] != "actual":
        raise ValueError("Expected the frozen NAV-tenth actual account")
    if int(account["expected_positions"]) != 1635:
        raise ValueError("Frozen current account position count changed")
    if not np.isclose(float(config["accounting"]["entry_fee_rate"]), EXPECTED_TAKER):
        raise ValueError("Current account entry fee differs from the native taker rate")
    configured = {float(k): float(v) for k, v in config["accounting"]["leg_fraction_by_tp_r"].items()}
    if configured != EXPECTED_LEG_FRACTIONS:
        raise ValueError("Current account child-leg weights differ from the frozen 2/3 scaleout")
    if config["network_policy"] != "preflight and mini are local-read-only; only explicit --collect may fetch sources":
        raise ValueError("Network policy changed")


def _current_contract_cohorts(config: dict) -> tuple[set[str], dict]:
    account_config = _read_json(ACCOUNT_CONFIG)
    cohort = _read_json(ARCHIVE_COHORT_SUMMARY)
    old_additions = set(map(str, cohort["archived_additions"]))
    supplemental = set(map(str, account_config["supplemental_exchange_records"]))
    coverage = pd.read_json(SOURCE_COVERAGE)
    if coverage.ticker.astype(str).duplicated().any() or set(coverage.ticker.astype(str)) != supplemental:
        raise ValueError("Supplemental contract coverage differs from the frozen metadata inventory")
    priced_new = set(coverage.loc[~coverage.cannot_observe_90d.astype(bool), "ticker"].astype(str))
    if old_additions & supplemental:
        raise ValueError("The two supplemental cohorts unexpectedly overlap")
    extra = old_additions | priced_new
    base = {p.name for p in archive.venue.WORK.iterdir() if p.name.endswith("USDT")}
    declared = base | old_additions | supplemental
    universe_config = config["contract_universe"]
    if len(base) != int(universe_config["base_contracts_with_native_paths"]):
        raise ValueError("The frozen Binance base contract inventory changed")
    if len(old_additions) != int(universe_config["previous_archived_additions"]):
        raise ValueError("The earlier archive cohort changed")
    if len(supplemental) != int(universe_config["supplemental_exchange_metadata_contracts"]):
        raise ValueError("The supplemental exchange metadata cohort changed")
    if len(supplemental - priced_new) != int(universe_config["too_recent_supplemental_without_90d_paths"]):
        raise ValueError("The too-recent supplemental contract count changed")
    if len(declared) != int(universe_config["expected_universe_size"]):
        raise ValueError("The declared 680-contract market universe changed")
    if not extra <= declared:
        raise ValueError("Priced source cohort contains a contract outside the declared universe")
    audit = {
        "base_contracts_with_native_paths": len(base),
        "previous_archive_additions": len(old_additions),
        "supplemental_metadata_contracts": len(supplemental),
        "supplemental_contracts_with_90d_paths": len(priced_new),
        "too_recent_supplemental_contracts_without_paths": len(supplemental - priced_new),
        "declared_universe_size": len(declared),
        "priced_contract_count": len(base | extra),
        "extra_path_contract_count": len(extra),
    }
    return extra, audit


def _read_funded_account(config: dict) -> pd.DataFrame:
    funded = pd.read_csv(ACCOUNT, dtype={"event_id": str, "parent_id": str, "symbol": str})
    required = {"event_id", "parent_id", "symbol", "side", "entry_ms", "exit_bar_ms",
                "cash_pnl", "notional", "net_return"}
    if required - set(funded.columns):
        raise ValueError(f"Frozen current account is missing columns: {sorted(required - set(funded.columns))}")
    if (funded.event_id.duplicated().any() or funded.parent_id.duplicated().any()
            or (funded.event_id.astype(str) != funded.parent_id.astype(str)).any()):
        raise ValueError("Actual current funded rows must have one immutable parent per event")
    if len(funded) != int(config["account"]["expected_positions"]):
        raise ValueError("Current funded account roster changed")
    for column in ("side", "entry_ms", "exit_bar_ms"):
        funded[column] = pd.to_numeric(funded[column], errors="raise").astype("int64")
    if not funded.side.isin([-1, 1]).all() or (funded.entry_ms > funded.exit_bar_ms).any():
        raise ValueError("Invalid side or event chronology in the current funded roster")
    if (funded.notional <= 0).any() or not np.isfinite(funded[["cash_pnl", "notional", "net_return"]]).all().all():
        raise ValueError("Current funded quantities and outcomes must be finite and positive")

    metrics = pd.read_csv(ACCOUNT_METRICS)
    row = metrics.loc[metrics.policy.eq("tenth") & metrics.scenario.eq("actual") & metrics.period.eq("all")]
    if len(row) != 1:
        raise ValueError("Could not uniquely identify the completed current-account metric row")
    record = row.iloc[0]
    expectations = config["account"]["expected_all_period"]
    if (int(record.trades) != len(funded)
            or not np.isclose(float(record.final_usdt), float(expectations["final_usdt"]), atol=1e-6)
            or not np.isclose(1000.0 + float(funded.cash_pnl.sum()), float(record.final_usdt), atol=1e-4)):
        raise ValueError("Funded rows do not reconcile to the frozen current-account terminal value")
    if int(funded.symbol.nunique()) != int(config["account"]["expected_traded_symbols"]):
        raise ValueError("Current funded symbol count changed")
    dev = metrics.loc[metrics.policy.eq("tenth") & metrics.scenario.eq("actual") & metrics.period.eq("dev")]
    if (len(dev) != 1 or int(dev.iloc[0].trades) != int(expectations["development_trades"])
            or not np.isclose(float(dev.iloc[0].final_usdt), float(expectations["development_final_usdt"]), atol=1e-6)):
        raise ValueError("Frozen current-account development metrics changed")
    return funded


def _source_paths(funded: pd.DataFrame, extra: set[str]) -> tuple[pd.DataFrame, dict]:
    """Load already-cached raw path rows; this function contains no fetch call."""
    # archive.pool reads the three registered local path roots and applies the
    # source's same eligibility and nonempty prior-rank filter.
    paths = archive.pool(extra)
    funded_ids = set(funded.event_id.astype(str))
    actual = paths.loc[
        (~paths.is_control.astype(bool)) & paths.event_id.astype(str).isin(funded_ids)
    ].copy()
    if set(actual.event_id.astype(str)) != funded_ids:
        absent = sorted(funded_ids - set(actual.event_id.astype(str)))
        raise ValueError(f"Current funded events lack cached actual native paths: {absent[:8]}")
    if actual.duplicated(["event_id", "tp_r"]).any() or not actual.groupby("event_id").size().eq(2).all():
        raise ValueError("Each current funded event must have exactly its runner and native 2R source paths")
    if not actual.groupby("event_id").tp_r.apply(lambda s: set(s.astype(float)) == {0.0, 2.0}).all():
        raise ValueError("Current native path pairs do not contain TP=0 and TP=2")
    audit = {
        "eligible_path_events_loaded": int(paths.event_id.nunique()),
        "funded_actual_positions_joined": int(actual.event_id.nunique()),
        "actual_native_path_rows_joined": int(len(actual)),
        "actual_symbols_with_positions": int(actual.symbol.nunique()),
        "path_pool_controls_excluded_from_funding": int(paths.is_control.astype(bool).sum()),
        "universe_path_load_made_network_request": False,
    }
    return actual, audit


def _construct_child_legs(selected: pd.DataFrame, funded: pd.DataFrame,
                          decisions: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, dict]:
    if selected.duplicated(["event_id", "tp_r"]).any() or len(selected) != 2 * len(funded):
        raise ValueError("Selected current paths are not an exact two-leg roster")
    if set(selected.event_id.astype(str)) != set(funded.event_id.astype(str)):
        raise ValueError("Selected current path IDs differ from funded IDs")
    if decisions.event_id.duplicated().any() or set(decisions.event_id.astype(str)) != set(funded.event_id.astype(str)):
        raise ValueError("Current actual direction decisions do not join one-to-one to funded IDs")

    account = funded.set_index("event_id")
    for column in ("parent_id", "symbol", "side", "entry_ms"):
        expected = selected.event_id.map(account[column])
        if not np.array_equal(selected[column].astype(str).to_numpy(), expected.astype(str).to_numpy()):
            raise ValueError(f"Current funded and native path records differ in {column}")
    last_native_exit = selected.groupby("event_id").exit_bar_ms.max().sort_index()
    last_funded_exit = account.exit_bar_ms.sort_index()
    if not last_native_exit.equals(last_funded_exit):
        raise ValueError("Native runner clock differs from the funded current position exit clock")

    legs = selected.merge(funded[["event_id", "cash_pnl", "notional"]], on="event_id",
                          how="left", validate="many_to_one", suffixes=("", "_account"))
    choice = decisions.set_index("event_id").choice
    legs["entry_choice"] = legs.event_id.map(choice)
    full_ids = set(choice.index[choice.eq("full_runner")].astype(str))
    runner = legs.loc[legs.tp_r.eq(0)].set_index("event_id")
    target = legs.loc[legs.tp_r.eq(2)].set_index("event_id")
    for column in ("exit_bar_ms", "exit_price", "exit_phase", "exit_fee_rate", "exit_reason"):
        if not runner.loc[list(full_ids), column].equals(target.loc[list(full_ids), column]):
            raise ValueError(f"Full-runner selection changed paired child {column}")

    fraction_by_tp = {float(k): float(v) for k, v in config["accounting"]["leg_fraction_by_tp_r"].items()}
    legs["tp_r"] = pd.to_numeric(legs.tp_r, errors="raise").astype(float)
    legs["leg_fraction"] = legs.tp_r.map(fraction_by_tp)
    if legs.leg_fraction.isna().any():
        raise ValueError("Unsupported funding leg target")
    legs["parent_notional_usdt"] = legs["notional"]
    legs["notional_usdt"] = legs.parent_notional_usdt * legs.leg_fraction
    legs["signed_size"] = legs.side * legs.notional_usdt / legs.entry_price
    legs["quantity_abs"] = legs.signed_size.abs()
    legs["entry_fee_rate"] = EXPECTED_TAKER
    legs["entry_fee_class"] = "taker"
    legs["entry_fee_usdt"] = legs.notional_usdt * legs.entry_fee_rate
    fee_rate = pd.to_numeric(legs.exit_fee_rate, errors="raise").astype(float)
    is_maker = np.isclose(fee_rate, EXPECTED_MAKER, rtol=0, atol=1e-12)
    is_taker = np.isclose(fee_rate, EXPECTED_TAKER, rtol=0, atol=1e-12)
    if not (is_maker | is_taker).all():
        raise ValueError("Current native exits use a fee rate outside the frozen maker/taker classes")
    legs["exit_fee_rate"] = fee_rate
    legs["exit_fee_class"] = np.where(is_maker, "resting_maker", "taker")
    legs["exit_fee_usdt"] = legs.quantity_abs * legs.exit_price * legs.exit_fee_rate
    legs["reconstructed_cash_pnl"] = (
        legs.side * (legs.exit_price - legs.entry_price) * legs.quantity_abs
        - legs.entry_fee_usdt - legs.exit_fee_usdt
    )
    legs["source_parent_cash_pnl"] = legs["cash_pnl"]
    per_event = legs.groupby("event_id", sort=False).agg(
        reconstructed_cash_pnl=("reconstructed_cash_pnl", "sum"),
        source_cash_pnl=("cash_pnl", "first"),
        child_notional_usdt=("notional_usdt", "sum"),
        parent_notional_usdt=("parent_notional_usdt", "first"),
        child_legs=("tp_r", "size"),
    )
    errors = (per_event.reconstructed_cash_pnl - per_event.source_cash_pnl).abs()
    max_error = float(errors.max()) if len(errors) else 0.0
    tolerance = float(config["accounting"]["cash_reconciliation_tolerance_usdt"])
    if max_error > tolerance:
        raise ValueError(f"Child path cash does not match frozen funded_tenth rows (max error {max_error:.9g})")
    if not per_event.child_legs.eq(2).all() or not np.allclose(
            per_event.child_notional_usdt, per_event.parent_notional_usdt,
            rtol=1e-10, atol=float(config["accounting"]["notional_reconciliation_tolerance_usdt"])):
        raise ValueError("Current child quantities do not reconcile once to parent notional")
    legs["is_control"] = False
    legs["entry_time_utc"] = pd.to_datetime(legs.entry_ms, unit="ms", utc=True).astype(str)
    legs["exit_bar_open_utc"] = pd.to_datetime(legs.exit_bar_ms, unit="ms", utc=True).astype(str)
    legs["is_control"] = legs["is_control"].astype(bool)
    audit = {
        "funded_positions": int(len(funded)),
        "native_child_legs": int(len(legs)),
        "full_runner_positions": int(len(full_ids)),
        "partial_2_3_positions": int(len(funded) - len(full_ids)),
        "entry_fee_class": "taker",
        "entry_fee_rate": EXPECTED_TAKER,
        "exit_fee_class_counts": legs.exit_fee_class.value_counts().to_dict(),
        "exit_phase_counts": legs.exit_phase.value_counts().to_dict(),
        "exit_reason_counts": legs.exit_reason.value_counts().to_dict(),
        "max_event_cash_reconciliation_error_usdt": max_error,
        "child_notional_reconciles_to_parent": True,
        "nav_recomputed": False,
    }
    return legs, audit


def build_current_legs(config_path: Path = CONFIG_PATH) -> tuple[pd.DataFrame, dict]:
    """Return funding-ready actual child legs from local frozen sources only."""
    config = _read_json(config_path)
    _validate_config(config)
    funded = _read_funded_account(config)
    extra, cohort_audit = _current_contract_cohorts(config)
    path_rows, path_audit = _source_paths(funded, extra)
    with archive.market_quotes(extra):
        decisions, selected = trend.inputs("Binance", path_rows)
    legs, leg_audit = _construct_child_legs(selected, funded, decisions, config)
    month_windows = ledger._month_windows(legs)
    audit = {
        "account": {
            "source_file": str(ACCOUNT.relative_to(ROOT)),
            "source_positions": int(len(funded)),
            "unique_funded_symbols": int(funded.symbol.nunique()),
            "parent_notional_sum_usdt": float(funded.notional.sum()),
            "cash_pnl_sum_usdt": float(funded.cash_pnl.sum()),
            "terminal_usdt_from_frozen_cash_rows": float(1000.0 + funded.cash_pnl.sum()),
            "source_account_final_usdt": float(config["account"]["expected_all_period"]["final_usdt"]),
        },
        "contract_cohorts": cohort_audit,
        "path_join": path_audit,
        "path_join_missing_efficiency_fallback_events": int(decisions.directional_efficiency.isna().sum()),
        "child_accounting": leg_audit,
        "held_symbol_month_windows": len(month_windows),
        "symbol_months": sorted({month for _, month in month_windows}),
        "funding_cache_root": config["funding_adapter"]["reuse_cache_root"],
        "network_requests_started": False,
    }
    return legs, audit


def preflight(config_path: Path = CONFIG_PATH) -> dict:
    legs, audit = build_current_legs(config_path)
    print(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return audit


def _assert_adapter_committed(config_path: Path) -> None:
    for path in (Path(__file__).resolve(), config_path.resolve()):
        relative = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode:
            raise RuntimeError("Commit the current-funding adapter and config before --collect")


def _execution_columns(legs: pd.DataFrame) -> pd.DataFrame:
    wanted = [
        "event_id", "tp_r", "parent_id", "symbol", "is_control", "side", "entry_ms",
        "entry_time_utc", "entry_price", "entry_fee_rate", "entry_fee_class", "entry_fee_usdt",
        "exit_bar_ms", "exit_bar_open_utc", "exit_phase", "exit_price", "exit_fee_rate",
        "exit_fee_class", "exit_fee_usdt", "exit_reason", "stop_price", "same_bar_ambiguous",
        "signed_size", "quantity_abs", "notional_usdt", "parent_notional_usdt", "entry_choice",
        "reconstructed_cash_pnl", "source_parent_cash_pnl",
    ]
    return legs.loc[:, [column for column in wanted if column in legs.columns]].copy()


def _write_execution_legs(path: Path, legs: pd.DataFrame) -> None:
    execution = _execution_columns(legs).sort_values(["entry_ms", "event_id", "tp_r"])
    if execution.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Execution leg receipt is not unique by event_id/tp_r")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    execution.to_csv(temporary, index=False, compression={"method": "gzip", "mtime": 0})
    temporary.replace(path)


def collect_current(config_path: Path = CONFIG_PATH) -> dict:
    """Explicit network-enabled current-scope collection; never used by preflight."""
    _assert_adapter_committed(config_path)
    ledger._assert_committed()
    config = _read_json(config_path)
    _validate_config(config)
    if (CURRENT_OUTPUT / "funding_audit.json").exists():
        raise FileExistsError(f"Current funding scope already has its completion receipt: {CURRENT_OUTPUT / 'funding_audit.json'}")
    legs, audit = build_current_legs(config_path)
    audit["native_path_preparation_made_network_request"] = False
    audit["network_requests_started"] = True
    execution_path = CURRENT_OUTPUT / "execution_legs.csv.gz"
    audit["execution_legs_file"] = str(execution_path.relative_to(ROOT))
    _write_execution_legs(execution_path, legs)
    result = ledger._write_scope(
        "current", config, legs, audit,
        experiment_dir=EXP,
        source_experiment=config["account"]["source_experiment"],
        source_strategy="680-contract actual NAV-tenth efficient-trend account",
    )
    return result


def resolve_missing(config_path: Path = CONFIG_PATH) -> dict:
    """Request only unresolved held windows, then rebuild the attribution from local data."""
    _assert_adapter_committed(config_path)
    ledger._assert_committed()
    config = _read_json(config_path)
    _validate_config(config)
    directory = EXP / 'resolved'
    if (directory / 'funding_audit.json').exists():
        raise FileExistsError('Resolved account funding attribution already exists')
    receipt = _read_json(CURRENT_OUTPUT / 'funding_audit.json')
    missing = {(r['symbol'],r['month']) for r in receipt['unresolved_months']}
    if not missing or len(missing) != receipt['unknown_month_windows']:
        raise ValueError('Original funding receipt has no distinct outstanding windows')
    months = _read_json(CURRENT_OUTPUT / 'symbol_month_audit.json')
    client = ledger.FundingClient('binance', ledger.CACHE / 'funding_api')
    for window in months:
        ticker,month = window['symbol'],window['month']
        if (ticker,month) not in missing:
            continue
        start,end = window['query_start_ms'],window['query_end_exclusive_ms']
        _,known = ledger._normalized_sources(ticker.removesuffix('USDT'),month,start,end)
        if not any(a['manifest_range_covers_query'] for a in known):
            _,manifest = ledger._api_rows(client,ticker.removesuffix('USDT'),month,start,end)
            if not manifest.get('api_range_traversed'):
                raise ValueError(f'Could not obtain complete settlement interval for {ticker} {month}')
    legs, audit = build_current_legs(config_path)
    audit['execution_legs_file'] = str((CURRENT_OUTPUT / 'execution_legs.csv.gz').relative_to(ROOT))
    audit['previous_attribution_receipt'] = str((CURRENT_OUTPUT / 'funding_audit.json').relative_to(ROOT))
    audit['outstanding_rate_windows_recovered'] = sorted(missing)
    return ledger._write_scope('resolved',config,legs,audit,experiment_dir=EXP,
                               source_experiment=config['account']['source_experiment'],
                               source_strategy='680-contract actual NAV-tenth efficient-trend account',
                               cache_only=True)


def mini_check() -> None:
    """Check 2/3 child sizing, maker/taker charges, and ledger window reuse."""
    cfg = _read_json(CONFIG_PATH)
    writer = inspect.signature(ledger._write_scope)
    for name in ("experiment_dir", "source_experiment", "source_strategy"):
        if name not in writer.parameters or writer.parameters[name].kind is not inspect.Parameter.KEYWORD_ONLY:
            raise AssertionError(f"Frozen funding writer does not expose keyword-only {name}")
    sample = pd.DataFrame([
        {"event_id": "mini", "parent_id": "mini", "symbol": "ADA", "side": 1,
         "entry_ms": 1_704_067_200_000, "exit_bar_ms": 1_704_070_800_000,
         "entry_price": 100.0, "exit_price": 104.0, "exit_phase": "intrabar",
         "exit_reason": "ema_flip", "exit_fee_rate": EXPECTED_TAKER, "tp_r": 0.0,
         "stop_price": 95.0, "same_bar_ambiguous": False},
        {"event_id": "mini", "parent_id": "mini", "symbol": "ADA", "side": 1,
         "entry_ms": 1_704_067_200_000, "exit_bar_ms": 1_704_070_800_000,
         "entry_price": 100.0, "exit_price": 103.0, "exit_phase": "intrabar",
         "exit_reason": "take_profit", "exit_fee_rate": EXPECTED_MAKER, "tp_r": 2.0,
         "stop_price": 95.0, "same_bar_ambiguous": False},
    ])
    # Expected cash: gross 3.0 minus 4.5bp entry (=0.045), 0.0156 taker
    # exit and 0.01236 maker exit = 2.92704 USDT.
    synthetic_parent = pd.DataFrame([{
        "event_id": "mini", "parent_id": "mini", "symbol": "ADA", "side": 1,
        "entry_ms": 1_704_067_200_000, "exit_bar_ms": 1_704_070_800_000,
        "cash_pnl": 2.92704, "notional": 90.0,
    }])
    decision = pd.DataFrame([{"event_id": "mini", "choice": "partial_2_3", "directional_efficiency": 0.2}])
    legs, audit = _construct_child_legs(sample, synthetic_parent, decision, cfg)
    if (len(legs) != 2 or not np.isclose(legs.quantity_abs.sum(), 0.9)
            or not np.isclose(legs.notional_usdt.sum(), 90.0)
            or audit["exit_fee_class_counts"] != {"taker": 1, "resting_maker": 1}
            or not np.isclose(legs.reconstructed_cash_pnl.sum(), 2.92704, atol=1e-12)):
        raise AssertionError("Synthetic actual child sizing and fee classes do not reconcile")
    windows = ledger._month_windows(legs)
    if not windows:
        raise AssertionError("Frozen funding-ledger phase-aware window adapter returned no windows")
    print("Binance current funding offline adapter mini-check: PASS", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true", help="Build the actual leg join without requests or output files")
    group.add_argument("--mini-check", action="store_true", help="Check child sizing/fee attribution in memory")
    group.add_argument("--collect", action="store_true", help="Explicitly collect funding; requires committed files and owner-scheduled network window")
    group.add_argument("--resolve-missing", action="store_true", help="Fill only the original receipt's missing settlement windows")
    args = parser.parse_args()
    if args.preflight:
        preflight()
    elif args.mini_check:
        mini_check()
    elif args.resolve_missing:
        resolve_missing()
    else:
        collect_current()


if __name__ == "__main__":
    main()
