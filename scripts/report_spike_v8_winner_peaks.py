"""Render frozen winner-path diagnostics, figures and a read-only notebook.

Only precomputed diagnostic and previous exit-study artifacts are read. No
market source or new stop/target policy is evaluated by this reporting module.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from report_spike_fanshen import fmt,table

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/active/exp-spike-v8-ict-winner-peaks-20260915-v1'
REPORT=ROOT/'analysis/p1_spike_v8_ict_winner_peaks_20260915.md'
LABEL={'development':'2023年8月—2024年末','validation':'2025全年','common':'2026年1—4月','available':'连续2023年8月—2026年4月'}
URLBASE='../../experiments/active/'+EXP.name+'/'


def beijing(value):return pd.Timestamp(value).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')


def figures(profiles,paths):
    out=EXP/'figures';out.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,
        'figure.facecolor':'#fafbfe','axes.facecolor':'#fafbfe','axes.labelcolor':'#29374c','text.color':'#29374c'})
    w=profiles['common'].loc[profiles['common'].winner].sort_values('entry_time');y=np.arange(len(w))
    fig,ax=plt.subplots(figsize=(12,6));ax.barh(y,w.peak_known_gross_r,color='#c5d8f2',height=.64,label='Peak gross R')
    ax.barh(y,w.net_r,color='#217970',height=.32,label='Realized net R')
    for i,r in enumerate(w.itertuples()):ax.text(r.peak_known_gross_r+.14,i,f'{r.peak_known_gross_r:.2f} / {r.net_r:.2f}',va='center',fontsize=9)
    ax.set_yticks(y,[beijing(r.entry_time)[5:]+('  L' if r.side==1 else '  S') for r in w.itertuples()]);ax.invert_yaxis()
    ax.set_xlim(0,20);ax.set_xlabel('Initial risk units (R)');ax.set_title('ETH 15m + ICT | 9 winning trades, Jan-Apr 2026',loc='left',pad=18)
    ax.legend(loc='lower right',frameon=False);ax.grid(axis='x',alpha=.15);fig.tight_layout()
    fig.savefig(out/'common_winner_peaks.png',dpi=160);plt.close(fig)
    d=profiles['available'];fig,axes=plt.subplots(1,2,figsize=(12,5))
    for iswin,color,label in [(False,'#b85053','Final net loss'),(True,'#217970','Final net win')]:
        sub=d.loc[d.winner==iswin];axes[0].scatter(sub.peak_known_gross_r,sub.net_r,color=color,s=30,alpha=.75,label=label)
    axes[0].axhline(0,color='#8290a3',lw=.8);axes[0].plot([0,36],[0,36],ls=':',color='#8290a3',label='Peak = exit reference')
    axes[0].set(xlabel='Peak gross R while held',ylabel='Realized net R',title='All 123 original trades')
    axes[0].legend(frameon=False,fontsize=8)
    wins=d.loc[d.winner];bins=[2,3,4,5,8,10,40];counts=pd.cut(wins.peak_known_gross_r,bins,right=False).value_counts(sort=False)
    bars=axes[1].bar(['2-3','3-4','4-5','5-8','8-10','10+'],counts.to_numpy(),color='#7298cf')
    axes[1].bar_label(bars);axes[1].set(xlabel='Peak gross R bin [left, right)',ylabel='Winning trades',title='Peak distribution of 32 winners',ylim=(0,12))
    fig.tight_layout();fig.savefig(out/'all_peak_distribution.png',dpi=160);plt.close(fig)
    chosen=pd.concat([d.nlargest(1,'peak_known_gross_r'),profiles['common'].nlargest(3,'peak_known_gross_r')]).drop_duplicates('signal_i')
    fig,axes=plt.subplots(2,2,figsize=(13,8))
    for ax,r in zip(axes.flat,chosen.itertuples()):
        t=paths.loc[paths.signal_i==r.signal_i];h=t.minutes_from_entry/60
        ax.fill_between(h,t.adverse_r,t.favorable_r,color='#dce4f0',alpha=.8,label='Fully held bar range')
        ax.plot(h+.25,t.close_r,color='#356aaa',lw=1.2,label='Closed-bar profit')
        ax.plot(h+.25,t.peak_so_far_r,color='#a99b80',lw=1,ls='--',label='Peak observed by close')
        # Shift next-bar protection by one bar so the visual shows activation.
        active=t.protection_next_bar_r.shift(fill_value=-1.)
        ax.step(h,active,color='#ac5347',lw=1.2,where='post',label='Protection active this bar')
        ax.scatter([r.holding_minutes_lower/60],[r.gross_r],color='#217970',zorder=5,label='Gross exit fill')
        ax.set(title=beijing(r.entry_time)+(' L' if r.side==1 else ' S')+f' | peak {r.peak_known_gross_r:.2f}R',xlabel='Hours since entry',ylabel='Gross R')
        ax.axhline(0,lw=.6,color='#8290a3');ax.grid(alpha=.12)
    handles,labels=axes.flat[0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',ncol=3,frameon=False,fontsize=9)
    fig.tight_layout(rect=(0,.08,1,1));fig.savefig(out/'large_winner_paths.png',dpi=160);plt.close(fig)


def main():
    out=EXP/'results';profiles={w:pd.read_csv(out/f'{w}_profiles.csv') for w in LABEL}
    summary=pd.read_csv(out/'summary.csv').set_index('window');thresholds=pd.read_csv(out/'thresholds.csv')
    paths=pd.read_csv(out/'available_held_bar_paths.csv.gz');figures(profiles,paths)
    allp=profiles['available'];winners=allp.loc[allp.winner];common=profiles['common'];cw=common.loc[common.winner]
    receipt=json.loads((out/'read_receipt.json').read_text());large=winners.loc[winners.peak_known_gross_r>=5]
    parts=['# ETH15min＋ICT：盈利单峰值与止盈诊断\n',
     '## 核心结论 / Executive Summary\n',
     '**盈利单多数能走到约4—6R，少数走到10R以上。** 连续历史123笔中32笔最终净盈利；赢家峰值中位4.36R、均值6.09R、最大35.59R。2026前4月9笔赢家的峰值中位5.24R、最大16.36R。只看最大值会被两笔超大行情带偏。\n',
     '**回吐确实存在，但不能把回吐总额当可追回利润。** 32笔赢家平均从峰值回吐2.79R后退出，最终兑现约54.2%的毛峰值；扣费后兑现约52.6%的净峰值。与此同时，14笔峰值≥5R的大赢家中，7笔在原跟踪激活后、抵达峰值前已有超过2R的回落。\n',
     '**下一步应区分盈利初期保本与趋势后段锁盈。** 不宜直接统一3R全平或把所有尾仓压成1—2R回撤；更值得单独验证“较晚启动的峰值比例保护”。本轮只做路径诊断，没有测试这个新规则，也没有证明它优于原版。\n',
     '## 盈利单的峰值究竟有多大\n',
     '这里的峰值是从真实开仓到真实退出之间的最大有利价格变化，除以初始价格止损距离。浮盈峰值为毛R，最终净R扣名义往返0.2%成本。所有32笔赢家都已核对退出根：该根的有利极值没有超过此前峰值，因此赢家峰值不受退出根先后顺序影响。\n']
    rows=[]
    for window,r in summary.iterrows():
        rows.append([LABEL[window],f'{int(r.wins)}/{int(r.n)}',fmt(r.winner_peak_min),fmt(r.winner_peak_p25),
            fmt(r.winner_peak_p50),fmt(r.winner_peak_p75),fmt(r.winner_peak_p90),fmt(r.winner_peak_max),fmt(r.winner_peak_mean),fmt(r.winner_net_median)])
    parts.append(table(['窗口','盈利/全部','最小峰值','P25','中位峰值','P75','P90','最大峰值','平均峰值','盈利单净R中位'],rows))
    parts.append('连续窗口与前三行重叠，不能加总样本。2025的平均峰值7.21R高于中位3.66R，主要受那笔35.59R推动；它独自贡献2025全部正收益的65.0%。研究止盈应主要看分布和逐笔路径，不能把平均或最大峰值直接设成统一目标。\n')
    parts.append('![全部交易峰值与32个赢家分布]('+URLBASE+'figures/all_peak_distribution.png)\n')
    counts=pd.cut(winners.peak_known_gross_r,[0,2,3,4,5,8,10,100],right=False).value_counts(sort=False)
    parts.append(table(['赢家峰值区间','笔数','占32笔赢家'],[[label,int(n),fmt(n/len(winners),True)] for label,n in zip(['不足2R','2—3R','3—4R','4—5R','5—8R','8—10R','10R以上'],counts)]))
    parts.append('32笔赢家里10笔止步于2—3R，14笔达到5R，7笔达到8R，只有2笔达到10R。这说明小幅盈利兑现和趋势尾仓确实有不同需求，但这些分组按最终结果划分，开仓时不能预先知道自己属于哪一组。\n')
    parts.append('## 2026年前四个月：9笔盈利单逐笔拆解\n')
    parts.append('先看最近讨论窗口。浅色条为峰值毛R，深色条为最终净R；两者差额包含价格回吐和成本，表格另列纯价格回吐，避免把手续费重复算成止盈问题。\n')
    parts.append('![9笔赢家峰值与兑现]('+URLBASE+'figures/common_winner_peaks.png)\n')
    rows=[]
    for r in cw.itertuples():
        rows.append([beijing(r.entry_time),'多' if r.side==1 else '空',fmt(r.peak_known_gross_r),fmt(r.max_closed_bar_r),
            fmt(r.gross_r),fmt(r.net_r),fmt(r.giveback_r),fmt(r.minutes_to_peak_lower/60),fmt(r.holding_minutes_lower/60)])
    parts.append(table(['入场北京时间','方向','盘中峰值毛R','最高收盘毛R','兑现毛R','兑现净R','价格回吐R','到峰值约小时','总持仓约小时'],rows))
    parts.append('最明显的两个问题：2月4日那笔峰值16.36R，最终毛10.19R/净10.05R，回吐6.17R；4月17日峰值5.85R，最终净2.06R，回吐3.55R。另一方面，2月18日那笔盘中虽到2.14R，最高收盘只有1.92R，原版“收盘2R才启动”始终没触发，最后由反向信号净赚0.68R退出。触及2R与收盘站上2R是两种不同的保护触发。\n')
    parts.append('时间按15min K线标签估计：峰值可能发生在该根任何时刻，盘中退出也没有精确秒，所以“约小时”不是精确成交时长。9笔赢家持仓中位26.75小时，到峰值中位25小时；不能因为使用15min信号，就假定盈利单都应在几根K线内结束。\n')
    parts.append('## 峰值之外，还要看最终亏损的单子\n')
    parts.append('只看赢家会高估目标可达性。以下以每个窗口的全部原版交易为分母，先按持仓期间达到阈值筛选，再观察最终结局。它描述原退出下的路径，既不是新固定止盈的成交胜率，也不能预测下一笔。\n')
    for window in ['available','common']:
        sub=thresholds.loc[(thresholds.window==window)&(thresholds.basis=='gross')&thresholds.level.isin([1,2,3,4,5,6,8,10])]
        parts.append('### '+LABEL[window]+'\n')
        parts.append(table(['曾达到毛R','到达/全部','最终赢/亏','最终净盈利比例','最终净R中位','下一档','继续到下一档'],[
            [int(r.level),f'{int(r.reached)}/{len(profiles[window])}',f'{int(r.wins)}/{int(r.losses)}',fmt(r.win_fraction,True),
             fmt(r.median_final_net_r),int(r.next_level),f'{int(r.reached_next)}/{int(r.reached)}'] for r in sub.itertuples()]))
    parts.append('完整历史达到1R的59笔有27笔最终净亏；达到2R的41笔仍有9笔净亏；达到3R的26笔有4笔净亏。达到4R的19笔最终均盈利，但其中只有14笔继续到5R。达到8R的7笔只有2笔继续到10R。不能把“19/19最终盈利”理解成未来必胜；样本和原退出共同决定了这个结果。\n')
    parts.append('## 大赢家的回吐，有多少发生在抵达峰值之前\n')
    parts.append('图中灰色带只画完整持仓K线；蓝线为收盘浮盈，虚线为此前已见峰值，红线为当根已生效保护位，绿点为最终毛R成交。保护位按前一根收盘计算，图中没有提前一根显示止损。\n')
    parts.append('![大赢家的持仓路径]('+URLBASE+'figures/large_winner_paths.png)\n')
    rows=[]
    for r in allp.nlargest(3,'peak_known_gross_r').itertuples():
        rows.append([beijing(r.entry_time),fmt(r.peak_known_gross_r),fmt(r.net_r),fmt(r.pre_peak_pullback_known_r),
            fmt(r.peak_bar_4atr_r),fmt(r.giveback_r)])
    parts.append(table(['入场北京时间','最终峰值R','最终净R','途中已确认回落至少R','峰值根4ATR相当R','峰后价格回吐R'],rows))
    parts.append(f'峰值≥5R的{len(large)}笔中，{int((large.pre_peak_pullback_known_r>2).sum())}笔在原收盘2R激活后、到最终峰值前已有超过2R的回落，中位至少{fmt(large.pre_peak_pullback_known_r.median())}R，最大至少{fmt(large.pre_peak_pullback_known_r.max())}R。因此从早期就用“见过的峰值减1R或2R”紧跟，会触碰到多笔本来可长成大赢家的途中回撤；具体新规则收益仍须重新模拟。\n')
    parts.append('途中回落定义严格限定在激活之后、最终峰值所在K线之前：此前已经发生的有利峰值减后来完整K线的不利极值，是顺序可确定的下界。另存同根高低顺序可能上界；峰值所在根的回落没有加入，以免把峰后回落误算成到峰值前必须承受的回落。\n')
    parts.append('## 为什么原4ATR看起来放得很宽\n')
    parts.append('R固定在入场风险，ATR会随行情变化。原跟踪是“收盘价±4×当前ATR”，所以4ATR不等于固定4R；波动扩大时，以初始R衡量的距离可以很大。旧止损位仍单向收紧，不能把距离扩大误说成止损价倒退。\n')
    rows=[]
    for r in cw.itertuples():
        rows.append([beijing(r.entry_time),fmt(r.peak_known_gross_r),fmt(r.peak_bar_close_r),fmt(r.peak_bar_4atr_r),
            fmt(r.peak_bar_protection_next_r),fmt(r.net_r)])
    parts.append(table(['入场北京时间','峰值毛R','峰值根收盘R','当根4ATR/R','收盘后保护毛R（下一根生效）','最终净R'],rows))
    parts.append('35.59R大赢家峰值附近4ATR相当于9.41R，16.36R赢家相当于5.46R；这解释了为何会允许较大回吐。4月17日峰值5.85R，峰值根收盘5.50R、4ATR约3.21R，保护只到2.30R左右。这里有测试另一种锁盈方式的理由，但ATR较宽也帮助前两笔熬过了途中回撤，不能只看最终一段。\n')
    parts.append('## 止盈优化应该怎样落到下一轮实验\n')
    parts.append('1. **保留早期含费保护作为单独对照。** 原版收盘2R激活与“盘中触及净2R、收盘确认后下一根保本”分开评估。上一轮净2R保护在2026前4月从22.74R改善到24.68R，但连续历史从−9.84R变成−10.92R，仍不能直接定为新默认。\n')
    parts.append('2. **晚启动的峰值比例保护值得研究，但早启动也会误伤。** 2月4日的大赢家曾有最高收盘5.019R，之后在北京时间2月5日02:30那根完整持仓K线，不利价到1.861R；若此时要求至少留住一半收盘峰值，下限2.510R已经高于该不利价，会在16.36R最终峰值前遇到止损。因此不能直接把“4R以后锁50%”当答案。一个更晚的待测样例是：最高收盘毛浮盈到8R后，至少保留其70%，下一根生效，与原4ATR保护取更紧且只向有利方向移动；独立比较保留比例60%/70%/80%，不同时加分批或保本。**当前历史只有3笔最高收盘达到8R，这个样例尚未回测，也没有足够样本支持定参数。**\n')
    rows=[]
    for level in [2,3,4,5,6,8,10]:
        sub=allp.loc[allp.peak_known_gross_r>=level]
        rows.append([level,len(sub),fmt(sub.gross_r.mean()),fmt(sub.net_r.mean())])
    parts.append('3. **先用到达目标后的平均兑现筛查分批想法。** 下面这些触达组，原版最终平均毛R都高于触达R；把部分仓位统一在该R兑现，并没有直接的平均收益优势。这里只是原持仓路径的诊断，不能替代限价、跳空与完整账户回放。\n')
    parts.append(table(['已触达毛R','交易数','原最终平均毛R','原最终平均净R'],rows))
    parts.append('例如到5R的14笔最终平均净5.59R、毛收益更高。因此本轮不优先把“5R兑现25%”当提高总收益的方案；若目标改为降低回撤，可以另行比较，但需同时接受可能少赚。上一轮2R兑现25%/50%已经降低近期和连续净收益。\n')
    parts.append('下一轮必须重新走完整串行入场与同入场配对，同时报告：每笔救回多少亏损、哪几笔大赢家被提前截断、完整净收益、回撤和倍投账户。开发/后续年份分开，不能用本轮已观察到的峰值事后挑最优参数；本轮没有运行以上新机制，也没有把峰值金额当成假设可获得的收益。\n')
    parts.append('## 前一轮退出实测与随机对照\n')
    previous=ROOT/'experiments/active/exp-spike-v8-ict-exits-20260915-v1/results/evaluate/summary.csv'
    old=pd.read_csv(previous)
    rows=[]
    for window in ['common','available']:
        for policy in ['original','cost_be2','partial25_at2','partial50_at2','fixed3r','trail2_atr']:
            r=old.loc[(old.window==window)&(old.policy==policy)].iloc[0]
            rows.append([LABEL[window],policy,int(r.natural),fmt(r.sum_net_r),fmt(r.random_mean_net_r),fmt(r.excess_net_r),fmt(r.p_holm)])
    parts.append(table(['窗口','前轮冻结规则','自然笔数','总净R','匹配随机均净R','匹配超额均净R','Holm校正p'],rows))
    parts.append('这张表直接引用上一轮冻结回测，不是本轮新实验。随机对照按同币、周期、方向、入场月、纽约小时/周末和先前120根波动桶匹配，每笔目标5个；随机均值/超额只用完整匹配子集。上述p针对相较随机入场的超额，不是不同退出方案的显著性检验。结果说明“看起来有很多回吐”并不意味着先前尝试的止盈规则已经改进净收益。\n')
    parts.append('## 风险与诚实声明\n')
    parts.append('这次是原版成交路径的描述性诊断。盈利单按最终净结果筛选，不能在开仓时识别；所有条件比例只适用于原退出允许的持仓长度，改退出后会改变成交集合。峰值位于已持有的K线，不计退出后的新行情。32赢家峰值无退出根歧义；123笔中仅一笔初始止损单的峰值可能为0—0.356R，不影响≥1R统计。\n')
    parts.append('普通OHLC不能提供每根内的精确高低顺序，订单和保护生效时序须明确建模，不能假定在事后最高点成交。[TradingView策略执行文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)\n')
    parts.append('数据截止2026-05-01 UTC之前，holdout消耗0。研究历史此前已看过，不是新盲测。仍是Python原V8＋纽约[02,11)实际下一开盘准入、周末保留；受保护ICT脚本冬季行为和TV原生逐笔对齐仍未完成。没有新方向性实验、预测模型或排名，AUC/top-decile/单特征排序不适用；用原MFE、开仓/退出价格、原4ATR保护位的逐笔重建及已有匹配随机结果提供同等严格的事实对照。费用仅为既定0.2%，未计资金费率和额外滑点，没有实盘、训练或promote。\n')
    parts.append('## 明细与复现\n')
    delivery=EXP/'delivery';delivery.mkdir(exist_ok=True)
    for window,df in profiles.items():
        df=df.loc[df.winner].copy();df['入场北京时间']=df.entry_time.map(beijing);df['方向']=df.side.map({1:'多',-1:'空'})
        cols={'peak_known_gross_r':'峰值毛R','peak_known_net_r':'峰值扣成本R','max_closed_bar_r':'最高收盘毛R','gross_r':'兑现毛R','net_r':'兑现净R',
            'giveback_r':'价格回吐R','minutes_to_peak_lower':'到峰值根分钟','holding_minutes_lower':'持仓根分钟','peak_bar_4atr_r':'峰值根4ATR折算R',
            'peak_bar_protection_next_r':'峰值根收盘后保护R','pre_peak_pullback_known_r':'到峰值前回落下界R','pre_peak_pullback_possible_r':'到峰值前回落可能上界R'}
        df=df[['入场北京时间','方向']+list(cols)].rename(columns=cols);f=delivery/f'ETH15m_{window}_winning_peaks.csv';df.to_csv(f,index=False,encoding='utf-8-sig')
        parts.append(f'- [{LABEL[window]}：全部盈利单峰值明细]({URLBASE}delivery/{f.name})\n')
    parts.append(f'- [可运行的数据核对Notebook]({URLBASE}peak_analysis.ipynb)\n')
    parts.append('生成代码、输入与输出SHA、123笔独立交易的核验记录见实验目录。其中122条非空路径共有6301根完整持仓bar，另1笔入场根即止损、没有完整持仓bar。报告图表来自同一profiles和持仓路径导出。运行检查75项通过（4项峰值专项＋71项层间边界），没有把上一轮更广检查的失败说成通过。\n')
    parts.append('```bash\ncd /Users/zhangzc/fable-trading\nexport PYTHONPATH=/Users/zhangzc/fable-trading/.venv/lib/python3.9/site-packages:/Users/zhangzc/fable-trading\n/usr/bin/python3 -m pytest -q tests/test_spike_v8_winner_peaks.py tests/boundaries/test_layer_imports.py\n# builder需先提交；仅在新结果目录不存在时运行，禁止覆盖本轮证据\n/usr/bin/python3 -m yoyo.evaluation.spike_v8_winner_peaks\n/usr/bin/python3 scripts/report_spike_v8_winner_peaks.py\n/usr/bin/python3 scripts/md_to_html.py analysis/p1_spike_v8_ict_winner_peaks_20260915.md --out-dir analysis/html\n```\n')
    REPORT.write_text('\n'.join(parts))
    cells=[{'cell_type':'markdown','metadata':{},'source':['# ETH15m ICT winning-peak evidence\n','Read-only analysis of frozen outputs. No OHLC, holdout, or new exit replay.\n']}]
    snippets=[f"from pathlib import Path\nimport pandas as pd\nexp = Path({str(EXP)!r})\nsummary = pd.read_csv(exp/'results/summary.csv')\nsummary",
        "trades = pd.read_csv(exp/'results/available_profiles.csv')\nwinners = trades.loc[trades.winner]\nassert len(trades) == 123 and len(winners) == 32\nassert (winners.peak_known_gross_r == winners.peak_possible_upper_r).all()\nwinners[['peak_known_gross_r','net_r','giveback_r']].describe()",
        "thresholds = pd.read_csv(exp/'results/thresholds.csv')\nthresholds.loc[(thresholds.window=='available') & (thresholds.basis=='gross')]",
        "large = winners.loc[winners.peak_known_gross_r>=5]\nassert len(large) == 14\nassert (large.pre_peak_pullback_known_r>2).sum() == 7\nlarge[['entry_time','peak_known_gross_r','net_r','pre_peak_pullback_known_r']]" ]
    env={}
    for source in snippets:
        exec(source,env)
        cells.append({'cell_type':'code','metadata':{},'source':source.splitlines(keepends=True),'execution_count':None,'outputs':[]})
    notebook={'cells':cells,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},'language_info':{'name':'python','version':'3.9.6'}},'nbformat':4,'nbformat_minor':5}
    (EXP/'peak_analysis.ipynb').write_text(json.dumps(notebook,ensure_ascii=False,indent=2)+'\n')
    print(REPORT)


if __name__=='__main__':main()
