"""Render a Chinese audit report from any linked frozen replay/post pair.

The report verifies the supplied replay result directory and its linked post
diagnostic directory before reading their CSVs.  It never replays trades,
chooses a parameter, fetches candles, or modifies either input.  Development
selection, seed ordering sensitivity, market-state descriptions, and account
paths are all calculated from manifest-pinned CSV receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd


# Prefer a host-provided CJK font so Chinese chart labels remain legible.  The
# fallback retains Matplotlib's ordinary font selection on non-macOS runners.
for _font_name in ("Hiragino Sans GB", "PingFang HK", "Noto Sans CJK SC", "Arial Unicode MS"):
    try:
        font_manager.findfont(_font_name, fallback_to_default=False)
    except ValueError:
        continue
    plt.rcParams["font.sans-serif"] = [_font_name]
    break
plt.rcParams["axes.unicode_minus"] = False


RESULT_FILES = frozenset((
    "summary.csv", "accepted_ledger.csv.gz", "rejections.csv.gz",
    "equity_curve.csv.gz", "daily_realized_pnl.csv.gz",
))
POST_FILES = frozenset((
    "development_selection.csv", "risk_summary.csv", "seed_sensitivity.csv",
    "regime_metrics.csv", "daily_realized_bjt.csv.gz", "best_days.csv",
    "milestone_chain.csv", "matched_control_reference.csv",
    "annotated_accepted.csv.gz",
))


def sha256_file(path: Path) -> str:
    """Return the byte hash used by the two frozen-bundle manifests."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_outputs(directory: Path, manifest_name: str, required: Iterable[str]) -> dict[str, Any]:
    """Fail closed unless every manifest-pinned output is present and unchanged."""
    manifest_path = directory / manifest_name
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(f"{manifest_path} has no outputs hash map")
    missing = set(required) - set(outputs)
    if missing:
        raise ValueError(f"{manifest_path} does not pin required outputs: {sorted(missing)}")
    for name, expected in outputs.items():
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(path)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"invalid SHA-256 receipt for {path}")
        observed = sha256_file(path)
        if observed != expected:
            raise ValueError(f"hash mismatch for {path}: expected {expected}, got {observed}")
    return manifest


def verify_input_bundles(result_dir: Path, post_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate full/post manifests and the post-to-replay manifest linkage."""
    result_dir, post_dir = Path(result_dir), Path(post_dir)
    replay = _verify_outputs(result_dir, "run_manifest.json", RESULT_FILES)
    post = _verify_outputs(post_dir, "post_manifest.json", POST_FILES)
    expected = post.get("account_replay_manifest_sha256")
    observed = sha256_file(result_dir / "run_manifest.json")
    if expected != observed:
        raise ValueError(
            "post manifest does not reference this account replay manifest: "
            f"expected {expected}, got {observed}"
        )
    return replay, post


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, float_precision="round_trip", low_memory=False)


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")


def _bool(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower().isin(("true", "1"))


def _money(value: object) -> str:
    value = float(value)
    return "—" if not math.isfinite(value) else f"{value:,.2f}"


def _percent(value: object) -> str:
    value = float(value)
    return "—" if not math.isfinite(value) else f"{value * 100:.2f}%"


def _number(value: object) -> str:
    value = float(value)
    return "—" if not math.isfinite(value) else f"{value:.4g}"


def _md_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    """Build a dependency-free Markdown table, keeping report values textual."""
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    body = ["| " + " | ".join(map(cell, headers)) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    body.extend("| " + " | ".join(map(cell, row)) + " |" for row in rows)
    return "\n".join(body)


def _save_selection_figure(selection: pd.DataFrame, path: Path) -> None:
    labels = [f"{row.source_arm}\n{row.venue_scope}" for row in selection.itertuples()]
    x = np.arange(len(selection))
    fig, ax = plt.subplots(figsize=(10, 5.4))
    ax.plot(x, selection["development_final_balance"], "o-", label="开发期（用于冻结选择）")
    ax.plot(x, selection["validation_final_balance"], "s-", label="验证期（样本外收据）")
    ax.set_title("开发期冻结选参与验证期终值")
    ax.set_ylabel("账户终值（起始 1,000）")
    ax.set_xticks(x, labels, rotation=20, ha="right")
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_risk_figure(risk: pd.DataFrame, path: Path) -> None:
    full = risk.loc[risk["period_scope"].eq("full")].copy()
    _require_columns(full, ("sizing", "risk_fraction", "median_final_balance"), "risk_summary.csv")
    fig, ax = plt.subplots(figsize=(9, 5.2))
    for sizing, group in full.groupby("sizing", sort=True):
        group = group.sort_values("risk_fraction")
        ax.plot(group["risk_fraction"] * 100, group["median_final_balance"], "o-", label=str(sizing))
    ax.set_title("全期参数网格：固定与复利账户中位终值")
    ax.set_xlabel("单笔风险（%）：3 / 5 / 10")
    ax.set_ylabel("账户终值（起始 1,000）")
    ax.set_xticks((3, 5, 10))
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=.25)
    ax.legend(title="仓位规则")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_validation_figure(selection: pd.DataFrame, path: Path) -> None:
    display = selection.sort_values("validation_final_balance", ascending=False).reset_index(drop=True)
    labels = [f"{row.source_arm}\n{row.timeframe}m {row.sizing} {row.risk_fraction:.0%}" for row in display.itertuples()]
    fig, ax = plt.subplots(figsize=(10, 5.4))
    ax.bar(np.arange(len(display)), display["validation_final_balance"], color="#527da8")
    ax.axhline(1000, color="#555555", linewidth=1, linestyle="--", label="起始余额")
    ax.set_title("开发期已冻结配置的验证期终值")
    ax.set_ylabel("账户终值")
    ax.set_xticks(np.arange(len(display)), labels, rotation=20, ha="right")
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_seed_figure(seed: pd.DataFrame, path: Path, *, focus_label: str) -> None:
    """Show one focus configuration's frozen seed replay by period."""
    grouped = seed.groupby("period_scope", sort=True)["final_balance"]
    labels, values = [], []
    for period, group in grouped:
        labels.append(str(period))
        values.append(group.to_numpy(dtype=float))
    fig, ax = plt.subplots(figsize=(10, 5.4))
    ax.boxplot(values, tick_labels=labels, showfliers=True)
    ax.axhline(1000, color="#555555", linewidth=1, linestyle="--", label="起始余额")
    ax.set_title(f"{focus_label}配置在同刻入场排序 seed 下的终值")
    ax.set_ylabel("账户终值")
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_path_figure(
    curve: pd.DataFrame,
    milestones: pd.DataFrame,
    path: Path,
    *,
    focus_label: str,
) -> None:
    curve = curve.sort_values("time").copy()
    curve["time"] = pd.to_datetime(curve["time"], utc=True, format="mixed")
    fig, ax = plt.subplots(figsize=(10, 5.4))
    ax.plot(curve["time"], curve["balance"], linewidth=1.5, color="#2f6f5f")
    if not milestones.empty:
        milestones = milestones.copy()
        milestones["first_reached_time"] = pd.to_datetime(milestones["first_reached_time"], utc=True, format="mixed")
        ax.scatter(milestones["first_reached_time"], milestones["balance"], color="#b44d3f", zorder=3)
        for row in milestones.itertuples():
            ax.annotate(f"{row.threshold:,.0f}", (row.first_reached_time, row.balance), xytext=(3, 5), textcoords="offset points", fontsize=8)
    ax.set_title(f"{focus_label}路径的峰值、回落与首次里程碑")
    ax.set_ylabel("账户余额")
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_operational_regime_figure(
    regime: pd.DataFrame,
    path: Path,
    *,
    focus_label: str,
) -> None:
    """Plot one frozen configuration's validation PnL by causal market state."""
    grouped = regime.groupby(["market_regime", "breadth_regime"], dropna=False, sort=True)["realized_pnl"].sum()
    labels = [f"{market}\n{breadth}" for market, breadth in grouped.index]
    fig, ax = plt.subplots(figsize=(10, 5.4))
    ax.bar(np.arange(len(grouped)), grouped.to_numpy(dtype=float), color="#6f8fb0")
    ax.axhline(0, color="#555555", linewidth=1)
    ax.set_title(f"{focus_label}配置：独立验证期市场状态的描述性已实现PnL")
    ax.set_ylabel("已实现PnL（含负值；横线为 0）")
    ax.set_xticks(np.arange(len(grouped)), labels, rotation=20, ha="right")
    ax.grid(axis="y", alpha=.25)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _figure_paths(figures_dir: Path) -> dict[str, Path]:
    figures_dir.mkdir(parents=True, exist_ok=True)
    return {name: figures_dir / f"{name}.png" for name in (
        "development_selection_vs_validation", "risk_grid_fixed_compound",
        "seed_sensitivity", "focus_historical_path",
        "operational_v7_validation_market_state",
    )}


def _fairness_rows(summary: pd.DataFrame) -> list[list[str]]:
    full = summary.loc[summary["period_scope"].eq("full")].copy()
    rows: list[list[str]] = []
    for (arm, contract), group in full.groupby(["source_arm", "source_contract"], sort=True):
        fair = "公平：共同 next-open / shared exit" if contract == "common_execution_next_open_shared_exit" else "非公平：原 V1 原生退出"
        rows.append([str(arm), fair, str(len(group)), _money(group["final_balance"].median()), _money(group["final_balance"].max())])
    return rows


def build_account_growth_report(
    result_dir: Path,
    post_dir: Path,
    report_path: Path,
    figures_dir: Path,
) -> dict[str, Any]:
    """Verify two frozen bundles and produce a Markdown report plus five PNGs."""
    replay_manifest, post_manifest = verify_input_bundles(result_dir, post_dir)
    result_dir, post_dir, report_path, figures_dir = map(Path, (result_dir, post_dir, report_path, figures_dir))
    summary = _read_csv(result_dir / "summary.csv")
    curve = _read_csv(result_dir / "equity_curve.csv.gz")
    accepted = _read_csv(result_dir / "accepted_ledger.csv.gz")
    selection = _read_csv(post_dir / "development_selection.csv")
    risk = _read_csv(post_dir / "risk_summary.csv")
    seed = _read_csv(post_dir / "seed_sensitivity.csv")
    regime = _read_csv(post_dir / "regime_metrics.csv")
    best_days = _read_csv(post_dir / "best_days.csv")
    milestones = _read_csv(post_dir / "milestone_chain.csv")
    matched = _read_csv(post_dir / "matched_control_reference.csv")
    _require_columns(summary, ("run_id", "source_arm", "venue_scope", "timeframe", "sizing", "risk_fraction", "source_contract", "period_scope", "final_balance", "initial_balance", "max_drawdown_fraction", "reached_100k", "portfolio_risk_cap"), "summary.csv")
    _require_columns(selection, ("source_arm", "venue_scope", "timeframe", "sizing", "risk_fraction", "development_final_balance", "validation_final_balance", "full_final_balance", "full_run_id", "validation_reached_100k", "full_reached_100k"), "development_selection.csv")
    _require_columns(seed, ("source_arm", "venue_scope", "timeframe", "sizing", "risk_fraction", "period_scope", "seed", "final_balance", "max_balance", "max_closed_drawdown_fraction", "reached_100k"), "seed_sensitivity.csv")
    _require_columns(regime, ("run_id", "period_scope", "market_regime", "breadth_regime", "closed", "wins", "realized_pnl"), "regime_metrics.csv")
    _require_columns(best_days, ("run_id", "date_bjt", "realized_pnl", "start_balance", "end_balance", "exit_events"), "best_days.csv")
    _require_columns(milestones, ("run_id", "threshold", "first_reached_time", "balance"), "milestone_chain.csv")
    _require_columns(matched, ("variant", "timeframe_min", "matched_months", "exploratory_month_block_sign_flip_p", "paired_mean_net_r_difference"), "matched_control_reference.csv")
    _require_columns(curve, ("run_id", "period_scope", "time", "balance"), "equity_curve.csv.gz")
    _require_columns(accepted, ("run_id", "period_scope", "base_asset", "side", "timeframe_min", "entry_time", "exit_time", "exit_r_multiple", "realized_pnl", "entry_balance", "balance_after_exit"), "accepted_ledger.csv.gz")

    full = summary.loc[summary["period_scope"].eq("full")].copy()
    if full.empty or selection.empty:
        raise ValueError("report requires full account rows and development selections")
    initial_balance = float(summary["initial_balance"].iloc[0])
    raw_best = full.sort_values(["final_balance", "run_id"], ascending=[False, True], kind="mergesort").iloc[0]
    raw_best_run = str(raw_best["run_id"])
    raw_best_timeframe = "4H" if str(raw_best.timeframe) == "240" else f"{raw_best.timeframe}m"
    raw_best_trade = accepted.loc[accepted["run_id"].eq(raw_best_run)].sort_values("exit_r_multiple", ascending=False).iloc[0]
    reached = seed.loc[_bool(seed["reached_100k"])].sort_values(
        ["max_balance", "seed"], ascending=[False, True], kind="mergesort",
    )
    detail_seed = int(pd.to_numeric(full["seed"], errors="raise").iloc[0])
    detailed_seed_rows = seed.loc[
        seed["period_scope"].eq("full")
        & pd.to_numeric(seed["seed"], errors="coerce").eq(detail_seed)
    ].sort_values(["reached_100k", "max_balance"], ascending=[False, False], kind="mergesort")
    if detailed_seed_rows.empty:
        raise ValueError(f"seed_sensitivity.csv has no full seed={detail_seed} detail path")
    transient = detailed_seed_rows.iloc[0]
    focus_reached_100k = bool(_bool(pd.Series([transient.reached_100k])).iloc[0])
    focus_label = "瞬时 100k" if focus_reached_100k else "历史最高峰值"
    key_columns = ("source_arm", "venue_scope", "timeframe", "sizing", "risk_fraction")
    transient_seed = seed.copy()
    for column in key_columns:
        transient_seed = transient_seed.loc[transient_seed[column].astype(str).eq(str(transient[column]))]
    transient_run = full.loc[
        full["source_arm"].astype(str).eq(str(transient.source_arm))
        & full["venue_scope"].astype(str).eq(str(transient.venue_scope))
        & full["timeframe"].astype(str).eq(str(transient.timeframe))
        & full["sizing"].astype(str).eq(str(transient.sizing))
        & np.isclose(full["risk_fraction"].astype(float), float(transient.risk_fraction))
    ].sort_values("run_id", kind="mergesort")
    if transient_run.empty:
        raise ValueError("transient seed configuration has no full result row")
    transient_run_id = str(transient_run.iloc[0]["run_id"])
    transient_curve = curve.loc[curve["run_id"].eq(transient_run_id) & curve["period_scope"].eq("full")].copy()
    if transient_curve.empty:
        raise ValueError(f"transient run {transient_run_id} has no full equity curve")
    transient_milestones = milestones.loc[milestones["run_id"].eq(transient_run_id)].copy()
    peak = transient_curve.sort_values("balance", ascending=False, kind="mergesort").iloc[0]
    peak_bjt_date = pd.to_datetime(peak["time"], utc=True, format="mixed").tz_convert("Asia/Shanghai").date().isoformat()
    transient_best_days = best_days.loc[best_days["run_id"].eq(transient_run_id)].sort_values(
        ["realized_pnl", "date_bjt"], ascending=[False, True], kind="mergesort",
    )
    if transient_best_days.empty:
        raise ValueError(f"transient run {transient_run_id} has no Beijing-day summary")
    best_day = transient_best_days.iloc[0]
    best_day_bjt = str(best_day["date_bjt"])
    transient_day = accepted.loc[accepted["run_id"].eq(transient_run_id)].copy()
    transient_day["exit_bjt_date"] = pd.to_datetime(transient_day["exit_time"], utc=True, format="mixed").dt.tz_convert("Asia/Shanghai").dt.date.astype(str)
    transient_day = transient_day.loc[transient_day["exit_bjt_date"].eq(best_day_bjt)].sort_values("exit_time")
    if transient_day.empty:
        raise ValueError(f"transient best day {best_day_bjt} has no accepted exits")
    transient_selection = selection.loc[
        selection["source_arm"].astype(str).eq(str(transient.source_arm))
        & selection["venue_scope"].astype(str).eq(str(transient.venue_scope))
        & selection["timeframe"].astype(str).eq(str(transient.timeframe))
        & selection["sizing"].astype(str).eq(str(transient.sizing))
        & np.isclose(selection["risk_fraction"].astype(float), float(transient.risk_fraction))
    ]
    if len(transient_selection) != 1:
        raise ValueError("transient configuration is not one frozen development selection")
    operational_validation_run = str(transient_selection.iloc[0]["validation_run_id"])
    operational_regime = regime.loc[
        regime["period_scope"].eq("validation")
        & regime["run_id"].astype(str).eq(operational_validation_run)
    ].copy()
    if operational_regime.empty:
        raise ValueError("transient configuration has no validation market-state rows")

    figures = _figure_paths(figures_dir)
    _save_selection_figure(selection, figures["development_selection_vs_validation"])
    _save_risk_figure(risk, figures["risk_grid_fixed_compound"])
    _save_seed_figure(transient_seed, figures["seed_sensitivity"], focus_label=focus_label)
    _save_path_figure(
        transient_curve,
        transient_milestones,
        figures["focus_historical_path"],
        focus_label=focus_label,
    )
    _save_operational_regime_figure(
        operational_regime,
        figures["operational_v7_validation_market_state"],
        focus_label=focus_label,
    )

    selected_validation_100k = int(_bool(selection["validation_reached_100k"]).sum())
    selected_validation_above = int(selection["validation_final_balance"].gt(initial_balance).sum())
    selected_validation_below = int(selection["validation_final_balance"].lt(initial_balance).sum())
    selected_validation_equal = len(selection) - selected_validation_above - selected_validation_below
    transient_summary = []
    for period, group in transient_seed.groupby("period_scope", sort=True):
        transient_summary.append([str(period), str(len(group)), str(int(_bool(group["reached_100k"]).sum())), _money(group["final_balance"].median()), _money(group["max_balance"].max()), _percent(group["max_closed_drawdown_fraction"].max())])
    operational_rows = []
    for (market, breadth), group in operational_regime.groupby(["market_regime", "breadth_regime"], dropna=False, sort=True):
        closed = int(group["closed"].sum())
        wins = int(group["wins"].sum())
        operational_rows.append([str(market), str(breadth), str(closed), str(wins), _percent(wins / closed) if closed else "—", _money(group["realized_pnl"].sum())])
    grid_keys = ["source_arm", "venue_scope", "timeframe", "sizing", "risk_fraction", "seed"]
    grid = summary.pivot(index=grid_keys, columns="period_scope", values="final_balance").reset_index()
    grid_mdd = summary.pivot(index=grid_keys, columns="period_scope", values="max_drawdown_fraction").reset_index()
    grid_mdd = grid_mdd.rename(columns={period: f"{period}_mdd" for period in ("development", "validation", "full")})
    grid = grid.merge(grid_mdd, on=grid_keys, how="left", validate="one_to_one")
    _require_columns(grid, ("development", "validation", "full"), "summary period grid")
    post_hoc = grid.loc[grid["development"].gt(initial_balance) & grid["validation"].gt(initial_balance)].copy()
    period_rows = []
    for (arm, timeframe), group in grid.groupby(["source_arm", "timeframe"], sort=True):
        leads = post_hoc.loc[post_hoc["source_arm"].eq(arm) & post_hoc["timeframe"].eq(timeframe)]
        period_rows.append([str(arm), str(timeframe), str(len(group)), _money(group["development"].median()), _money(group["validation"].median()), str(len(leads))])
    matched_rows = [[str(row.variant), str(row.timeframe_min), str(int(row.matched_months)), _number(row.paired_mean_net_r_difference), _number(row.exploratory_month_block_sign_flip_p)] for row in matched.itertuples()]
    selection_rows = [[str(row.source_arm), str(row.venue_scope), f"{row.timeframe}m", str(row.sizing), _percent(row.risk_fraction), _money(row.development_final_balance), _money(row.validation_final_balance), _money(row.full_final_balance)] for row in selection.itertuples()]
    risk_rows = [[str(row.sizing), _percent(row.risk_fraction), str(int(row.paths)), _money(row.median_final_balance), _money(row.best_final_balance), _money(row.worst_final_balance), str(int(row.reached_100k_paths)), _percent(row.median_closed_mdd)] for row in risk.loc[risk["period_scope"].eq("full")].sort_values(["sizing", "risk_fraction"]).itertuples()]
    arithmetic_rows = []
    for risk_fraction in sorted(risk["risk_fraction"].astype(float).unique()):
        compound_wins = math.ceil(math.log(100.0) / math.log1p(risk_fraction))
        fixed_r = (100.0 - 1.0) / risk_fraction
        arithmetic_rows.append([_percent(risk_fraction), str(compound_wins), f"{fixed_r:,.0f}R"])
    post_hoc_rows = [[
        str(row.source_arm), str(row.venue_scope), str(row.timeframe), str(row.sizing),
        _percent(row.risk_fraction), _money(row.development), _percent(row.development_mdd),
        _money(row.validation), _percent(row.validation_mdd), _money(row.full),
    ] for row in post_hoc.sort_values(["validation", "development"], ascending=False).itertuples()]
    chain_rows = [[
        str(row.base_asset), "多" if int(row.side) == 1 else "空", str(int(row.timeframe_min)),
        str(row.entry_time), str(row.exit_time), _number(row.exit_r_multiple),
        _money(row.realized_pnl), _money(row.balance_after_exit),
    ] for row in transient_day.itertuples()]
    figure_links = {
        name: Path(os.path.relpath(path, start=report_path.parent)).as_posix()
        for name, path in figures.items()
    }
    source_range = replay_manifest.get("source_time_range", {})
    output_counts = replay_manifest.get("output_row_counts", {})
    match_passes = int(matched["exploratory_month_block_sign_flip_p"].lt(.01).sum())
    replay_reproduce_dir = result_dir.parent / "reproduce_full_v1"
    post_reproduce_dir = post_dir.parent / "reproduce_post_v2"
    total_seed_hits = int(_bool(seed["reached_100k"]).sum())
    focus_full_seed_count = int(transient_seed["period_scope"].eq("full").sum())
    focus_description = (
        f"{focus_label}的详细路径为 `{transient_run_id}`（seed={detail_seed}）：峰值 "
        f"{_money(transient.max_balance)}，终值 {_money(transient.final_balance)}，最大已实现回撤 "
        f"{_percent(transient.max_closed_drawdown_fraction)}。"
    )
    if not focus_reached_100k:
        focus_description += (
            f" 该配置的 {focus_full_seed_count} 个 full seed 收据中没有可配对到详细账本的 100k 路径；全部 seed 合计 "
            f"{total_seed_hits} 次触及 100k，下面仍展示可审计的 seed={detail_seed} 最高峰值路径。"
        )
    report = f"""# SPIKE 账户冻结回放技术报告

## 数据质量、范围与完整重现

本报告只读取 `--result` 与 `--post` 中 manifest 固定的 CSV。生成前已校验 replay 的 {len(replay_manifest.get("outputs", {}))} 个输出、post 的 {len(post_manifest.get("outputs", {}))} 个输出的 SHA-256，并校验二者关联。两个源文件共有 {int(replay_manifest.get("source_rows", 0)):,} 行，载入相关交易 {int(replay_manifest.get("loaded_relevant_rows", 0)):,} 行；账户网格 {int(replay_manifest.get("runs", len(summary))):,} 条，保留 accepted {int(output_counts.get("accepted_ledger", len(accepted))):,} 行。源 entry 时间范围为 {source_range.get("entry_time_min", "—")} 至 {source_range.get("entry_time_max", "—")}；起始余额 {_money(initial_balance)}。holdout 授权记录：本配置 **第 1 次** 消耗 holdout，报告只描述冻结结果。

```bash
python3 -m yoyo.evaluation.spike_account_growth_study --output {replay_reproduce_dir} --seed 0
python3 -m yoyo.evaluation.spike_account_growth_post --result {replay_reproduce_dir} --output {post_reproduce_dir}
python3 -m yoyo.evaluation.spike_account_growth_report --result {replay_reproduce_dir} --post {post_reproduce_dir} --report {report_path} --figures {figures_dir}
python3 scripts/md_to_html.py {report_path} --out-dir {report_path.parent / 'html'}
```

上面的 `reproduce_*` 必须是新的空目录；冻结输入目录只读，禁止把重现输出写回 `{result_dir}` 或 `{post_dir}`。

## 方法与独立 validation 结果

开发期只冻结每个 source-arm × venue 的一个参数；validation 是独立余额、独立准入路径的收据。{selected_validation_below}/{len(selection)} development-selected 配置的 validation 终值低于 {_money(initial_balance)}，{selected_validation_equal}/{len(selection)} 持平，{selected_validation_above}/{len(selection)} 高于起始余额，{selected_validation_100k}/{len(selection)} 达到 100,000。full 不能拼接 development+validation：余额会改变仓位、风险上限和候选准入。

{_md_table(["策略臂", "场所", "周期", "仓位", "风险", "开发终值", "验证终值", "全期终值"], selection_rows)}

![开发冻结选择与独立 validation]({figure_links["development_selection_vs_validation"]})

本报告不训练分类器，也不产生排序分数，因此 val AUC、top-decile 毛/净收益和单特征基线不适用，不能编造。对应的零假设证据使用上游同币、同月、同波动桶的匹配随机入场，并在后文逐周期列出净 R 差与月块 sign-flip p；共享账户层另用 32 个不读取结果的同刻排序 seed 检查容量路径敏感性。

## 3/5/10 风险网格与不批准结论

{_md_table(["仓位", "风险", "路径数", "中位终值", "最好终值", "最差终值", "达 100k", "中位已实现回撤"], risk_rows)}

![固定与复利的全期 3/5/10 风险网格]({figure_links["risk_grid_fixed_compound"]})

本轮没有任何 3% / 5% / 10% 风险配置可批准为实盘。fixed 10% 的单笔目标风险为 10%，组合初始风险上限为 {_percent(raw_best.portfolio_risk_cap)}，通常只能持有一仓；亏损后固定风险额仍按初始余额计算，可能高于当时可用额度，因而无法继续开仓。这是账户准入机制，不能作策略因果解释。

达到 100 倍的纯算术门槛如下：假定每一次都是连续净 +1R，复利账户余额每次乘以 `1+r`，fixed 风险账户则累计每次 `r` 倍初始余额。它只是算术，不是概率、胜率或预测；上面的独立 validation 失败正是不能把该门槛当成可达性证据的原因。

{_md_table(["单笔风险", "复利：连续净+1R次数", "fixed：累计净R"], arithmetic_rows)}

## 两种历史“最佳”必须分开

raw full hindsight 的最高**终值**路径为 `{raw_best_run}`：`{raw_best.source_arm}` / {raw_best.venue_scope} / {raw_best_timeframe} / {raw_best.sizing} {_percent(raw_best.risk_fraction)}，终值 {_money(raw_best.final_balance)}。其最大单笔为 {raw_best_trade.base_asset} {_number(raw_best_trade.exit_r_multiple)}R（账户 PnL {_money(raw_best_trade.realized_pnl)}）；它没有达到 100,000。

{focus_description} 这不是最高终值，也不能被写成“实现 100 倍”。同一冻结配置在排序 seed 中，full 有 {int(_bool(transient_seed.loc[transient_seed["period_scope"].eq("full"), "reached_100k"]).sum())}/{int(transient_seed["period_scope"].eq("full").sum())} 曾触及 100k，validation 为 {int(_bool(transient_seed.loc[transient_seed["period_scope"].eq("validation"), "reached_100k"]).sum())}/{int(transient_seed["period_scope"].eq("validation").sum())}。

{_md_table(["周期", "seed数", "曾达100k", "终值中位数", "最高峰值", "最大已实现回撤"], transient_summary)}

![{focus_label}配置的 seed 敏感性]({figure_links["seed_sensitivity"]})

## {focus_label}的北京日链路

{focus_label}路径的最高已实现 PnL 日为北京时间 {best_day_bjt}：日初 {_money(best_day.start_balance)}，{int(best_day.exit_events)} 笔合计 {_money(best_day.realized_pnl)}，日终 {_money(best_day.end_balance)}；账户峰值也落在 {peak_bjt_date}。以下逐笔从冻结 `accepted_ledger.csv.gz` 按实际 exit 时间换算北京日期得到，只解释该日账面链路。

{_md_table(["资产", "方向", "周期(分)", "入场UTC", "退出UTC", "账户R", "已实现PnL", "退出后余额"], chain_rows)}

![{focus_label}路径、回落和里程碑]({figure_links["focus_historical_path"]})

## 周期、市场状态与 post-hoc 线索

{_md_table(["策略臂", "周期（分钟/all）", "完整参数格", "开发中位终值", "validation中位终值", "开发与validation均盈利"], period_rows)}

development 与 validation 同时高于起始余额的组合是 {len(post_hoc)}/{len(grid)}；策略臂为 {", ".join(sorted(post_hoc["source_arm"].unique())) or "—"}。这是 post-hoc leads，不能据此选择周期、仓位或上线规则。

{_md_table(["策略臂", "场所", "周期", "仓位", "风险", "开发终值", "开发MDD", "验证终值", "验证MDD", "全期终值"], post_hoc_rows)}

市场状态来自已完成 BTC/ETH 4H，宽度不含结果标签。下表只描述历史{focus_label}配置 `{operational_validation_run}` 在独立 validation 的联合分层，不能推断状态导致结果。该配置 validation 从 1,000U 降到 {_money(transient_selection.iloc[0].validation_final_balance)}；任何看似盈利的局部状态都没有救活整个账户。

{_md_table(["市场状态", "宽度", "平仓数", "胜数", "胜率", "已实现PnL"], operational_rows)}

![{focus_label}配置的独立 validation 市场状态描述]({figure_links["operational_v7_validation_market_state"]})

## V1/V7 合同性与 matched control

共同 next-open / shared-exit 的 V1/V7 可公平横比；原 V1 native exit 是非公平历史口径。matched control 是事件层参考而非共享账户模拟；{match_passes}/{len(matched)} 个 CSV p 值低于 0.01，故没有一项通过 p<0.01 门槛。

{_md_table(["策略臂", "口径", "full路径数", "中位终值", "最高终值"], _fairness_rows(summary))}

{_md_table(["变体", "周期（分）", "匹配月数", "配对净R差", "月块 sign-flip p"], matched_rows)}

## 限制与下周方案

- 回放是冻结 ledger 的现金簿模拟，不含真实成交、完整 funding、保证金、清算和滑点分布；full、development、validation 因余额和准入路径依赖不可相加。
- OKX 官方说明杠杆会同时放大盈利与亏损，保证金与清算约束会改变真实存活路径；永续资金费率通常按 8 小时结算，也可能改为 1、2 或 4 小时。本回放没有完整模拟这些机制，不能把“bankrupt=0”解释成实盘不会爆仓：[杠杆与保证金](https://www.okx.com/en-gb/help/understanding-leverage-futures-and-margin)、[永续合约](https://www.okx.com/en-gb/help/i-perpetual-swaps)、[资金费率机制](https://www.okx.com/en-sg/help/perps-funding-fee-mechanism)。
- 瞬时 100k、最高终值、最大单笔和北京日链路都是 hindsight 描述；禁止据此改阈值、风险或生产配置。
- 下周的可执行结论是先保住这 1,000U：登记本轮为 rejected，保持配置冻结，不以 3%/5%/10% 风险实盘。下一轮只提名 `v7_bb_both / OKX / 4H` 做事前冻结的前向纸面验证，必须沿用相同账户约束、记录所有拒单，并以至少 100 笔新鲜平仓和匹配账户对照作为裁决；风险、阈值或生产切换仍需 owner 另行批准。
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return {"report": report_path, "figures": figures, "raw_best_run_id": raw_best_run, "transient_run_id": transient_run_id, "replay_manifest": replay_manifest, "post_manifest": post_manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the frozen SPIKE account-growth Markdown report and figures.")
    parser.add_argument("--result", type=Path, required=True, help="Frozen full_v1 replay result directory.")
    parser.add_argument("--post", type=Path, required=True, help="Linked frozen post-diagnostics directory.")
    parser.add_argument("--report", type=Path, required=True, help="Markdown report output path.")
    parser.add_argument("--figures", type=Path, required=True, help="Directory for static PNG figures.")
    args = parser.parse_args(argv)
    build_account_growth_report(args.result, args.post, args.report, args.figures)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
