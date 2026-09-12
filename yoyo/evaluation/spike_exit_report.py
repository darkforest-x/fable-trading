"""Build the receipt-bound Chinese exit-policy report; never tune from validation.

Reads only complete fixed-policy evaluation artifacts. Cluster resampling uses
asset aggregates to avoid treating the same underlying on three venues as three
independent samples. It evaluates exit changes relative to the unchanged policy,
not a claim of predictive entry alpha against random market exposure.
"""
from pathlib import Path
import argparse,json,hashlib
import numpy as np
import pandas as pd

EXP=Path('experiments/active/exp-spike-exit-policy-20260912-v1')
LABEL={'v1_common_long':'V1 多头／统一退出','v6_both':'V6 双向','v7_both':'V7 双向'}
POLICY={'baseline':'基线：反向＋跟踪','no_reverse':'取消反向退出','be095_price':'0.95R 价格保本','be1_price':'1R 价格保本','be1_cost':'1R 含费保本','partial_1_25':'1R 平25%','partial_1_25_3_35':'1R 平25%＋3R 平35%','partial_be1_cost':'分批＋1R 含费保本','fast_ma_exit':'跌破／升破双20线退出','md_cross_exit':'IMACD 反向交叉退出'}
TF={30:'30m',60:'1H',240:'4H'}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def pct(v):return '—' if pd.isna(v) else f'{v*100:,.2f}%'
def num(v):return '—' if pd.isna(v) else f'{v:,.2f}'
def probability(v):return '—' if pd.isna(v) else f'{v:.4f}'
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|',*['| '+' | '.join(map(str,r))+' |' for r in rows]])
def grouped_effect(a,selection,out):
    a=a.loc[a.risk_fraction.eq(.01)&a.notional_cap.astype(str).eq('1.0')&a.period.eq('validation')&a.valid].copy()
    base=a.loc[a.policy.eq('baseline'),['stream_key','cohort','net_return']].rename(columns={'net_return':'base'})
    chosen=a.merge(selection[['timeframe_min','cohort','policy']],on=['timeframe_min','cohort','policy']).merge(base,on=['stream_key','cohort'])
    rng=np.random.default_rng(20260912);rows=[]
    for (tf,cohort),g in chosen.groupby(['timeframe_min','cohort']):
        diff=g.net_return-g.base;g=g.assign(diff=diff)
        groups=g.groupby('asset').agg(total=('diff','sum'),count=('diff','size'))
        sums=groups.total.to_numpy();counts=groups['count'].to_numpy();n=len(groups)
        bs=np.empty(2000);flips=np.empty(2000)
        for i in range(2000):
            pick=rng.integers(0,n,size=n);bs[i]=sums[pick].sum()/counts[pick].sum()
            flips[i]=(sums*rng.choice([-1,1],n)).sum()/counts.sum()
        observed=float(diff.mean());p=(1+np.sum(np.abs(flips)>=abs(observed)))/2001
        gain=g.assign(positive=g.net_return.clip(lower=0)).groupby('asset').positive.sum().sort_values(ascending=False)
        top= gain.index[0] if len(gain) and gain.iloc[0]>0 else None
        trimmed=g.loc[g.asset.ne(top)].net_return
        rows.append(dict(timeframe_min=tf,cohort=cohort,policy=g.policy.iloc[0],paired_streams=len(g),asset_clusters=n,paired_mean=observed,ci_low=np.quantile(bs,.025),ci_high=np.quantile(bs,.975),permutation_p=p,top_positive_asset=top,top_positive_share=float(gain.iloc[0]/gain.sum()) if top else 0.,return_mean_excluding_top_asset=float(trimmed.mean()) if len(trimmed) else np.nan))
    df=pd.DataFrame(rows);order=np.argsort(df.permutation_p.to_numpy());q=np.minimum.accumulate((df.permutation_p.to_numpy()[order]*len(df)/np.arange(1,len(df)+1))[::-1])[::-1];df['bh_q']=np.nan;df.loc[order,'bh_q']=np.minimum(q,1.)
    df.to_csv(out/'selected_policy_cluster_checks.csv',index=False)
    return df

def figure(summary,selection,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3,3,figsize=(14,10),layout='constrained')
    for j,cohort in enumerate(LABEL):
        for i,tf in enumerate(TF):
            ax=axes[j,i];policy=selection.loc[selection.cohort.eq(cohort)&selection.timeframe_min.eq(tf),'policy'].iloc[0]
            for key,color in [('baseline','#8b95a5'),(policy,'#169b83')]:
                g=summary.loc[summary.venue_scope.eq('all')&summary.period.eq('validation')&summary.notional_cap.astype(str).eq('1.0')&summary.cohort.eq(cohort)&summary.timeframe_min.eq(tf)&summary.policy.eq(key)].sort_values('risk_fraction')
                ax.plot(g.risk_fraction*100,g.return_mean*100,'o-',color=color,label=key)
            ax.axhline(0,color='#d6dde5',lw=.8);ax.grid(alpha=.2);ax.set_title(f'{cohort} / {TF[tf]}');ax.set_xlabel('Target stop risk (% equity)');ax.set_ylabel('Mean independent-account return (%)');ax.legend(fontsize=7)
    fig.suptitle('Reused second-year evaluation | Entry notional capped at 1x equity',fontsize=14)
    fig.savefig(out/'capital_risk_comparison.png',dpi=160);plt.close(fig)

def build(engine,post,report):
    manifest=json.loads((post/'post_manifest.json').read_text());em=json.loads((engine/'engine_manifest.json').read_text())
    if not manifest['complete'] or not em['complete'] or manifest['streams']!=3531:raise ValueError('complete scope required')
    for name,digest in manifest['outputs'].items():
        if sha(post/name)!=digest:raise ValueError('post artifact changed: '+name)
    summary=pd.read_csv(post/'account_summary.csv');events=pd.read_csv(post/'event_summary.csv');selection=pd.read_csv(post/'development_selection.csv')
    account=pd.read_csv(post/'independent_account_rows.csv.gz',usecols=['stream_key','cohort','policy','timeframe_min','risk_fraction','notional_cap','period','valid','venue','asset','net_return','max_close_drawdown'])
    audit=grouped_effect(account,selection,post);figure(summary,selection,post)
    cap=summary.loc[summary.venue_scope.eq('all')&summary.period.eq('validation')&summary.notional_cap.astype(str).eq('1.0')]
    ref=cap.loc[cap.risk_fraction.eq(.01)]
    rows=[]
    for s in selection.sort_values(['cohort','timeframe_min']).itertuples():
        base=ref.loc[ref.cohort.eq(s.cohort)&ref.timeframe_min.eq(s.timeframe_min)&ref.policy.eq('baseline')].iloc[0]
        chosen=ref.loc[ref.cohort.eq(s.cohort)&ref.timeframe_min.eq(s.timeframe_min)&ref.policy.eq(s.policy)].iloc[0]
        rows.append([LABEL[s.cohort],TF[s.timeframe_min],POLICY[s.policy],pct(base.return_mean),pct(chosen.return_mean),pct(chosen.return_median),pct(chosen.drawdown_mean),pct(chosen.drawdown_max),int(chosen.invalid_accounts)])
    body=['# SPIKE V1／V6／V7：退出规则与账户风险比较','',
    '**这是完整固定方案回放，不是实盘收益承诺。** 本轮保持入场不变，比较10种退出方案及1%／3%／5%／10%账户风险。先用第一年选退出方案，再查看第二年；这些历史曾用于研究，因此第二年不是盲测。',
    '', '## 先看第一年选出的方案，在第二年如何表现',
    '下表每个币种／交易所／周期分别分配10,000单位初始资金，入场名义金额最多为当时权益的1倍，目标初始风险1%。均值、回撤均值及最坏回撤来自这些独立账户；不是把所有信号塞进一个账户的收益或回撤。',
    table(['版本','周期','第一年选出退出','基线均值','选出方案均值','方案中位数','平均最大回撤','最坏账户回撤','无有效估值账户'],rows),
    '', '选择规则：第一年1%目标风险、1倍名义金额上限下的平均账户收益最高；同收益选较低平均回撤。无信号账户包含在均值中。不同交易所同一币种存在重复，不能当成独立发现。',
    '', '## 1R 与账户3%／5%／10%的关系',
    '图上1R是入场价与初始止损价的距离。账户风险比例决定数量：数量＝账户权益×风险比例÷初始止损距离。它不是币价涨幅，也不是把1R定义为固定账户收益；费用、缺口会改变实际盈亏。随着分批退出，剩余数量减少，后续价格再走1R也不会为全仓增加1R收益。',
    '主结果限制入场名义金额≤权益1倍，因此设为10%目标风险也未必真的用到10%；它可能与3%／5%得到相同仓位。另列无上限数学压力测试，不能代替带资金费率、维持保证金和强平规则的合约回测。',
    '[仓位与止损距离：CME](https://www.cmegroup.com/education/courses/trade-and-risk-management/proper-position-size)。[OKX 强平依据包含标记价格](https://www.okx.com/en-gb/help/frequently-issues-of-contracts-for-compulsory-liquidation)，本轮没有这些完整输入。',
    '![不同目标风险的账户比较](../experiments/active/exp-spike-exit-policy-20260912-v1/post_full_v1/capital_risk_comparison.png)',
    '', '## 风险比例逐项比较：仍使用第一年选出的退出规则']
    rows=[]
    selected=cap.merge(selection[['timeframe_min','cohort','policy']],on=['timeframe_min','cohort','policy'])
    for r in selected.sort_values(['cohort','timeframe_min','risk_fraction']).itertuples():
        rows.append([LABEL[r.cohort],TF[r.timeframe_min],pct(r.risk_fraction),pct(r.return_mean),pct(r.return_median),pct(r.drawdown_mean),pct(r.drawdown_max),int(r.ruined_accounts)])
    body +=[table(['版本','周期','目标初始风险','平均收益','中位收益','平均最大回撤','最坏回撤','权益归零账户'],rows)]
    body +=['','## 具体到 OKX 的 ETH／BTC 1H 独立账户','以下可以解释为仅在这个合约上逐笔执行的模拟账户，仍然有1倍入场名义金额上限。分别展示基线和第一年全池选出的方案；若选中基线，只出现一组。']
    detail=account.loc[account.venue.eq('okx')&account.asset.isin(['ETH','BTC'])&account.timeframe_min.eq(60)&account.period.eq('validation')&account.notional_cap.eq('1.0')]
    rows=[]
    for r in detail.sort_values(['asset','cohort','policy','risk_fraction']).itertuples():
        choice=selection.loc[selection.cohort.eq(r.cohort)&selection.timeframe_min.eq(60),'policy'].iloc[0]
        if r.policy not in ('baseline',choice):continue
        rows.append([r.asset,LABEL[r.cohort],POLICY[r.policy],pct(r.risk_fraction),pct(r.net_return),pct(r.max_close_drawdown),'有效' if r.valid else '估值不完整'])
    body +=[table(['币种','版本','退出','目标风险','净收益','收盘最大回撤','估值'],rows)]
    body +=['','## 无名义金额上限：仅作数学压力测试','此处允许按风险距离放大任意名义金额。不模拟实际交易所强平，不应把本表最高值作为实盘建议。权益≤0按吸收归零处理，随后不能靠模拟交易复活；无有效估值账户另列。']
    stress=summary.loc[summary.venue_scope.eq('all')&summary.period.eq('validation')&summary.notional_cap.eq('uncapped_stress')].merge(selection[['timeframe_min','cohort','policy']],on=['timeframe_min','cohort','policy'])
    rows=[]
    for r in stress.sort_values(['cohort','timeframe_min','risk_fraction']).itertuples():rows.append([LABEL[r.cohort],TF[r.timeframe_min],pct(r.risk_fraction),pct(r.return_mean),pct(r.return_median),pct(r.drawdown_max),int(r.ruined_accounts),int(r.invalid_accounts),num(r.full_run_max_observed_close_leverage)])
    body +=[table(['版本','周期','风险','平均收益','中位收益','最坏回撤','归零','无估值','全程最高观测名义/权益'],rows)]
    body +=['','## 保本、分批止盈与提前退出：全部方案','这里展示第二年所有方案，方便比较；不把第二年最优项追认成事先选出的方案。费用为原始入场名义金额的0.2%往返，逐段按原始数量比例计提。事件PF按单位原始名义金额净收益计算，不等于复利账户收益。']
    for cohort in LABEL:
        body+=['',f'### {LABEL[cohort]}'];rows=[]
        for r in ref.loc[ref.cohort.eq(cohort)].sort_values(['timeframe_min','policy']).itertuples():
            ev=events.loc[events.venue.eq('all')&events.cohort.eq(cohort)&events.timeframe_min.eq(r.timeframe_min)&events.policy.eq(r.policy)&events.period.eq('validation')].iloc[0]
            rows.append([TF[r.timeframe_min],POLICY[r.policy],int(ev.closed),pct(ev.win_rate),num(ev.event_pf),int(ev.mfe_ge_10r),int(ev.net_ge_10r),pct(r.return_mean),pct(r.drawdown_mean)])
        body+=[table(['周期','退出方案','已关闭交易','净胜率','净PF','持有时曾到10R','实现≥10R笔数','账户均值','平均最大回撤'],rows)]
    body +=['','## 收益是否靠少数大行情，以及配对对照','对选出方案和基线采用同一币种账户配对。按基础币种聚类做2,000次重采样和符号置换，同币跨交易所一起变化；9项比较给出BH校正q。基线是退出效果的对照，不能替代随机入场的方向性收益对照。']
    rows=[]
    for r in audit.itertuples():rows.append([LABEL[r.cohort],TF[r.timeframe_min],pct(r.paired_mean),f'{pct(r.ci_low)} ~ {pct(r.ci_high)}',probability(r.permutation_p),probability(r.bh_q),r.top_positive_asset,pct(r.top_positive_share),pct(r.return_mean_excluding_top_asset)])
    body +=[table(['版本','周期','相对基线均值差','币种聚类95%区间','p','BH q','最大正贡献币','占正贡献','剔除该币后的均值'],rows),
    '', '## 精确执行规则',
    '- 所有入场在信号收盘后的下一根实际开盘执行。V1沿用此前统一执行口径的多头事件，V6/V7双向；这不是把V1原生退出重新标成统一退出，也不是补了未测试的V1空头。',
    '- 初始止损：前5根结构极值、0.2ATR缓冲、至少2ATR风险距离；2R后启用4ATR跟踪，并保持启用。除取消反向退出方案外，原有反向V6信号收盘后下一开盘退出保留。',
    '- 保本条件以收盘浮盈达到0.95R或1R确认，下一根起生效。价格保本设入场价；含费保本设入场价±0.2%入场价，针对剩余单位头寸，不保证跳空时净零。该含费边界为解析价，尚未模拟每家交易所订单精度取整。',
    '- 分批方案按原始数量在1R平25%、3R再平35%，余40%继续跟踪。若一个收盘同时越过两档，下一个开盘合计平60%。旧止损优先，不把本根高点新触发的保本回填到本根低点。',
    '- 行情不对的两种附加退出分别是：多头收盘同时跌破SMA20/EMA20（空头镜像），或IMACD主线与信号线反向交叉。均在下一开盘执行，不读未来数据。',
    '- 每种方案重新回放完整入场流，因此更早退出可以改变之后能否再入场；不是给旧成交表换个收益数字。',
    '', '## 范围、验证与风险诚实声明',
    f'- 3,531个完整输入流；Binance／OKX／Gate，30m／1H／4H；2024-09-10至2026-09-10。开发区间第一年，第二年为复用历史验证。账户明细{manifest["account_rows"]:,}行（包括周期分段），事件汇总{manifest["event_rows"]:,}行。15m不在这轮与此前V1一致的比较范围。',
    '- 每个输入流的三个基线逐笔对齐旧账本，校验信号、入场、退出时间、方向、价格、初始风险与净收益；输入缓存和输出均有SHA256回执。账户逐段成交量守恒、费用核算及快速/参考算法一致性有自动测试。',
    '- 账户权益按K线收盘盯市，报告的是收盘最大回撤，可能低估盘中回撤。初始/跟踪止损允许盘中触发，但没有订单簿、资金费率、标记价格、强平、最小下单量和容量模型。不能从这里推出10%风险实际可用。',
    '- 数据缺口持仓终止估值并计入无有效估值账户；研究截止时未平仓用最后完整收盘及预计费用作边界估值，不冒充已成交。独立账户跨年持仓与浮盈会继承，不在分界日无偿清仓重开。',
    '- 事件统计按入场时间分年；账户统计按日历权益分年，两种表不应混算。全程实际风险/杠杆诊断明确标记全程，不把后一年数据当成第一年的已知信息。',
    '- 第一年的参数选择仍有多重试验及均值偏向尾部的问题，且第二年已研究过，不能称为一次未见数据的最终验收。本轮旧holdout日期按Owner已有全日期授权读取；本配置在此数据上的首次完整回放，既往研究消耗不重置。',
    '- AUC、top-decile排序与单特征排名不适用于这组固定硬规则退出。配对的未修改退出规则是单变量对照；新的随机入场＋全部退出方案没有重跑，因此不声称验证了新的方向性alpha。此前随机对照结论见[上一轮报告](p1_spike_v7_v1_compare_20260912.md)。',
    '', '## TradingView 显示交付',
    'V1／V6／V7默认显示1R、2R、3R参考虚线和价格，历史交易保留标线；参考位不等于固定全仓止盈。仅更新显示，研究中的保本／分批／风险比例没有自动修改实盘、通知或已部署规则。保存与编译回执见实验receipts目录。',
    '', '## 可复现命令',
    '```bash\nOPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest -q tests/evaluation/test_spike_exit_policy_study.py tests/evaluation/test_spike_exit_accounts.py tests/evaluation/test_spike_exit_post.py tests/test_spike_burst_display_contract.py\n.venv/bin/python -m yoyo.evaluation.spike_exit_policy_study --output experiments/active/exp-spike-exit-policy-20260912-v1/engine_results/full_v1\n.venv/bin/python -m yoyo.evaluation.spike_exit_post --engine experiments/active/exp-spike-exit-policy-20260912-v1/engine_results/full_v1 --output experiments/active/exp-spike-exit-policy-20260912-v1/post_full_v1\n.venv/bin/python -m yoyo.evaluation.spike_exit_report\n.venv/bin/python scripts/md_to_html.py analysis/p1_spike_exit_policy_20260912.md --out-dir analysis/html\n```',
    '源缓存来自上一轮被冻结完整数据与回执，需先恢复该实验本地输入。新配置或源码需新输出目录，不能覆盖旧回执。',
    '', '## 后续决策',
    '依据本表选择候选退出规则后，下一项是独立前向纸面成交与带真实资金费率/强平规则的共享账户模拟。涉及实盘仓位、生产规则切换仍由Owner决定；本轮不自动部署收益排序最高项。']
    report.write_text('\n\n'.join(body))
    (post/'report_receipt.json').write_text(json.dumps({'builder_sha256':sha(__file__),'report_sha256':sha(report),'post_manifest_sha256':sha(post/'post_manifest.json'),'engine_manifest_sha256':sha(engine/'engine_manifest.json'),'figure_sha256':sha(post/'capital_risk_comparison.png'),'cluster_checks_sha256':sha(post/'selected_policy_cluster_checks.csv')},indent=2))
    print(report)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--engine',type=Path,default=EXP/'engine_results/full_v1');p.add_argument('--post',type=Path,default=EXP/'post_full_v1');p.add_argument('--report',type=Path,default=Path('analysis/p1_spike_exit_policy_20260912.md'));a=p.parse_args();build(a.engine,a.post,a.report)
