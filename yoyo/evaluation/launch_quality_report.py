"""Render saved launch-quality accounts and source-checked examples as HTML.

This report never chooses thresholds or reruns strategy outcomes. Winning,
missed and losing examples are explicitly retrospective illustrations. Earlier
period pictures are physically truncated at their evaluation cutoff. The
price/exit consistency checks are reused from the audited prior report.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.launch_quality_accounts import EXPERIMENT, OLD, ROOT, PERIODS, sha
from yoyo.evaluation.altseason_research import read_events, scope_rows
from yoyo.evaluation.altseason_report import _plot_setup, _case_plot

LABELS={'baseline':'原1H启动＋主线回零','volume4':'原版＋量比≥4','expansion3':'原版＋振幅扩张≥3'}
PERIOD_LABELS={'earlier':'较早复核：05/15—07/10','known':'已知样本：07/10—09/09'}
COLORS={'baseline':'#367cad','volume4':'#ce8a41','expansion3':'#179984'}


def table(rows, columns):
    labels=[label for key,label in columns]
    lines=['|'+'|'.join(labels)+'|','|'+'|'.join('---' for _ in columns)+'|']
    for row in rows:
        vals=[]
        for key,_ in columns:
            value=row.get(key)
            if value is None or (isinstance(value,(float,np.floating)) and not np.isfinite(value)):text='不可估'
            elif isinstance(value,(float,np.floating)):text=f'{value:.4f}' if key in ('holm_p','permutation_p','auc_positive_net') else f'{value:.2f}'
            else:text=str(value)
            vals.append(text.replace('|','/'))
        lines.append('|'+'|'.join(vals)+'|')
    return '\n'.join(lines)


def enriched(frame):
    frame=frame.copy()
    frame['era']=frame.period.map(PERIOD_LABELS)
    frame['rule']=frame.variant.map(LABELS)
    for name in ('win_rate','initial_stop_rate','natural_win_rate','top1_positive_profit_share','top5_positive_profit_share','paired0_fraction','any_control_fraction'):
        if name in frame:frame[name+'_pct']=frame[name]*100
    return frame


def render(folder=EXPERIMENT/'results'):
    folder=Path(folder);gallery=folder/'gallery';gallery.mkdir(exist_ok=True)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    source=Path(__file__)
    if subprocess.check_output(['git','show','HEAD:'+str(source.relative_to(ROOT))],cwd=ROOT)!=source.read_bytes():
        raise ValueError('Commit report builder before rendering')
    manifest=json.loads((folder/'accounts_manifest.json').read_text())
    for item in manifest['artifacts']:
        if sha(item['path'])!=item['sha256']:raise ValueError('Changed result: '+item['path'])
    accounts=enriched(pd.read_csv(folder/'accounts_summary.csv'))
    events=enriched(pd.read_csv(folder/'event_summary.csv'))
    scores=pd.read_csv(folder/'score_summary.csv')
    thinning=pd.read_csv(folder/'thinning_summary.csv')
    full=accounts.loc[accounts.scope.eq('combined')&accounts.account.eq('full')]
    normal=full.loc[full.population.eq('all_assets')].copy()
    paired=accounts.loc[accounts.scope.eq('combined')&accounts.population.eq('all_assets')&accounts.account.ne('full')]
    for account,name in [('paired_actual','paired_strategy'),('paired_random','paired_random')]:
        p=paired.loc[paired.account.eq(account),['period','variant','return_pct']].rename(columns={'return_pct':name})
        normal=normal.merge(p,on=['period','variant'],how='left')
    normal=normal.merge(events.loc[events.scope.eq('combined'),['period','variant','paired0_fraction_pct']],on=['period','variant'])
    plt=_plot_setup();figures=[]
    for period in PERIODS:
        for population in ('all_assets','without_useless'):
            fig,(ax,dd)=plt.subplots(2,1,figsize=(13,6.6),sharex=True,gridspec_kw={'height_ratios':[3,1]},constrained_layout=True)
            for variant in LABELS:
                path=folder/'accounts'/f'{period}_combined_{variant}_{population}_full_curve.csv.gz'
                data=pd.read_csv(path);times=pd.to_datetime(data.time,utc=True)
                ax.plot(times,(data.equity/100000-1)*100,label=LABELS[variant],color=COLORS[variant],linewidth=1.4)
                dd.plot(times,data.drawdown*100,color=COLORS[variant],linewidth=1)
            ax.set_title(PERIOD_LABELS[period]+(' · 全资产' if population=='all_assets' else ' · 剔除整个USELESS后重新配资'),loc='left')
            ax.set_ylabel('Account return %');dd.set_ylabel('Close DD %');ax.axhline(0,color='#9baab7',linewidth=.7);ax.legend(frameon=False)
            ax.grid(alpha=.25,linestyle=':');dd.grid(alpha=.25,linestyle=':')
            p=gallery/f'{period}_{population}.png';fig.savefig(p,dpi=140);plt.close(fig);figures.append(p)
        fig,ax=plt.subplots(figsize=(11,4.5),constrained_layout=True)
        for pos,variant in enumerate(('volume4','expansion3')):
            values=thinning.loc[thinning.period.eq(period)&thinning.variant.eq(variant),'return_pct'].to_numpy()
            ax.scatter(np.linspace(pos-.16,pos+.16,len(values)),values,s=27,color='#a3b4c3',label='随机稀释19组' if pos==0 else None)
            observed=float(normal.loc[normal.period.eq(period)&normal.variant.eq(variant),'return_pct'].iloc[0])
            ax.scatter([pos],[observed],marker='D',s=70,color=COLORS[variant],label=LABELS[variant])
        ax.set_xticks([0,1],['量比过滤','振幅过滤']);ax.set_ylabel('Account return %');ax.axhline(0,color='#9baab7',linewidth=.7)
        ax.set_title(PERIOD_LABELS[period]+' · 同币同周、相同信号数量对照',loc='left');ax.legend(frameon=False);ax.grid(axis='y',alpha=.25,linestyle=':')
        p=gallery/f'{period}_thinning.png';fig.savefig(p,dpi=140);plt.close(fig);figures.append(p)
    pieces=['# spike · 1H 启动质量：过滤假启动，会不会也过滤大赢家？',
        '本轮固定原1H蓄势启动和主线回零退出，只分别增加量比≥4或TR/前ATR≥3。阈值来自上一轮SMA60退出的描述分桶，这里检验能否迁移到主线回零退出；没有组合过滤、搜索阈值或改变线上规则。',
        '**较早复核为2026-05-15至07-10；已知样本为07-10至09-09，均UTC且右端不含。两段独立启动账户。前者在上轮只用于预热，但并非全项目从未看过的历史；后者明确已看过。都不叫新鲜前向样本外。**']
    findings=EXPERIMENT/'FINDINGS.md'
    if findings.exists():pieces.append(findings.read_text())
    pieces+=['## 先看真实资金约束下的结果',
        '每个账户100000USDT、最多10资产、无杠杆；同资产跨所互斥，仓位仍受原风险和成交额容量约束。基线扣0.2%入场名义往返成本，尚未完整计资金费及真实滑点；收益含段末持仓盯市，回撤按收盘权益。配对账户是可匹配候选子集。',
        table(normal.to_dict('records'),[('era','时段'),('rule','规则'),('return_pct','组合收益%'),('max_drawdown_pct','最大回撤%'),('trades','成交数'),('win_rate_pct','胜率%'),('paired_strategy','配对策略%'),('paired_random','配对随机%'),('paired0_fraction_pct','配对覆盖%')]),
        '## 去掉USELESS后，还剩多少？',
        '下面从三个交易所候选里删除整个USELESS资产，并从初始资金重新运行账户，空出的资金和席位可以进入其他币。它不是静态扣掉某笔盈利，也不是建议上线一个事后黑名单。',
        table(full.loc[full.population.eq('without_useless')].to_dict('records'),[('era','时段'),('rule','规则'),('return_pct','重新配资收益%'),('max_drawdown_pct','最大回撤%'),('trades','成交数'),('top1_positive_profit_share_pct','最大赢家占正利润%'),('return_minus_top1_contribution_pct','再静态减最大赢家后%')]),
        '## 少交易本身，能否解释过滤后的变化？']
    thinrows=[]
    for (period,variant),g in thinning.groupby(['period','variant'],sort=False):
        ids=json.loads((folder/(period+'_'+variant+'_thinning_ids.json')).read_text())
        thinrows.append(dict(era=PERIOD_LABELS[period],rule=LABELS[variant],actual=float(normal.loc[normal.period.eq(period)&normal.variant.eq(variant),'return_pct'].iloc[0]),minimum=g.return_pct.min(),median=g.return_pct.median(),maximum=g.return_pct.max(),
            replaceable=ids['summary'].get('replaceable_target_candidate_fraction',np.nan)*100))
    pieces+=[table(thinrows,[('era','时段'),('rule','规则'),('actual','过滤账户%'),('minimum','随机稀释最低%'),('median','随机稀释中位%'),('maximum','随机稀释最高%'),('replaceable','可替换候选占比%')]),
        '在交易所×资产×UTC自然周内，随机保留与过滤后一样多的原始有效信号，固定19个种子。只有一个候选的组通常完全不能替换，因此必须看可替换比例。随机周调度是离线对照，不能拿来直接实盘；19组只是描述分布，不提供p<0.01验收。',
        '## 资金曲线与回撤']
    pieces+=['![两时期和稀释对照]('+str(p.resolve())+')' for p in figures]
    pieces+=['## 初损失败与大趋势保留',
        '这些是独立候选路径，可能同币跨所重复，不能相加为账户收益。大赢家指自然退出净价格涨幅≥50%；段末仍持有的路径单列。初损退出率不等于所有假启动的完整标签。',
        table(events.loc[events.scope.eq('combined')].to_dict('records'),[('era','时段'),('rule','规则'),('valid','有效路径'),('initial_stop_rate_pct','初损退出率%'),('natural_exits','自然退出'),('censored','段末盯市'),('natural_big_winners','自然退出≥50%'),('mean_net_bp','平均净bp'),('matched_random_mean_bp','匹配随机bp'),('asset_balanced_excess_bp','资产等权超额bp'),('holm_p','Holm p')]),
        '## 分交易所：结果能否迁移',
        table(accounts.loc[accounts.population.eq('all_assets')&accounts.account.eq('full')&accounts.scope.ne('combined')].to_dict('records'),[('era','时段'),('scope','交易所'),('rule','规则'),('return_pct','组合收益%'),('max_drawdown_pct','最大回撤%'),('trades','成交数')]),
        '各所账户独立使用100000资金，不能直接相加收益。相同币在不同交易所出现不算独立重复验证。',
        '## 保留、错过和失败的全局图']
    cases=[];seen=set()
    for period,(_,end) in PERIODS.items():
        data=scope_rows(read_events(folder/(period+'_events.csv.gz')),'combined')
        valid=data.loc[data.valid.eq(True)].copy()
        rv=np.isfinite(valid.relative_volume)&valid.relative_volume.ge(4)
        tr=np.isfinite(valid.tr_expansion)&valid.tr_expansion.ge(3)
        pools=[('两个门槛均保留的自然赢家',valid.loc[rv&tr&valid.natural_exit.eq(True)&valid.net_return.gt(0)],False),
            ('放量门槛过滤掉的自然赢家',valid.loc[~rv&valid.natural_exit.eq(True)&valid.net_return.gt(0)],False),
            ('扩张门槛过滤掉的自然赢家',valid.loc[~tr&valid.natural_exit.eq(True)&valid.net_return.gt(0)],False),
            ('两个门槛均通过却亏损',valid.loc[rv&tr&valid.net_return.lt(0)],True)]
        for caption,pool,ascending in pools:
            pool=pool.sort_values(['net_return','event_id'],ascending=[ascending,True])
            pool=pool.loc[~pool.event_id.isin(seen)]
            if pool.empty:continue
            row=pool.iloc[0].to_dict();seen.add(row['event_id'])
            f=pd.read_pickle(row['features_path']);f=f.loc[f.index<end].copy()
            dest=gallery/f'case_{len(cases)+1:02d}.png'
            check=_case_plot(f,row,dest);cases.append(dict(period=period,caption=caption,event_id=row['event_id'],source_sha256=sha(row['features_path']),**check))
            pieces+=['### '+PERIOD_LABELS[period]+' · '+caption+' · '+row['venue']+' '+row['symbol'],
                f"量比{row['relative_volume']:.2f}；TR扩张{row['tr_expansion']:.2f}；模拟净价格收益{row['net_return']*100:.2f}%；初损风险{row['initial_risk_frac']*100:.2f}%。入场{row['entry_time']}，退出{row['exit_time']}（UTC）。这是按结果选的解释图，不是独立盈利证据。",
                '![信号到真实模拟退出的全局图]('+str(dest.resolve())+')']
            figures.append(dest)
    pieces+=['## 分数诊断：并非训练模型',
        'AUC的标签是该路径净收益是否为正；Top10%按本时段分数事后分位定义，仅作单特征诊断，不是已知可执行门槛。这里没有训练或验证分类模型。所有24个规则/时期/交易所统计统一Holm；资产间仍有共同市场风险。',
        table(scores.to_dict('records'),[('period','时段'),('scope','市场'),('feature','单特征'),('auc_positive_net','排序AUC'),('top_decile_n','Top10%路径'),('top_decile_gross_bp','毛bp'),('top_decile_net_bp','净bp'),('top_decile_matched_actual_bp','匹配策略bp'),('top_decile_random_bp','匹配随机bp')]),
        '## 成本、数据与诚实限制',
        '费用压力表保持原成交和数量，仅增加成本贡献，没有重算费用变化后配资。资金费不完整，未将未知填零；Binance此前403仍按原记录保留，未绕过或重试。本轮不据这些账面数字宣称完整实盘收益。',
        table(normal.to_dict('records'),[('era','时段'),('rule','规则'),('return_pct','原20bp%'),('stress40_static_pct','40bp静态%'),('stress60_static_pct','60bp静态%'),('boundary_marks','段末盯市')]),
        '目录是之前冻结的当时目录及可获取历史，历史退市覆盖不完整。较早时期同样有幸存者偏差。两门槛来自已看过数据的线索，未达到跨期和对照一致时不能改称最优参数。无新训练、无自动promote，无线上策略或通知变更。',
        '## 验证与复现',
        'known无过滤基线四个账户对原报告收益、回撤、成交数逐项复现一致。earlier在本段截止处冻结退出；全部源/特征SHA、控制索引、配资账本、随机稀释ID与状态在本实验results中。该配置首次评分消耗holdout第1次；报告仅复用保存账本，图中价格一致性验证不再重跑参数。',
        '构建提交：`'+manifest['code_commit']+'`；报告提交：`'+head+'`。']
    runbook=EXPERIMENT/'RUNBOOK.md'
    if runbook.exists():pieces.append(runbook.read_text())
    report=ROOT/'analysis/p1_launch_quality_20260910.md';report.write_text('\n\n'.join(pieces)+'\n')
    subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/md_to_html.py'),str(report),'--out-dir',str(ROOT/'analysis/html')],check=True,cwd=ROOT)
    html=ROOT/'analysis/html'/report.with_suffix('.html').name
    receipt=dict(builder_commit=head,report=str(report),html=str(html),report_sha256=sha(report),html_sha256=sha(html),
        accounts_manifest_sha256=sha(folder/'accounts_manifest.json'),figures=[dict(path=str(p),sha256=sha(p)) for p in figures],cases=cases,scoring_performed=False)
    (gallery/'report_manifest.json').write_text(json.dumps(receipt,indent=2,default=str)+'\n')
    print(json.dumps(dict(report=str(html),figures=len(figures),cases=len(cases))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--results',type=Path,default=EXPERIMENT/'results')
    render(parser.parse_args().results)
