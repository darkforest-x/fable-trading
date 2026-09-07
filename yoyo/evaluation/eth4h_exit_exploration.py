"""Preregistered two-hypothesis ETH4h research, no threshold grid search.

All adaptive decisions use development2021-2024 only and are written before
exposed replication. Raw evidence is never overwritten. No native Pine VM claim.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation.eth4h_trend_candidate import close_boundary
from yoyo.evaluation.pine_allin_eth4h_replay import ROOT, clean, digest, inference, load_source, save_json, table, trade_stats
from yoyo.layers.l3_backtest.eth4h_exit_exploration import BASE, BE_OFF, ExplorationPolicy, ExplorationReplay

EXP=ROOT/'experiments/active/exp-eth4h-exit-exploration-20260907-v2'
OUT=EXP/'results'
WINDOWS={'development':('2021-01-01','2025-01-01'),
         'exposed_replication':('2025-01-01','2026-05-01'),
         'continuous':('2021-01-01','2026-05-01')}


class ResearchReplay(ExplorationReplay):
    def run(self,start,end,*,injected=None):
        return close_boundary(super().run(start,end,injected=injected),self.frame,end,self.policy.fee)


def bounds(frame,window):
    return tuple(int(frame.open_time.searchsorted(pd.Timestamp(d,tz='UTC'))) for d in WINDOWS[window])


def enrich_controls(frame,runner,trades,start,end):
    """Fixed pre-outcome matching; require each control's entry eligibility."""
    months=frame.open_time.dt.strftime('%Y-%m').to_numpy()
    blocks=(frame.hk_hour//6).to_numpy(); bins=frame.vol_bin.to_numpy()
    pool={}
    for i in range(start,end-1):
        if runner.allowed[i] and not runner.raw[i] and bins[i]>=0:
            pool.setdefault((months[i],blocks[i],bins[i]),[]).append(i)
    rows,matches=[],[]
    for tid,t in trades.iterrows():
        i,side=int(t.signal_i),int(t.direction)
        eligible=[j for j in pool.get((months[i],blocks[i],bins[i]),[])
                  if abs(i-j)>12 and runner.entry_signal_allowed(j,side,None)]
        key=f'allin-eth4h-v1|{i}|{side}'
        chosen=sorted(eligible,key=lambda j:hashlib.sha256(f'{key}|{j}'.encode()).digest())[:3]
        vals=[]
        for j in chosen:
            out=runner.run(j,end,injected=(j,side))
            if out['trades'].empty:
                raise AssertionError('A selected admissible control failed to produce an event')
            c=out['trades'].iloc[0];vals.append(c.net_return)
            rows.append({'trade_id':int(tid),'case_signal_i':i,'control_signal_i':j,'direction':side,
                         'month':months[i],'hk_6h_block':int(blocks[i]),'vol_bin':int(bins[i]),
                         'control_entry_i':int(c.entry_i),'control_exit_i':int(c.exit_i),
                         'control_entry_price':c.entry_price,'control_exit_price':c.exit_price,
                         'control_net_return':c.net_return,'control_gross_return':c.gross_return})
        matches.append({'trade_id':int(tid),'n_controls':len(chosen),'matched':bool(chosen),
                        'control_mean':float(np.mean(vals)) if vals else np.nan})
    if not len(trades):
        raise AssertionError('No trades; record failure explicitly before continuing')
    t=trades.join(pd.DataFrame(matches).set_index('trade_id'))
    t['paired_excess']=t.net_return-t.control_mean
    return t,pd.DataFrame(rows)


def annual_returns(e):
    previous=500.;rows=[]
    for year,g in e.groupby((e.time-pd.Timedelta(nanoseconds=1)).dt.year):
        last=float(g.equity.iloc[-1])
        rows.append({'year':int(year),'return_pct':(last/previous-1)*100,'end_equity':last})
        previous=last
    return rows


def account(frame,policy,window):
    start,end=bounds(frame,window)
    runner=ResearchReplay(frame,policy);out=runner.run(start,end)
    t,c=enrich_controls(frame,runner,out['trades'],start,end)
    stem=f'{window}_{policy.name}'
    for suffix,df in [('trades',t),('controls',c),('equity',out['equity']),('events',out['events'])]:
        df.to_csv(OUT/f'{stem}_{suffix}.csv',index=False)
    assert np.isclose(500+t.net_pnl.sum(),out['final_equity'])
    years=annual_returns(out['equity'])
    row={'window':window,'arm':policy.name,**trade_stats(t),'return_pct':(out['final_equity']/500-1)*100,
         'dd_pct':out['max_drawdown_path']*100,'close_dd_pct':out['max_drawdown_close']*100,
         'final_equity':out['final_equity'],'fees':out['fees'],'yearly':years,
         'profitable_years':sum(r['return_pct']>0 for r in years),
         'nonpositive_equity_seen':out['nonpositive_equity_seen'],'exposure_pct':out['exposure_fraction']*100,
         'ranking':inference(t),'n_raw_signals':int(((runner.raw[start:end]!=0)&runner.allowed[start:end]).sum())}
    save_json(OUT/f'{stem}_summary.json',row)
    print(json.dumps(clean({k:row[k] for k in ['window','arm','return_pct','dd_pct','trades','net_bp','excess_bp']})),flush=True)
    return row,out['equity']


def choose(parent,candidate):
    rules={'at_least20_trades':candidate['trades']>=20,
           'positive_better_account_return':candidate['return_pct']>max(0,parent['return_pct']),
           'better_unit_return':candidate['net_bp']>parent['net_bp'],
           'positive_nonworse_matched_excess':candidate['excess_bp']>0 and candidate['excess_bp']>=parent['excess_bp'],
           'drawdown_at_most20pct':candidate['dd_pct']<=20,
           'nonfewer_profitable_years':candidate['profitable_years']>=parent['profitable_years'],
           'no_insolvency':not candidate['nonpositive_equity_seen']}
    return {'parent':parent['arm'],'candidate':candidate['arm'],'criteria':rules,
            'selected':candidate['arm'] if all(rules.values()) else parent['arm'],
            'decision_window':'development_only','production_approval':False}


def verify_r1(frame):
    previous=ROOT/'experiments/active/exp-eth4h-trend-candidate-20260907-v1/results'
    checks={}
    for window in WINDOWS:
        a=ResearchReplay(frame,BASE).run(*bounds(frame,window))
        b=pd.read_csv(previous/f'{window}_S5_close_only_trades.csv',float_precision='round_trip')
        for col in ['signal_i','entry_i','exit_i','qty','entry_price','exit_price','net_pnl']:
            checks[window+'_'+col]=bool(np.array_equal(a['trades'][col],b[col]))
    assert all(checks.values()),checks
    return checks


def common_cohort(frame):
    """Fixed all-eligible raw events; exits differ without portfolio occupancy."""
    start,end=bounds(frame,'development')
    frames,controls_by_arm={},{}
    base=ResearchReplay(frame,BASE)
    ids=[i for i in range(start,end-1) if base.raw[i] and base.allowed[i]]
    for policy in [BASE,BE_OFF]:
        runner=ResearchReplay(frame,policy);events=[]
        for i in ids:
            event=runner.run(i,end,injected=(i,int(base.raw[i])))['trades']
            assert len(event)==1
            events.append(event.iloc[0].to_dict())
        t,c=enrich_controls(frame,runner,pd.DataFrame(events),start,end)
        t.to_csv(OUT/f'common_cohort_{policy.name}_trades.csv',index=False)
        c.to_csv(OUT/f'common_cohort_{policy.name}_controls.csv',index=False)
        frames[policy.name]=t;controls_by_arm[policy.name]=c
    a,b=frames[BASE.name],frames[BE_OFF.name]
    assert np.array_equal(a.signal_i,b.signal_i)
    for col in ['trade_id','case_signal_i','control_signal_i']:
        assert np.array_equal(controls_by_arm[BASE.name][col],controls_by_arm[BE_OFF.name][col])
    delta=b.net_return-a.net_return
    paired_delta=b.paired_excess-a.paired_excess
    paired=pd.DataFrame({'signal_time':a.signal_time,'net_delta':delta,'paired_delta':paired_delta})
    paired.to_csv(OUT/'common_cohort_deltas.csv',index=False)
    g=paired.groupby(paired.signal_time.dt.strftime('%Y-%m')).paired_delta.sum().to_numpy()
    n=int(paired.paired_delta.notna().sum());rng=np.random.default_rng(20260907)
    null=(rng.choice([-1,1],(10000,len(g)))*g[None,:]).sum(axis=1)/max(1,n)
    obs=float(paired.paired_delta.mean())
    return {'rows':[{'arm':name,**trade_stats(t)} for name,t in frames.items()],
            'n_common_events':len(a),'be_off_minus_on_net_bp':delta.mean()*1e4,
            'paired_delta_bp':obs*1e4,'paired_delta_month_signflip_p':(1+int((null>=obs).sum()))/10001,
            'overlapping_events_not_portfolio':True,'control_ids_identical':True}


def write_pine(policy):
    source=(ROOT/'experiments/active/exp-eth4h-trend-candidate-20260907-v1/eth4h_trend_r1.pine').read_text()
    source=source.replace('ETH 4H Trend R1','ETH 4H Trend R2').replace('ETH4H R1','ETH4H R2')
    if not policy.breakeven:
        source=source.replace('if strategy.position_size > 0 and high > entry * 1.015','if false and strategy.position_size > 0 and high > entry * 1.015')
        source=source.replace('if strategy.position_size < 0 and low < entry * 0.985','if false and strategy.position_size < 0 and low < entry * 0.985')
    gate='side * (slowMa - slowMa[1]) > 0' if policy.slope_gate else 'true'
    source=source.replace('if not hasPosition and quantity > 0',f'if not hasPosition and quantity > 0 and ({gate})')
    source=source.replace('// Research candidate.',f'// Frozen policy: breakeven={policy.breakeven}, flat_slope_gate={policy.slope_gate}.\n// Research candidate.')
    (EXP/'eth4h_trend_r2.pine').write_text(source)


def report(payload):
    cols=[('arm','版本'),('return_pct','净收益%'),('dd_pct','模拟回撤%'),('trades','笔数'),('win_rate_pct','净胜率%'),
          ('net_bp','每笔净bp'),('matched_case_bp','匹配病例bp'),('random_control_bp','随机bp'),('excess_bp','配对超额bp')]
    selected=payload['selected_policy']['name']
    chunks=['# ETH4h R2：保本与慢均线方向的有限探索',
            f"最终固定候选：**{selected}**。两项选择仅用2021–2024开发期；后段历史已经暴露，不是全新样本外。原生Pine撮合尚未核对，所有结果仅为历史研究。",
            '[Pine 源码](eth4h_trend_r2.pine)。每边手续费0.1%，价格止损风险0.5%权益，名义仓位最多1倍；初始止损仍为4ATR和3%取小。没有增加资金费或滑点假设。',
            '## 预先冻结的选择与结果',
            '```json\n'+json.dumps(clean(payload['decisions']),ensure_ascii=False,indent=2)+'\n```']
    for window in WINDOWS:
        rows=[r for r in payload['summary'] if r['window']==window]
        chunks += ['## '+window,table(rows,cols)]
    chunks += ['## 同一入场事件的保本反事实对照',
               table(payload['cohort']['rows'],[('arm','版本'),('trades','固定事件数'),('net_bp','净bp'),('matched_case_bp','匹配病例bp'),('random_control_bp','随机bp'),('excess_bp','配对超额bp')]),
               '```json\n'+json.dumps(clean({k:v for k,v in payload['cohort'].items() if k!='rows'}),ensure_ascii=False,indent=2)+'\n```',
               '入场身份与控制身份相同，退出后独立终止。事件彼此可能重叠，不能将事件收益累加成资金曲线；组合结果包含仓位占用及冷却的后续影响。',
               '## 排序诊断与分年结果']
    for row in payload['summary']:
        if row['arm']==selected:
            chunks += ['### '+row['window'],table([row['ranking']],[('n','样本'),('auc_abs_osc_vs_net_positive','AUC'),('top_decile_gross_bp','前10%毛bp'),('top_decile_net_bp','前10%净bp'),('top_decile_control_bp','随机bp'),('ranking_permutation_p','排序p'),('matched_month_cluster_signflip_p','配对月簇p')]),
                       table(row['yearly'],[('year','年份'),('return_pct','账户收益%'),('end_equity','年末权益')])]
    chunks += ['## 风险与诚实声明',
               '- 两个假设与每个候选的父版本在结果前冻结。完整保留失败分支；不是大规模参数搜索，也不是生产批准。',
               '- 开发期的自适应选择及此前回测造成选择偏差；所有p值仅描述性，不能冒充未见验证。收益分组与年度结果均为事后解释。',
               '- 2020预热，221952根15m聚合13872根4h，完整16根且无间隙；只读取固定哈希的pre-May2026文件，不读取2026-05-04起holdout。',
               '- 初始保护、同向收紧、反向只平、冷却和边界平仓保留。关闭保本不等于取消硬止损。慢均线方向门只限制空仓开仓，不阻止持仓退出。',
               '- 控制同币、月、HK6h、因果波动桶和方向；缺少匹配不补造。它们有独立状态，不是可交易随机组合。',
               '- 模拟没有额外滑点、资金费、交易所数量取整、保证金强平；尚无原生Pine编译或逐笔一致性证据。',
               '## 资金曲线','![资金曲线](../experiments/active/exp-eth4h-exit-exploration-20260907-v2/results/equity.png)',
               '## 复现',
               '```bash\ncd /Users/zhangzc/fable-trading\n.venv/bin/python -m pytest tests/test_pine_allin_eth4h.py tests/test_eth4h_trend_candidate.py tests/test_eth4h_exit_exploration.py -q\n.venv/bin/python -m yoyo.evaluation.eth4h_exit_exploration\n```',
               '已有结果拒绝覆盖；仅重建报告用 --report-only。输入哈希、源码提交及选择见实验目录。',
               '来源：[TradingView策略执行](https://www.tradingview.com/pine-script-docs/v5/concepts/strategies/)。']
    md=ROOT/'analysis/p0_eth4h_exit_exploration_20260907.md';md.write_text('\n\n'.join(chunks)+'\n')
    subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/md_to_html.py'),str(md),'--out-dir',str(ROOT/'analysis/html')],check=True)
    (ROOT/'analysis/html/eth4h_trend_r2.pine').write_bytes((EXP/'eth4h_trend_r2.pine').read_bytes())


def main():
    if OUT.exists():raise RuntimeError('Output exists; refuse to overwrite')
    config=json.loads((EXP/'config.json').read_text())
    assert config['baseline']==asdict(BASE) and config['be_off']==asdict(BE_OFF)
    paths=[Path(__file__),ROOT/'yoyo/layers/l3_backtest/eth4h_exit_exploration.py',ROOT/'yoyo/layers/l3_backtest/pine_allin_eth4h.py',ROOT/'yoyo/layers/l3_backtest/eth4h_trend_candidate.py',EXP/'config.json',EXP/'PROJECT_PLAN.md',ROOT/'yoyo/evaluation/eth4h_trend_candidate.py',ROOT/'yoyo/evaluation/pine_allin_eth4h_replay.py',ROOT/'yoyo/layers/l3_backtest/pine_allin_v7.py']
    for p in paths:
        frozen=subprocess.check_output(['git','show',f'HEAD:{p.relative_to(ROOT)}'])
        assert hashlib.sha256(frozen).hexdigest()==digest(p),p
    frame,receipt=load_source();regression=verify_r1(frame)
    OUT.mkdir()
    payload={'generated_at':datetime.now(timezone.utc),'builder_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
             'code_hashes':{str(p.relative_to(ROOT)):digest(p) for p in paths},'data':receipt,'r1_golden_regression':regression,
             'summary':[],'decisions':[],'holdout_consumed':False,'production_eligible':False,'training_eligible':False}
    save_json(OUT/'pre_run_receipt.json',payload)
    a,_=account(frame,BASE,'development');b,_=account(frame,BE_OFF,'development')
    payload['summary'] += [a,b]
    decision=choose(a,b);payload['decisions'].append(decision)
    save_json(OUT/'decision_be.json',decision)
    parent=BE_OFF if decision['selected']==BE_OFF.name else BASE
    parent_row=b if parent==BE_OFF else a
    slope=replace(parent,name=parent.name+'_slope',slope_gate=True)
    s,_=account(frame,slope,'development');payload['summary'].append(s)
    decision=choose(parent_row,s);payload['decisions'].append(decision)
    final=slope if decision['selected']==slope.name else parent
    save_json(OUT/'decision_slope_and_final.json',{'decision':decision,'selected_policy':asdict(final)})
    payload['selected_policy']=asdict(final)
    write_pine(final)
    payload['cohort']=common_cohort(frame)
    comparator=replace(final,name='SMA_comparator',cross_only=True)
    comp,_=account(frame,comparator,'development');payload['summary'].append(comp)
    curves={}
    for window in ['exposed_replication','continuous']:
        for policy in [BASE,BE_OFF,slope,comparator]:
            row,e=account(frame,policy,window);payload['summary'].append(row)
            if window=='continuous':curves[policy.name]=e
    fig,axes=plt.subplots(2,1,figsize=(10,6),sharex=True)
    for name,e in curves.items():
        axes[0].plot(e.time,e.equity,label=name)
        axes[1].plot(e.time,(e.equity/e.equity.cummax().clip(lower=500)-1)*100,label=name)
    axes[0].set_title('ETH 4h R2 | two frozen hypotheses | fees 0.1% per fill')
    axes[0].set_ylabel('USDT equity');axes[1].set_ylabel('Close drawdown %')
    for ax in axes:ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.tight_layout();fig.savefig(OUT/'equity.png',dpi=150);plt.close(fig)
    save_json(OUT/'summary.json',payload)
    pd.DataFrame([{k:v for k,v in r.items() if k not in ['yearly','ranking']} for r in payload['summary']]).to_csv(OUT/'summary.csv',index=False)
    report(payload)
    print(json.dumps({'selected':final.name,'decisions':clean(payload['decisions'])},indent=2),flush=True)


if __name__=='__main__':
    import sys
    if sys.argv[1:]==['--report-only']:report(json.loads((OUT/'summary.json').read_text()))
    elif sys.argv[1:]:raise SystemExit('Only --report-only is supported')
    else:main()
