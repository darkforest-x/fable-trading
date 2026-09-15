"""Receipt-backed V9 cost-BE readout; event R is never account equity.

Only this experiment's completed ledgers are consumed. Chronological subgroup
tables exclude trades and matched controls that span a subgroup boundary.
Original-entry counterfactuals and changed serial admissions remain separate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v9_cost_be2_study import EXP, ROOT, ARMS, sha, save
from yoyo.evaluation.spike_v9_full_report import block_statistics
from yoyo.evaluation.spike_be05_report import metrics


def longest(flags):
    best=run=0
    for flag in flags:
        run=run+1 if flag else 0;best=max(best,run)
    return best


def boolean(series):
    out=series.astype(str).str.lower().map({'true':True,'false':False})
    if out.isna().any():raise ValueError('invalid boolean')
    return out.astype(bool)


def load(scope):
    out=EXP/'results'/scope
    manifest=json.loads((out/'manifest.json').read_text())
    if manifest['status']!='complete' or manifest['streams']!=manifest['expected_streams']:
        raise ValueError('incomplete requested scope')
    folders=sorted(p for p in (out/'streams').iterdir() if (p/'completion.json').exists())
    if len(folders)!=manifest['streams']:raise ValueError('stream coverage differs')
    trades=[];pairs=[];controls=[]
    for folder in folders:
        receipt=json.loads((folder/'completion.json').read_text())
        if not receipt['baseline_parent_parity'] or not receipt['fixed_baseline_parity']:raise ValueError('missing baseline check')
        for name,digest in receipt['files'].items():
            if sha(folder/name)!=digest:raise ValueError('output changed: '+str(folder/name))
        for arm in ARMS:trades.append(pd.read_csv(folder/f'{arm}.trades.csv.gz'))
        pairs.append(pd.read_csv(folder/'paired.csv.gz'));controls.append(pd.read_csv(folder/'controls.csv.gz'))
    result=[]
    for kind,frames in [('trades',trades),('pairs',pairs),('controls',controls)]:
        frame=pd.concat(frames,ignore_index=True)
        for c in frame.columns:
            if c in ['entry_time','exit_time','exit_time_original','exit_time_be2','target_exit_time','control_entry_time','control_exit_time']:
                frame[c]=pd.to_datetime(frame[c],utc=True)
            if c in ['censored','near_be','full_initial_stop','censored_original','censored_be2','joint_closed','matched','target_censored','be_armed','be_lock_changed']:
                frame[c]=boolean(frame[c])
        identity=['event_key','window']+([] if kind=='pairs' else ['arm'])
        if frame.duplicated(identity).any():raise ValueError('duplicate output event')
        result.append(frame)
    return (*result,manifest)


def slices(scope,trades):
    if scope=='eth':
        for (minutes,window),part in trades.groupby(['timeframe_min','window']):
            yield f'ETH {minutes}m',window,part,None,None
    else:
        periods=[('full',None,None),('earlier',None,pd.Timestamp('2025-09-10T00:00Z')),
            ('later',pd.Timestamp('2025-09-10T00:00Z'),None),
            ('pre_holdout',None,pd.Timestamp('2026-05-04T00:00Z')),
            ('holdout',pd.Timestamp('2026-05-04T00:00Z'),None)]
        groups=[('ALL',trades)]
        groups += [(f'{venue} {minutes}m',part) for (venue,minutes),part in trades.groupby(['venue','timeframe_min'])]
        groups += [(f'ETH {venue} {minutes}m',part) for (venue,minutes),part in trades.loc[trades.asset.eq('ETH')].groupby(['venue','timeframe_min'])]
        for group,table in groups:
            for period,left,right in periods:
                part=table
                if left is not None:part=part.loc[part.entry_time>=left]
                if right is not None:part=part.loc[(part.entry_time<right)&(part.exit_time<right)]
                yield group,period,part,left,right


def summarize(scope,trades,pairs,controls):
    rows=[];paired_rows=[]
    for group,period,table,left,right in slices(scope,trades):
        ids=table[['event_key','window']].drop_duplicates()
        paired=ids.merge(pairs,on=['event_key','window'],validate='one_to_one')
        paired=paired.loc[paired.joint_closed]
        if left is not None:paired=paired.loc[paired.entry_time>=left]
        if right is not None:paired=paired.loc[(paired.exit_time_original<right)&(paired.exit_time_be2<right)]
        delta=paired.delta_r
        gain_loser=(paired.net_r_original<0)&(delta>1e-9)
        harmed_winner=(paired.net_r_original>0)&(delta < -1e-9)
        tails=paired.net_r_original>=10
        paired_rows.append(dict(group=group,period=period,paired_closed=len(paired),
            paired_delta_r=float(delta.sum()),improved_losers=int(gain_loser.sum()),
            improved_loser_delta_r=float(delta[gain_loser].sum()),harmed_winners=int(harmed_winner.sum()),
            harmed_winner_delta_r=float(delta[harmed_winner].sum()),original_ge10=int(tails.sum()),
            retained_original_ge10=int((tails & paired.net_r_be2.ge(10)).sum()),
            **block_statistics(delta,paired.entry_time.dt.strftime('%Y-%m'))))
        for arm,one in table.groupby('arm'):
            closed=one.loc[~one.censored].sort_values(['exit_time','entry_time','event_key'])
            match=one[['event_key','window','arm']].merge(controls,on=['event_key','window','arm'],validate='one_to_one')
            match=match.loc[match.matched]
            if left is not None:match=match.loc[match.control_entry_time>=left]
            if right is not None:match=match.loc[match.control_exit_time<right]
            excess=match.target_net_r-match.control_net_r
            m=metrics(one)
            rows.append(dict(group=group,period=period,arm=arm,**m,
                gross_r=float(closed.gross_r.sum()),cost_r=float(closed.cost_r.sum()),
                near_be=int(closed.near_be.sum()),material_wins=int((closed.net_r>closed.one_tick_r+1e-9).sum()),
                max_net_loss_streak=longest(closed.net_r<0),max_initial_stop_streak=longest(closed.full_initial_stop),
                be_armed=int(one.be_armed.sum()),be_changed=int(one.be_lock_changed.sum()),
                matched_pairs=len(match),unmatched=len(one)-len(match),
                control_mean_r=float(match.control_net_r.mean()),paired_excess_r=float(excess.mean()),
                **block_statistics(excess,match.entry_time.dt.strftime('%Y-%m'))))
    summary=pd.DataFrame(rows);paired_summary=pd.DataFrame(paired_rows)
    for frame in (summary,paired_summary):
        frame['p_holm']=np.nan
        valid=frame.loc[frame.p_month_signflip.notna()].sort_values('p_month_signflip')
        if len(valid):frame.loc[valid.index,'p_holm']=np.minimum(1,np.maximum.accumulate(valid.p_month_signflip.to_numpy()*np.arange(len(valid),0,-1)))
    return summary,paired_summary


def md_table(frame,columns):
    def fmt(v):
        if isinstance(v,(float,np.floating)):
            return '—' if not np.isfinite(v) else f'{v:.2f}'
        return str(v)
    keys=list(columns);head='| '+' | '.join(columns.values())+' |'
    return '\n'.join([head,'| '+' | '.join(['---']*len(keys))+' |',
        *['| '+' | '.join(fmt(row[k]) for k in keys)+' |' for _,row in frame.iterrows()]])


def run(scopes):
    summaries={};paired_summaries={};manifests={}
    for scope in scopes:
        trades,pairs,controls,manifest=load(scope)
        summary,paired=summarize(scope,trades,pairs,controls)
        out=EXP/'results'/scope
        summary.to_csv(out/'summary.csv',index=False);paired.to_csv(out/'paired_summary.csv',index=False)
        summaries[scope]=summary;paired_summaries[scope]=paired;manifests[scope]=manifest
    columns={'group':'范围','period':'区间','arm':'退出','closed':'自然平仓','win_rate_pct':'净正率%','near_be':'近保本',
        'total_r':'净R','pf_r':'PF','event_drawdown_r':'结算回撤R','max_net_loss_streak':'最长净亏',
        'matched_pairs':'随机匹配','control_mean_r':'随机均R','paired_excess_r':'超额均R','p_holm':'Holm p'}
    texts=[]
    for scope,summary in summaries.items():
        display=summary.copy();display['win_rate_pct']=display.win_rate*100
        if scope=='universe':display=display.loc[display.group.eq('ALL') | (display.period.eq('full') & ~display.group.str.startswith('ETH'))]
        texts.append(('## 原V9全币种池' if scope=='universe' else '## OKX ETH 各周期')+'\n\n'+md_table(display,columns))
        pdsp=paired_summaries[scope]
        if scope=='universe':pdsp=pdsp.loc[pdsp.group.eq('ALL')]
        texts.append('### 固定原入场的退出变化\n\n'+md_table(pdsp,{'group':'范围','period':'区间','paired_closed':'配对',
            'paired_delta_r':'改动净R','improved_losers':'亏单改善','improved_loser_delta_r':'改善R',
            'harmed_winners':'赢单变差','harmed_winner_delta_r':'损失R','original_ge10':'原净10R','retained_original_ge10':'保留净10R','p_holm':'Holm p'}))
    config=json.loads((EXP/'config.json').read_text())
    is_holdout='universe' in scopes
    command='PYTHONPATH="$PWD/.venv/lib/python3.9/site-packages:$PWD" /usr/bin/python3'
    report=ROOT/'analysis/p1_spike_v9_cost_be2_20260915.md'
    body=f'''# V9 加入净2R后的0.2%保护：同入场与串行回测

本轮已实现 `SPIKE V9 · 净2R保本`，只新增退出保护。收益结论见下列完整对照；不做参数搜索、不以净正率增加代替净收益改善。原V9与监控代码保持其原身份，新增Pine为可加载交付，未声称TradingView已保存/编译或实盘启用。

## 冻结交易规则

V9原多空准入全部保留。当前有效止损先执行；存活K线收盘时，以本根多头最高/空头最低计算毛有利R，减固定往返0.2%的成本R，达到净2R后，下一根多单保护至少为开仓×1.002、空单至多为×0.998。按tick向有利方向取整，只收紧。原收盘毛2R启动4ATR跟踪与原始V6反向下一开盘退出继续有效。没有ICT、分批、固定TP或倍投。

1R是实际下一开盘到信号时冻结初始止损的价格距离。Pine沿用确认收盘参考价，研究回测用下一开盘实际价格；图上的参考R不能当成交账本。触发根曾达净2R但收盘已回落时，仍只从下一根更新；跳空可能低于保护目标成交。

## 数据、边界与验收

- ETH：OKX，3m/5m/15m/30m/1H/4H。`available`从2023-08-01与各源1500根预热结束的较晚者起，`common`从2026-01-01与上述起点较晚者起，均止于2026-05-01 UTC。各周期精确起止与数据行数见inputs和completion收据；5m历史短，不用其他交易所补齐。
- 原池（仅在本次范围包含时）：3531冻结流，2024-09-10至2026-09-10 UTC，分界2025-09-10。全量指原V9固定池，不代表所有曾上市合约；沿用原池覆盖和存活偏差。分段排除跨右边界交易，跨边界仓位不会塞进早期样本。
- 本轮holdout：{'本精确配置第1次消耗holdout；只在代码、参数和授权冻结后最终评估，复用已暴露历史，不是盲OOS。' if is_holdout else '消耗0次，未读取2026-05-04及以后价格；原全量池尚未评估。'}
- 每条流关闭新规则后对齐原串行引擎，每笔原始入场的固定回放也逐笔对齐。{'全币种旧V9账本另有逐笔归档parity。' if is_holdout else ''} 全部自然交易的进出成本、平仓数量、毛净收益和R换算独立核对；未闭合或数据缺口记censored，不计自然胜负。
- 净正率严格按净R>0；`近保本`为绝对净R不超过一个价格tick对应的R，单独展示。取整微盈仍可能使净正率看起来更高。最长连亏按实际退出排序，保本不会偿还前序倍投亏损。
- 随机对照匹配同币、同方向、同月、因果ATR/价格桶和评估时间段，周日同样禁入，同退出同成本；一次固定抽样，未完成不补抽。月块同时覆盖所有资产/交易所，2000次置换/重采样。Holm对本scope内展示及导出全部分组检验统一校正。
- AUC、top-decile与单特征排名不适用：无模型训练或预测分数。原版固定同入场是退出规则的单变量基线，匹配随机入场是方向收益的零假设对照。

{chr(10).join(texts)}

## 如何读结果

串行表包含保护导致的提前退出与新增交易；固定原入场表只量化同一批交易如何变化。两者不能混为一个收益数字。救回亏单与提前截断赢家均列出；达到过净10R的事后峰值不用于触发本轮规则，尾部保留按原实际净10R平仓计算。

多币总R和逐笔结算回撤R属于事件统计，不是1000U共享账户权益或保证金回撤；跨币交易可能同时持仓，未建立共享资金占用模型。ETH各周期也是独立策略流，不合并成组合。保本次数不等于赚到1R或收回旧损失。

## 风险与诚实声明

固定0.2%是原研究往返成本假设，没有另加资金费率和额外滑点。0.2%保护目标不保证实际成交覆盖一切成本。OHLC无法证明根内先后路径，旧止损优先、下一根生效避免乐观回写。旧结果已经暴露；本轮固定参数比较不能证明未来盈利，也没有自动promote、改仓或发通知。部分月份、周期样本小；看一张较好区间表不能替代全表结论。

## 复现与交付

```bash
cd /Users/zhangzc/fable-trading
{command} -m pytest -q tests/evaluation/test_spike_v9_cost_be2_engine.py tests/evaluation/test_spike_v9_cost_be2_study.py
{command} -m yoyo.evaluation.spike_v9_cost_be2_study eth
{(command+' -m yoyo.evaluation.spike_v9_cost_be2_study universe --workers 4') if is_holdout else '# 未授权全量holdout时，不运行universe命令。'}
{command} -m yoyo.evaluation.spike_v9_cost_be2_report {' '.join(scopes)}
/usr/bin/python3 scripts/md_to_html.py analysis/p1_spike_v9_cost_be2_20260915.md --out-dir analysis/html
```

运行前要求builder/config/tests已提交且逐字不变；全量另验证配置特定授权，禁止仅重用旧V9授权。结果文件夹为 `experiments/active/exp-spike-v9-cost-be2-20260915-v1/results/`，每流保留原版/新版trades、fills、events、固定入场BE路径、paired和controls，manifest逐个绑定SHA。

Pine：[SPIKE V9 · 净2R保本](/Users/zhangzc/fable-trading/yoyo/evaluation/pine/spike_burst_v9_cost_be2.pine)。当前交付含代码与回测，TradingView编辑器实际加载、监控部署属于另一次明确动作，未把本地文件存在称为已经上线。

后续可由Owner依据净收益、尾部损失与回撤决定是否采用；任何新阈值定义新实验。此次不自动挑选赢家周期、扩大仓位或启动倍投。
'''
    report.write_text(body)
    subprocess.run(['/usr/bin/python3','scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    save(EXP/'report_manifest.json',dict(scopes=scopes,report=str(report.relative_to(ROOT)),report_sha256=sha(report),
        html_sha256=sha(ROOT/'analysis/html/p1_spike_v9_cost_be2_20260915.html'),
        summaries={scope:{name:sha(EXP/'results'/scope/name) for name in ['summary.csv','paired_summary.csv','manifest.json']} for scope in scopes}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('scopes',nargs='+',choices=['eth','universe'])
    run(parser.parse_args().scopes)
