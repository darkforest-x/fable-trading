"""Render the fixed trend study from verified saved evidence, without fitting.

All returns are historical static-cost research quantities. Charts consume the
same saved portfolio marks as tables; state contributions are additive PnL,
not a hypothetical strategy that trades only during retrospectively selected
months. Source rows and strategy parameters are never changed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation.altseason_trend_statistics import event_descriptives

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-altseason-donchian-ewmac-20260910-v1"
REPORT = ROOT / "analysis/p1_altseason_donchian_ewmac_20260910.md"
NAMES = {"D": "Donchian 4H", "E": "EWMAC 日线方向版", "D_E": "Donchian 入场＋EWMAC 退出",
         "random_D": "D 匹配随机", "random_E": "E 匹配随机", "random_D_E": "D_E 匹配随机", "buy_hold": "52币现金买持"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fmt(value, places=2):
    return "不足/不适用" if value is None or pd.isna(value) else f"{float(value):.{places}f}"


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
                     + ["| " + " | ".join(map(str, r)) + " |" for r in rows])


def charts(outdir, equity, events, states):
    assets = outdir / "charts"
    assets.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    colors = {"D": "#2563eb", "E": "#16a34a", "D_E": "#a855f7", "random_D": "#94a3b8", "buy_hold": "#f97316"}
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), layout="constrained")
    for col, fold in enumerate(("development", "validation")):
        for name, color in colors.items():
            g = equity.loc[equity.fold.eq(fold) & equity.portfolio.eq(name)].sort_values("bar_open")
            curve = g.equity.to_numpy(float)
            x = pd.to_datetime(g.bar_open, utc=True)
            axes[0, col].plot(x, (curve - 1) * 100, label=name, color=color, lw=1.5)
            peak = np.maximum.accumulate(np.r_[1., curve])[1:]
            axes[1, col].plot(x, (curve / peak - 1) * 100, color=color, lw=1.2)
        axes[0, col].set_title(fold.title() + " | fixed 52 sleeves")
        axes[0, col].set_ylabel("Static-cost portfolio return (%)")
        axes[1, col].set_ylabel("Close-marked drawdown (%)")
        for ax in axes[:, col]:
            ax.axhline(0, color="#64748b", lw=.6)
            ax.grid(alpha=.18)
            ax.tick_params(axis="x", rotation=25)
        axes[0, col].legend(ncol=3, fontsize=8)
    fig.savefig(assets / "equity_drawdown.png", dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), layout="constrained")
    for ax, fold in zip(axes, ("development", "validation")):
        g = states.loc[states.fold.eq(fold)]
        order = list(colors)
        base_positive = np.zeros(len(order)); base_negative = np.zeros(len(order))
        for state, color in (("strong", "#16a34a"), ("other", "#ef4444"), ("unknown", "#94a3b8")):
            v = g.loc[g.state.eq(state)].set_index("portfolio").reindex(order).pnl_contribution_pp.to_numpy(float)
            baseline = np.where(v >= 0, base_positive, base_negative)
            ax.bar(order, v, bottom=baseline, color=color, label=state)
            base_positive += np.maximum(v, 0); base_negative += np.minimum(v, 0)
        ax.axhline(0, color="#64748b", lw=.6)
        ax.set_title(fold.title() + " | state known at bar open")
        ax.set_ylabel("Additive PnL / initial capital (pp)")
        ax.legend()
    fig.savefig(assets / "state_contributions.png", dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), layout="constrained")
    for ax, arm in zip(axes, ("D", "E", "D_E")):
        g = events.loc[events.arm.eq(arm) & events.fold.eq("validation") & events.natural_exit.eq(True)]
        values = g.net_return.to_numpy(float) * 100
        if len(values):
            ax.hist(values, bins=50, color=colors[arm], alpha=.8)
            ax.axvline(0, color="#334155", lw=1)
        ax.set_title(f"{arm} | natural events n={len(values)}")
        ax.set_xlabel("Net event return (%)")
        ax.set_ylabel("Overlapping event count")
    fig.savefig(assets / "event_distribution.png", dpi=150)
    plt.close(fig)
    return assets


def build(outdir: Path, report: Path = REPORT):
    manifest = json.loads((outdir / "manifest.json").read_text())
    for name, record in manifest["artifacts"].items():
        if digest(outdir / name) != record["sha256"]:
            raise ValueError("Saved evidence changed: " + name)
    summary = pd.read_csv(outdir / "event_summary.csv")
    portfolios = pd.read_csv(outdir / "portfolios.csv")
    states = pd.read_csv(outdir / "state_contributions.csv")
    coverage = pd.read_csv(outdir / "coverage.csv")
    events = pd.read_csv(outdir / "events.csv.gz")
    ledger = pd.read_csv(outdir / "portfolio_ledger.csv.gz")
    equity = pd.read_csv(outdir / "portfolio_equity.csv.gz")
    exit_summary = pd.read_csv(outdir / "same_entry_exit_summary.csv")
    regime = pd.read_csv(outdir / "market_regime.csv.gz", index_col="bar_open", parse_dates=True)
    assets = charts(outdir, equity, events, states)
    rel = "../" + str(assets.relative_to(ROOT))
    passed = [r["arm"] for r in manifest["verdicts"] if r["research_gate_pass"]]
    verdict = "、".join(passed) + "通过本轮有限研究门；仍非上线验收。" if passed else "没有策略通过预先规定的完整研究门，不能宣称已找到可应对未来山寨季的盈利系统。"
    portfolio_lookup = portfolios.set_index(["fold", "portfolio"])
    state_lookup = states.set_index(["fold", "portfolio", "state"])
    observations = []
    for fold, label in (("development", "2023—2024"), ("validation", "2025")):
        d = portfolio_lookup.loc[(fold, "D")]
        control = portfolio_lookup.loc[(fold, "random_D")]
        strong_d = state_lookup.loc[(fold, "D", "strong"), "pnl_contribution_pp"]
        strong_control = state_lookup.loc[(fold, "random_D", "strong"), "pnl_contribution_pp"]
        observations.append(f"- {label}：D组合{fmt(d.net_pct)}%，同风险规则的匹配随机组合{fmt(control.net_pct)}%；强势时段利润贡献分别{fmt(strong_d)}和{fmt(strong_control)}个百分点。")
    lines = ["# 山寨强势行情：Donchian 与 EWMAC 固定规则回测", "", "> " + verdict, "",
             "研究日期：2026-09-10。结果覆盖2023—2025，只研究已冻结的52山寨便利池；正式holdout消耗0次。未来是否发生山寨季没有在本报告中作预测。", "",
             *observations, "",
             "解读：上涨阶段有利润，还必须检验相同市场条件的其他入场能否做得更好；低回撤也包含低资金占用的作用，不能全部归功于预测。下面完整列出开发、验证、匹配超额及每月账户。", "",
             "## 结论与实际账户表现", "",
             "下表为52个等初始资本分账户、同币单仓、每次1%分账户风险预算且现金封顶的历史模拟持仓路径。未上市或预热不足的份额保持现金。现金买持采用全额资本，风险水平不同；随机组合使用相同策略风险与退出。所有数字只扣固定0.2%入场名义往返成本，未含完整资金费。", ""]
    for fold in ("development", "validation"):
        lines += ["### " + ("开发：2023—2024" if fold == "development" else "验证：2025"), ""]
        rows = []
        for _, row in portfolios.loc[portfolios.fold.eq(fold)].iterrows():
            actual = ledger.loc[ledger.fold.eq(fold) & ledger.portfolio.eq(row.portfolio) & ledger.accepted.eq(True)]
            desc = event_descriptives(actual)
            rows.append([NAMES[row.portfolio], fmt(row.net_pct), fmt(row.mdd_pct), fmt(row.mean_exposure * 100),
                         len(actual) if row.portfolio != "buy_hold" else "不适用", desc["n_censored"] if row.portfolio != "buy_hold" else "边界清算",
                         fmt(desc["win_rate"] * 100 if desc["win_rate"] is not None else None), fmt(desc["profit_factor"])])
        lines += [table(["政策/同期对照", "组合净收益%", "收盘MDD%", "平均占用%", "实际成交", "边界清算", "自然胜率%", "自然PF"], rows), "",
                  "现金基准收益/回撤均为0。买持成交不在策略事件账本中，表中成交及胜率不适用。自然PF按交易收益率汇总，不等于资金金额PF。", ""]
    lines += [f"![真实组合与回撤]({rel}/equity_drawdown.png)", "", "## 行情好的时候究竟怎样", "",
              "事前强势代理：最近完整UTC日线的BTC高于EMA200，同时至少10个充分预热山寨中超过50%高于EMA50。它是透明的市场强势分类，不是证明山寨整体跑赢BTC的官方山寨季指数。分类不作本轮入场过滤。", "",
              "下面按每根4H开始时已知的状态归因实际持仓损益。各状态的利润贡献加总等于全期利润；不能把不连续强势时段拼接后称为可执行的年化策略。", ""]
    rows = []
    for _, row in states.loc[states.state.eq("strong")].iterrows():
        rows.append([row.fold, NAMES[row.portfolio], int(row.bars), fmt(row.pnl_contribution_pp)])
    lines += [table(["时间折", "政策/对照", "强势4H根数", "强势期利润贡献/初始资本pp"], rows), "",
              f"![强势和其他阶段贡献]({rel}/state_contributions.png)", "", "## 独立事件与匹配随机入场", "",
              "独立事件允许重叠，用于比较入场时机与退出，数量不能冒充实盘交易数。每个候选匹配同币、同月、同因果波动桶、同市场状态、同决策小时的非自身时点；映射不复用对照、允许其他信号时点入选。D和D_E共用匹配时点。缺样不放宽条件。", "",
              "等风险超额：各事件都以相同初始权益、1%风险预算、同现金上限计算；先每月跨币聚合，再做月份块置换和bootstrap。表内p为三个固定政策的Holm校正值；强势切片属于单独探索族。", ""]
    for cohort in ("all", "strong"):
        rows = []
        for _, row in summary.loc[summary.cohort.eq(cohort)].iterrows():
            rows.append([row.fold, row.arm, int(row.n_valid), int(row.n_natural), int(row.n_censored), int(row.matched_n),
                         fmt(row.mean_net_bp), fmt(row.matched_strategy_mean_net_bp), fmt(row.control_mean_net_bp), fmt(row.block_risk_excess_bp),
                         f"[{fmt(row.block_ci_low_bp)}, {fmt(row.block_ci_high_bp)}]", int(row.n_blocks), fmt(row.p_holm, 4)])
        lines += ["### " + ("全部入场" if cohort == "all" else "入场时为强势：完整交易结果"), "",
                  table(["折", "政策", "有效", "自然", "边界", "匹配", "全部事件净bp", "匹配策略净bp", "匹配对照净bp", "月均等风险超额bp", "95%CI", "月份块", "校正p"], rows), ""]
    diagnostic_rows = []
    for _, row in summary.loc[summary.cohort.eq("all")].iterrows():
        diagnostic_rows.append([row.fold, row.arm, int(row.n), fmt(row.n_positive / row.n_scored * 100 if row.n_scored else None),
                                fmt(row.mean_gross_bp), fmt(row.median_net_bp), fmt(row.win_rate * 100), fmt(row.profit_factor),
                                fmt(row.median_hold_hours / 24), fmt(row.mean_mfe_bp), fmt(row.median_capture_ratio * 100)])
    lines += ["### 事件分布补充", "",
              table(["折", "政策", "候选", "自然正类率%", "毛均bp", "净中位bp", "自然胜率%", "自然PF", "持有中位天", "平均MFE bp", "捕获中位%"], diagnostic_rows), "",
              "MFE为持仓期间事后最大有利位移，不是可以事前兑现的利润。捕获=净收益/MFE，MFE为0时不适用；亏损时可低于-100%，接近0的分母会放大均值，因此表中显示中位数，完整均值仍保留在CSV。", ""]
    exit_rows = []
    for _, row in exit_summary.iterrows():
        exit_rows.append([row.fold, row.cohort, int(row.paired_n), int(row.censored_either), fmt(row.mean_delta_net_bp),
                          fmt(row["mean"] * 10000), f"[{fmt(row.ci_low * 10000)}, {fmt(row.ci_high * 10000)}]", fmt(row.p, 4)])
    lines += ["## 固定同一入场：只换退出", "",
              "D_E减D；正值表示EWMAC退出更好。这里逐一核对相同币/时间/入场价，以独立事件比较退出本身，包含并单列折末删失；它不是两条占仓路径不同的账户曲线相减。p为探索性未校正月块检验，不参与三政策研究门。", "",
              table(["折", "入场状态", "同入场配对", "任一删失", "退出差额净bp", "月均等风险差bp", "95%CI", "探索p"], exit_rows), "",
              "## 排序诊断与赢家依赖", "",
              "预定分数：D/D_E为突破距离/ATR；E为固定日线EWMAC组合。AUC和top-decile仅在自然退出事件中描述，边界删失单列；名义前10%边界的同分全部保留，EWMAC限幅时实际top组可能超过10%。top组不是用于实盘的事后阈值。top p检验该组相对匹配随机的月块超额，不是把重叠逐笔当独立样本的排序检验。", ""]
    rows = []
    for _, row in summary.loc[summary.cohort.eq("all")].iterrows():
        rows.append([row.fold, row.arm, fmt(row.auc, 3), int(row.top_n), fmt(row.top_mean_gross_bp), fmt(row.top_mean_net_bp),
                     fmt(row.top_mean_matched_excess_bp), fmt(row.top_p_holm, 4),
                     fmt(row.top3_positive_profit_share * 100), fmt(row.net_ex_top3_mean_bp)])
    lines += [table(["折", "政策", "AUC", "top数", "top毛bp", "top净bp", "top匹配超额bp", "top校正p", "前三占正利润%", "去前三净均bp"], rows), "",
              "前三赢家依赖在此表针对重叠研究事件，不等于三笔实盘；完整实际成交金额与持有时长见portfolio_ledger。", "",
              f"![自然事件收益分布]({rel}/event_distribution.png)", "", "## 数据、规则与复现", "",
              f"来源54币，其中52山寨；{len(coverage.loc[coverage.symbol.isin(set(coverage.symbol)-{'BTC','ETH'}) & coverage.first_ready.notna()])}个山寨在截止前达到共同预热。共同256个完整日线预热会排除新币早期走势，不能将结果外推给刚上市的山寨。", "",
              "D：4H收盘突破此前20根high，下一open买入；收盘跌破此前10根low后下一open退出。E：日线8/32与32/128 EWMAC固定归一化，日闭合且正值才入场，非正退出。D_E只替换D退出。所有政策都有2ATR初始止损，无加仓、固定TP或30天强制到期。止损穿越开盘按更差open处理。", "",
              "本轮没有新训练或多参数搜索。D是快速Donchian改编，E是方向简化版，不等于Carver完整连续调仓系统。D与E整套比较不能解释一个指标的独立贡献；D对D_E独立事件才是固定入场的退出比较。旧ETH15m组合过滤实验的失败仍有效，不能用本轮覆盖。", "",
              "规则来源：[vn.py Turtle源码](https://github.com/vnpy/vnpy_ctastrategy/blob/6ef76981624bf55b2ea978f8587f74d633aafc72/vnpy_ctastrategy/strategies/turtle_signal_strategy.py)、[Carver示例配置](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/systems/provided/example/simplesystemconfig.yaml)。本轮改编参数以预注册和已提交源码为准。", "",
              f"源builder提交：`{manifest['generator_commit']}`。全部输入SHA、实际首末日期、warmup、随机映射、逐笔与曲线在本实验results/manifest.json及CSV中。", "",
              "```bash", "cd /Users/zhangzc/fable-trading", "git branch --show-current",
              ".venv/bin/python -m pytest -q tests/test_altseason_trend_data.py tests/test_altseason_trend_engine.py tests/test_altseason_trend_statistics.py tests/test_altseason_trend_research.py",
              ".venv/bin/python -m yoyo.evaluation.altseason_trend_research --out experiments/active/exp-altseason-donchian-ewmac-20260910-v1/reproduction",
              ".venv/bin/python -m yoyo.evaluation.altseason_trend_report --results experiments/active/exp-altseason-donchian-ewmac-20260910-v1/reproduction",
              "python3 scripts/md_to_html.py analysis/p1_altseason_donchian_ewmac_20260910.md --out-dir analysis/html",
              "```", "", "复现必须从该builder版本的相同源码和已有冻结输入运行；runner拒绝源码未提交、输入SHA变化或覆盖已有结果。没有源数据时不能仅凭报告重造行情。", "",
              "## 风险与诚实声明", "",
              "- 52币是现成便利/幸存者池，未完整覆盖历史退市币；结果不是历史全市场无偏估计。",
              "- 2023—2025已被其他实验研究过；本轮固定策略并无参数搜索，但2025不应称为研究者从未见过的盲测。",
              "- 静态0.2%成本未含完整资金费率和实际执行延迟/冲击；退出也按入场名义计费，上涨时未随退出名义金额增加。不能按这些曲线直接推算永续实盘收益。",
              "- 跨币高度相关、交易重叠、月份块较少，置信区间与p的解释依赖月份近似独立及符号可交换性。更少于6个月不作显著性结论。",
              "- 自然退出胜率/PF排除折末删失，可能偏向较短持仓；组合净收益包含边界清算。最大回撤是4H收盘盯市，不能代表完整盘中最大风险。",
              "- 未来是否出现山寨季、是否重复历史幅度、哪些币领涨都没有被本实验验证。本轮不产生交易指令或上线授权。", "",
              "## 下一步选项", "",
              "1. 若完整门未通过，保留负面结果；可以将规则作为离线观察基线，不能借强势片段宣布整体盈利。",
              "2. 若考虑永续应用，需要完整历史资金费与执行核对，再独立前向纸面观察；触及新holdout或改变成本/障碍须owner另行明确授权。",
              "3. 不把本次最赚钱的币、月份或参数拿来重新定义研究池；进一步研究必须另行预注册。", ""]
    monthly = []
    for fold in ("development", "validation"):
        base = equity.loc[equity.fold.eq(fold)].copy()
        base["bar_open"] = pd.to_datetime(base.bar_open, utc=True)
        wide = base.pivot(index="bar_open", columns="portfolio", values="equity").sort_index()
        month_key = (wide.index + pd.Timedelta(hours=4) - pd.Timedelta(nanoseconds=1)).strftime("%Y-%m")
        known_state = regime.strong.shift(1).reindex(wide.index)
        previous = pd.Series(1., index=wide.columns)
        for month in pd.unique(month_key):
            selected = month_key == month
            final = wide.loc[selected].iloc[-1]
            ret = (final / previous - 1) * 100
            monthly.append([fold, month, fmt(known_state.loc[selected].eq(True).mean() * 100),
                            *[fmt(ret[a]) for a in ("D", "E", "D_E", "buy_hold")]])
            previous = final
    lines += ["## 附录：每月账户表现", "", "列出全部月份，仓位跨月延续。强势比例使用每根bar开始时已知状态；未知仍计入月份分母。", "",
              table(["折", "UTC月份", "强势时间%", "D收益%", "E收益%", "D_E收益%", "买持收益%"], monthly), "",
              "## 附录：逐币共同预热覆盖", "",
              table(["币", "可交易4H根数", "首次共同ready的决策时间"],
                    [[row.symbol, int(row.ready_rows), str(row.first_ready) if pd.notna(row.first_ready) else "截止前未满足"]
                     for _, row in coverage.iterrows()]), ""]
    report.write_text("\n".join(lines))
    subprocess.run(["python3", "scripts/md_to_html.py", str(report.relative_to(ROOT)), "--out-dir", "analysis/html"], cwd=ROOT, check=True)
    html = ROOT / "analysis/html" / (report.stem + ".html")
    receipt = {"report": str(report), "report_sha256": digest(report), "html": str(html), "html_sha256": digest(html),
               "result_manifest_sha256": digest(outdir / "manifest.json"), "report_builder_sha256": digest(Path(__file__)),
               "charts": {p.name: digest(p) for p in assets.glob("*.png")}}
    (outdir / "report_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=EXP / "results")
    args = parser.parse_args()
    build(args.results.resolve())


if __name__ == "__main__":
    main()
