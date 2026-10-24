"""Write source-backed Chinese interpretation into the existing MA6 report data.

Only frozen summary/funnel/metric exports are read. This narrative builder does
not run signals, choose parameters, simulate fills, or alter historical returns.
"""
from __future__ import annotations
import json
import pandas as pd
from yoyo.evaluation.monday_ma6_analysis import ART,EXP,read
from yoyo.evaluation.range_framework import dump

LABEL={'monday_full':'周一对侧全平','opposite_1618_full':'另一侧1.618全平','half_each':'两个目标各半'}
ASSET={'BTC':'BTC','ETH':'ETH','XAUUSD':'XAUUSD','ALTCOINS':'山寨组'}


def number(value,digits=3):
    return '无可计算值' if pd.isna(value) else f'{float(value):,.{digits}f}'


def pct(value):
    return '无可计算值' if pd.isna(value) else f'{100*float(value):.2f}%'


def main():
    payload=json.loads((ART/'summary.json').read_text())
    if payload.get('complete') is not True:raise RuntimeError('Interpret only the complete native universe')
    s=read(ART/'report-data/summary.csv.gz');f=read(ART/'report-data/funnel.csv.gz')
    all_=s[s.period.eq('all')]
    lines=[
        '## 这次究竟测试了什么',
        '1.618首次触价只启动观察。首次完整15分钟收盘越过六均线，后面连续三根收盘仍在同侧，再回踩最高均线（空头为最低均线）挂单。累计第三根刷新不利极值、入场前已回周一区间等条件会终止尚未成交的机会。均线挂价和止损只在完整15分钟收盘后更新，成交后止损冻结。',
        '**本轮是三种退出比较：** 周一对侧边界全平、另一侧1.618全平、两个目标各平一半。多头目标是周一高点及上方1.618，空头目标是周一低点及下方1.618。余仓保持原止损，没有TP1后保本，也没有固定持仓时间止盈。未成交挂单到下个纽约周一00:00失效；已成交仓位仍使用原周锚点。',
        f'完整回放覆盖 {payload["completed_symbols"]} 个品种，记录 {payload["source_opportunities"]:,} 个观察机会、{payload["submitted_orders"]:,} 个策略及随机挂单、{payload["native_positions"]:,} 条原生目标腿和 {payload["native_fills"]:,} 次原生成交。三个退出版本不能当成三倍独立机会；随机对照也不计入策略交易笔数。',
        '## 各组收益、胜率与盈亏比',
        '以下为各自已闭合交易的统计。平均盈亏比是“平均盈利÷平均亏损绝对值”，计划RR是入场到目标距离÷止损距离；两者不等同。R是每笔按原始风险归一后的独立收益，不是资金账户收益率。',
    ]
    for asset in ['BTC','ETH','XAUUSD','ALTCOINS']:
        a=all_[all_.asset_group.eq(asset)].set_index('exit_mode')
        lines.append(f'### {ASSET[asset]}'+('（只报毛收益）' if asset=='XAUUSD' else '（扣成交手续费后）'))
        for mode in LABEL:
            r=a.loc[mode]
            lines.append(f'- **{LABEL[mode]}：** {int(r.closed_n):,} 笔已闭合，{int(r.censored_n):,} 笔未决；胜率 {pct(r.win_rate)}，平均 {number(r.mean_r)}R，盈利因子 {number(r.profit_factor)}。平均盈利 {number(r.mean_win_r)}R、平均亏损 {number(r.mean_loss_r)}R，实际盈亏比 {number(r.payoff)}；单笔名义收益均值 {number(r.mean_bp,2)}bp。')
        best=a.mean_r.idxmax();r=a.loc[best]
        threshold=1/(1+r.payoff) if pd.notna(r.payoff) and r.payoff>0 else float('nan')
        lines.append(f'该组全期已闭合均R最高的是“{LABEL[best]}”，但这只是本次固定三方案的描述性排序。按该方案实际平均盈亏比，维持同等盈亏幅度时的打平胜率约 {pct(threshold)}；实际胜率为 {pct(r.win_rate)}。'+('黄金的券商费用、买卖价差未知，不能把毛均值当作净盈利。' if asset=='XAUUSD' else f'其平均成交手续费为 {number(r.mean_fee_r)}R，毛均值 {number(r.mean_gross_r)}R，净均值 {number(r.mean_r)}R。'))
        later=s[(s.asset_group.eq(asset))&s.period.eq('later')&s.exit_mode.eq(best)].iloc[0]
        lines.append(f'同一退出方式在2023年起的后期：{int(later.closed_n):,} 笔、胜率 {pct(later.win_rate)}、平均 {number(later.mean_r)}R；前后期按实际入场柱开始时间划分，跨切点持仓剔除。所有历史此前均可用于研究，这不是未使用过的新留出集。')
    lines += ['## 分批止盈到底改变了什么',
        '为排除远端目标尚未结束造成的分母差异，下面只比较三种退出都已闭合的相同入场。排除未决只用于这个对齐比较；全部未决记录仍在明细和上一节保留。']
    for asset in ASSET:
        a=all_[all_.asset_group.eq(asset)].set_index('exit_mode')
        near,far,half=(a.loc[x] for x in LABEL)
        lines.append(f'- **{ASSET[asset]}：** 共同闭合 {int(half.common_closed_n):,} 笔；周一对侧全平 {number(near.common_mean_r)}R / 胜率 {pct(near.common_win_rate)}，另一侧1.618全平 {number(far.common_mean_r)}R / {pct(far.common_win_rate)}，各半 {number(half.common_mean_r)}R / {pct(half.common_win_rate)}。各半方案中，{int(half.tp_legs_then_sl_n):,} 笔出现一腿止盈、余腿止损。')
    lines.append('在这个没有保本、移动止损或仓位相互影响的设定中，每笔各半收益严格等于两种全平收益的平均值。因此共同样本的各半平均R必然处于两种全平之间；它可能改变胜率和兑现时间，但不能凭线性分仓创造额外均值。要判断TP1后保本，必须另做会改变后续成交路径的测试，不能从本轮结果直接推断。')
    lines += ['## 浮盈回吐、连亏与等待成本',
        '浮盈只取入场柱之后、退出柱之前的完整5分钟柱。入场和退出柱内的真实先后未知，因此下面是“至少发生过”的保守数量；没有计入不能证明没浮盈。各半的MFE/MAE描述原始价格风险尺度，不是已减仓后的账户浮动盈亏。']
    for asset in ASSET:
        a=all_[(all_.asset_group.eq(asset))&all_.exit_mode.eq('half_each')].iloc[0]
        span=json.loads(a.single_symbol_streak_span)
        lines.append(f'- **{ASSET[asset]}各半：** 至少 {int(a.profitable_excursion_then_sl_n):,} 笔曾有正向浮盈后发生止损，其中最终整体亏损 {int(a.losing_profitable_then_sl_n):,} 笔；'+('费用未知，不能报扣费保本后的浮盈数量。' if asset=='XAUUSD' else f'至少 {int(a.losing_fee_positive_then_sl_n):,} 笔曾覆盖预估市价退出费用、最后仍整体亏损。')+f'持仓中位数 {number(a.median_holding_hours,2)} 小时，P90 {number(a.p90_holding_hours,2)} 小时。按退出时间合并排序最长连亏 {int(a.longest_loss_streak)} 笔、两腿都止损最长连续 {int(a.longest_stop_streak)} 笔；单一品种最长连亏 {int(a.longest_single_symbol_loss_streak)} 笔'+(f'（{span.get("symbol")}，{span.get("first_exit")} 至 {span.get("last_exit")}）' if span.get('symbol') else '。'))
    lines.append('跨币合并连亏会受到同一行情的相关交易和同一时刻排序影响，不代表单仓账户的连续交易过程；可按明细ID检查对应区间。多个重叠独立持仓也不能直接合成为资金曲线。')
    lines += ['## 相对于随机挂单时机，确认条件有没有增益',
        '对照在同币、同纽约周、同方向、同因果波动桶中选择其他合格挂单时机，使用当时六均线、相同止损缓冲、周一目标与费用规则。由于当时均线不同，绝对止损距离和计划RR可以不同。严格比较要求每个策略信号的三个随机对照全部成交、闭合并满足分期匹配；机会期望另把未成交计作零暴露，不能混作逐笔胜率。']
    for asset in ASSET:
        r=all_[(all_.asset_group.eq(asset))&all_.exit_mode.eq('half_each')].iloc[0]
        lines.append(f'- **{ASSET[asset]}各半：** 严格完整配对 {int(r.fully_matched_signal_n):,} 组；配对策略 {number(r.matched_signal_mean_r)}R、随机均值 {number(r.matched_control_mean_r)}R，差 {number(r.paired_excess_r)}R；按市场周聚类的95%区间 [{number(r.paired_ci_low)}, {number(r.paired_ci_high)}]R，多重比较校正p={number(r.get("paired_p_holm"),4)}。提交队列完整配对 {int(r.complete_opportunity_pairs):,} 组，机会期望差 {number(r.attempt_paired_excess_r)}R/机会。')
    lines.append('区间跨零或有效配对太少时，不能把正的点估计写成稳定优势；少亏于随机也不等于自身盈利。完整三对照要求会筛掉部分机会，因此原始全体收益与严格配对收益分别展示。')
    lines += ['## 机会为什么消失，以及数据边界']
    for asset in ASSET:
        q=f[(f.asset_group.eq(asset))&f.period.eq('all')].set_index('stage').n
        lines.append(f'- **{ASSET[asset]}：** 首次触价 {int(q.get("first_touch",0)):,} 次，完成四根确认 {int(q.get("confirmed",0)):,} 次，提交策略挂单 {int(q.get("submitted",0)):,} 次，成交 {int(q.get("filled",0)):,} 次，到期未成交 {int(q.get("expired",0)):,} 次，其他撤销或取整无效 {int(q.get("cancelled",0)):,} 次。失效原因、改单与成交区间均可查下方明细。')
    far=all_[all_.exit_mode.eq('opposite_1618_full')]
    lines.append(f'另一侧1.618数学目标落在非正价格的已成交交易共 {int(far.unreachable_target_n.sum()):,} 笔；这些目标不可达，原始记录保留并标注，不能虚构止盈。两种单目标方案的退出柱同时覆盖TP与SL共 {int(all_[all_.exit_mode.ne("half_each")].same_bar_target_stop_n.sum()):,} 条目标腿；本轮采用框架O-H-L-C顺序，不能据此还原真实柱内先后。')
    lines.append('加密数据来自既有Binance历史，费用采用2026-10-23记录的OKX Lv1情景：挂单入场/目标0.02%，保护止损及边界结算0.05%，每次成交分别计费。未模拟滑点、资金费、排队与容量。黄金为清理冲突后的HistData bid-only数据，未知成交量、买卖价差和费用。各品种来源截止时间不同，下面保留逐品种覆盖；全局最新时间不能当作所有品种共同终点。')
    lines.append('这轮只验证已确认的固定规则与三种退出，没有参数搜索，也没有调整生产资格。详细逐笔、盈亏样本及匹配随机一起保留，任何后续入场或保本改动都需要按新的交易语义另行验证。')
    payload['interpretation_markdown']='\n\n'.join(lines)
    for dest in [ART/'summary.json',EXP/'summary.json']:dump(dest,payload)
    print('Interpretation added to existing report metadata:',len(payload['interpretation_markdown']),'characters')


if __name__=='__main__':main()
