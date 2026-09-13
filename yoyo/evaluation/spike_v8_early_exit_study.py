"""Receipt-bound V8 dynamic replay for fixed causal early-failure exits.

V8 is rebuilt from authenticated raw contexts: its <=3 ATR admission mask is
injected only into the V7 admission flag.  Raw V6 confirmations remain the
opposite-exit feed.  Each candidate therefore changes an exit, never removes a
raw reverse close or filters a pre-existing V8 trade table.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import subprocess
from time import perf_counter

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_early_failure_exit_study as early
from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation.spike_v8_replay import v8_admissions


EXP = Path("experiments/active/exp-spike-v8-early-exit-20260913-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
HOLDOUT_USAGE = EXP / "holdout_usage.json"
POLICIES = early.POLICIES
SEED = 20260913


def sha256(path: Path) -> str:
    """Return the immutable byte digest used in source and output receipts."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _period(stamps: pd.Series) -> pd.Series:
    """Classify an entry by its fill/availability clock, never bar-open labels."""
    stamps = pd.to_datetime(stamps, utc=True)
    return pd.Series(np.where(stamps < base.SPLIT, "development", "validation_reused_history"), index=stamps.index)


def v8_context(context: base.StreamContext) -> base.StreamContext:
    """Inject V8 permission while deliberately retaining raw V6 signal columns.

    ``replay_policy`` reads the unmodified ``signals`` table for opposite exits.
    Only ``prior_squeeze_run3`` is replaced, the same narrow admission seam used
    by the completed V8 serial replay.
    """
    gates = v8_admissions(context)
    cache = dict(context.cache)
    cache["bb"] = context.cache["bb"].copy()
    cache["bb"]["prior_squeeze_run3"] = gates.v8.reindex(cache["bb"].index).fillna(False).astype(bool)
    return replace(context, cache=cache)


def replay_policy(context: base.StreamContext, *, policy: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay one fixed V8 arm through the audited five-arm early-exit machine."""
    return early.replay_policy(v8_context(context), policy=policy)


def _development_context(context: base.StreamContext) -> base.StreamContext:
    """Return a pre-split prefix while retaining the final eligible next-open fill.

    A signal at bar ``t`` fills at ``t + timeframe``.  Keeping bar opens before
    the split permits every development entry and censors boundary positions,
    without reading any later outcome.
    """
    bars = context.cache["bars"]
    keep = bars.index < base.SPLIT
    cache = {key: (value.loc[keep].copy() if isinstance(value, (pd.Series, pd.DataFrame)) and value.index.equals(bars.index) else value)
             for key, value in context.cache.items()}
    ledger_time = pd.to_datetime(context.signals_ledger.signal_bar_open, utc=True)
    ledger = context.signals_ledger.loc[ledger_time < base.SPLIT].copy()
    return replace(context, cache=cache, signals_ledger=ledger)


def validate_v8_baseline(context: base.StreamContext, trades: pd.DataFrame, replay_root: Path, *, before: pd.Timestamp | None = None) -> None:
    """Require exact closed-trade parity with frozen V8, not the V7 ledger."""
    from pandas.testing import assert_frame_equal

    path = replay_root / "streams" / f"{context.key}.trades.csv.gz"
    old = pd.read_csv(path)
    expected = old.loc[old.arm.eq("v8") & ~old.censored.astype(bool)].copy()
    actual = trades.loc[~trades.censored.astype(bool)].copy()
    if before is not None:
        expected_entry = pd.to_datetime(expected.entry_time, utc=True)
        expected_exit = pd.to_datetime(expected.exit_time, utc=True)
        expected = expected.loc[expected_entry.lt(before) & expected_exit.lt(before)]
    columns = ["signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price", "exit_price", "initial_stop", "initial_risk", "net_return", "net_r"]
    assert_frame_equal(expected.reindex(columns=columns).sort_values("signal_i").reset_index(drop=True),
                       actual.reindex(columns=columns).sort_values("signal_i").reset_index(drop=True),
                       check_dtype=False, rtol=1e-9, atol=1e-9)


def _signal_rows(context: base.StreamContext) -> pd.DataFrame:
    """Record V8 admissions on the same confirmation/entry clock as replay."""
    gates = v8_admissions(context)
    confirm = gates.index + pd.Timedelta(minutes=context.minutes)
    keep = gates.v8 & (confirm >= base.START) & (confirm < base.END)
    rows = gates.loc[keep, ["side", "rope_distance_atr"]].copy()
    rows["signal_bar_open"] = rows.index
    rows["entry_time"] = confirm[keep]
    rows["period"] = _period(rows.entry_time).to_numpy()
    rows["direction"] = np.where(rows.side.eq(1), "long", "short")
    rows["stream_key"] = context.key
    rows["timeframe_min"] = context.minutes
    for key, value in context.identity.items():
        rows[key] = value
    return rows.reset_index(drop=True)


def _drawdown_r(values: pd.Series) -> float:
    """Return closed-trade drawdown from an explicit zero-equity origin."""
    curve = pd.concat([pd.Series([0.0]), values.reset_index(drop=True).cumsum()], ignore_index=True)
    return float((curve.cummax() - curve).max())


def _metrics(trades: pd.DataFrame, signals: pd.DataFrame, *, monthly: bool = False) -> pd.DataFrame:
    """Produce all closed-trade summaries using entry time for split attribution."""
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    if closed.empty:
        return pd.DataFrame()
    closed["period"] = _period(closed.entry_time).to_numpy()
    closed["direction"] = np.where(closed.side.eq(1), "long", "short")
    closed["month"] = pd.to_datetime(closed.entry_time, utc=True).dt.strftime("%Y-%m")
    signal_keys = ["period", "timeframe_min", "direction"] + (["month"] if monthly else [])
    signal_counts = signals.copy()
    signal_counts["month"] = pd.to_datetime(signal_counts.entry_time, utc=True).dt.strftime("%Y-%m")
    count = signal_counts.groupby(signal_keys, dropna=False).size().rename("signals")
    group_keys = ["policy", *signal_keys]
    rows: list[dict[str, object]] = []
    for keys, part in closed.groupby(group_keys, dropna=False):
        policy, *event_keys = keys
        gains = float(part.loc[part.net_r > 0, "net_r"].sum())
        losses = float(-part.loc[part.net_r < 0, "net_r"].sum())
        row = dict(zip(["policy", *signal_keys], [policy, *event_keys]))
        row.update({"signals": int(count.get(tuple(event_keys), 0)), "trades": len(part),
                    "net_r": float(part.net_r.sum()), "win_rate": float((part.net_r > 0).mean()),
                    "profit_factor": gains / losses if losses else math.inf,
                    "mean_loss_r": float(part.loc[part.net_r < 0, "net_r"].mean()) if (part.net_r < 0).any() else math.nan,
                    "closed_trade_max_drawdown_r": _drawdown_r(part.sort_values("exit_time").net_r),
                    "realized_ge_10r_count": int((part.net_r >= 10).sum())})
        rows.append(row)
    return pd.DataFrame(rows)


def _stream_universe(folders: list[Path]) -> pd.DataFrame:
    """Read the fixed 3,531-stream identity pool without loading market outcomes."""
    rows = []
    for folder in folders:
        receipt = pd.read_csv(folder / "receipt.csv").iloc[0]
        rows.append({"stream_key": folder.name, "timeframe_min": int(receipt.minutes), "asset": str(receipt.asset)})
    return pd.DataFrame(rows)


def _stream_accounts(trades: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Simulate every independent 1%-risk stream, including zero-trade streams.

    A nonpositive one-trade account factor terminates that independent account
    at -100%; it is never multiplied through a negative NAV and later allowed
    to appear profitable.
    """
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    closed["period"] = _period(closed.entry_time).to_numpy()
    rows: list[dict[str, object]] = []
    grouped = {(policy, period, stream): part for (policy, period, stream), part
               in closed.groupby(["policy", "period", "stream_key"], dropna=False)}
    for policy in POLICIES:
        for period in ("development", "validation_reused_history"):
            for identity in universe.itertuples(index=False):
                part = grouped.get((policy, period, identity.stream_key))
                values = np.array([], dtype=float) if part is None else part.sort_values("exit_time").net_r.to_numpy(float)
                factors = 1 + .01 * values
                bankrupt = bool((factors <= 0).any())
                account_return = -1.0 if bankrupt else float(np.prod(factors) - 1)
                rows.append({"policy": policy, "period": period, "stream_key": identity.stream_key,
                             "timeframe_min": int(identity.timeframe_min), "asset": identity.asset,
                             "closed_trades": len(values), "net_r": float(values.sum()),
                             "account_return_1pct_risk": account_return,
                             "closed_trade_max_drawdown_r": _drawdown_r(pd.Series(values)),
                             "account_status": "bankrupt_nonpositive_factor" if bankrupt else "active_or_zero_trade",
                             "account_kind": "independent_per_stream_not_shared_or_real"})
    return pd.DataFrame(rows)


def _exact_tail(trades: pd.DataFrame) -> pd.DataFrame:
    """Measure tail preservation only on baseline's exact same-entry winners."""
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    closed["period"] = _period(closed.entry_time).to_numpy()
    base_rows = closed.loc[closed.policy.eq("baseline"), ["stream_key", "signal_i", "side", "period", "net_r"]].rename(columns={"net_r": "baseline_net_r"})
    rows: list[dict[str, object]] = []
    for policy in POLICIES:
        candidate = closed.loc[closed.policy.eq(policy), ["stream_key", "signal_i", "side", "net_r"]].rename(columns={"net_r": "candidate_net_r"})
        paired = base_rows.merge(candidate, on=["stream_key", "signal_i", "side"], how="left", validate="one_to_one")
        winners = paired.loc[paired.baseline_net_r.ge(10)]
        for period in ("development", "validation_reused_history"):
            part = winners.loc[winners.period.eq(period)]
            exact = part.candidate_net_r.notna()
            retained = part.candidate_net_r.ge(10)
            rows.append({"policy": policy, "period": period, "baseline_exact_realized_ge_10r": len(part),
                         "candidate_exact_entries": int(exact.sum()), "exact_realized_ge_10r_retained": int(retained.sum()),
                         "exact_realized_ge_10r_retention": float(retained.mean()) if len(part) else 1.0,
                         "missed_10r": int((~retained).sum())})
    return pd.DataFrame(rows)


def _gates(trades: pd.DataFrame, accounts: pd.DataFrame, tails: pd.DataFrame) -> pd.DataFrame:
    """Apply the predeclared one-variable gate to development rows only."""
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    closed["period"] = _period(closed.entry_time).to_numpy()
    dev = closed.loc[closed.period.eq("development")]
    baseline = dev.loc[dev.policy.eq("baseline")]
    base_account = accounts.loc[(accounts.policy == "baseline") & (accounts.period == "development")]
    base_loss = float(baseline.loc[baseline.net_r < 0, "net_r"].mean())
    base_dd = float(base_account.closed_trade_max_drawdown_r.mean())
    base_return = float(base_account.account_return_1pct_risk.mean())
    rows = []
    for policy in POLICIES:
        part = dev.loc[dev.policy.eq(policy)]
        account = accounts.loc[(accounts.policy == policy) & (accounts.period == "development")]
        tail = tails.loc[(tails.policy == policy) & (tails.period == "development")].iloc[0]
        mean_loss = float(part.loc[part.net_r < 0, "net_r"].mean())
        mean_dd = float(account.closed_trade_max_drawdown_r.mean())
        mean_return = float(account.account_return_1pct_risk.mean())
        improved = mean_loss > base_loss or mean_dd < base_dd
        rows.append({"policy": policy, "development_net_r": float(part.net_r.sum()), "mean_loss_r": mean_loss,
                     "mean_independent_stream_closed_trade_max_drawdown_r": mean_dd,
                     "mean_independent_stream_account_return_1pct_risk": mean_return,
                     "baseline_mean_loss_r": base_loss, "baseline_mean_drawdown_r": base_dd,
                     "baseline_mean_account_return_1pct_risk": base_return,
                     "loss_or_drawdown_improved": improved, "account_not_worse": mean_return >= base_return,
                     "exact_realized_ge_10r_retention": float(tail.exact_realized_ge_10r_retention),
                     "passes_gate": bool(policy != "baseline" and improved and mean_return >= base_return and float(tail.exact_realized_ge_10r_retention) >= .95)})
    return pd.DataFrame(rows)


def _paired_null(trades: pd.DataFrame, policy: str, *, period: str | None, draws: int = 10000) -> dict[str, object]:
    """Use monthly/asset same-entry sign flips; reentry outcomes are excluded."""
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    left = closed.loc[closed.policy.eq("baseline"), ["stream_key", "signal_i", "side", "asset", "entry_time", "net_r"]]
    right = closed.loc[closed.policy.eq(policy), ["stream_key", "signal_i", "side", "net_r"]]
    paired = left.merge(right, on=["stream_key", "signal_i", "side"], suffixes=("_baseline", "_candidate"), validate="one_to_one")
    paired["delta_r"] = paired.net_r_candidate - paired.net_r_baseline
    paired["month"] = pd.to_datetime(paired.entry_time, utc=True).dt.strftime("%Y-%m")
    paired["period"] = _period(paired.entry_time).to_numpy()
    if period is not None:
        paired = paired.loc[paired.period.eq(period)]
    blocks = paired.groupby(["month", "asset"], dropna=False).delta_r.sum().to_numpy(float)
    observed = float(blocks.sum()) if len(blocks) else math.nan
    if not len(blocks):
        exceed, p = 0, math.nan
    else:
        rng = np.random.default_rng(SEED)
        simulated = (rng.choice(np.array([-1.0, 1.0]), size=(draws, len(blocks))) * blocks).sum(axis=1)
        exceed = int((np.abs(simulated) >= abs(observed)).sum())
        p = float((exceed + 1) / (draws + 1))
    return {"policy": policy, "period": period or "all_descriptive", "null": "same-entry month_asset sign_flip; reentry outcomes excluded", "paired_original_entries": len(paired),
            "blocks": len(blocks), "delta_net_r": observed, "draws": draws, "exceedances": exceed, "two_sided_p": p,
            "p_formula": "(exceedances + 1) / (draws + 1)"}


def _write_gzip(path: Path, table: pd.DataFrame) -> None:
    table.to_csv(path, index=False, compression={"method": "gzip", "compresslevel": 1, "mtime": 0})


def _committed(paths: tuple[Path, ...]) -> bool:
    """Require reviewed committed sources before official evidence can materialize."""
    root = Path.cwd().resolve()
    for path in paths:
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return False
        tracked = subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], check=False, capture_output=True).returncode == 0
        clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)], check=False).returncode == 0
        if not tracked or not clean:
            return False
    return True


def _identity(config: dict[str, object]) -> dict[str, str]:
    return {str(path): sha256(path) for path in (Path(__file__), CONFIG, PLAN, HOLDOUT_USAGE,
            Path(str(config["raw"])) / "manifest.json", Path(str(config["v8_replay"])) / "manifest.json")}


def run(output: Path, *, official: bool = False, limit: int | None = None) -> dict[str, object]:
    """Run development selection first, then authorized V8 reused-history scoring."""
    config = json.loads(CONFIG.read_text())
    raw, frozen = Path(str(config["raw"])), Path(str(config["v8_replay"]))
    if sha256(raw / "manifest.json") != str(config["raw_manifest_sha256"]):
        raise ValueError("authenticated raw manifest changed")
    if sha256(frozen / "manifest.json") != str(config["v8_replay_manifest_sha256"]):
        raise ValueError("frozen V8 replay manifest changed")
    if official:
        if not _committed((Path(__file__), CONFIG, PLAN, HOLDOUT_USAGE)):
            raise ValueError("official output requires the exact study source/config/plan/holdout receipt committed to HEAD")
        if EXP not in output.parents:
            raise ValueError("official output must stay under this experiment directory")
    elif EXP in output.parents:
        raise ValueError("experiment results are reserved for a committed official replay; use /tmp for smoke")
    folders = sorted(p for p in (raw / "streams").iterdir() if (p / "completion.json").is_file())
    if len(folders) != int(config["expected_streams"]):
        raise ValueError("unexpected authenticated stream count")
    if limit is not None:
        folders = folders[:limit]
    universe = _stream_universe(folders)
    identity = _identity(config)
    if output.exists():
        if any(output.iterdir()):
            identity_path = output / "identity.json"
            if not identity_path.is_file() or json.loads(identity_path.read_text()) != identity:
                raise ValueError("output identity differs; preserve old results and use a new directory")
        else:
            (output / "identity.json").write_text(json.dumps(identity, indent=2, sort_keys=True))
    else:
        output.mkdir(parents=True)
        (output / "identity.json").write_text(json.dumps(identity, indent=2, sort_keys=True))
    streams_out = output / "streams"; streams_out.mkdir(exist_ok=True)

    # Selection phase loads only prefix contexts; no reused validation outcomes exist in memory here.
    selection_path = output / "selected_rule.json"
    if not selection_path.exists():
        dev_trades, dev_signals = [], []
        for folder in folders:
            context = base.load_verified_stream(folder)
            prefix = _development_context(context)
            baseline, _, _ = replay_policy(prefix, policy="baseline")
            validate_v8_baseline(context, baseline, frozen, before=base.SPLIT)
            for policy in POLICIES:
                trades = baseline if policy == "baseline" else replay_policy(prefix, policy=policy)[0]
                dev_trades.append(trades)
            dev_signals.append(_signal_rows(prefix))
        dev = pd.concat(dev_trades, ignore_index=True) if dev_trades else pd.DataFrame(columns=base.TRADE_COLUMNS)
        signals = pd.concat(dev_signals, ignore_index=True) if dev_signals else pd.DataFrame()
        accounts = _stream_accounts(dev, universe)
        tails = _exact_tail(dev)
        gates = _gates(dev, accounts, tails)
        eligible = gates.loc[gates.passes_gate]
        selected = None if eligible.empty else str(eligible.sort_values(["mean_loss_r", "development_net_r"], ascending=[False, False]).iloc[0].policy)
        selection = {"status": "selected" if selected else "reject_no_candidate_passed", "selected_policy": selected,
                     "decision_clock": "entry_time < 2025-09-10T00:00:00Z; prefix replay censors split-boundary positions",
                     "validation_note": "authorized reused historical data, nonblind; not read before this fixed selection",
                     "gate": config["selection"]}
        selection_path.write_text(json.dumps(selection, indent=2, sort_keys=True))
        gates.to_csv(output / "development_selection.csv", index=False)
        tails.to_csv(output / "development_exact_tail.csv", index=False)
        _write_gzip(output / "development_trades.csv.gz", dev)
        _write_gzip(output / "development_signals.csv.gz", signals)
        _write_gzip(output / "development_independent_stream_accounts.csv.gz", accounts)

    started = perf_counter()
    for number, folder in enumerate(folders, 1):
        receipt_path = streams_out / f"{folder.name}.json"
        table_path = streams_out / f"{folder.name}.trades.csv.gz"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if not table_path.is_file() or sha256(table_path) != receipt.get("trades_sha256"):
                raise ValueError(f"stream receipt mismatch: {folder.name}")
            continue
        context = base.load_verified_stream(folder)
        baseline, _, _ = replay_policy(context, policy="baseline")
        validate_v8_baseline(context, baseline, frozen)
        pieces = []
        for policy in POLICIES:
            trades = baseline if policy == "baseline" else replay_policy(context, policy=policy)[0]
            pieces.append(trades)
        combined = pd.concat(pieces, ignore_index=True)
        _write_gzip(table_path, combined)
        receipt_path.write_text(json.dumps({"stream_key": context.key, "trades_sha256": sha256(table_path),
                                            "cache_sha256": context.receipt["cache_sha256"], "v8_baseline_parity": True}, indent=2))
        if number % 100 == 0 or number == len(folders):
            print(json.dumps({"completed_streams": number, "target": len(folders), "elapsed_seconds": round(perf_counter() - started, 1)}), flush=True)

    tables = [pd.read_csv(streams_out / f"{folder.name}.trades.csv.gz") for folder in folders]
    trades = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=base.TRADE_COLUMNS)
    signal_rows = [_signal_rows(base.load_verified_stream(folder)) for folder in folders]
    signals = pd.concat(signal_rows, ignore_index=True) if signal_rows else pd.DataFrame()
    accounts = _stream_accounts(trades, universe)
    tails = _exact_tail(trades)
    metrics = _metrics(trades, signals)
    monthly = _metrics(trades, signals, monthly=True)
    nulls = pd.DataFrame([_paired_null(trades, policy, period=period)
                          for policy in POLICIES if policy != "baseline"
                          for period in ("development", "validation_reused_history", None)])
    _write_gzip(output / "trades.csv.gz", trades)
    _write_gzip(output / "independent_stream_accounts.csv.gz", accounts)
    metrics.to_csv(output / "metrics_by_period_timeframe_direction.csv", index=False)
    monthly.to_csv(output / "metrics_by_month_timeframe_direction.csv", index=False)
    tails.to_csv(output / "exact_tail_retention.csv", index=False)
    nulls.to_csv(output / "same_entry_month_asset_signflip.csv", index=False)
    manifest = {"complete": limit is None, "official": official, "streams": len(folders), "source": identity,
                "baseline": "completed V8 arm=v8 frozen-replay parity", "admission": config["admission"],
                "execution_contract": config["execution_contract"], "policies": list(POLICIES),
                "selection": json.loads(selection_path.read_text()), "validation": "authorized reused nonblind history; holdout-era use #1",
                "random_controls": config["random_control_reuse"], "elapsed_seconds": perf_counter() - started}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    receipt = {"complete": limit is None, "manifest_sha256": sha256(output / "manifest.json"),
               "outputs": {path.name: sha256(path) for path in sorted(output.iterdir()) if path.is_file()}}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return {"streams": len(folders), "selected_policy": manifest["selection"]["selected_policy"], "elapsed_seconds": manifest["elapsed_seconds"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.output, official=args.official, limit=args.limit), sort_keys=True))


if __name__ == "__main__":
    main()
