"""Synthesize frozen V8 gate and four-hour exit outcomes without candle reads.

Every source aggregate is checksum verified. Pair outcomes only by original
entry identity; sum R and exit-ordered drawdown describe events, not an account.
The builder retains failures, timezone sensitivities and right-tail losses.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_be05_report import metrics, paired_stats, SPLIT
from yoyo.evaluation.spike_six_filter_statistics import digest, prepare, windows

EXP = Path('experiments/active/exp-spike-v8-six-filters-20260914-v1')
LABELS = {
 'baseline': 'V8 原版', 'rv_gt50': '量比 >50 剔除', 'risk_gt30pct': '初始风险宽度 >30% 剔除',
 'usdc_base': 'USDC 标的剔除', 'stock_linked_all': '股票挂钩标的剔除',
 'h00_utc': 'UTC 0 点剔除', 'sunday_utc': 'UTC 周日剔除',
 'joint_rv_gt50_tratr_gt10': '量比 >50 且 TR/ATR >10 剔除',
 'h00_asia_shanghai': '北京 0 点剔除（敏感性）', 'sunday_asia_shanghai': '北京周日剔除（敏感性）',
 'four_hour_mfe_lt1_close_nonpositive_next_open': '4 小时仍弱提前退出',
 'holding_lt4h_NONCAUSAL': '实际持仓 <4h（事后，禁止准入）',
}


def verify(root, receipt_name):
    rec = json.loads((root / receipt_name).read_text())
    for filename, expected in rec['files'].items():
        if digest(root / filename) != expected:
            raise ValueError(f'Changed source aggregate: {root / filename}')
    return rec


def exit_tables(root: Path, out: Path):
    receipt = verify(root, 'receipt.json')
    if receipt['status'] != 'complete' or receipt['streams'] != 3531:
        raise ValueError('Incomplete four-hour experiment')
    serial = prepare(pd.read_csv(root / 'serial_trades.csv.gz', dtype={'asset': str}))
    fixed = prepare(pd.read_csv(root / 'fixed_original_entries.csv.gz', dtype={'asset': str}))
    rows = []
    for mode, data in [('serial', serial), ('fixed', fixed)]:
        if data.duplicated(['policy','event_key']).any():
            raise ValueError('Duplicate exit event identity')
        for policy, group in data.groupby('policy'):
            for period, part in windows(group):
                for minutes in ('all', 30, 60, 240):
                    tf = part if minutes == 'all' else part.loc[part.timeframe_min.eq(minutes)]
                    rows.append(dict(mode=mode,policy=policy,period=period,timeframe_min=minutes,**metrics(tf)))
    pd.DataFrame(rows).to_csv(out/'exit_metrics.csv',index=False)
    old = fixed.loc[fixed.policy.eq('baseline')]
    new = fixed.loc[~fixed.policy.eq('baseline')]
    ids = ['event_key','timeframe_min','side','entry_time']
    values = ['exit_time','net_r','censored']
    p = old[ids+values].merge(new[ids+values],on=ids,suffixes=('_baseline','_be05'),validate='one_to_one')
    if len(p)!=len(old) or len(p)!=len(new): raise ValueError('Unmatched fixed entry')
    p['joint_closed'] = ~p.censored_baseline & ~p.censored_be05
    p['delta_r'] = p.net_r_be05-p.net_r_baseline
    paired = []
    for period, g in [('full',p),('earlier',p.loc[(p.entry_time<SPLIT)&(p.exit_time_baseline<SPLIT)&(p.exit_time_be05<SPLIT)]),('later',p.loc[p.entry_time>=SPLIT])]:
        for minutes in ('all',30,60,240):
            tf = g if minutes=='all' else g.loc[g.timeframe_min.eq(minutes)]
            stats={k.replace('be05','four_hour'):v for k,v in paired_stats(tf).items()}
            paired.append(dict(period=period,timeframe_min=minutes,**stats))
    pd.DataFrame(paired).to_csv(out/'exit_paired_changes.csv',index=False)
    p.rename(columns=lambda c:c.replace('be05','four_hour')).to_csv(out/'exit_paired_trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    return pd.DataFrame(rows), pd.DataFrame(paired), receipt


def fmt(x, decimals=2):
    if x is None or pd.isna(x): return '不适用'
    return f'{x:,.{decimals}f}'


def table(headers, rows):
    return '| '+' | '.join(headers)+' |\n|'+'|'.join(['---']*len(headers))+'|\n'+'\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows)+'\n'


def run(admission: Path, exit_root: Path, out: Path):
    out.mkdir(parents=True,exist_ok=False)
    sr = verify(admission,'statistics_receipt.json')
    s = pd.read_csv(admission/'serial_metrics.csv',dtype={'timeframe_min':str,'side_group':str})
    f = pd.read_csv(admission/'fixed_effects.csv',dtype={'timeframe_min':str,'side_group':str})
    changes = pd.read_csv(admission/'serial_entry_changes.csv',dtype={'timeframe_min':str})
    null = pd.read_csv(admission/'matched_null.csv',dtype={'timeframe_min':str})
    em, ep, er = exit_tables(exit_root,out)
    em['timeframe_min']=em.timeframe_min.astype(str); ep['timeframe_min']=ep.timeframe_min.astype(str)
    # Fixed prior entries identify source losses; actual serial paths can differ.
    serial = pd.read_csv(admission/'serial_trades.csv.gz',dtype={'asset':str})
    serial['entry_time']=pd.to_datetime(serial.entry_time,utc=True)
    serial['exit_time']=pd.to_datetime(serial.exit_time,utc=True)
    contrib=[]
    for period, group in windows(serial):
        closed=group.loc[~group.censored]
        a=closed.groupby(['policy','venue','asset','timeframe_min']).net_r.agg(['size','sum']).reset_index()
        b=a.loc[a.policy.eq('baseline')].drop(columns='policy').rename(columns={'size':'baseline_n','sum':'baseline_r'})
        for policy in LABELS:
            if policy=='baseline': continue
            candidate=a.loc[a.policy.eq(policy)].drop(columns='policy').rename(columns={'size':'candidate_n','sum':'candidate_r'})
            if candidate.empty: continue
            z=b.merge(candidate,on=['venue','asset','timeframe_min'],how='outer').fillna(0)
            z['delta_r']=z.candidate_r-z.baseline_r; z['policy']=policy; z['period']=period
            contrib.append(z)
    pd.concat(contrib,ignore_index=True).to_csv(out/'asset_contributions.csv',index=False)
    full=s.query("period=='full' and timeframe_min=='all' and side_group=='all'")
    baseline=full.loc[full.policy.eq('baseline')].iloc[0]
    if int(baseline.closed)!=94746 or not np.isclose(baseline.total_r,-2021.782949,atol=1e-5):
        raise ValueError('Historical V8 baseline total changed')
    order=[p for p in LABELS if p in set(full.policy)]
    sections=['# V8：六批 V1 观察的独立过滤回放（2026-09-14）',
    '本轮把原始 V1 观察迁移到 V8，逐项测试，不叠加。数据是重复研究过的历史，结论属于探索证据。正文与每笔账本保持相同样本、成本、规则；未修改 Pine、线上监控、Bark 或实盘。',
    '## 两年全部交易：完整重跑后的结果',
    '数据：Binance、OKX、Gate，共 3,531 条合约×周期数据流；30m、1H、4H；2024-09-10（含）至 2026-09-10（不含），UTC。前后两年以 2025-09-10 切分。原版共 '+str(int(baseline.events))+' 笔执行事件，'+str(int(baseline.closed))+' 笔已结束，'+str(int(baseline.censored))+' 笔未完整结束。',
    '所有 R 为按单初始价格风险计算并扣 0.2% 名义往返成本的事件统计；R 相加不是账户收益率，事件序列回撤不是共享资金的最大回撤。',
    table(['规则','已结束笔数','净胜率','平均净R','合计净R','较原版ΔR','R利润因子','事件回撤R','兑现≥10R笔数'],[
      [LABELS[p],fmt(r.closed,0),fmt(r.win_rate*100)+'%',fmt(r.mean_r,4),fmt(r.total_r),fmt(r.total_r-baseline.total_r),fmt(r.pf_r,3),fmt(r.event_drawdown_r),fmt(r.realized_ge10,0)] for p in order for _,r in full.loc[full.policy.eq(p)].iterrows()]),
    '## 各周期与前后年份',
    '每格是相对同周期原版的合计净 R 变化。前一年排除跨切点未退出的交易；后一年按入场时间归属。不能只选改善的周期或年份。']
    for period,title in [('full','两年'),('earlier','前一年'),('later','后一年')]:
        g=s.query('period==@period and side_group=="all"')
        data=[]
        for p in order:
            row=[LABELS[p]]
            for tf in ('30','60','240'):
                r=g.loc[g.policy.eq(p)&g.timeframe_min.eq(tf)].iloc[0]
                b=g.loc[g.policy.eq('baseline')&g.timeframe_min.eq(tf)].iloc[0]
                row.append(fmt(r.total_r) if p=='baseline' else fmt(r.total_r-b.total_r))
            data.append(row)
        sections += ['### '+title,table(['规则（原版行是总净R，其余是ΔR）','30m','1H','4H'],data)]
    ff=f.query("period=='full' and timeframe_min=='all' and side_group=='all'")
    sections += ['## 保留原入场的归因，与完整重跑的区别',
    table(['规则','固定原单删除ΔR','避开的亏损R','牺牲的盈利R','少掉原入场','新增后续入场','原≥10R保留'],[
      [LABELS[p],fmt(r.fixed_delta_r),fmt(r.saved_loss_r),fmt(r.lost_winner_r),fmt(c.lost_entry_events,0),fmt(c.added_entry_events,0),f'{int(c.retained_original_ge10)}/{int(c.original_ge10)}']
      for p in order if p!='baseline' for _,r in ff.loc[ff.policy.eq(p)].iterrows() for _,c in changes.loc[changes.policy.eq(p)&changes.timeframe_min.eq('all')].iterrows()]),
    '各项有重叠，ΔR 不能相加。完整串行回放维持每条流单仓，原始反向信号仍能结束旧仓，即使反向新入场被过滤。跨币、跨所不合并风险，所以上表不表示账户可兑现回报。',
    '## 少交易是否真的选掉了更差的交易',
    '后一年每规则×周期做 2,000 次匹配随机删除：同交易所、标的、周期、方向、入场月份、固定信号 ATR/价格桶，删除相同数量。共 27 项，Bonferroni 乘 27。extraΔR 为比匹配随机少交易额外少亏的 R。整币排除在同币内无法识别，明确不报告显著性。该对照检验 V8 信号池内的选择，不证明相对任意市场入场有 alpha。',
    table(['规则','周期','实际固定ΔR','相对匹配额外ΔR','可随机重排删除数','强制删除数','校正p'],[
      [LABELS[r.policy],r.timeframe_min,fmt(-r.removed_net_r),fmt(r.selection_extra_saved_r),fmt(r.movable_rejections,0),fmt(r.forced_rejections,0),fmt(r.p_bonferroni_27,4)] for _,r in null.iterrows()]),
    '## 持仓不足 4 小时：单独的因果退出试验',
    '不能在开仓时知道最终持仓时长。替代规则只在实际入场后首次达到 4 小时的已收盘 K 线检查一次：截至当时的 MFE<1R，且收盘毛浮盈≤0R，下一根开盘退出。旧止损/跳空优先，其次原反向退出；此前已止损的单子仍计亏损。4H 在入场所在 4H K 收盘时检查，30m/1H 分别对应第8/4根。没有把上一轮 0.5R 保本或分档锁利混入。',
    table(['周期','原版净R','4h退出净R','串行ΔR','原版胜率','4h退出胜率','原版兑现≥10R','4h退出兑现≥10R'],[
      [tf,fmt(b.total_r),fmt(c.total_r),fmt(c.total_r-b.total_r),fmt(b.win_rate*100)+'%',fmt(c.win_rate*100)+'%',fmt(b.realized_ge10,0),fmt(c.realized_ge10,0)]
      for tf in ['all','30','60','240'] for _,b in em.loc[em['mode'].eq('serial')&em.policy.eq('baseline')&em.period.eq('full')&em.timeframe_min.eq(tf)].iterrows()
      for _,c in em.loc[em['mode'].eq('serial')&~em.policy.eq('baseline')&em.period.eq('full')&em.timeframe_min.eq(tf)].iterrows()]),
    table(['周期','同入场双结束笔数','配对ΔR','改善亏单','伤害赢单','原10R保留','每事件ΔR周块95%区间'],[
      [r.timeframe_min,fmt(r.joint_closed,0),fmt(r.delta_r),fmt(r.rescued_losers,0),fmt(r.harmed_winners,0),f'{int(r.retained_original_ge10)}/{int(r.original_realized_ge10)}',fmt(r.mean_delta_weekly_ci_low,4)+' 至 '+fmt(r.mean_delta_weekly_ci_high,4)] for _,r in ep.loc[ep.period.eq('full')].iterrows()]),
    '## 原 V1 表述的来源复核',
    '原始账本为 6,253 行（含日线），其中 6,170 笔已结束、83 笔未结束。以下是旧账本静态分组，不是重新跑策略得到的可回收收益。',]
    audit=pd.read_csv(EXP/'source_audit/source_claims.csv')
    sections.append(table(['分组','笔数','胜率','该分组净R'],[[LABELS.get(r.policy,r.policy),fmt(r.rows,0),fmt(r.win_rate*100)+'%',fmt(r.sum_net_r)] for _,r in audit.loc[audit.scope.eq('source_with_daily')].iterrows()]))
    sections += ['量比 >50 的约 -171R 可复现。原 Notion 的校正 p=0.0001 归于“量比>50 且 TR/ATR>10”的联合区，不属于单独量比上限；缺少原检验运行产物，不能把该 p 当成本轮证据。H00 原时钟未写明，本轮按 UTC 与北京时间分别记录。股票规则采用交易所元数据全部股票挂钩类型，不能冒充严格“美股” +44R；USDC 是底层标的过滤，不是计价币过滤。',
    '## 交易与过滤的精确定义',
    'V8 保留原 BB 压缩、过热和确认/延迟准入。量比与 TR/ATR 读最终确认 K 线的冻结特征，不读早先强势证据 K 的最大值。风险宽度是下一根开盘报价下的初始止损距离/报价，>0.30 是30%，开仓前判断，不把止损缩窄。时段过滤用信号收盘确认时间，下一根开盘成交；不是把 K 线开盘时间当成确认时间。',
    '原执行参数：5 根结构止损窗口、0.2ATR 缓冲、2ATR 最小风险；收盘浮盈达到2R才启动4ATR 跟随保护。每根先检查既有止损，再于收盘更新下一根保护；不把同一根的高点用于提前止损。缺口按可成交开盘处理，数据断档/期末未退出列为删失。不设固定止盈。',
    '未知类型或缺失特征保留但标为 unknown，不默认为已通过证据。元数据采用冻结当前目录；不是包含退市合约的完整历史时点分类。',
    '## 风险与诚实声明',
    '- 全部为被多次研究过的历史，不是新的盲测或部署验收。观察原 V1 后测试 V8 仍然有选择偏差；月份切分不能消除前期反复研究。',
    '- 同币跨交易所、相近时间信号相关；胜率不是独立伯努利样本，采用周块区间与匹配删除。没有完整历史资金费率、订单簿、滑点和账户仓位模型，结果不是实际账户收益。',
    '- FOMO、薄市、session 错配属于机制假设，这轮价格与成交量回放不能直接证明原因。过滤提高某项合计值也可能丢失右尾大趋势。',
    '- AUC、模型 top-decile 毛/净收益：不适用；本轮未训练/评分分类器，也未据未来收益选 top-decile。严格对照是同事件原版、串行原版与匹配随机删除，而非虚构模型指标。缺少匹配任意市场入场对照，因此不宣称完整策略 alpha。',
    '- 研究与报告不更改线上准入、执行、通知或持仓。',
    '## 产物与复现',
    '完整逐笔文件、按周期/多空/月份统计、匹配检验和标的贡献见本地实验 delivery 目录。HTML 是可直接阅读版本；MD 与 builder 进入 git，较大的 gzip 账本保留本地并由 SHA receipt 绑定。',
    '```bash\n.venv/bin/python -m experiments.active.exp-spike-v8-six-filters-20260914-v1.reconcile_source\n.venv/bin/python -m yoyo.evaluation.spike_v8_six_filters --official --output experiments/active/exp-spike-v8-six-filters-20260914-v1/admission/results/reproduce_v1\n.venv/bin/python -m yoyo.evaluation.spike_v8_four_hour_exit --output experiments/active/exp-spike-v8-six-filters-20260914-v1/exit/reproduce_v1\n.venv/bin/python -m yoyo.evaluation.spike_six_filter_statistics --input experiments/active/exp-spike-v8-six-filters-20260914-v1/admission/results/reproduce_v1 --output experiments/active/exp-spike-v8-six-filters-20260914-v1/delivery/reproduce_statistics\n.venv/bin/python -m yoyo.evaluation.spike_six_filters_report --admission experiments/active/exp-spike-v8-six-filters-20260914-v1/delivery/reproduce_statistics --exit experiments/active/exp-spike-v8-six-filters-20260914-v1/exit/reproduce_v1 --output experiments/active/exp-spike-v8-six-filters-20260914-v1/delivery/reproduce_summary\n.venv/bin/python scripts/md_to_html.py experiments/active/exp-spike-v8-six-filters-20260914-v1/delivery/reproduce_summary/report_generated.md --out-dir experiments/active/exp-spike-v8-six-filters-20260914-v1/delivery/reproduce_summary/html\n```',
    '来源：'+str(admission)+'；'+str(exit_root)+'；原始 V1 Notion https://app.notion.com/p/nick-wiki/3d88856479af806d8abcf00b1c393d85 。']
    (out/'report_generated.md').write_text('\n\n'.join(sections)+'\n')
    receipt=dict(builder_sha256=digest(Path(__file__)),admission_receipt_sha256=digest(admission/'statistics_receipt.json'),exit_receipt_sha256=digest(exit_root/'receipt.json'),files={p.name:digest(p) for p in out.iterdir() if p.is_file()})
    (out/'summary_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(out/'report_generated.md')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--admission',type=Path,required=True);p.add_argument('--exit',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.admission,a.exit,a.output)
