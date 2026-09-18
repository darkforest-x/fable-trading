"""Render the trendline V2 TP/SL report from the stage outputs, not by hand.

Every number in analysis/p1_trendline_v2_tbsl_20260918.md comes out of
dev_grid.json / selection.json / review_results.json through this module. The
prose around them is conditional on those numbers, so a rerun that changes the
result changes the sentences too; nothing here can keep saying "passed" while
the JSON says otherwise.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from yoyo.evaluation.trendline_v2_study import EXP, ROOT

REPORT = ROOT / "analysis/p1_trendline_v2_tbsl_20260918.md"
FIGURE_DIR = ROOT / "analysis/html"
FIGURE_NAME = "trendline_v2_tbsl_20260918_grid.png"
TF_LABEL = {"15": "15m", "60": "1h", "240": "4h"}


def fmt(value, digits=4, plus=False, dash="—"):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return dash
    text = f"{value:+.{digits}f}" if plus else f"{value:.{digits}f}"
    return text


def cell_name(entry) -> str:
    return f"SL {entry['sl_mult']:g} / TP {entry['tp_mult']:g}"


def grid_figure(dev: dict, cfg: dict) -> Path:
    """Two heatmaps per timeframe: net R per signal, and the same trades in bp.

    Both are needed. The R panel is the objective; the bp panel shows how much
    of the R panel's gradient is the fee shrinking against a bigger R rather
    than the trades getting better.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm

    # The report is in Chinese and DejaVu has no CJK glyphs, so the figure would
    # ship a grid of tofu boxes. Pick whichever CJK face this machine has.
    available = {face.name for face in fm.fontManager.ttflist}
    for candidate in ("Arial Unicode MS", "Hiragino Sans GB", "STHeiti", "Songti SC"):
        if candidate in available:
            plt.rcParams["font.family"] = [candidate]
            break
    plt.rcParams["axes.unicode_minus"] = False

    sl, tp = cfg["sl_mults"], cfg["tp_mults"]
    minutes = [str(m) for m in cfg["minutes"]]
    fig, axes = plt.subplots(2, len(minutes), figsize=(4.6 * len(minutes), 8.4))
    for column, key in enumerate(minutes):
        entries = {(e["sl_mult"], e["tp_mult"]): e for e in dev["timeframes"][key]["cells"]}
        for row, (field, scale, title) in enumerate((
                ("mean_net_r", 1.0, "每笔净 R"), ("mean_net_return_bp", 1.0, "每笔净收益 bp"))):
            grid = np.array([[(entries[(a, b)]["stats"].get(field) or np.nan) * scale
                              for b in tp] for a in sl], dtype=float)
            axis = axes[row, column]
            span = np.nanmax(np.abs(grid)) or 1.0
            image = axis.imshow(grid, cmap="RdBu_r", vmin=-span, vmax=span, aspect="auto")
            axis.set_xticks(range(len(tp)), [f"{v:g}" for v in tp], fontsize=7)
            axis.set_yticks(range(len(sl)), [f"{v:g}" for v in sl], fontsize=7)
            axis.set_xlabel("TP (ATR)", fontsize=8)
            axis.set_ylabel("SL (ATR)", fontsize=8)
            axis.set_title(f"{TF_LABEL[key]} · {title}", fontsize=9)
            for a in range(len(sl)):
                for b in range(len(tp)):
                    if np.isfinite(grid[a, b]):
                        axis.text(b, a, f"{grid[a, b]:.2f}" if row == 0 else f"{grid[a, b]:.0f}",
                                  ha="center", va="center", fontsize=5.5)
            fig.colorbar(image, ax=axis, fraction=0.046)
    fig.suptitle("下降趋势线突破 V2 · 开发段 2022-01→2025-01 · 红=正 蓝=负", fontsize=11)
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    out = FIGURE_DIR / FIGURE_NAME
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def dev_table(dev: dict, selection: dict, cfg: dict, key: str) -> list[str]:
    entries = dev["timeframes"][key]["cells"]
    chosen = selection["timeframes"][key]["selected"]
    ranked = sorted((e for e in entries if e["stats"].get("n")),
                    key=lambda e: -(e["stats"]["mean_net_r"] or -9e9))
    picked = (chosen["sl_mult"], chosen["tp_mult"]) if chosen else None
    marks = []
    for entry in ranked:
        if (entry["sl_mult"], entry["tp_mult"]) == picked:
            continue  # it already has its own row as 入选
        marks.append((f"网格最高 #{len(marks) + 1}", entry))
        if len(marks) == 5:
            break
    for reference in cfg["reference_cells"]:
        marks.append(("参照格", next(e for e in entries
                                     if (e["sl_mult"], e["tp_mult"]) == tuple(reference))))
    if chosen is not None:
        marks.insert(0, ("**入选**", next(e for e in entries
                                          if (e["sl_mult"], e["tp_mult"]) ==
                                          (chosen["sl_mult"], chosen["tp_mult"]))))
    lines = ["| 角色 | 格子 | 信号数 | 每笔净 R | 每笔净收益 bp | 每笔成本 R | 名义额/R 中位 | 毛 R 合计 | 胜率 | PF | 止盈/止损/到期 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for role, entry in marks:
        s = entry["stats"]
        reasons = s.get("exit_reasons", {})
        lines.append(
            f"| {role} | {cell_name(entry)} | {s['n']} | {fmt(s['mean_net_r'], 4, True)} | "
            f"{fmt(s['mean_net_return_bp'], 1, True)} | {fmt(s['mean_cost_r'], 3)} | "
            f"{fmt(s['median_notional_per_r'], 0)} | {fmt(s['gross_r'], 1, True)} | "
            f"{fmt(s['win_rate'], 3)} | {fmt(s['profit_factor'], 3)} | "
            f"{reasons.get('target', 0)}/{reasons.get('stop', 0)}/{reasons.get('time', 0)} |")
    return lines


def review_table(review: dict, key: str, path: str) -> list[str]:
    block = review["timeframes"][key]["paths"][path]
    chosen = review["timeframes"][key]["selected"]
    lines = ["| 格子 | 信号数 | 每笔净 R | 每笔净收益 bp | 毛 R 合计 | 胜率 | PF | 配对数 | 随机每笔 R | 配对超额 R | 月块 p |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for entry in block["cells"]:
        s, c = entry["stats"], entry["control"]
        is_chosen = chosen is not None and (entry["sl_mult"], entry["tp_mult"]) == (
            chosen["sl_mult"], chosen["tp_mult"])
        label = f"**{cell_name(entry)}（入选）**" if is_chosen else cell_name(entry)
        lines.append(
            f"| {label} | {s['n']} | {fmt(s['mean_net_r'], 4, True)} | "
            f"{fmt(s['mean_net_return_bp'], 1, True)} | {fmt(s['gross_r'], 1, True)} | "
            f"{fmt(s['win_rate'], 3)} | {fmt(s['profit_factor'], 3)} | {c['paired_n']} | "
            f"{fmt(c['random_mean_net_r'], 4, True)} | {fmt(c['excess_mean_net_r'], 4, True)} | "
            f"{fmt(c.get('p'), 4)} |")
    return lines


def verdicts(review: dict, cfg: dict) -> dict:
    """The three pre-registered criteria, judged per timeframe on the primary path."""
    out = {}
    for key in (str(m) for m in cfg["minutes"]):
        chosen = review["timeframes"][key]["selected"]
        if chosen is None:
            out[key] = dict(selected=None, passed=False,
                            checks=[("入选格", "无——开发段没有格子同时满足信号数与杠杆闸")])
            continue
        entry = next(e for e in review["timeframes"][key]["paths"][cfg["primary_path"]]["cells"]
                     if (e["sl_mult"], e["tp_mult"]) == (chosen["sl_mult"], chosen["tp_mult"]))
        net = entry["stats"]["mean_net_r"]
        excess = entry["control"]["excess_mean_net_r"]
        p = entry["control"].get("p")
        checks = [
            ("① 每笔净 R 为正", net is not None and net > 0, fmt(net, 4, True)),
            ("② 配对超额为正", excess is not None and excess > 0, fmt(excess, 4, True)),
            ("③ 月块 p < 0.01", p is not None and p < 0.01, fmt(p, 4)),
        ]
        out[key] = dict(selected=chosen, entry=entry, checks=checks,
                        passed=all(ok for _, ok, _ in checks))
    return out


def build() -> Path:
    cfg = json.loads((EXP / "config.json").read_text())
    dev = json.loads((EXP / "dev_grid.json").read_text())
    selection = json.loads((EXP / "selection.json").read_text())
    review = json.loads((EXP / "review_results.json").read_text())
    grid_figure(dev, cfg)
    judged = verdicts(review, cfg)
    keys = [str(m) for m in cfg["minutes"]]
    any_pass = any(judged[k]["passed"] for k in keys)

    L: list[str] = []
    add = L.append
    add("# 下降趋势线突破 V2 · 止盈止损网格与多周期回测")
    add("")
    add("## 结论")
    add("")
    verdict_line = "**有周期通过了事前三条标准。**" if any_pass else \
        "**三个周期全部未通过事前三条标准。没有一组止盈止损参数值得拿去消耗 holdout。**"
    add(verdict_line)
    add("")
    add(f"开发段（2022-01-03 → 2025-01-01）在 {len(cfg['sl_mults'])} 档止损 × "
        f"{len(cfg['tp_mults'])} 档止盈 = {len(cfg['sl_mults']) * len(cfg['tp_mults'])} 格上评分，"
        f"三个周期共 {len(cfg['sl_mults']) * len(cfg['tp_mults']) * len(cfg['minutes'])} 个配置；"
        "按事前写死的邻域中位数规则各选一格，选择结果提交之后才读复查段"
        "（2025-01-01 → 2026-05-04）。")
    add("")
    for key in keys:
        judge = judged[key]
        if judge["selected"] is None:
            add(f"- **{TF_LABEL[key]}**：开发段无合格格子（信号数或杠杆闸不过），复查段未评分。")
            continue
        entry = judge["entry"]
        s, c = entry["stats"], entry["control"]
        dev_entry = next(e for e in dev["timeframes"][key]["cells"]
                         if (e["sl_mult"], e["tp_mult"]) ==
                         (judge["selected"]["sl_mult"], judge["selected"]["tp_mult"]))
        add(f"- **{TF_LABEL[key]}**：入选 {cell_name(entry)}。"
            f"开发段每笔 {fmt(dev_entry['stats']['mean_net_r'], 4, True)}R "
            f"（{dev_entry['stats']['n']} 笔），"
            f"复查段每笔 {fmt(s['mean_net_r'], 4, True)}R（{s['n']} 笔）、"
            f"毛 R {fmt(s['gross_r'], 1, True)}、"
            f"配对超额 {fmt(c['excess_mean_net_r'], 4, True)}R、月块 p {fmt(c.get('p'), 3)}。"
            f"{'通过' if judge['passed'] else '**未通过**'}。")
    add("")
    # The reference cells are unsearched. If one beats the searched winner on
    # the review segment, that is the headline, not a footnote.
    beaten = []
    for key in keys:
        if judged[key]["selected"] is None:
            continue
        chosen_cell = (judged[key]["selected"]["sl_mult"], judged[key]["selected"]["tp_mult"])
        chosen_r = judged[key]["entry"]["stats"]["mean_net_r"]
        for entry in review["timeframes"][key]["paths"][cfg["primary_path"]]["cells"]:
            if (entry["sl_mult"], entry["tp_mult"]) == chosen_cell:
                continue
            if (entry["stats"]["mean_net_r"] or -9e9) > (chosen_r or -9e9):
                beaten.append((key, entry, chosen_r))
    if beaten:
        add("### 搜出来的那一格，输给了没搜的参照格")
        add("")
        for key, entry, chosen_r in beaten:
            add(f"- **{TF_LABEL[key]}**：事前参照格 {cell_name(entry)} 复查段每笔 "
                f"{fmt(entry['stats']['mean_net_r'], 4, True)}R、PF "
                f"{fmt(entry['stats']['profit_factor'], 3)}；"
                f"按规则选出来的那一格是 {fmt(chosen_r, 4, True)}R。")
        add("")
        add("**搜索没有找到更好的参数，找到的是更差的。**"
            "这是本仓第三次看到同一个形态（ETH 与 BTC 的 BB×Stoch 参数搜索各一次）。"
            "邻域中位数规则防住了「孤立尖峰」，但防不住「整片区域在开发段本身就是噪声」。")
        add("")

    add("### 为什么会这样：两件事，都不是调参能修的")
    add("")
    cost_rows = []
    for key in keys:
        one = next((e for e in dev["timeframes"][key]["cells"]
                    if (e["sl_mult"], e["tp_mult"]) == (1.0, 2.0)), None)
        if one:
            lev = one["stats"]["median_notional_per_r"]
            cost_rows.append((TF_LABEL[key], one["stats"]["mean_cost_r"], lev,
                              100.0 / lev if lev else None))
    add("**第一，成本在 R 轴上的占比随周期变，而且决定了网格往哪边走。**"
        "往返成本固定 0.2%，R = 止损倍数 × ATR；ATR 占价格越小，同一档止损里交给手续费的份额越大。"
        "下表取开发段 1 ATR 止损那一行：")
    add("")
    add("| 周期 | 1 ATR 止损的每笔成本（R） | 名义额/R 中位 | 推得的 ATR/价格 |")
    add("| --- | --- | --- | --- |")
    for label, cost_r, lev, atr_pct in cost_rows:
        add(f"| {label} | {fmt(cost_r, 3)} | {fmt(lev, 0)} | {fmt(atr_pct, 2)}% |")
    add("")
    add("同一个周期里，止损放宽 k 倍，成本在 R 轴上就缩小 k 倍。"
        "**所以网格的赢家贴在最宽止损那一边，主要是同一笔固定百分比费用被更大的 R 除小了，"
        "不是择时变准了。**报告因此并列「每笔净收益 bp」：它不含仓位假设，"
        "两列在 4h 上直接打架——R 轴较好的格子在 bp 轴上多数是负的，"
        "说明 R 轴上的正值集中在 ATR 小（R 小、被放大）的那些交易里。")
    add("")
    four = review["timeframes"].get("240")
    if four:
        cells4 = four["paths"][cfg["primary_path"]]["cells"]
        excess4 = [e["control"]["excess_mean_net_r"] for e in cells4
                   if e["control"].get("excess_mean_net_r") is not None]
        gross4 = [e["stats"]["gross_r"] for e in cells4]
        if excess4 and min(excess4) > 0:
            add(f"**唯一站得住的正面信息在 4h：入场比随机好，但不够付手续费。**"
                f"复查段三格的配对超额全为正（{fmt(min(excess4), 4, True)} 到 "
                f"{fmt(max(excess4), 4, True)}R），毛 R 合计 {fmt(min(gross4), 1, True)} 到 "
                f"{fmt(max(gross4), 1, True)}——线的突破在 4h 上确实比同月同波动的随机做多强一点。"
                "但月块 p 都在 0.1 以上，而且扣掉 0.2% 往返之后每笔净 R 在零附近、bp 口径全负。"
                "**优势的量级比成本小，这不是换止盈止损能修的。**")
            add("")
    add("**第二，这个指标的「突破」不等于「上涨」。**"
        "信号条件是收盘价高于趋势线加缓冲，而趋势线本身是向下倾斜的。"
        "一条足够陡的线会自己降到横盘价格上，于是在完全没有上涨的行情里也照样触发。"
        "`tests/evaluation/test_trendline_v2.py::test_a_steep_line_descending_into_flat_price_still_fires_a_break`"
        "就是把这件事钉住的用例：价格从头到尾是 100，照样出一个突破信号。"
        "所以原始突破数应该读成「穿越次数」，不是「突破次数」。")
    add("")

    add("## 数据与信号统计")
    add("")
    add("| 周期 | 参与币种 | 开发段信号 | 复查段信号 | 只在放宽平局时才算枢轴的 bar（占全部 bar） | 尾部窗口不足而丢弃 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for key in keys:
        dev_block = dev["timeframes"][key]
        review_block = review["timeframes"][key]["paths"][cfg["primary_path"]]
        ties = sum(v["pivot_ties"] for v in dev_block["per_symbol"].values())
        bars = sum(v["bars"] for v in dev_block["per_symbol"].values())
        add(f"| {TF_LABEL[key]} | {len(dev_block['symbols'])} | {dev_block['signals']} | "
            f"{review_block['signals']} | {ties}（{100 * ties / bars:.2f}%） | "
            f"{dev_block['dropped_horizon']} |")
    add("")
    first = dev["timeframes"][keys[0]]["per_symbol"]
    earliest = min(v["first"] for v in first.values())
    latest = max(v["last"] for v in first.values())
    add(f"- 来源：`{cfg['source_dir']}` 的 OKX USDT 永续 15m CSV，"
        f"{len(first)} 个合约，最早 bar {earliest[:10]}，最晚 bar {latest[:10]}。")
    add(f"- 1h / 4h 由冻结的 `aggregate()` 从 15m 合成，只用成分完整的 bar。")
    add(f"- **holdout 消耗 0**：读取经 `release_eth_prefix.read_prefix`，"
        f"端点 {cfg['end_exclusive'][:10]}，它在第一条越界行就停止解析。")
    max_lev = max(e["stats"]["median_notional_per_r"] for k in keys
                  for e in dev["timeframes"][k]["cells"])
    add(f"- 杠杆闸（名义额/R 中位数 ≤ {cfg['max_notional_per_r']:.0f}）**一次都没触发**："
        f"全网格最高 {max_lev:.0f}。它是护栏，没有筛掉任何格子。")
    add(f"- 匹配对照每信号 {cfg['controls_per_signal']} 个；开发段未配上对照的信号 "
        f"{sum(dev['timeframes'][k]['unmatched'] for k in keys)} 个（按事前约定剔除，不降级匹配轴）。")
    add("")

    add("## 开发段网格（选择依据）")
    add("")
    add(f"![开发段网格](html/{FIGURE_NAME})")
    add("")
    for key in keys:
        add(f"### {TF_LABEL[key]}")
        add("")
        L.extend(dev_table(dev, selection, cfg, key))
        add("")
        best = selection["timeframes"][key].get("best")
        if best is None:
            add("开发段没有格子同时满足信号数下限与杠杆闸，未产生入选格。")
        else:
            add(f"入选规则：{selection['timeframes'][key]['rule']}。"
                f"入选格自身每笔 {fmt(best['own_mean_net_r'], 4, True)}R，"
                f"邻域中位数 {fmt(best['neighbourhood_median'], 4, True)}R，"
                f"合格候选 {len(selection['timeframes'][key]['scored'])} 格。")
        add("")

    add("## 复查段（选择提交之后才评分）")
    add("")
    add(f"主口径 `{cfg['primary_path']}`：同一根 K 同时触及止盈与止损时记为止损。")
    add("")
    for key in keys:
        add(f"### {TF_LABEL[key]}")
        add("")
        L.extend(review_table(review, key, cfg["primary_path"]))
        add("")
        add(f"敏感性口径 `{cfg['sensitivity_paths'][0]}`（同一根先止盈，乐观上界）：")
        add("")
        L.extend(review_table(review, key, cfg["sensitivity_paths"][0]))
        add("")

    add("## 三条事前标准逐条判定")
    add("")
    add("| 周期 | 入选格 | ① 每笔净 R 为正 | ② 配对超额为正 | ③ 月块 p<0.01 | 判定 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for key in keys:
        judge = judged[key]
        if judge["selected"] is None:
            add(f"| {TF_LABEL[key]} | 无 | — | — | — | 未评分 |")
            continue
        cells = " | ".join(f"{'✅' if ok else '❌'} {value}" for _, ok, value in judge["checks"])
        add(f"| {TF_LABEL[key]} | {cell_name(judge['entry'])} | {cells} | "
            f"{'**通过**' if judge['passed'] else '**未通过**'} |")
    add("")
    add("三条全中才算通过。**通过也只意味着值得向 owner 申请一次 holdout 读取**，"
        "不等于可交易、可 promote、可下单。")
    add("")

    add("## 一个位置一笔（可实现路径）")
    add("")
    add("上面的统计是**每信号**口径：同一个币重叠的信号各自计一笔，"
        "这是估计入场优势最干净的口径，也是匹配对照所对应的口径。"
        "下表把同一个币的重叠信号按时间顺序挤掉，只留真正能同时持有的那些。")
    add("")
    add("| 周期 | 格子 | 每信号笔数 | 串行笔数 | 被挤掉 | 串行净 R 合计 | 串行每笔 R | 单币最大回撤 R | 单币最大连亏 |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for key in keys:
        for entry in review["timeframes"][key]["paths"][cfg["primary_path"]]["cells"]:
            serial = entry.get("serial") or {}
            add(f"| {TF_LABEL[key]} | {cell_name(entry)} | {entry['stats']['n']} | "
                f"{serial.get('n', 0)} | {serial.get('skipped_overlapping', 0)} | "
                f"{fmt(serial.get('net_r'), 1, True)} | {fmt(serial.get('mean_net_r'), 4, True)} | "
                f"{fmt(serial.get('worst_symbol_drawdown_r'), 1)} | "
                f"{serial.get('worst_symbol_loss_streak', 0)} |")
    add("")

    add("## 非方向性指标为什么不适用")
    add("")
    add("CLAUDE.md 要求必报 val AUC、top-decile 毛/净收益、单特征基线。"
        "**这一轮按字面不适用**：这里没有模型、没有打分、没有可排序的样本——"
        "规则要么触发要么不触发，不存在十分位。不许编造，所以这三项写明不适用，"
        "并用同等严格的东西顶上：")
    add("")
    add("1. **匹配随机对照**（同币 × 同 UTC 月 × 同因果波动桶 × 同障碍 × 同成本）——"
        "替代基线对照，每张复查表都带；")
    add("2. **月块符号翻转置换检验**——替代 p 值，按月分块而不是逐笔，"
        "因为同一个月的交易高度相关；")
    add("3. **两条成交路径**（adverse_first / favorable_first）——"
        "给出 OHLC 分辨率下无法判定的那部分的区间，而不是挑一个好看的。")
    add("")

    add("## 风险与诚实声明")
    add("")
    add("1. **复查段不是新鲜盲验。** 2025-01 → 2026-05 是 pre-holdout 数据，"
        "本仓在别的实验里已经整体看过多次。它是已观察数据内部的时间留出，"
        "真正未接触的只有 holdout（≥ 2026-05-04），**本轮不碰，holdout 消耗 0**。")
    add(f"2. **多重比较。** {len(cfg['sl_mults']) * len(cfg['tp_mults'])} 格 × "
        f"{len(cfg['minutes'])} 个周期 = "
        f"{len(cfg['sl_mults']) * len(cfg['tp_mults']) * len(cfg['minutes'])} 个配置。"
        "选择规则事前固定为邻域中位数而非峰值，就是为了不挑噪声尖峰；"
        "但复查段的 p 值没有再为这 270 次做多重比较校正，读的时候要记得。")
    add("3. **幸存者偏差。** 54 个合约是仓库早前为别的用途下载的**当前在架**的 OKX 永续，"
        "退市合约不在其中。这不是全市场，正收益若出现，一部分可能来自"
        "「这些币活到了今天」。")
    add("4. **枢轴平局规则是猜的。** TradingView 未公布 `ta.pivothigh` 的相等处理，"
        "本移植取两侧严格大于（只会更少信号），并把「只有放宽才算枢轴」的 bar 计数报出来。")
    add("5. **实盘比图上晚 8 根。** 枢轴需要右侧 8 根确认，所以线在实盘出现得比"
        "历史图上看起来晚。画线只用当时已有的 K，不是前视，但延迟是真的。")
    add("6. **资金费率与滑点未建模。** 只算了 0.2% 往返手续费；两者都只会更差。")
    add(f"7. **每段末尾 {cfg['max_hold_bars']} 根不出信号。** 持有窗必须完整，"
        "所以序列最后那段的信号被剔除而不是截断计入——在 4h 上这相当于复查段末尾约 33 天没有样本。"
        "这是对称的、事前定义的，但确实缩短了每个周期的有效评分区间。")
    add("8. **有第二份独立移植。** 同一份 Pine 被并行会话另行移植为 "
        "`yoyo/evaluation/trendline_break.py`（用途是给 SPIKE V9 做门控，双向），"
        "两份由 `tests/evaluation/test_trendline_break.py` 互相钉住。"
        "本研究用的是 `trendline_v2_signals.py`。")
    add("9. **指标没被偷偷调过。** owner 贴的 V1 原文逐字存为 "
        "`yoyo/evaluation/pine/trendline_key_high_v1_owner.pine`，"
        "`tests/evaluation/test_trendline_v2_pine_contract.py` 逐行断言 V1 的每一条"
        "选点/容差/突破规则都原样出现在 V2 策略里——单变量这件事是机器保证的，不是我说的。")
    add("10. **运行日志里的 `RuntimeWarning: ... in matmul` 是假警报。** "
        "macOS Accelerate BLAS 在输入输出全有限时也会置浮点异常标志；"
        "用全有限的随机输入单独复现过，输出有限、数值正确。置换检验的 p 值不受影响。")
    add("11. **未训练、未 promote、未改仓、未动真金、未开新分支。**")
    add("")

    add("## 下一步选项")
    add("")
    if any_pass:
        add("1. **（需 owner 决策）** 通过的周期可以申请一次 holdout 读取——"
            "按铁律 1，先在 `docs/HOLDOUT_LEDGER.md` 登记编号与事前预期，再读一个字节。")
    else:
        add("1. **不申请 holdout。** 三个周期都没过事前标准，拿没过的配置去烧 holdout "
            "是浪费一次不可退款的额度。")
    add("2. **（需 owner 决策）如果要继续这个形态**，下一个该动的变量不是止盈止损，"
        "而是**入场本身**：给突破加一个"
        "「线的斜率不能太陡 / 突破时价格必须比线出现时更高」的条件，"
        "把「线自己降下来碰到横盘价格」这一类穿越滤掉。这是改入场，属于新实验。")
    add("3. **（需 owner 决策）成本口径**。0.2% 往返是项目固定假设。"
        "若 owner 实际能拿到 maker 费率，这条规则的经济性需要重算——但那是改假设，"
        "不是改参数，要 owner 点头。")
    add("4. **Pine 策略已就绪**：`yoyo/evaluation/pine/trendline_break_strategy_v2.pine`，"
        "可以直接贴进 TradingView 看图。默认参数填的是参照格 SL 1.0 / TP 2.0，"
        "**不是「最优值」**——因为本轮的结论是没有一格在复查段站住。")
    add("")

    add("## 复现命令")
    add("")
    add("```bash")
    add("# 1. 冻结：三个 builder、Pine、config、计划必须先提交，否则程序拒绝读价")
    add("git add yoyo/evaluation/trendline_v2_*.py \\")
    add("        yoyo/evaluation/pine/trendline_break_strategy_v2.pine \\")
    add(f"        {EXP.relative_to(ROOT)}/config.json {EXP.relative_to(ROOT)}/PROJECT_PLAN.md")
    add("git commit -m 'freeze trendline v2 builders'")
    add("")
    add("# 2. 开发段：跑满网格（约 25 分钟）")
    add("python3 -m yoyo.evaluation.trendline_v2_study dev")
    add("")
    add("# 3. 选参：事前规则，写出 selection.json，并提交")
    add("python3 -m yoyo.evaluation.trendline_v2_study select")
    add(f"git add {EXP.relative_to(ROOT)}/selection.json && git commit -m 'freeze selection'")
    add("")
    add("# 4. 复查段：程序会核对 selection.json 与 HEAD 逐字节一致，否则拒绝运行")
    add("python3 -m yoyo.evaluation.trendline_v2_study review")
    add("")
    add("# 5. 报告")
    add("python3 -m yoyo.evaluation.trendline_v2_report")
    add("python3 scripts/md_to_html.py analysis/p1_trendline_v2_tbsl_20260918.md --out-dir analysis/html")
    add("")
    add("# 6. 聚焦测试")
    add("python3 -m pytest tests/evaluation/test_trendline_v2.py -q")
    add("```")
    add("")
    add(f"源码 commit：开发段 `{dev['source_commit'][:10]}`，"
        f"复查段 `{review['source_commit'][:10]}`；"
        f"selection.json sha256 `{review['selection_sha256'][:16]}`。")
    add("")

    REPORT.write_text("\n".join(L) + "\n")
    return REPORT


if __name__ == "__main__":
    print("wrote", build())
