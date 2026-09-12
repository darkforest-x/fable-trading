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
    signal = signals.groupby(["period", "timeframe_min"], as_index=False).agg(v7_admissions=("v7", "sum"), v8_admissions=("v8", "sum"))
    signal["admission_reduction"] = 1 - signal.v8_admissions / signal.v7_admissions.replace(0, np.nan)
    signal_by_side_venue = signals.groupby(["period", "timeframe_min", "side", "venue"], as_index=False).agg(
        v7_admissions=("v7", "sum"), v8_admissions=("v8", "sum"))
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
    auc["scope"] = "development_only_causal_feature_screen"
    deciles["scope"] = "development_only_causal_feature_screen"
    result = {
        "signal_summary": signal, "signal_side_venue_summary": signal_by_side_venue,
        "event_summary": event, "account_summary": account,
        "side_summary": side, "venue_summary": venue, "retention_summary": retention_summary,
        "paired_asset_effect": paired, "matched_random_control": control,
        "filter_feature_summary": feature_summary, "filtered_failure_summary": failure_summary,
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
    validation = summary["event_summary"].query("period == 'validation'")
    text = [
        "# SPIKE V8 noise filter: frozen V7 versus V8 replay",
        "V8 only suppresses a new V7 entry when its confirmation close is more than 3 ATR beyond the directional six-MA rope edge. Raw opposite V6 confirmations remain available to close an open position. This is research only: it does not change production monitoring, notifications, ACTIVE, or promotion. Any Pine expression remains a research prototype.",
        "## Scope and frozen contracts",
        f"Authenticated replay: {contract['complete_streams']} streams; **{contract['v7_admissions']:,} V7 admissions**; **{contract['v7_trade_rows']:,} actual V7 serial trade rows**. Admissions are candidate confirmations; actual trades are smaller because an occupied stream position can suppress execution. These are intentionally reported as different denominators.",
        "The development-only discovery selected and froze the single rule before validation outcomes were summarized. All validation tables below are a frozen-rule evaluation, not an additional validation search. Continuous-feature AUC and deciles are copied only from the causal development discovery artifact.",
        "## V7/V8 admissions and executed trades",
        _md_table(summary["signal_summary"], ["period", "timeframe_min", "v7_admissions", "v8_admissions", "admission_reduction"]),
        _md_table(summary["event_summary"], ["arm", "period", "timeframe_min", "trade_rows", "closed", "censored", "pf", "net_return_sum", "win_rate", "realized_10r", "mfe_10r"]),
        "## Independent stream accounts and asset-clustered paired effect",
        _md_table(summary["account_summary"], ["arm", "period", "timeframe_min", "streams", "mean_net_return", "mean_max_drawdown", "worst_max_drawdown"]),
        _md_table(summary["paired_asset_effect"], ["period", "timeframe_min", "delta", "low", "high", "p", "assets"]),
        "Effects pair V8 against V7 in the same stream and period, then resample/sign-flip at base-asset level so cross-venue duplicates do not create independent evidence. This is a robust descriptive comparison, not a production acceptance test.",
        "## Exact large-winner retention",
        _md_table(summary["retention_summary"], ["period", "timeframe_min", "baseline_executed", "baseline_realized_10r", "retained_realized_10r", "realized_10r_retention", "baseline_mfe_10r", "retained_mfe_10r", "mfe_10r_retention", "removed_losers", "missed_realized_winners"]),
        "Realized ≥10R means the original closed trade actually reached that net outcome. MFE ≥10R is only a path high and is never presented as realized profit.",
        "## Side, venue, and matched random comparisons",
        _md_table(summary["signal_side_venue_summary"], ["period", "timeframe_min", "side", "venue", "v7_admissions", "v8_admissions", "admission_reduction"], 36),
        _md_table(summary["side_summary"], ["arm", "period", "timeframe_min", "side", "trade_rows", "pf", "win_rate", "realized_10r"]),
        _md_table(summary["venue_summary"], ["arm", "period", "timeframe_min", "venue", "trade_rows", "pf", "win_rate"]),
        _md_table(summary["matched_random_control"], ["arm", "period", "timeframe_min", "sampled", "matched", "delta", "low", "high", "p", "assets"]),
        "Matched controls preserve the frozen per-stream direction, period, prior-volatility bucket, exit engine and 0.2% cost assumptions. Unmatched candidates remain excluded rather than being silently replaced.",
        "## Filtered/retained feature and outcome analysis",
        _md_table(summary["filter_feature_summary"], ["period", "timeframe_min", "v8_kept", "admissions", "executed", "realized_10r", "mfe_10r", "net_positive", "mean_rope_distance_atr", "mean_efficiency3", "mean_current_volume_ratio"]),
        _md_table(summary["filtered_failure_summary"], ["period", "timeframe_min", "failure_reason", "filtered_admissions", "executed", "missed_realized_10r", "removed_nonpositive"], 24),
        "## Causal continuous feature screen: development only",
        _md_table(summary["feature_auc_development"], ["timeframe_min", "feature", "direction", "auc_net_positive", "auc_realized_10r", "known", "scope"], 30),
        "Top-decile evidence is in `summary_v1/feature_deciles_development.csv`. Its inputs are causal at signal close; it is not recalculated on validation and does not choose a new V8 rule.",
        "## Figures",
        *[f"![{figure['selection']}](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/{figure['file']})" for figure in figures],
        "The K-line gallery selects up to two validation cases each for retained realized ≥10R, filtered loser, and missed realized ≥10R. It loads each raw cache only through its authenticated receipt; a category with fewer than two cases shows all available examples. Signal-close, next-open entry, exit, directional rope edge, 3 ATR boundary, and V8 filter reason are marked. Blue future shading is retrospective display only and cannot select rules.",
        "## Holdout-era exposure and honest limits",
        "This frozen V8 configuration records **holdout-era exposure 1**. Its V7 foundation data had already been exposed by earlier research, so this is not blind holdout evidence. The two time periods are reported separately; no parameter or threshold was changed after validation aggregation. There is no production/promote decision. Costs remain the inherited 0.2% round-trip assumption and do not include full funding, order-book impact, or shared-portfolio capacity. Stream accounts are independent and closing-price drawdown can understate intrabar drawdown.",
        "## Reproduction",
        f"```bash\n.venv/bin/python -m pytest -q tests/evaluation/test_spike_v8_replay.py tests/evaluation/test_spike_v8_noise_study.py tests/evaluation/test_spike_v8_report.py\n.venv/bin/python -m yoyo.evaluation.spike_v8_report --replay {replay} --discovery {discovery} --output {output} --report {report}\n.venv/bin/python scripts/md_to_html.py {report} --out-dir analysis/html\n```",
    ]
    report.write_text("\n\n".join(text) + "\n")
    receipt = dict(**contract, builder_sha256=sha256(Path(__file__)), report_sha256=sha256(report),
                   files={path.name: sha256(path) for path in output.iterdir() if path.is_file()},
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
