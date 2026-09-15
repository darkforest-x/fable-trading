"""Render the frozen MA Shift/Stoch result without rerunning price research."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import pandas as pd
import numpy as np
from yoyo.evaluation.ma_shift_stoch_study import EXP, ROOT


def num(value, digits=2):
    return '不适用' if value is None else f'{value:,.{digits}f}'


def table(headers,rows):
    return '\n'.join(['|'+'|'.join(headers)+'|','|'+'|'.join(['---']*len(headers))+'|',*['|'+'|'.join(map(str,r))+'|' for r in rows]])+'\n'


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    out=EXP/'results'; summary=json.loads((out/'summary.json').read_text()); arms=summary['arms']
    names={'ma_stoch':'15m颜色 + 5m Stoch','stoch_only':'单用5m Stoch'}
    fig,ax=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
    for arm,color,label in [('ma_stoch','#008e83','15m MA filter + 5m Stoch'),('stoch_only','#bf6f29','5m Stoch only')]:
        curve=pd.read_csv(out/f'{arm}_equity_curve.csv'); x=pd.to_datetime(curve.time,utc=True)
        eq=curve.equity.to_numpy(float); ax[0].plot(x,eq,label=label,color=color,lw=1.5)
        ax[1].plot(x,100*(eq/np.maximum.accumulate(eq)-1),color=color,lw=1.2)
    ax[0].axhline(1000,color='#888888',ls='--',lw=.7);ax[0].legend(loc='best')
    ax[0].set_ylabel('Equity (USDT)');ax[1].set_ylabel('Drawdown (%)');ax[1].set_xlabel('UTC date')
    ax[0].set_title('ETH perpetual | 1x equity notional | 20 bp round trip | closed-bar signals')
    for a in ax: a.grid(alpha=.15)
    fig.tight_layout();fig.savefig(out/'equity.png',dpi=150);plt.close(fig)
    a,b=arms['ma_stoch'],arms['stoch_only'];src=summary['source'];receipt=json.loads((out/'evaluation_started.json').read_text())
    md=['# ETH 近一月：15分钟颜色 × 5分钟 Stoch\n',
        f'**反向箭头全平主方案：1000U → {num(a["final_equity"])}U，净收益 {num(a["mtm_return_pct"])}%，最大持仓回撤 {num(a["mtm_max_drawdown_pct"])}%。** 已平仓{a["n"]}笔，净胜率{num(a["net_win_rate"]*100 if a["net_win_rate"] is not None else None)}%，期末持仓{a["open_count"]}笔。\n',
        f'同区间单独用Stoch：{num(b["mtm_return_pct"])}%。加入15m颜色后的账户收益变化为{num(a["mtm_return_pct"]-b["mtm_return_pct"])}个百分点；这是一段已暴露历史的冻结规则回测，不是生产准入结论。\n',
        '## 1. 你当前图上的两个指标\n',
        'TradingView桌面实查为OKX:ETHUSDT.P、普通K线。MA Shift [ChartPrime]设为SMA40、hl2；K线染色由hl2相对SMA决定。Stoch为你保存的「翻身版本V1-2025-07-06」，K/D为5/3/3。\n',
        '- 多头：最近已收盘15m的hl2 ≥ SMA40(hl2)，且5m的K上穿D、交叉当根K和D都 <20。\n- 空头：最近已收盘15m的hl2 < SMA40(hl2)，且5m的K下穿D、交叉当根K和D都 >80。\n- 相反5m箭头收盘后，下一根开盘全平；退出不受15m方向限制。相反方向同时准入时允许平后反开。15m单独翻色不平仓。\n- 单仓、不加仓，无额外止损/固定盈利目标。分批触发点和比例尚未确定，本次没有替你挑选盈利目标。\n',
        'MA Shift的15/0.5振荡器参数不参与K线染色；Stoch的多头报警额外含WVF绿色柱条件，本次按图上箭头而非报警。公式来自当前编辑器，尚未做TradingView全部历史逐bar数值核验。[ChartPrime原始发布](https://www.tradingview.com/script/aApUyBnk-Moving-Average-Shift-ChartPrime/)\n',
        '## 2. 数据、成交和成本\n',
        f'北京时间2026-08-15 00:00—2026-09-15 18:15，冻结结束时间，不随运行延长。OKX ETH-USDT-SWAP原生5m，共{src["confirmed"]}根，其中720根只预热；区间9147根。缺根0、重复0、未收盘0；15m严格由同源完整三根聚合。\n',
        '信号只在收盘确认，下一根开盘成交；同收盘的5m可使用刚完成的15m，但较早5m不能看到该15m最终颜色。起点空仓，期末未平仓只按最后收盘估值并预留退出费，排除真实平仓胜率。\n',
        '资金示例每笔按入场前权益1倍名义建仓，初始1000U，逐笔复投；20bp往返以入场名义计，开平各10bp。没有额外资金费率或滑点，不能当成交易所真实账单。最大回撤从每根5m收盘估值计算，未模拟K线内最坏路径。\n',
        f'**这是该配置第1次消耗holdout。** 用户本次明确要求近一月；原预热和评估区间、单Stoch消融及匹配控制均提前冻结，未调参。旧策略曾观察本历史，不能称为盲OOS。源码冻结提交 `{receipt["source_commit"]}`。\n',
        '## 3. 结果与单变量基线\n']
    rows=[]
    for key,r in arms.items():
        c=r['control']; rows.append([names[key],r['candidates'],r['entries'],r['n'],f'{num(r["net_win_rate"]*100)}%',num(r['pf']),num(r['gross_mean_bp']),num(r['net_mean_bp']),f'{num(r["mtm_return_pct"])}%',f'{num(r["mtm_max_drawdown_pct"])}%',num(c.get('control_mean_bp')),num(c.get('excess_mean_bp'))])
    md += [table(['规则','准入箭头','入场','已平仓','净胜率','PF净收益比','单笔毛bp','单笔净bp','1x账户净收益','最大回撤','匹配随机净bp','配对超额bp'],rows),
           'PF采用逐笔净收益率正值之和/负值绝对值之和。每笔bp以该笔入场名义为分母；账户收益含复投和期末估值，不能把逐笔收益率直接相加冒充账户收益。没有旧版同策略，表中单Stoch是去掉MA过滤的消融基线。\n',
           f'![1x净权益与持仓回撤]({out/"equity.png"})\n',
           f'组合每笔平均毛收益{num(a["gross_mean_bp"])}bp，扣20bp后为{num(a["net_mean_bp"])}bp；单Stoch对应{num(b["gross_mean_bp"])}→{num(b["net_mean_bp"])}bp。MA改变准入及后续空仓机会，因此两组交易不是简单的一一删选关系。\n',
           '## 4. 多空与时间稳定性\n']
    rows=[]
    for key,r in arms.items():
        control=pd.read_csv(out/f'{key}_controls.csv')
        for label,side in [('long',1),('short',-1)]:
            v=r['sides'][label]; c=control[(control.side==side)&control.matched] if len(control) else control
            rows.append([names[key],'多' if side==1 else '空',v['n'],f'{num(None if v["net_win_rate"] is None else v["net_win_rate"]*100)}%',num(v['net_mean_bp']),num(v['pf']),num(c.control_net_return.mean()*1e4 if len(c) else None)])
    md.append(table(['规则','方向','已平仓','净胜率','平均净bp','PF','同方向匹配随机净bp'],rows))
    rows=[]
    for key,r in arms.items():
        targets=pd.read_csv(out/f'{key}_trades.csv')
        controls=pd.read_csv(out/f'{key}_controls.csv')
        midpoint=pd.Timestamp(summary['config']['start_utc'])+(pd.Timestamp(summary['config']['end_utc'])-pd.Timestamp(summary['config']['start_utc']))/2
        for label,v in r['entry_time_halves'].items():
            early=pd.to_datetime(targets.entry_time,utc=True)<midpoint
            ids=targets.loc[early if label=='earlier' else ~early,'trade_id']
            paired=controls[controls.target_trade_id.isin(ids)&controls.matched]
            rows.append([names[key],'前半窗' if label=='earlier' else '后半窗',v['n'],num(v['net_mean_bp']),num(v['pf']),num(paired.control_net_return.mean()*1e4 if len(paired) else None)])
    md += [table(['规则','按入场分段','已平仓','平均净bp','PF','分段匹配随机净bp'],rows),
           '上表仅按入场时间把连续回测账本分两半，不重开账户、不改退出，不用这两半选参数；对应随机对照完整保留在CSV（同UTC日桶），全期对照见主表。\n',
           f'组合最长连续净亏{a["longest_loss"]}笔；持仓中位{num(a["median_hold_minutes"])}分钟、最长{num(a["max_hold_minutes"])}分钟。最大单笔净收益{num(a["max_trade_net_pct"])}%、最小{num(a["min_trade_net_pct"])}%。无初始止损风险单位，因此R、10R频率不适用。\n',
           '## 5. 匹配随机对照和统计边界\n']
    rows=[]
    for key,r in arms.items():
        c=r['control']; rows.append([names[key],c.get('matched'),c.get('unmatched'),c.get('unique_controls'),c.get('utc_day_blocks'),num(c.get('target_mean_bp')),num(c.get('control_mean_bp')),num(c.get('excess_mean_bp')),num(c.get('p_greater'),4),num(c.get('p_two_sided'),4)])
    md += [table(['规则','配对数','未配对','独立控制键数','UTC日块','目标净bp','控制净bp','超额bp','单侧p','双侧p'],rows),
           '每个已平目标抽一个同ETH、同方向、同UTC日、同20根均值真实波幅/close固定桶的随机入场；不看未来结果重抽。控制使用同一反向箭头退出、同成本。随机交易可能跨目标重复或重叠，因此只作事件对照，不能把它们相加为真实单仓账户。少数删失配对被列出，目标/控制均已平仓才入配对检验。\n',
           '预设9999次UTC日块符号置换，单侧检验正超额、双侧检验差异；零假设依赖日块交换对称，仅为描述性配对检验。约一个月、跨日持仓及有限波动桶可能残留相关性，p值不证明因果或未来盈利。\n',
           '**必报项的适用性：** 无模型训练、验证集或预测概率，val样本数/val AUC不适用；没有预先定义排序分数，top-decile毛净收益不适用，不能用事后收益排序来凑指标。替代证据是全部冻结交易、单Stoch消融、匹配随机对照、按日块置换。\n',
           '## 6. 风险与诚实声明\n',
           '- 本次只对反向箭头全平给结论，分批止盈仍待确定规则。没有止损意味着持仓风险只由未来反向箭头终止，表内历史最大亏损不是风险上限。\n- 15m使用收盘确认颜色；实时正在形成的颜色可能变化，本研究没有重建盘中颜色变化。\n- 20bp为固定研究成本，资金费率、额外滑点、强平、交易量冲击均未建模，净结果可能进一步变差。\n- 周期与标的为用户指定；单月可能受行情主导，已经暴露的历史不适合继续挑最佳参数。\n- 当前公式和时间处理经源码及定向测试核对，未宣称TradingView策略测试器与Python全部成交逐笔一致。\n- 研究结果不能自动promote，training_eligible与production_eligible均为false。\n',
           '## 7. 复现、验证与交付\n',
           '```bash\ncd /Users/zhangzc/fable-trading\n.venv/bin/python -m yoyo.data.spike_eth_yolo_sources --config experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/config.json --output experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/sources\n.venv/bin/python -m pytest -q tests/test_ma_shift_stoch.py tests/test_spike_fanshen_exit.py\n.venv/bin/python -m yoyo.evaluation.ma_shift_stoch_study\n.venv/bin/python -m yoyo.evaluation.ma_shift_stoch_report\n```\n',
           '上面的研究runner拒绝覆盖已有summary；从零复现需在没有结果的新恢复副本中使用冻结源文件，或保留原results后单独安排重现版本。抓取器可验证相同配置缓存；以已冻结CSV SHA作为本次事实，不能把未来重新抓取后改变的行情视为同一输入。\n',
           f'来源CSV SHA256：`{src["csv_sha256"]}`。验证收据和独立核对随结果目录保存。\n',
           f'[组合全部逐笔CSV]({out/"ma_stoch_trades_zh.csv"}) · [组合成交份额账本]({out/"ma_stoch_fills.csv"}) · [随机配对CSV]({out/"ma_stoch_controls.csv"}) · [所有结果摘要]({out/"summary.json"})\n',
           '## 8. 下一步\n',
           '先按逐笔记录核对入场箭头和15m颜色。若继续比较分批止盈，需要Owner先定触发点与每批比例；新退出规则另建版本，保留本次失败或成功记录，不在当前月上反复择优后声称样本外有效。\n']
    trades=pd.read_csv(out/'ma_stoch_trades.csv')
    for col in ['entry_signal_time','entry_time','exit_signal_time','exit_time']:
        trades[col]=pd.to_datetime(trades[col],utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    trades['side']=trades.side.map({1:'做多',-1:'做空'})
    trades['gross_return']=trades.gross_return*100;trades['net_return']=trades.net_return*100
    trades.rename(columns={'trade_id':'序号','side':'方向','entry_signal_time':'入场信号收盘北京时间','entry_time':'入场北京时间','entry_price':'入场价','exit_signal_time':'退出信号收盘北京时间','exit_time':'退出北京时间','exit_price':'退出价','gross_return':'毛收益百分比','net_return':'净收益百分比'}).to_csv(out/'ma_stoch_trades_zh.csv',index=False,encoding='utf-8-sig')
    report=ROOT/'analysis/p1_ma_shift_stoch_eth_month_20260915.md';report.write_text('\n'.join(md))
    subprocess.run(['python3','scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    print(report)

if __name__=='__main__': main()
