"""Aggregate all receipt-bound episode ablations and render honest review cases.

Outcome sorting is used only for illustrative retrospective figures; it never
feeds signal construction. Paired statistics cluster cross-venue duplicates by
base asset. Independent accounts are not described as one shared portfolio.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from yoyo.evaluation.spike_v7_episode_study import EXP, ARMS, sha256, episode_admissions
from yoyo.evaluation.spike_exit_policy_study import load_verified_stream

LABELS = {"baseline": "B 原版V7", "first": "A 每段首次", "first_break": "C 首次突破边界"}


def normalize_booleans(tables):
    """Empty CSV streams can upcast actual bool values to object after concat.

    Object-bool inversion produces integers -1/-2, so enforce actual Boolean
    dtype before using any mask. Missing control matches mean unmatched only.
    """
    required = {"accounts": ["valid"], "trades": ["censored"],
                "retention": ["exact_retained", "same_episode_entry"], "controls": ["matched"]}
    for kind, columns in required.items():
        for column in columns:
            values = tables[kind][column]
            if not values.dropna().map(lambda v: isinstance(v, (bool, np.bool_))).all():
                raise ValueError(f"nonboolean values in {kind}.{column}")
            if values.isna().any() and kind != "controls":
                raise ValueError(f"missing required flag {kind}.{column}")
            tables[kind][column] = values.fillna(False).astype(bool)
    return tables


def cluster_effect(values):
    """Paired stream-weighted mean with whole-base-asset resampling/sign flips."""
    clusters = values.groupby("asset").delta.agg(["sum", "count"])
    if clusters.empty:
        return dict(delta=np.nan, low=np.nan, high=np.nan, p=np.nan, assets=0)
    sums, counts = clusters["sum"].to_numpy(), clusters["count"].to_numpy()
    observed = sums.sum()/counts.sum()
    rng = np.random.default_rng(20260912)
    ids = rng.integers(0, len(sums), size=(2000, len(sums)))
    boot = sums[ids].sum(axis=1)/counts[ids].sum(axis=1)
    perm = (rng.choice([-1, 1], size=(2000, len(sums)))*sums).sum(axis=1)/counts.sum()
    return dict(delta=float(observed), low=float(np.quantile(boot, .025)), high=float(np.quantile(boot, .975)),
                p=float((1+(perm >= observed).sum())/2001), assets=len(sums))


def collect(result):
    if (result / "INVALIDATED.json").exists():
        raise ValueError("invalidated replay cannot be reported")
    manifest = json.loads((result / "manifest.json").read_text())
    if not manifest["complete"] or manifest["streams"] != 3531:
        raise ValueError("report requires all 3531 streams")
    receipts = sorted((result / "streams").glob("*.json"))
    if len(receipts) != 3531:
        raise ValueError("missing stream receipts")
    identity_path = result / "identity.json"
    if sha256(identity_path) != manifest["identity_sha256"]:
        raise ValueError("run identity receipt changed")
    for name, digest in json.loads(identity_path.read_text()).items():
        if sha256(Path(name)) != digest:
            raise ValueError("builder/config/source changed after replay")
    parts = {k: [] for k in ["accounts", "events", "trades", "retention", "signals", "controls"]}
    old_root = Path("experiments/active/exp-spike-exit-policy-20260912-v1/engine_results/full_v1/streams")
    checked, censored = 0, 0
    parity_cols = ["trade_id", "signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "exit_i", "exit_time", "exit_reason", "entry_price",
                   "exit_price", "initial_stop", "initial_risk", "net_return", "net_r", "censored",
                   "exit_time_precision", "qty_realized", "qty_remaining"]
    for i, path in enumerate(receipts, 1):
        row = json.loads(path.read_text())
        stream_tables = {}
        for name, digest in row["files"].items():
            p = path.parent / name
            if sha256(p) != digest:
                raise ValueError("altered stream result")
            kind = name.rsplit(".", 3)[1]
            table = pd.read_csv(p)
            parts[kind].append(table)
            stream_tables[kind] = table
        old_receipt = json.loads((old_root / (path.stem+".completion.json")).read_text())
        old_path = old_root / (path.stem+".trades.csv.gz")
        if sha256(old_path) != old_receipt["output_sha256"][old_path.name]:
            raise ValueError("prior baseline ledger changed")
        old = pd.read_csv(old_path)
        old = old.loc[old.cohort.eq("v7_both") & old.policy.eq("baseline")]
        new = stream_tables["trades"].loc[stream_tables["trades"].arm.eq("baseline")]
        assert_frame_equal(old[parity_cols].sort_values("trade_id").reset_index(drop=True),
                           new[parity_cols].sort_values("trade_id").reset_index(drop=True),
                           check_dtype=False, rtol=1e-9, atol=1e-9)
        checked += len(old); censored += int(old.censored.sum())
        if i % 500 == 0:
            print(json.dumps({"report_verified_streams": i}), flush=True)
    tables = {k: pd.concat(v, ignore_index=True) for k, v in parts.items()}
    normalize_booleans(tables)
    return tables, dict(complete_streams=3531, all_baseline_rows_checked=checked, censored_rows_checked=censored)


def summarize(tables, output):
    accounts, events, ret, trades, controls = (tables[k] for k in ["accounts", "events", "retention", "trades", "controls"])
    summaries, effects, retention = [], [], []
    keys = ["arm", "timeframe_min", "period"]
    for key, group in accounts.groupby(keys):
        valid = group.loc[group.valid]
        summaries.append(dict(zip(keys, key), streams=len(group), valid=len(valid),
                              mean_return=valid.net_return.mean(), median_return=valid.net_return.median(),
                              mean_dd=valid.max_close_drawdown.mean(), worst_dd=valid.max_close_drawdown.max()))
    for (arm, minutes, p), group in ret.groupby(keys):
        winners = group.net_r.ge(10)
        retention.append(dict(arm=arm, timeframe_min=minutes, period=p, base_trades=len(group),
            base_winners10=int(winners.sum()), kept_winners10=int((winners & group.exact_retained).sum()),
            episode_winners10=int((winners & group.same_episode_entry).sum()),
            removed_losers=int((group.net_return.lt(0) & ~group.exact_retained).sum()),
            removed_winners=int((group.net_return.gt(0) & ~group.exact_retained).sum())))
    baseline = accounts.loc[accounts.arm.eq("baseline"), ["stream_key", "period", "net_return"]].rename(columns={"net_return": "base_return"})
    paired = accounts.merge(baseline, on=["stream_key", "period"], validate="many_to_one")
    paired["delta"] = paired.net_return-paired.base_return
    for (arm, minutes, p), group in paired.loc[paired.arm.ne("baseline") & paired.period.ne("full") & paired.valid].groupby(keys):
        effects.append(dict(arm=arm, timeframe_min=minutes, period=p, **cluster_effect(group)))
    effect = pd.DataFrame(effects)
    order = np.argsort(effect.p.to_numpy())
    adjusted = np.minimum.accumulate((effect.p.to_numpy()[order]*len(effect)/np.arange(1, len(effect)+1))[::-1])[::-1]
    effect["q"] = 1.; effect.loc[order, "q"] = np.minimum(adjusted, 1)
    counts = events.groupby(keys, as_index=False)[["entries", "closed", "censored", "wins", "losses", "gain_sum", "loss_sum", "net_r_sum", "gross_return_sum", "net_return_sum", "realized_10r", "mfe_10r", "signals", "fallback_signals"]].sum()
    counts["win_rate"] = counts.wins/counts.closed.replace(0, np.nan)
    counts["pf"] = counts.gain_sum/counts.loss_sum.replace(0, np.nan)
    signal_base = counts.loc[counts.arm.eq("baseline"), ["timeframe_min", "period", "signals"]].rename(columns={"signals": "base_signals"})
    counts = counts.merge(signal_base, on=["timeframe_min", "period"])
    counts["signal_reduction"] = 1-counts.signals/counts.base_signals.replace(0, np.nan)
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    closed["win"] = closed.net_return.gt(0)
    closed["tail10"] = closed.net_r.ge(10)
    by_side = closed.groupby(keys+["side"], as_index=False).agg(
        trades=("net_r", "size"), win_rate=("win", "mean"), mean_net_r=("net_r", "mean"), realized_10r=("tail10", "sum"))
    dropped = ret.loc[~ret.exact_retained].copy()
    dropped["winner10"] = dropped.net_r.ge(10)
    dropped["loser"] = dropped.net_return.lt(0)
    reasons = dropped.groupby(keys+["reason"], as_index=False).agg(
        trades=("net_r", "size"), missed_winners10=("winner10", "sum"), removed_losers=("loser", "sum"))
    crows = []
    for key, group in controls.groupby(keys):
        matched = group.loc[group.matched.fillna(False).astype(bool)].copy()
        matched["delta"] = matched.net_return_difference
        crows.append(dict(zip(keys, key), sampled=len(group), matched=len(matched),
                         **cluster_effect(matched)))
    output.mkdir(parents=True, exist_ok=True)
    result = {"account_summary": pd.DataFrame(summaries), "event_summary": counts,
              "retention_summary": pd.DataFrame(retention), "paired_effect": effect, "matched_reference": pd.DataFrame(crows),
              "side_summary": by_side, "rejection_reasons": reasons}
    for name, table in result.items():
        table.to_csv(output/(name+".csv"), index=False)
    for name in ["accounts", "trades", "retention", "controls"]:
        tables[name].to_csv(output/(name+".csv.gz"), index=False, compression={"method": "gzip", "compresslevel": 1, "mtime": 0})
    return result


def render_cases(tables, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle
    retention, trades = tables["retention"], tables["trades"]
    r = retention.loc[retention.arm.eq("first_break") & retention.period.eq("validation")]
    cases = []
    for label, data, ascending in [
        ("Retained winner", r.loc[r.exact_retained & r.net_r.ge(10)], False),
        ("Missed winner", r.loc[~r.exact_retained & r.net_r.ge(10)], False),
        ("Removed loser", r.loc[~r.exact_retained & r.net_return.lt(0)], True),
    ]:
        for _, row in data.sort_values("net_r", ascending=ascending).drop_duplicates(["asset", "timeframe_min"]).head(2).iterrows():
            cases.append((label, row))
    receipts = []
    raw = Path(json.loads((EXP/"config.json").read_text())["raw"])
    for k, (label, row) in enumerate(cases, 1):
        context = load_verified_stream(raw/"streams"/row.stream_key)
        b = context.cache["bars"]
        signal = pd.Timestamp(row.signal_bar_open)
        match = trades.loc[trades.arm.eq("baseline") & trades.stream_key.eq(row.stream_key) & trades.signal_bar_open.eq(row.signal_bar_open) & trades.side.eq(row.side)].iloc[0]
        i, j = int(b.index.get_loc(signal)), int(b.index.searchsorted(pd.Timestamp(match.exit_time)))
        left, right = max(0, i-100), min(len(b), max(i+100, j+24))
        shown = b.iloc[left:right].copy()
        stride = max(1, int(np.ceil(len(shown)/1600)))
        # Aggregate OHLC for display rather than silently dropping volatile bars.
        groups = np.arange(len(shown))//stride
        agg = {name: "last" for name in ["s20", "e20", "s60", "e60", "s120", "e120", "md", "sb"]}
        agg.update(open="first", high="max", low="min", close="last")
        view = shown.groupby(groups).agg(agg)
        times = shown.index[np.minimum(np.arange(len(view))*stride, len(shown)-1)]
        x = np.arange(len(view))
        fig, (ax, osc) = plt.subplots(2, 1, figsize=(16, 8), sharex=True, gridspec_kw={"height_ratios": [4, 1]}, facecolor="#0b121b")
        for panel in (ax, osc):
            panel.set_facecolor("#0e1824"); panel.grid(alpha=.15, color="#8092a4"); panel.tick_params(colors="#afbfca")
            for spine in panel.spines.values(): spine.set_color("#263746")
        colors = np.where(view.close >= view.open, "#4bd0a8", "#ef7984")
        ax.add_collection(LineCollection([[(a, l), (a, h)] for a, l, h in zip(x, view.low, view.high)], colors=colors, linewidths=.7))
        for a, o, c, color in zip(x, view.open, view.close, colors):
            ax.add_patch(Rectangle((a-.3, min(o, c)), .6, max(abs(c-o), c*.00001), color=color, linewidth=0))
        for col, color in zip(["s20", "e20", "s60", "e60", "s120", "e120"], ["#59c6ba", "#86d2c9", "#6496cb", "#8bafd5", "#a5abb8", "#d2d8e0"]):
            ax.plot(x, view[col], color=color, linewidth=.8, alpha=.7)
        osc.plot(x, view.md, color="#6d9eff", linewidth=1.2); osc.plot(x, view.sb, color="#eeb465", linewidth=1.2)
        osc.axhline(0, color="#8797a7", linewidth=.6)
        gates = episode_admissions(b, context.cache["signals"], context.cache["bb"], context.cache["data_gap"])
        g = gates.loc[signal]
        si, ei = (i-left)/stride, (j-left)/stride
        prior = gates.loc[(gates.index < signal) & gates.episode.eq(g.episode) & gates.baseline & gates.first_break]
        if g.episode >= 0 and len(prior):
            first_clock = prior.index[0]
            q = int(b.index.get_loc(first_clock))
            if q >= left:
                qx, qy = (q-left)/stride, float(b.close.iloc[q])
                ax.scatter([qx], [qy], marker="^" if prior.side.iloc[0] == 1 else "v", color="#bac4d0", s=45, zorder=5)
                ax.annotate("Earlier C signal", (qx, qy), xytext=(0, 24), textcoords="offset points", color="#bac4d0", fontsize=8, ha="center",
                            arrowprops={"arrowstyle": "-", "color": "#bac4d0", "lw": .7})
        for panel in (ax, osc):
            panel.axvspan((i+.5-left)/stride, len(view), color="#527ea2", alpha=.08)
            panel.axvline(si, color="#edd586", linestyle="--", linewidth=1)
            panel.axvline(ei, color="#f194a4", linestyle=":", linewidth=1)
        for value in (g.upper, g.lower):
            if np.isfinite(value): ax.hlines(value, max(0, si-70/stride), si, color="#e4b960", linestyles="--", linewidth=1)
        entry_x = (int(b.index.get_loc(pd.Timestamp(match.entry_time)))-left)/stride
        ax.scatter([entry_x], [match.entry_price], marker="^" if row.side == 1 else "v", color="#edd586", s=70, zorder=5)
        ax.scatter([ei], [match.exit_price], marker="x", color="#f194a4", s=60, zorder=5)
        low, high = float(view.low.min()), float(view.high.max())
        pad = max(high-low, abs(high)*1e-6)*.05
        ax.set_ylim(low-pad, high+pad); ax.set_xlim(-1, len(view))
        ax.set_title(f"{label} | {row.venue.upper()} {row.symbol} {row.timeframe_min}m | "
                     f"{'LONG' if row.side == 1 else 'SHORT'} | baseline realized {row.net_r:.2f}R", loc="left", color="#e6edf5", fontsize=13, pad=15)
        ticks = np.linspace(0, len(view)-1, 7).astype(int)
        osc.set_xticks(ticks, [times[a].tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M") for a in ticks], fontsize=8)
        fig.text(.06, .052, f"C rule: {row.reason}. Gold vertical: signal bar; triangle: next-open entry. Pink cross: baseline exit.", color="#afbfca", fontsize=9)
        fig.text(.06, .035, "Gold horizontals: range known before the signal. Shading begins after the signal candle closes.", color="#afbfca", fontsize=9)
        fig.text(.06, .018, f"Shaded future is review only. Each displayed candle aggregates {stride} original bars. UTC+8.", color="#afbfca", fontsize=9)
        fig.subplots_adjust(left=.065, right=.97, top=.93, bottom=.12, hspace=.1)
        path = output/f"case_{k:02d}.png"; fig.savefig(path, dpi=130); plt.close(fig)
        receipts.append(dict(file=str(path), selection=label, asset=row.asset, timeframe=row.timeframe_min,
                             net_r=row.net_r, reason=row.reason, cache_sha256=context.receipt["cache_sha256"], stride=stride))
    (output/"figures.json").write_text(json.dumps(receipts, indent=2))
    return receipts


def table(frame, columns, percent=()):
    lines = ["| " + " | ".join(title for _, title in columns) + " |", "|" + "---|"*len(columns)]
    for _, row in frame.iterrows():
        cells = []
        for key, _ in columns:
            v = row[key]
            if key == "arm": v = LABELS[v]
            if key == "period": v = {"development": "开发年", "validation": "复用验证年", "full": "两年"}[v]
            if key == "timeframe_min": v = {30: "30m", 60: "1H", 240: "4H"}.get(v, v)
            if key in percent: v = f"{100*float(v):.2f}%" if pd.notna(v) else "N/A"
            elif key in {"base_winners10", "kept_winners10", "episode_winners10", "removed_losers", "removed_winners"} and pd.notna(v): v = str(int(v))
            elif isinstance(v, (float, np.floating)): v = f"{v:.4f}" if pd.notna(v) else "N/A"
            cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build(result, output):
    tables, parity = collect(result)
    summaries = summarize(tables, output)
    cases = render_cases(tables, output)
    universe = tables["accounts"].drop_duplicates("stream_key")
    coverage = universe.groupby(["venue", "timeframe_min"], as_index=False).agg(streams=("stream_key", "size"), assets=("asset", "nunique"))
    coverage.to_csv(output/"coverage.csv", index=False)
    acc, events, ret, effects, controls = (summaries[k] for k in ["account_summary", "event_summary", "retention_summary", "paired_effect", "matched_reference"])
    joined = events.merge(ret, on=["arm", "timeframe_min", "period"], how="left")
    joined["tail_retention"] = joined.kept_winners10/joined.base_winners10.replace(0, np.nan)
    checks = []
    for arm in ARMS[1:]:
        for minutes in [30, 60, 240]:
            part = joined.loc[joined.arm.eq(arm) & joined.timeframe_min.eq(minutes)]
            delta = effects.loc[effects.arm.eq(arm) & effects.timeframe_min.eq(minutes)]
            passed = len(part) == 2 and part.signal_reduction.ge(.30).all() and part.tail_retention.ge(.80).all() and len(delta) == 2 and delta.delta.gt(0).all()
            checks.append(dict(arm=arm, timeframe_min=minutes, descriptive_target_pass=bool(passed)))
    checks = pd.DataFrame(checks); checks.to_csv(output/"targets.csv", index=False)
    report = Path("analysis/p1_spike_v7_first_launch_20260912.md")
    content = ["# V7 同一段压缩的首次启动：固定对照研究",
        f"**本轮 {int(checks.descriptive_target_pass.sum())}/6 个方案×周期组合同时达到预定探索目标。** 目标是开发年和复用验证年都减少至少30%信号、保留至少80%的原版已兑现10R交易，并提高配对平均独立账户收益。即使达到，也不能视为通过盲测或允许部署。",
        "只改入场许可：B为原版V7；A为同一压缩段只保留首次原版信号；C为该段首次收盘突破已知压缩价格边界的原版信号。A和C的首次消耗各自独立，C可以选中较晚的原版信号；这轮不在没有原信号的K线上补造确认。",
        f"共{universe.asset.nunique()}个基础资产标识、{len(universe)}个数据流。基础资产标识相同用于跨交易所聚类；并不保证不同交易所同名代币在经济上完全等价。",
        table(coverage, [("venue", "交易所"), ("timeframe_min", "周期min"), ("streams", "数据流"), ("assets", "基础资产标识")]),
        "## 信号减少与大赢家保留",
        table(joined.loc[joined.arm.ne("baseline")], [("arm", "方案"), ("timeframe_min", "周期min"), ("period", "区间"), ("signals", "信号数"), ("signal_reduction", "信号减少"), ("base_winners10", "原版兑现≥10R"), ("kept_winners10", "原位保留"), ("tail_retention", "保留率"), ("episode_winners10", "同段同方向有入场"), ("removed_losers", "原亏损交易未保留"), ("removed_winners", "原盈利交易未保留")], ["signal_reduction", "tail_retention"]),
        "未保留原亏损交易不等于避免全部亏损：过滤改变持仓占用，可能产生新增交易，下表统计完整重放的真实模拟成交。‘同段有入场’也不等于兑现了相同收益，不能代替原位保留率。无≥10R分母时保留率N/A，不凑成100%。",
        "## 独立账户：不是一个全市场共享账户",
        "每个交易所×币种×周期从10,000资金开始；目标初始风险1%，含入场费用名义金额不超过权益1倍。跨年持仓继承，期末按已知收盘估值。无信号账户也在均值中。",
        "沿用当前Python引擎，收盘确认、次根开盘模拟入场；这也符合TradingView策略默认的下一可用tick成交时序。[TradingView策略文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)。更换为Freqtrade也仍然需要选择K线内成交假设，不能自动获得实盘真实性；其文档明确说明默认开盘入场和区间内无滑点成交等假设。[Freqtrade回测假设](https://www.freqtrade.io/en/stable/backtesting/#assumptions-made-by-backtesting)。",
        table(acc.loc[acc.period.ne("full")], [("arm", "方案"), ("timeframe_min", "周期min"), ("period", "区间"), ("streams", "账户数"), ("mean_return", "平均净收益"), ("median_return", "中位净收益"), ("mean_dd", "平均收盘最大回撤"), ("worst_dd", "最坏回撤")], ["mean_return", "median_return", "mean_dd", "worst_dd"]),
        "## 相对B的配对收益差",
        table(effects, [("arm", "方案"), ("timeframe_min", "周期min"), ("period", "区间"), ("delta", "收益差pp"), ("low", "95%下界"), ("high", "95%上界"), ("p", "单侧p"), ("q", "12比较BH q")], ["delta", "low", "high"]),
        "收益差列百分数是百分点差。按基础币种整体聚类2000次重采样/符号置换，同币跨交易所一起变化；未同时对市场时间冲击聚类，因此显著性不能作为部署许可。",
        "## 每笔交易结果",
        table(events, [("arm", "方案"), ("timeframe_min", "周期min"), ("period", "区间"), ("entries", "入场"), ("closed", "自然退出"), ("censored", "边界估值"), ("win_rate", "净胜率"), ("pf", "事件PF"), ("realized_10r", "兑现≥10R"), ("mfe_10r", "持有期间曾浮盈≥10R"), ("fallback_signals", "前缀未知放行")], ["win_rate"]),
        "事件PF按单位名义金额净收益统计，不能替代按账户冻结仓位计算的账户收益。路径最高浮盈不是可成交利润。",
        "## 多空分别统计",
        table(summaries["side_summary"], [("arm", "方案"), ("timeframe_min", "周期min"), ("period", "区间"), ("side", "方向1多/-1空"), ("trades", "自然退出"), ("win_rate", "净胜率"), ("mean_net_r", "平均净R"), ("realized_10r", "兑现≥10R")], ["win_rate"]),
        "## 误删原因",
        table(summaries["rejection_reasons"], [("arm", "方案"), ("timeframe_min", "周期min"), ("period", "区间"), ("reason", "原因"), ("trades", "原交易未保留"), ("missed_winners10", "其中≥10R赢家"), ("removed_losers", "其中亏损")]),
        "episode_consumed：此前同段已有许可；inside_envelope：当时收盘尚未突破旧边界；first/first_escape：信号本身获准，但不同的先前持仓占用导致没有在原时点成交。原因来自当时已知状态。",
        "## 匹配随机事件对照",
        table(controls, [("arm", "方案"), ("timeframe_min", "周期min"), ("period", "区间"), ("sampled", "抽取目标"), ("matched", "可匹配"), ("delta", "平均净超额pp"), ("low", "95%下界"), ("high", "95%上界"), ("p", "单侧p")], ["delta", "low", "high"]),
        "每个流/方向/方案/年度最多抽1笔自然退出，按时间+方向SHA排序，不按盈亏选择；随机点匹配同流、方向、月份、由前120根ATR/价格形成的波动四分位桶。保持相同历史就绪门、退出和0.2%成本。无法匹配的逐项保留；只用seed0，为稀疏事件参照，不能解释成一个随机实盘账户。AUC与top-decile排序不适用于本次没有连续评分的硬规则，使用配对原版和随机事件两类零假设。",
        "## 全局案例图",
        "以下按复用验证年C保留赢家、误删赢家、删去亏损各选最多2例，属于结果已知的解释图，不参与特征或参数选择。阴影区域是信号当时未知的后续走势。",
    ]
    interpretation = EXP/"INTERPRETATION.md"
    if interpretation.exists():
        content.insert(2, interpretation.read_text())
    cn = {"Retained winner": "保留的赢家", "Missed winner": "被误删的赢家", "Removed loser": "被过滤的亏损"}
    for case in cases:
        content.append(f"### {cn[case['selection']]}：{case['asset']} {case['timeframe']}m，原版兑现{case['net_r']:.2f}R\n\nC判定原因：`{case['reason']}`。\n\n![{cn[case['selection']]}](../{case['file']})")
    content.extend(["## 因果与复现核验", f"完整{parity['complete_streams']}流；与上一轮已认证V7基线逐项比较了{parity['all_baseline_rows_checked']}笔，包含{parity['censored_rows_checked']}笔边界/缺口估值。交易身份、方向、价格、止损、净收益、数量与退出原因一致。合成测试验证后续K线不能改变前缀信号，拒绝反向入场不会关闭反向退出保护。",
        "压缩三连结束点相隔≤10根时连接为同一段，属于明确的新结构假设；不是每次压缩标志暂时关闭就算新段。边界只由合格压缩K线构成，不覆盖所有间隔K线。信号使用前一根收盘已经知道的边界；当前K线不能扩大自己的比较边界。原始V6实体站上六线允许前后结构确认，未额外要求同一根。",
        "## 风险与诚实声明",
        "数据2024-09-10至2026-09-10，30m/1H/4H，Binance/OKX/Gate已冻结的3531流。新上市合约历史可能不足两年，当前目录存在存活偏差。同币跨交易所数据不独立，不能把流数叫币种数。Owner已授权所有历史日期；这是该配置第2次尝试接触含holdout的历史、第1次完成全量比较；这些日期以前已经研究过，不是新盲测。第1次results_v1因缓存前缀结构错误停止，1803个完整回执作废保留，未解读其聚合收益。smoke是同配置小范围执行核验。",
        "没有新调参数网格，没有改变退出/成本。未包含真实资金费率、订单簿冲击、标记价格强平及完整下单精度；回撤基于收盘盯市，可能低估盘中回撤。不能将此结果转成10%风险实盘建议。没有共享资金、多币相关风险或同时成交容量模型，这些是后续独立工作。",
        "## 下一步",
        "按开发和复用验证两段一起判读，不因少提示就称为更赚钱。不通过目标的方案保留失败证据；若减少噪音同时误删大行情，先看误删图解释机制，不继续在同一验证年搜索阈值。后续可单独检验明确失效退出或前向冻结验证，当前Pine/监控/通知不自动更换。",
        "## 复现命令", f"```bash\n.venv/bin/python -m pytest -q tests/evaluation/test_spike_v7_episode_study.py tests/evaluation/test_spike_v7_episode_report.py\nOMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -W ignore::FutureWarning -m yoyo.evaluation.spike_v7_episode_study --output {result}\n.venv/bin/python -W ignore::FutureWarning -m yoyo.evaluation.spike_v7_episode_report --result {result} --output {output}\n.venv/bin/python scripts/md_to_html.py {report} --out-dir analysis/html\n```",
    ])
    report.write_text("\n\n".join(content).replace("周期min", "周期")+"\n")
    receipt = dict(**parity, builder_sha256=sha256(Path(__file__)), engine_manifest_sha256=sha256(result/"manifest.json"),
                   interpretation_sha256=sha256(interpretation) if interpretation.exists() else None,
                   report_sha256=sha256(report), files={p.name: sha256(p) for p in output.iterdir() if p.is_file() and p.name != "report_manifest.json"},
                   generated_at=pd.Timestamp.now(tz="UTC").isoformat())
    (output/"report_manifest.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(parity), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--result", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(); build(args.result, args.output)
