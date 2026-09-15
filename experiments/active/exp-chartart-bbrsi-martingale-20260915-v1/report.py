"""Render the frozen ChartArt sizing results without rerunning or selecting arms."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parent
OUT = EXP/'results'
REPORT = ROOT/'analysis/p1_chartart_bbrsi_martingale_20260915.md'


def fmt(value, places=2):
    return '不适用/无样本' if value is None else f'{value:.{places}f}'


def main():
    manifest = json.loads((OUT/'manifest.json').read_text())
    for rel, digest in manifest['files'].items():
        assert hashlib.sha256((OUT/rel).read_bytes()).hexdigest() == digest, rel
    rows = json.loads((OUT/'summary.json').read_text())
    started = json.loads((OUT/'started.json').read_text())
    inputs = json.loads((OUT/'inputs.json').read_text())
    cfg = started['config']
    cases = [r for r in rows if r['window'] == 'jan_apr']
    fig, ax = plt.subplots(figsize=(9,4.6))
    x = np.arange(len(cases)); width=.34
    for j,m in enumerate([1,2]):
        values=[next(a for a in r['accounts'] if a['multiplier']==m and a['margin_leverage']==1)['net_profit'] for r in cases]
        bars=ax.bar(x+(j-.5)*width, values,width,label='Fixed 100 USDT' if m==1 else 'Double after net loss',color=['#517caa','#e68b46'][j])
        ax.bar_label(bars,labels=[f'{v:+.2f}' for v in values],padding=4,fontsize=10)
    ax.set_xticks(x,[str(r['minutes'])+'m' for r in cases]);ax.axhline(0,color='#555',linewidth=.7)
    ax.set_ylabel('Net P&L (USDT), open position marked after cost')
    ax.set_title('OKX ETH | Jan 1 – May 1, 2026 UTC | 1x initial-margin budget')
    ax.legend(frameon=False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True);ax.margins(y=.3)
    fig.tight_layout();fig.savefig(OUT/'comparison.png',dpi=160,bbox_inches='tight');plt.close(fig)

    text=['# ChartArt BB＋RSI v1.1：原逻辑与亏损后翻倍\n',
          '本报告是固定规则的研究模拟，源策略与仓位层分别核查。未复现用户截图那段近期交易，未运行或部署实盘。\n',
          '## 原策略究竟在做什么\n',
          '- RSI使用收盘价、长度6，阈值是50/50；布林带使用SMA200、总体标准差乘2。\n',
          '- 多头要求同一根已收盘bar：RSI从≤50穿到>50，价格从下轨外回到下轨内；空头要求同bar RSI下穿50且价格回落到上轨内。必须两次cross同时发生。\n',
          '- 原版没有固定止损、固定止盈或移动保本。持仓等相反入场信号反手，同向不重复加仓。名称中的Double是两个指标共同确认，不是资金倍投。\n',
          '- strategy.entry里的stop是入场订单参数。条件成立时该触发价已经被越过，默认历史执行可简化为下一open。取消未成交订单不会平掉持仓；图表的背景颜色条件也不是入场条件。\n',
          '[作者原版](https://www.tradingview.com/script/uCV8I4xA-Bollinger-RSI-Double-Strategy-by-ChartArt-v1-1/) · [TradingView订单执行文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)\n',
          '## 用户截图是观察，不是本次验证结果\n',
          '|截图周期|显示日期|闭合笔数|显示胜率|显示PF|显示净利润|反推平均盈利/平均亏损|\n|---|---|---:|---:|---:|---:|---:|\n',
          '|15m|2026-06-01—09-15|31|80.65%|2.083|+821.14 USDT|约0.50|\n',
          '|3m|2026-08-24—09-15|17|76.47%|3.502|+271.97 USDT|约1.08|\n',
          '|1m|2026-09-07—09-15|27|85.19%|20.8|+579.69 USDT|约3.62|\n',
          '上述比例=截图PF×亏损笔数÷盈利笔数，受显示四舍五入影响。截图均显示初始资金100K，不能直接当成1000U账户收益；下单单位、手续费、滑点、保证金属性未核实。日期不一致，不能据此排周期优劣。截图15m高胜率仍可能对应平均一次亏损约两次盈利。\n',
          '## 数据与研究假设\n',
          f'本金{cfg["initial_cash"]}U，首单**名义仓位**{cfg["base_notional"]}U；不是每单风险1U。原版无初始止损，所以R、止损次数、10R单数不适用。固定仓与亏损后2倍分别模拟；净赢重置、净零维持，未平仓不改变下一档。\n',
          '每笔固定往返成本0.2%×入场名义价值，入场/出场各半，退出费也按入场本金；这与仓库旧研究口径一致，不代表OKX实际费表。没有额外资金费率或滑点。反手先结算再开新仓，两腿假设同open价、无延迟，因此倍投执行是乐观近似。\n',
          '1x/10x仅指初始保证金预算。下一档保证金加开仓成本超过余额即停止，不能偷偷封顶后重置；bar内净权益耗尽不可因后面反弹复活。未建模OKX维持保证金与阶梯费率，真实强平通常会更早，本报告不能提供精确爆仓概率或强平价。\n',
          '|源周期|源首bar UTC|批准前缀末bar闭合UTC|读取bar数|缺根/重复|\n|---|---|---|---:|---|\n']
    for receipt in inputs:
        text.append(f'|{receipt["minutes"]}m|{receipt["first_open"]}|{receipt["last_close"]}|{receipt["rows"]}|0/0|\n')
    text += ['\n所有计算≤2026-05-01，新增holdout消耗0次。1m仅4月18日起；3/5/15m共同1月1日起；3/15m另从2024年1月1日起。窗口分别空仓重置，较长窗口包含短窗口，**不是独立验证集**。没有从结果选参数。\n',
             '## 不倍投的单位交易：胜率、亏损幅度与随机对照\n',
             '单位收益用每笔入场名义本金归一；未平仓从胜率、PF和连亏排除。匹配对照是同币×同方向×同UTC月×决策时BB宽度桶的随机入场，同反向信号退出、同成本。\n',
             '|周期/窗口|闭合/未平|毛胜率|净胜率|PF净|均值毛/净bp|平均赢/亏bp|最大单亏bp|最长净连亏|随机净bp|匹配超额bp|周块p / Holm|\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n']
    for r in rows:
        m=r['metrics']
        text.append(f'|{r["minutes"]}m/{r["window"]}|{m["closed"]}/{m["open"]}|{fmt(None if m["gross_win_rate"] is None else m["gross_win_rate"]*100)}%|{fmt(None if m["net_win_rate"] is None else m["net_win_rate"]*100)}%|{fmt(m["profit_factor"])}|{fmt(m["mean_gross_bp"])}/{fmt(m["mean_net_bp"])}|{fmt(m["avg_win_bp"])}/{fmt(m["avg_loss_bp"])}|{fmt(m["max_loss_bp"])}|{m["max_consecutive_losses"]}|{fmt(m["control_mean_net_bp"])}|{fmt(m["excess_net_bp"])}|{fmt(m["p_value"],4)} / {fmt(m.get("p_holm"),4)}|\n')
    text += ['\n随机样本每笔20次固定seed抽取，保留censored且不补抽；表中超额只基于共同可闭合样本。周块符号置换4000次，6个窗口做Holm校正；少量周块不足以证明稳健。无概率模型，val AUC、top-decile与单特征排序不适用；原固定仓是仓位实验单变量基线，匹配随机是入场的零假设对照。\n',
             '## 1000U账户：固定仓 vs 亏损后翻倍\n',
             '所有末权益包含未平仓按收盘估值并预留退出费。回撤使用bar内假定价格路径，顺序为open→较近极值→另一极值→close。每个账户表行的策略方向背景对照见上表对应窗口，不能把重叠随机交易拼成可执行账户。\n',
             f'![共同窗口账户比较]({OUT/"comparison.png"})\n',
             '|周期/窗口|保证金预算|仓位规则|末权益U|净盈亏U|最大回撤U/%|最高执行/请求倍数|已平笔数|亏后首赢仍未回本的循环|停止原因|\n|---|---:|---|---:|---:|---:|---:|---:|---:|---|\n']
    for r in rows:
        for a in r['accounts']:
            text.append(f'|{r["minutes"]}m/{r["window"]}|{a["margin_leverage"]}x|{"固定" if a["multiplier"]==1 else "亏后2倍"}|{a["ending_equity"]:.2f}|{a["net_profit"]:+.2f}|{a["max_drawdown_amount"]:.2f}/{a["max_drawdown_pct"]:.2f}%|{a["max_executed_multiplier"]:g}/{a["max_requested_multiplier"]:g}|{a["closed_trades"]}|{a["negative_closed_cycles"]}|{a["stopped_reason"] or "未停止"}|\n')
    text += ['\n“亏后首赢仍未回本”指连续净亏后第一次净赢结束的循环总和仍为负，不包括还没结束的亏损循环。保证金不足不是已经爆仓，也不能标成完成整个区间。最高请求倍数包含因资金不足未执行的下一档。\n',
             '## 风险与诚实声明\n',
             '- 这是原版交易语义的独立Python重建，未在用户TV逐笔比对。UI属性、历史预热起点和旧Pine版本差异可能影响截图笔数；不声称已经复现截图。\n',
             '- 图表高胜率与倍投可持续性是不同问题：无固定止损使每次亏损幅度不同，下一笔翻倍后的盈利不保证盖住上一笔亏损。表内用真实循环净额检验。\n',
             '- 没有优化入场或止盈止损，没有加保本。不得将这些结果套到V9，或把观察性好成绩标记为实盘准入。没有训练、promote或真金改动。\n',
             '- 本轮只消费5月以前价格。用户提供的近期截图已有结果暴露，不能作为盲测；复核其逐笔必须另行记录该新配置的明确holdout授权。\n',
             '## 复现与逐笔文件\n',
             f'冻结builder提交：`{started["source_commit"]}`。源码证据见source_receipt.json，实际输入及所有结果SHA见results/inputs.json和manifest.json。原作者完整源码仅保存在本地source/；不重新公开发布。\n',
             '```bash\ncd /Users/zhangzc/fable-trading\n.venv/bin/python -m pytest -q tests/evaluation/test_chartart_bbrsi.py tests/evaluation/test_chartart_bbrsi_study.py tests/evaluation/test_chartart_martingale_account.py\n# Builders/config/plan/tests must already match committed HEAD. Results path must not exist.\n.venv/bin/python -m yoyo.evaluation.chartart_bbrsi_study\n.venv/bin/python experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/report.py\n.venv/bin/python scripts/md_to_html.py analysis/p1_chartart_bbrsi_martingale_20260915.md --out-dir analysis/html\n```\n',
             '完整结果含每笔方向、进出价、净收益、最大浮亏与浮盈，倍投下单数额、循环亏损、停止记录；密集账户曲线本地保留，哈希入manifest。\n']
    for r in rows:
        folder=OUT/f'{r["minutes"]}m_{r["window"]}'
        text.append(f'- {r["minutes"]}m/{r["window"]}（{r["start"]}—{r["end"]}）：[原交易]({folder/"unit_trades.csv"}) · [1x保证金倍投]({folder/"m2_l1_trades.csv"}) · [10x保证金倍投]({folder/"m2_l10_trades.csv"})\n')
    text += ['\n## 下一步\n\n先依据本表判断倍投是否改善同一窗口；若要对应截图31/17/27笔，则需确认TV属性并授权这套新配置读取截图期间历史，冻结后只作最终核对。任何另加固定止损、分批止盈或保本均属于后续新实验。\n']
    REPORT.write_text(''.join(text))
    print(REPORT)


if __name__ == '__main__':
    main()
