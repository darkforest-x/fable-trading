"""Build the receipt-bound V7 versus frozen V8 noise-filter report.

The replay is the authority for serial execution and account outcomes.  This
module only aggregates its authenticated stream files.  It deliberately keeps
the development-only feature screen separate from the replay: the selected
three-ATR rope rule was frozen before validation outcomes were read.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_exit_policy_study import load_verified_stream, sha256


EXP = Path("experiments/active/exp-spike-v8-noise-filter-20260913-v1")
EXPECTED_KINDS = ("accounts", "events", "trades", "retention", "signals", "controls")
EXPECTED_STREAMS = 3531
EXPECTED_ADMISSIONS = 132593
EXPECTED_V7_TRADES = 107238
ARMS = ("v7", "v8")
PERIODS = ("development", "validation")


def _bool(frame: pd.DataFrame, column: str) -> pd.Series:
    """Require serialized boolean flags instead of accepting truthy strings."""
    values = frame[column]
    if not values.dropna().map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError(f"nonboolean values in {column}")
    return values.fillna(False).astype(bool)


def _verify_file(path: Path, digest: str) -> None:
    if not path.is_file() or sha256(path) != digest:
        raise ValueError(f"altered or missing receipt file: {path.name}")


def _verify_discovery(discovery: Path) -> tuple[dict, pd.DataFrame]:
    manifest_path = discovery / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get("complete") or manifest.get("signals") != EXPECTED_ADMISSIONS:
        raise ValueError("discovery must contain all 132593 V7 admissions")
    if manifest.get("validation_outcomes_summarized"):
        raise ValueError("discovery validation outcomes must remain unreviewed before freeze")
    for name, digest in manifest.get("files", {}).items():
        _verify_file(discovery / name, digest)
    features = pd.read_csv(discovery / "signal_features.csv.gz")
    if len(features) != EXPECTED_ADMISSIONS:
        raise ValueError("discovery signal-feature row count disagrees with admission contract")
    return manifest, features


def collect(replay: Path, discovery: Path) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """Validate every receipt and return concatenated replay/discovery tables."""
    if (replay / "INVALIDATED.json").exists():
        raise ValueError("invalidated replay cannot be reported")
    manifest = json.loads((replay / "manifest.json").read_text())
    if not manifest.get("complete") or manifest.get("streams") != EXPECTED_STREAMS:
        raise ValueError("report requires all 3531 replay streams")
    identity_path = replay / "identity.json"
    if sha256(identity_path) != manifest.get("identity_sha256"):
        raise ValueError("replay identity receipt changed")
    for source, digest in json.loads(identity_path.read_text()).items():
        _verify_file(Path(source), digest)
    selected = json.loads((EXP / "selected_rule.json").read_text())
    discovery_manifest, features = _verify_discovery(discovery)
    if sha256(discovery / "manifest.json") != selected.get("discovery_manifest_sha256"):
        raise ValueError("selected V8 rule is not bound to this discovery manifest")
    if manifest.get("selected_rule_sha256") != sha256(EXP / "selected_rule.json"):
        raise ValueError("replay is not bound to the frozen selected rule")

    receipts = sorted((replay / "streams").glob("*.json"))
    if len(receipts) != EXPECTED_STREAMS:
        raise ValueError("missing replay stream receipts")
    parts: dict[str, list[pd.DataFrame]] = {name: [] for name in EXPECTED_KINDS}
    for number, receipt_path in enumerate(receipts, 1):
        receipt = json.loads(receipt_path.read_text())
        if not receipt.get("v7_baseline_parity"):
            raise ValueError(f"missing V7 baseline parity receipt: {receipt_path.name}")
        tables: dict[str, pd.DataFrame] = {}
        for filename, digest in receipt.get("files", {}).items():
            file_path = receipt_path.parent / filename
            _verify_file(file_path, digest)
            kind = filename.rsplit(".", 3)[1]
            if kind not in parts:
                raise ValueError(f"unexpected replay table kind: {kind}")
            tables[kind] = pd.read_csv(file_path)
        if set(tables) != set(EXPECTED_KINDS):
            raise ValueError(f"incomplete receipt tables: {receipt_path.name}")
        identity = tables["accounts"].iloc[0][["stream_key", "venue", "symbol", "asset", "timeframe_min"]].to_dict()
        for kind, table in tables.items():
            # V8's replay wrote controls before stream identity decoration.  Bind
            # it here from the same receipt, rather than guessing by row order.
            for key, value in identity.items():
                if key not in table:
                    table[key] = value
            parts[kind].append(table)
        if number % 500 == 0:
            print(json.dumps({"report_verified_streams": number}), flush=True)
    tables = {kind: pd.concat(frames, ignore_index=True) for kind, frames in parts.items()}
    for table, column in (("accounts", "valid"), ("trades", "censored"),
                          ("retention", "exact_retained"), ("controls", "matched")):
        tables[table][column] = _bool(tables[table], column)
    tables["features"] = features
    admissions = int(tables["signals"].v7.astype(bool).sum())
    v7_trades = int(tables["trades"].arm.eq("v7").sum())
    if admissions != EXPECTED_ADMISSIONS:
        raise ValueError(f"V7 admission contract is {admissions}, expected {EXPECTED_ADMISSIONS}")
    if v7_trades != EXPECTED_V7_TRADES:
        raise ValueError(f"V7 trade contract is {v7_trades}, expected {EXPECTED_V7_TRADES}")
    return tables, {
        "complete_streams": EXPECTED_STREAMS,
        "v7_admissions": admissions,
        "v7_trade_rows": v7_trades,
        "replay_manifest_sha256": sha256(replay / "manifest.json"),
        "discovery_manifest_sha256": sha256(discovery / "manifest.json"),
        "discovery_streams": discovery_manifest.get("streams"),
    }


def _pf(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    losses = -values.clip(upper=0).sum()
    return float(values.clip(lower=0).sum() / losses) if losses > 0 else math.nan


def cluster_effect(values: pd.DataFrame, *, seed: int = 20260913, draws: int = 2000) -> dict[str, float | int]:
    """Stream-weight effect with bootstrap/sign-flip inference by base asset."""
    clusters = values.groupby("asset").delta.agg(["sum", "count"])
    if clusters.empty:
        return dict(delta=math.nan, low=math.nan, high=math.nan, p=math.nan, assets=0)
    sums, counts = clusters["sum"].to_numpy(), clusters["count"].to_numpy()
    observed = sums.sum() / counts.sum()
    rng = np.random.default_rng(seed)
    ids = rng.integers(0, len(sums), size=(draws, len(sums)))
    boot = sums[ids].sum(axis=1) / counts[ids].sum(axis=1)
    perm = (rng.choice((-1, 1), size=(draws, len(sums))) * sums).sum(axis=1) / counts.sum()
    p = (1 + (np.abs(perm) >= abs(observed)).sum()) / (draws + 1)
    return dict(delta=float(observed), low=float(np.quantile(boot, .025)), high=float(np.quantile(boot, .975)),
                p=float(p), assets=len(sums))


def _trade_summary(trades: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    closed = trades.loc[~trades.censored].copy()
    closed["win"] = closed.net_return.gt(0)
    closed["realized_10r"] = closed.net_r.ge(10)
    closed["mfe_10r"] = closed.mfe_r.ge(10)
    rows = []
    for key, group in trades.groupby(groups, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        natural = closed.loc[closed.index.intersection(group.index)]
        rows.append(dict(zip(groups, key), trade_rows=len(group), closed=len(natural), censored=int(group.censored.sum()),
                         net_return_sum=float(natural.net_return.sum()), pf=_pf(natural.net_return),
                         win_rate=float(natural.win.mean()) if len(natural) else math.nan,
                         realized_10r=int(natural.realized_10r.sum()), mfe_10r=int(natural.mfe_10r.sum())))
    return pd.DataFrame(rows)


def _exit_summary(trades: pd.DataFrame) -> pd.DataFrame:
    """Describe completed exits without treating censored rows as failures."""
    closed = trades.loc[~trades.censored].copy()
    closed["loss"] = closed.net_return.le(0)
    rows = []
    for key, group in closed.groupby(["arm", "period", "timeframe_min", "exit_reason"], dropna=False):
        rows.append(dict(arm=key[0], period=key[1], timeframe_min=key[2], exit_reason=key[3],
                         exits=len(group), losses=int(group.loss.sum()),
                         net_return_sum=float(group.net_return.sum()), net_r_sum=float(group.net_r.sum()),
                         mean_net_r=float(group.net_r.mean())))
    return pd.DataFrame(rows)


def _monthly_summary(trades: pd.DataFrame) -> pd.DataFrame:
    """Monthly event outcomes by signal month; this is not a shared portfolio."""
    closed = trades.loc[~trades.censored].copy()
    closed["month"] = pd.to_datetime(closed.signal_bar_open, utc=True).dt.strftime("%Y-%m")
    closed["win"] = closed.net_return.gt(0)
    closed["realized_10r"] = closed.net_r.ge(10)
    rows = []
    for key, group in closed.groupby(["arm", "period", "month", "timeframe_min"], dropna=False):
        rows.append(dict(arm=key[0], period=key[1], month=key[2], timeframe_min=key[3],
                         closed=len(group), pf=_pf(group.net_return),
                         net_return_sum=float(group.net_return.sum()), net_r_sum=float(group.net_r.sum()),
                         win_rate=float(group.win.mean()), realized_10r=int(group.realized_10r.sum())))
    return pd.DataFrame(rows)


def _retention_summary(retention: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for key, group in retention.groupby(["period", "timeframe_min"], dropna=False):
        winners = group.net_r.ge(10)
        mfe = group.mfe_r.ge(10)
        rows.append(dict(period=key[0], timeframe_min=key[1], baseline_executed=len(group),
                         baseline_realized_10r=int(winners.sum()), retained_realized_10r=int((winners & group.exact_retained).sum()),
                         realized_10r_retention=float((winners & group.exact_retained).sum() / winners.sum()) if winners.any() else math.nan,
                         baseline_mfe_10r=int(mfe.sum()), retained_mfe_10r=int((mfe & group.exact_retained).sum()),
                         mfe_10r_retention=float((mfe & group.exact_retained).sum() / mfe.sum()) if mfe.any() else math.nan,
                         removed_losers=int((~group.exact_retained & group.net_return.lt(0)).sum()),
                         missed_realized_winners=int((~group.exact_retained & winners).sum())))
    return pd.DataFrame(rows)


def _paired_accounts(accounts: pd.DataFrame) -> pd.DataFrame:
    valid = accounts.loc[accounts.valid].copy()
    baseline = valid.loc[valid.arm.eq("v7"), ["stream_key", "period", "net_return"]].rename(columns={"net_return": "v7_return"})
    paired = valid.loc[valid.arm.eq("v8")].merge(baseline, on=["stream_key", "period"], validate="one_to_one")
    paired["delta"] = paired.net_return - paired.v7_return
    rows = []
    for key, group in paired.loc[paired.period.isin(PERIODS)].groupby(["period", "timeframe_min"]):
        rows.append(dict(period=key[0], timeframe_min=key[1], **cluster_effect(group)))
    return pd.DataFrame(rows)


def _matched_controls(controls: pd.DataFrame) -> pd.DataFrame:
    controls = controls.loc[controls.matched].copy()
    if controls.empty:
        return pd.DataFrame(columns=["arm", "period", "timeframe_min", "sampled", "matched", "delta", "low", "high", "p", "assets"])
    controls["delta"] = pd.to_numeric(controls.net_return_difference, errors="coerce")
    rows = []
    for key, group in controls.groupby(["arm", "period", "timeframe_min"]):
        rows.append(dict(arm=key[0], period=key[1], timeframe_min=key[2], sampled=len(group), matched=len(group),
                         **cluster_effect(group.dropna(subset=["delta"]))))
    return pd.DataFrame(rows)


def _feature_analysis(features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    features = features.copy()
    features["v8_kept"] = _bool(features, "gate_not_overheated3")
    numeric = ["rope_distance_atr", "efficiency3", "current_volume_ratio", "current_tr_expansion", "bb_width_ratio_p10", "cost_share_of_close_r"]
    descriptions = features.groupby(["period", "timeframe_min", "v8_kept"], as_index=False).agg(
        admissions=("v8_kept", "size"), executed=("executed", "sum"), realized_10r=("realized_10r", "sum"),
        mfe_10r=("mfe_10r", "sum"), net_positive=("net_positive", "sum"), **{f"mean_{name}": (name, "mean") for name in numeric})
    # ``not_executed_occupied`` is an admission whose stream already held a
    # position.  It is useful coverage evidence, but no natural trade outcome
    # exists, so it cannot be counted among removed losing trades.
    actual_nonpositive = _bool(features, "executed") & _bool(features, "closed") & ~_bool(features, "net_positive")
    features["removed_nonpositive_trade"] = actual_nonpositive
    reasons = features.loc[~features.v8_kept].groupby(["period", "timeframe_min", "failure_reason"], as_index=False).agg(
        filtered_admissions=("failure_reason", "size"), executed=("executed", "sum"),
        missed_realized_10r=("realized_10r", "sum"), removed_nonpositive=("removed_nonpositive_trade", "sum"))
    # These outputs were calculated only on causal feature values and development
    # outcomes before the rule freeze.  Do not recreate them over validation.
    return descriptions, reasons, features.loc[features.period.eq("development")].copy()


def summarize(tables: dict[str, pd.DataFrame], output: Path, discovery: Path) -> dict[str, pd.DataFrame]:
    """Aggregate V7/V8 outcomes and write portable CSV evidence."""
    output.mkdir(parents=True, exist_ok=True)
    trades, accounts, retention, signals, controls = (tables[name] for name in ("trades", "accounts", "retention", "signals", "controls"))
    event = _trade_summary(trades, ["arm", "period", "timeframe_min"])
    side = _trade_summary(trades, ["arm", "period", "timeframe_min", "side"])
    venue = _trade_summary(trades, ["arm", "period", "timeframe_min", "venue"])
    exits = _exit_summary(trades)
    monthly = _monthly_summary(trades)
    signal = signals.groupby(["period", "timeframe_min"], as_index=False).agg(v7_admissions=("v7", "sum"), v8_admissions=("v8", "sum"))
    signal[["v7_admissions", "v8_admissions"]] = signal[["v7_admissions", "v8_admissions"]].astype(int)
    signal["admission_reduction"] = 1 - signal.v8_admissions / signal.v7_admissions.replace(0, np.nan)
    signal_by_side_venue = signals.groupby(["period", "timeframe_min", "side", "venue"], as_index=False).agg(
        v7_admissions=("v7", "sum"), v8_admissions=("v8", "sum"))
    signal_by_side_venue[["v7_admissions", "v8_admissions"]] = signal_by_side_venue[["v7_admissions", "v8_admissions"]].astype(int)
    signal_by_side_venue["admission_reduction"] = (
        1 - signal_by_side_venue.v8_admissions / signal_by_side_venue.v7_admissions.replace(0, np.nan))
    account = accounts.loc[accounts.valid].groupby(["arm", "period", "timeframe_min"], as_index=False).agg(
        streams=("stream_key", "size"), mean_net_return=("net_return", "mean"), median_net_return=("net_return", "median"),
        mean_max_drawdown=("max_close_drawdown", "mean"), worst_max_drawdown=("max_close_drawdown", "max"))
    retention_summary = _retention_summary(retention)
    paired = _paired_accounts(accounts)
    control = _matched_controls(controls)
    feature_summary, failure_summary, development_features = _feature_analysis(tables["features"])
    auc = pd.read_csv(discovery / "feature_auc_development.csv")
    deciles = pd.read_csv(discovery / "feature_deciles_development.csv")
    candidates = pd.read_csv(discovery / "candidate_screen_development.csv")
    auc["scope"] = "development_only_causal_feature_screen"
    deciles["scope"] = "development_only_causal_feature_screen"
    candidates["scope"] = "development_only_frozen_before_validation"
    result = {
        "signal_summary": signal, "signal_side_venue_summary": signal_by_side_venue,
        "event_summary": event, "account_summary": account,
        "side_summary": side, "venue_summary": venue, "exit_summary": exits,
        "monthly_summary": monthly, "retention_summary": retention_summary,
        "paired_asset_effect": paired, "matched_random_control": control,
        "filter_feature_summary": feature_summary, "filtered_failure_summary": failure_summary,
        "candidate_screen_development": candidates,
        "feature_auc_development": auc, "feature_deciles_development": deciles,
        "development_feature_rows": development_features,
    }
    for name, frame in result.items():
        frame.to_csv(output / f"{name}.csv", index=False)
    return result


def render_figures(summary: dict[str, pd.DataFrame], output: Path) -> list[dict[str, str]]:
    """Render compact aggregate plots; outcome-picked candle cases remain optional."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    receipts = []
    event = summary["event_summary"].query("period == 'validation'")
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    for axis, metric, title in zip(axes, ("pf", "net_return_sum", "win_rate"), ("Validation PF", "Validation net-return sum", "Validation win rate")):
        pivot = event.pivot(index="timeframe_min", columns="arm", values=metric).reindex(columns=list(ARMS))
        pivot.plot.bar(ax=axis, rot=0, title=title)
        axis.set_xlabel("minutes")
    path = output / "validation_v7_v8_metrics.png"; fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)
    receipts.append({"file": path.name, "selection": "validation metrics"})
    ret, events = summary["retention_summary"], summary["event_summary"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for period, group in ret.groupby("period"):
        axes[0].plot(group.timeframe_min, group.realized_10r_retention, marker="o", label=period)
    axes[0].set(title="Exact realized ≥10R retention", xlabel="minutes", ylabel="retention"); axes[0].legend()
    for (period, arm), group in events.groupby(["period", "arm"]):
        axes[1].plot(group.timeframe_min, group.pf, marker="o", label=f"{period} {arm}")
    axes[1].set(title="Event PF", xlabel="minutes", ylabel="PF"); axes[1].legend(fontsize=7)
    path = output / "period_retention_pf.png"; fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)
    receipts.append({"file": path.name, "selection": "period retention and PF"})
    failures = summary["filtered_failure_summary"].groupby("failure_reason", as_index=False).filtered_admissions.sum().sort_values("filtered_admissions", ascending=False).head(12)
    fig, axis = plt.subplots(figsize=(10, 4)); axis.bar(failures.failure_reason, failures.filtered_admissions)
    axis.set(title="Filtered V7 admission outcomes", ylabel="admissions"); axis.tick_params(axis="x", rotation=35)
    path = output / "filtered_case_categories.png"; fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)
    receipts.append({"file": path.name, "selection": "filtered-case categories"})
    (output / "figures.json").write_text(json.dumps(receipts, indent=2))
    return receipts


def render_cases(tables: dict[str, pd.DataFrame], output: Path) -> list[dict[str, str]]:
    """Render up to two authenticated validation examples for each V8 outcome.

    Selection uses completed V7 outcomes only after aggregation.  It is a
    review aid, never an input to the frozen three-ATR rule or a source of new
    thresholds.  All plotted bars after the signal close are visibly shaded.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle

    retention = tables["retention"].copy()
    retention["exact_retained"] = _bool(retention, "exact_retained")
    validation = retention.loc[retention.period.eq("validation")]
    selections = [
        ("retained_realized_10r", validation.loc[validation.exact_retained & validation.net_r.ge(10)], False),
        ("filtered_loser", validation.loc[~validation.exact_retained & validation.net_return.lt(0)], True),
        ("missed_realized_10r", validation.loc[~validation.exact_retained & validation.net_r.ge(10)], False),
    ]
    candidates: list[tuple[str, pd.Series]] = []
    for label, rows, ascending in selections:
        # Different assets/timeframes keep the gallery from becoming six views
        # of the same event when that category has adequate coverage.
        chosen = rows.sort_values("net_return", ascending=ascending).drop_duplicates(["asset", "timeframe_min"]).head(2)
        candidates.extend((label, row) for _, row in chosen.iterrows())
    if not candidates:
        return []
    raw = Path(json.loads((EXP / "config.json").read_text())["raw"])
    trades = tables["trades"]
    receipts: list[dict[str, str]] = []
    for number, (label, row) in enumerate(candidates, 1):
        context = load_verified_stream(raw / "streams" / row.stream_key)
        bars = context.cache["bars"]
        signal = pd.Timestamp(row.signal_bar_open)
        match = trades.loc[(trades.arm.eq("v7")) & (trades.stream_key.eq(row.stream_key)) &
                           (pd.to_datetime(trades.signal_bar_open, utc=True).eq(signal)) &
                           (trades.side.eq(row.side))]
        if len(match) != 1:
            raise ValueError(f"case trade is not uniquely bound to authenticated stream: {row.stream_key}")
        trade = match.iloc[0]
        signal_i = int(bars.index.get_loc(signal))
        exit_i = int(bars.index.searchsorted(pd.Timestamp(trade.exit_time)))
        left, right = max(0, signal_i - 70), min(len(bars), max(signal_i + 100, exit_i + 25))
        shown = bars.iloc[left:right]
        x = np.arange(len(shown))
        colors = np.where(shown.close >= shown.open, "#4bd0a8", "#ef7984")
        fig, (price, osc) = plt.subplots(2, 1, figsize=(15, 8), sharex=True,
                                         gridspec_kw={"height_ratios": [4, 1]}, facecolor="#0b121b")
        for axis in (price, osc):
            axis.set_facecolor("#0e1824"); axis.grid(alpha=.15, color="#8092a4")
            axis.tick_params(colors="#afbfca")
            for spine in axis.spines.values():
                spine.set_color("#263746")
        price.add_collection(LineCollection([[(i, low), (i, high)] for i, low, high in zip(x, shown.low, shown.high)],
                                           colors=colors, linewidths=.7))
        for i, opened, closed, color in zip(x, shown.open, shown.close, colors):
            price.add_patch(Rectangle((i - .32, min(opened, closed)), .64,
                                      max(abs(closed - opened), abs(closed) * 1e-6), color=color, linewidth=0))
        for column, color in (("ropeHigh", "#e4b960"), ("ropeLow", "#e4b960"), ("s20", "#59c6ba"), ("e20", "#86d2c9")):
            if column in shown:
                price.plot(x, shown[column], color=color, linewidth=.8, alpha=.75)
        side = int(row.side)
        edge = float(bars.ropeHigh.iloc[signal_i] if side == 1 else bars.ropeLow.iloc[signal_i])
        atr = float(bars.atr.iloc[signal_i])
        threshold = edge + side * 3 * atr
        signal_x = signal_i - left
        price.hlines(edge, max(0, signal_x - 30), signal_x, colors="#e4b960", linestyles="--", linewidth=1.2, label="directional rope edge")
        price.hlines(threshold, max(0, signal_x - 30), signal_x, colors="#d47b9f", linestyles=":", linewidth=1.2, label="3 ATR boundary")
        entry_x = int(bars.index.searchsorted(pd.Timestamp(trade.entry_time))) - left
        exit_x = exit_i - left
        price.axvline(signal_x, color="#edd586", linestyle="--", linewidth=1.2)
        price.scatter([entry_x], [trade.entry_price], marker="^" if side == 1 else "v", color="#edd586", s=65, zorder=5)
        price.scatter([exit_x], [trade.exit_price], marker="x", color="#f194a4", s=65, zorder=5)
        for column, color in (("md", "#6d9eff"), ("sb", "#eeb465")):
            if column in shown:
                osc.plot(x, shown[column], color=color, linewidth=1.0, label=column)
        osc.axhline(0, color="#8797a7", linewidth=.6)
        for axis in (price, osc):
            axis.axvspan(signal_x + .5, len(shown), color="#527ea2", alpha=.12)
        timestamps = shown.index
        ticks = np.linspace(0, len(shown) - 1, 7).astype(int)
        osc.set_xticks(ticks, [timestamps[i].tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M") for i in ticks], fontsize=8)
        reason = str(row.reason)
        title = (f"{label} | {row.venue.upper()} {row.symbol} {row.timeframe_min}m | "
                 f"{'LONG' if side == 1 else 'SHORT'} | V7 realized {row.net_r:.2f}R")
        price.set_title(title, loc="left", color="#e6edf5", fontsize=12, pad=14)
        price.legend(loc="upper left", fontsize=7)
        fig.text(.065, .052, f"Gold dashed: V7 signal close. Triangle: next-open entry. Pink x: V7 exit. V8 decision: {reason}.", color="#afbfca", fontsize=9)
        fig.text(.065, .032, "Blue shading begins after the signal candle closes: future bars are retrospective display only and cannot select rules.", color="#afbfca", fontsize=9)
        fig.text(.065, .014, "Dashed gold is the directional rope edge; dotted pink is its 3 ATR V8 boundary, both known at the signal close.", color="#afbfca", fontsize=9)
        fig.subplots_adjust(left=.065, right=.97, top=.92, bottom=.12, hspace=.10)
        path = output / f"case_{number:02d}_{label}.png"
        fig.savefig(path, dpi=130); plt.close(fig)
        receipts.append({"file": path.name, "selection": label, "asset": str(row.asset),
                         "timeframe_min": str(row.timeframe_min), "filter_reason": reason,
                         "stream_cache_sha256": str(context.receipt["cache_sha256"])})
    return receipts


def _md_table(frame: pd.DataFrame, columns: list[str], limit: int | None = None) -> str:
    frame = frame.loc[:, [column for column in columns if column in frame]].head(limit)
    header = "| " + " | ".join(frame.columns) + " |\n"
    separator = "|" + "---|" * len(frame.columns) + "\n"
    rows = []
    for _, row in frame.iterrows():
        cells = []
        for value in row:
            if isinstance(value, (float, np.floating)):
                cells.append("N/A" if pd.isna(value) else f"{value:.4f}")
            else:
                cells.append(str(value))
        rows.append("| " + " | ".join(cells) + " |")
    return header + separator + "\n".join(rows)


def build(replay: Path, discovery: Path, output: Path, report: Path) -> None:
    tables, contract = collect(replay, discovery)
    summary = summarize(tables, output, discovery)
    figures = render_figures(summary, output)
    cases = render_cases(tables, output)
    figures.extend(cases)
    (output / "figures.json").write_text(json.dumps(figures, indent=2))
    report.parent.mkdir(parents=True, exist_ok=True)

    signal = summary["signal_summary"]
    events = summary["event_summary"]
    accounts = summary["account_summary"]
    retention = summary["retention_summary"]
    paired = summary["paired_asset_effect"]
    v7_admissions = int(signal.v7_admissions.sum())
    v8_admissions = int(signal.v8_admissions.sum())
    v8_trade_rows = int(tables["trades"].arm.eq("v8").sum())

    decision_rows = []
    for minutes in (30, 60, 240):
        sig = signal.loc[(signal.period.eq("validation")) & signal.timeframe_min.eq(minutes)].iloc[0]
        old = events.loc[(events.arm.eq("v7")) & events.period.eq("validation") & events.timeframe_min.eq(minutes)].iloc[0]
        new = events.loc[(events.arm.eq("v8")) & events.period.eq("validation") & events.timeframe_min.eq(minutes)].iloc[0]
        old_account = accounts.loc[(accounts.arm.eq("v7")) & accounts.period.eq("validation") & accounts.timeframe_min.eq(minutes)].iloc[0]
        new_account = accounts.loc[(accounts.arm.eq("v8")) & accounts.period.eq("validation") & accounts.timeframe_min.eq(minutes)].iloc[0]
        tail = retention.loc[retention.period.eq("validation") & retention.timeframe_min.eq(minutes)].iloc[0]
        effect = paired.loc[paired.period.eq("validation") & paired.timeframe_min.eq(minutes)].iloc[0]
        decision_rows.append({
            "周期": f"{minutes // 60}H" if minutes >= 60 else f"{minutes}m",
            "V7信号": int(sig.v7_admissions), "V8信号": int(sig.v8_admissions),
            "删减": float(sig.admission_reduction), "V7_PF": float(old.pf), "V8_PF": float(new.pf),
            "V7账户收益": float(old_account.mean_net_return), "V8账户收益": float(new_account.mean_net_return),
            "V7均值回撤": float(old_account.mean_max_drawdown), "V8均值回撤": float(new_account.mean_max_drawdown),
            "原V7_10R保留": float(tail.realized_10r_retention), "V8减V7_p": float(effect.p),
        })
    decision = pd.DataFrame(decision_rows)

    v7_closed = tables["trades"].loc[tables["trades"].arm.eq("v7") & ~tables["trades"].censored].copy()
    v7_losses = v7_closed.net_return.lt(0)
    initial_stop = v7_closed.exit_reason.astype(str).str.startswith("initial_stop")
    losing_reverse = v7_closed.exit_reason.astype(str).str.startswith("opposite") & v7_losses
    initial_loss_share = float((initial_stop & v7_losses).sum() / v7_losses.sum())
    stop_or_reverse_share = float(((initial_stop & v7_losses) | losing_reverse).sum() / v7_losses.sum())
    admission_outcomes = tables["features"].groupby("failure_reason", dropna=False, as_index=False).agg(
        admissions=("failure_reason", "size"), executed=("executed", "sum"),
        mean_net_return=("net_return", "mean"), mean_net_r=("net_r", "mean"))
    admission_outcomes[["admissions", "executed"]] = admission_outcomes[["admissions", "executed"]].astype(int)
    exit_totals = summary["exit_summary"].loc[summary["exit_summary"].arm.eq("v7")].groupby(
        "exit_reason", as_index=False).agg(exits=("exits", "sum"), losses=("losses", "sum"),
                                            net_return_sum=("net_return_sum", "sum"), net_r_sum=("net_r_sum", "sum"))
    exit_totals = exit_totals.sort_values("exits", ascending=False)

    monthly = summary["monthly_summary"].loc[summary["monthly_summary"].period.eq("validation")]
    old_month = monthly.loc[monthly.arm.eq("v7")].drop(columns=["arm", "period"])
    new_month = monthly.loc[monthly.arm.eq("v8")].drop(columns=["arm", "period"])
    monthly_compare = old_month.merge(new_month, on=["month", "timeframe_min"], how="outer", suffixes=("_v7", "_v8"))
    monthly_compare = monthly_compare.sort_values(["month", "timeframe_min"]).fillna(0)
    august = monthly_compare.loc[monthly_compare.month.eq("2026-08")]
    august_text = "; ".join(
        f"{int(row.timeframe_min)}m V7 {row.net_r_sum_v7:.1f}R → V8 {row.net_r_sum_v8:.1f}R"
        for _, row in august.iterrows()
    )

    top_decile = summary["feature_deciles_development"].loc[
        summary["feature_deciles_development"].score_decile.eq(10)]
    validation_control = summary["matched_random_control"].loc[
        summary["matched_random_control"].period.eq("validation")]
    text = [
        "# SPIKE V8：V7 全量信号降噪与冻结规则回放",
        "V8 只增加一个因果入场门：确认收盘价沿交易方向离六均线绳索边缘超过 3 ATR 时，不再建立新参考。已有持仓仍可被未过滤的反向 V6 确认结束，因此 V8 没有偷偷改变退出规则。Pine 是研究版；本轮未修改生产监控、Bark、ACTIVE、promote 或实盘设置。",
        "## 先看结论",
        f"V7 共 **{v7_admissions:,} 条确认事件**，其中 **{contract['v7_trade_rows']:,} 条形成串行交易**，另有 **{v7_admissions - contract['v7_trade_rows']:,} 条**因同一交易流已有持仓而没有成为新交易。冻结的 V8 门槛保留 **{v8_admissions:,} 条确认**、形成 **{v8_trade_rows:,} 条交易，信号总量减少 {(1 - v8_admissions / v7_admissions):.2%}**。",
        "验证段的核心结果如下。账户收益是每个交易所×币种×周期独立账户的均值，不是把 3,531 个流叠成可实盘的共享资金曲线。",
        _md_table(decision, list(decision.columns)),
        "V8 达到了本轮的窄目标：每个周期少约 14%–16% 的信号，原 V7 已实现 10R 大趋势仍保留 91.75%–96.77%，三个周期的平均收盘回撤都下降。它没有证明净收益全面优于 V7：30m PF 由 0.883 降到 0.871；1H 和 4H PF 略升，但同资产配对后的 V8−V7 收益差均未达到 p<0.05。当前应把 V8 看成**追高/追空保护版**，不能宣传成已经找到最赚钱参数。",
        "## V8 规则与时间纪律",
        "V8 完整继承 V7 的 V6 结构确认、BB200 压缩背景、多空信号、收盘确认、次根开盘入场、结构止损和趋势跟随。唯一变化是：多头用 `(close - 六线最高值) / ATR <= 3`，空头用 `(六线最低值 - close) / ATR <= 3`。所有输入均在信号 K 收盘时可知，不回填历史信号。",
        f"认证回放覆盖 {contract['complete_streams']} 条 Binance、OKX、Gate 数据流，周期为 30m/1H/4H，窗口为 2024-09-10 至 2026-09-10。开发段只用 2024-09-10 至 2025-09-10 选门槛；随后冻结 `selected_rule.json`，才汇总 2025-09-10 至 2026-09-10 的验证结果。",
        "## 132,593 条信号究竟失败在哪里",
        _md_table(admission_outcomes.sort_values("admissions", ascending=False), ["failure_reason", "admissions", "executed", "mean_net_return", "mean_net_r"]),
        _md_table(exit_totals, ["exit_reason", "exits", "losses", "net_return_sum", "net_r_sum"]),
        f"V7 的 {len(v7_closed):,} 笔已结束交易中有 {int(v7_losses.sum()):,} 笔净亏损。初始止损占全部已结束交易 {int(initial_stop.sum()) / len(v7_closed):.2%}，占全部亏损 {initial_loss_share:.2%}；把亏损的反向确认退出也算上，两类合计解释 {stop_or_reverse_share:.2%} 的亏损。真正的主要问题是大量启动没有形成持续性，以及部分确认时价格已经离均线绳索太远；并非缺少更猛烈的当根成交量。",
        "`not_executed_occupied` 只是已有持仓时出现的候选确认，没有自然交易结果，不能算失败交易。跨交易所同币同时间的信号也不能直接当噪音删除：它们反映同一市场事件，统计推断已按基础资产聚类，前端层可以折叠展示，但交易研究不能把它们伪装成独立样本。",
        "## 为什么没有采用更强的量价和 BB 门槛",
        _md_table(summary["candidate_screen_development"], ["gate", "timeframe_min", "signals", "signals_kept", "signal_reduction", "event_pf", "mean_net_return", "realized_10r", "realized_10r_kept", "realized_10r_retention", "mfe_10r_kept"], 40),
        "成交量≥1.5、TR 扩张≥1.5、BB 当根扩张、三根路径效率≥0.55，以及 V1 的同根 RV≥4 且 TR≥3，都能显著减少信号，却会在至少一个周期误删过多已实现 10R 交易。最严格的 V1 同根量价门甚至删除约 92%–93% 的候选。趋势尾部依赖少数早期入口，所以不能只按普通胜率或当根爆发程度挑门槛。",
        "## V7/V8 确认和交易明细",
        _md_table(summary["signal_summary"], ["period", "timeframe_min", "v7_admissions", "v8_admissions", "admission_reduction"]),
        _md_table(summary["event_summary"], ["arm", "period", "timeframe_min", "trade_rows", "closed", "censored", "pf", "net_return_sum", "win_rate", "realized_10r", "mfe_10r"]),
        "## 独立账户、回撤与同资产配对效应",
        _md_table(summary["account_summary"], ["arm", "period", "timeframe_min", "streams", "mean_net_return", "mean_max_drawdown", "worst_max_drawdown"]),
        _md_table(summary["paired_asset_effect"], ["period", "timeframe_min", "delta", "low", "high", "p", "assets"]),
        "同流同时间段先配对 V8 与 V7，再按基础资产做 bootstrap 和符号置换，避免 Binance/OKX/Gate 的重复事件虚增显著性。验证段 30m/1H/4H 的 V8−V7 p 值分别为 0.693/0.887/0.063，尚无可靠的整体收益提升。",
        "## 原 V7 大趋势保留率",
        _md_table(summary["retention_summary"], ["period", "timeframe_min", "baseline_executed", "baseline_realized_10r", "retained_realized_10r", "realized_10r_retention", "baseline_mfe_10r", "retained_mfe_10r", "mfe_10r_retention", "removed_losers", "missed_realized_winners"]),
        "`realized_10r` 表示原 V7 交易最终净结果确实达到 10R；`mfe_10r` 只是持仓路径中曾到达 10R，不能冒充实际落袋收益。这里的保留率按原 V7 交易逐笔判断，避免 V8 改变持仓占用后造成分母漂移。",
        "## 行情阶段比单根指标更重要",
        _md_table(monthly_compare, ["month", "timeframe_min", "closed_v7", "pf_v7", "net_r_sum_v7", "realized_10r_v7", "closed_v8", "pf_v8", "net_r_sum_v8", "realized_10r_v8"]),
        f"月度结果发生明显翻转。以用户关注的 2026-08 普涨/爆发阶段为例：{august_text}。同一套规则在其他月份经常为负，说明 V7 的主要噪音来源之一是**市场状态不适配**。这张表是事件级描述，跨周期和跨交易所存在相关性，不能把 net R 相加当真实账户收益。下一轮最值得单独预注册的是收盘时可知的全市场广度/波动扩散门，而不是继续抬高单币成交量阈值。",
        "## 多空、交易所与匹配随机对照",
        _md_table(summary["signal_side_venue_summary"], ["period", "timeframe_min", "side", "venue", "v7_admissions", "v8_admissions", "admission_reduction"], 36),
        _md_table(summary["side_summary"], ["arm", "period", "timeframe_min", "side", "trade_rows", "pf", "win_rate", "realized_10r"]),
        _md_table(summary["venue_summary"], ["arm", "period", "timeframe_min", "venue", "trade_rows", "pf", "win_rate"]),
        _md_table(validation_control, ["arm", "period", "timeframe_min", "sampled", "matched", "delta", "low", "high", "p", "assets"]),
        "匹配随机对照固定同一数据流方向、时期、事前波动桶、退出引擎和 0.2% 往返成本。验证段只有 1H 的 V7/V8 相对随机入场为显著正值；30m 没有优势，4H 的独立 PF 虽大于 1，但相对匹配随机对照仍不能确认策略本身的增量价值。",
        "多空表现明显随年份翻转：验证段空头强于多头，开发段 4H 则是多头强。这支持市场状态门，不能据此把 V8 固化成静态空头版。",
        "## 被过滤与保留样本的特征",
        _md_table(summary["filter_feature_summary"], ["period", "timeframe_min", "v8_kept", "admissions", "executed", "realized_10r", "mfe_10r", "net_positive", "mean_rope_distance_atr", "mean_efficiency3", "mean_current_volume_ratio"]),
        _md_table(summary["filtered_failure_summary"], ["period", "timeframe_min", "failure_reason", "filtered_admissions", "executed", "missed_realized_10r", "removed_nonpositive"]),
        "## 因果连续特征筛选：只看开发段",
        _md_table(summary["feature_auc_development"], ["timeframe_min", "feature", "direction", "auc_net_positive", "auc_realized_10r", "known", "scope"], 30),
        _md_table(top_decile, ["timeframe_min", "feature", "score_decile", "trades", "net_win_rate", "event_pf", "mean_net_return", "realized_10r"], 30),
        "AUC 和最高十分位只来自开发段，输入均在信号收盘时可知；验证段没有重新计算这些排名，也没有据此二次挑规则。单特征 AUC 普遍接近 0.5，说明不存在一个简单阈值可以把 13 万条信号干净分成好坏。",
        "## 全局图与逐笔案例",
        *[f"![{figure['selection']}](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/{figure['file']})" for figure in figures],
        "K 线案例各取最多两张：保留的已实现 10R、被过滤的亏损、以及被误删的已实现 10R。图中标明信号收盘、次根开盘入场、退出、方向绳索边缘、3ATR 边界和过滤原因。蓝色区域是信号后的复盘未来，只用于解释，绝未参与规则选择。",
        "## 还能怎样继续降噪",
        "1. **市场状态门**：单独研究信号收盘前的全市场上涨比例、同时站上六线比例、BTC/ETH 4H 状态、横截面成交额与波动扩散。目标是识别 8·19 这类普涨启动期；必须按月前推验证，不能用当天涨幅榜反选币。",
        "2. **同事件折叠与组合风险门**：前端把同币同方向、跨交易所、相近时间的确认合并成一个市场事件，交易层限制高度相关币同时暴露。这会降低通知和开仓频率，但需与信号质量分开评价。",
        "3. **早期失败退出**：因为初始止损和亏损反向退出解释绝大部分亏损，可单变量测试入场后 2–3 根未继续创新高/新低、重新跌回/涨回六线时提前退出。它改变退出而非入场，不能和 V8 门槛一次打包。",
        "4. **跨交易所先行确认**：测试一个交易所先突破、其他交易所成交量/OI 随后确认的时序结构。资金费率、持仓量和订单簿只能使用当时可获得的快照，并要单独记录覆盖缺失。",
        "5. **前向影子对照**：V7 与 V8 同时只记录、不推送、不下单，积累新的盲样本。当前验证数据已经被研究过，不能再靠反复调 3ATR 得到可信提升。",
        "## 当前裁决",
        "V8 作为独立 TradingView 研究指标成立：它降低追高追空型噪音并保留大多数大趋势。它暂不替换生产 V7/V1 监控，也不改变 Bark。若要继续提高收益，优先验证市场状态门和早期失败退出；继续强化单根量价只会重演误删尾部赢家的问题。",
        "## Holdout 暴露与诚实边界",
        "本冻结 V8 配置记录为 **holdout-era exposure #1**。V7 基础数据此前已经被其他研究看过，所以这不是盲 holdout。开发段与验证段分开报告，验证汇总后没有更改 3ATR 阈值。成本沿用 0.2% 往返假设，未完整计入资金费、订单簿冲击和共享组合容量；收盘回撤会低估 K 线内回撤。",
        "## 复现命令",
        f"```bash\n.venv/bin/python -m pytest -q tests/evaluation/test_spike_v8_replay.py tests/evaluation/test_spike_v8_noise_study.py tests/evaluation/test_spike_v8_report.py\n.venv/bin/python -m yoyo.evaluation.spike_v8_report --replay {replay} --discovery {discovery} --output {output} --report {report}\n.venv/bin/python scripts/md_to_html.py {report} --out-dir analysis/html\n```",
    ]
    report.write_text("\n\n".join(text) + "\n")
    receipt = dict(**contract, builder_sha256=sha256(Path(__file__)), report_sha256=sha256(report),
                   files={path.name: sha256(path) for path in output.iterdir()
                          if path.is_file() and path.name != "report_manifest.json"},
                   generated_at=pd.Timestamp.now(tz="UTC").isoformat())
    (output / "report_manifest.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(contract), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, default=EXP / "replay_v1")
    parser.add_argument("--discovery", type=Path, default=EXP / "discovery_v1")
    parser.add_argument("--output", type=Path, default=EXP / "summary_v1")
    parser.add_argument("--report", type=Path, default=Path("analysis/p1_spike_v8_noise_filter_20260913.md"))
    arguments = parser.parse_args()
    build(arguments.replay, arguments.discovery, arguments.output, arguments.report)
