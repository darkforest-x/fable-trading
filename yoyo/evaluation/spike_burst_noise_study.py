"""Single-variable V2 density-episode reuse audit; no live code or orders.

Uses authenticated prior 1H features and unchanged event labels. Selection
reads only current/prior OHLCV, progressive fields and causal episode state.
All candidates and same-asset/week/causal-volatility controls are frozen before
next-open outcomes. This is retrospective seen history, not independent OOS.
"""
from pathlib import Path
import argparse
import json
import shutil
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_recall_study as old
from yoyo.evaluation import spike_burst_early_warning as auth
from yoyo.evaluation import spike_burst_progressive as base
from yoyo.evaluation import spike_burst_episode as episode
from yoyo.evaluation.spike_burst_dataset import load_feature, match_controls
from yoyo.evaluation.spike_burst_execution import simulate_trade

ROOT = old.ROOT
EXP = ROOT / 'experiments/active/exp-spike-burst-noise-20260910-v4'
OUT = EXP / 'results'
ARMS = ('v2', 'episode')
CONFIG = dict(schema='v2-density-episode-v4', intervention='consume latest prior12 density episode once',
              risk_costs='unchanged V2', direction='long', holdout_consumption=1, retrospective=True)


def pins():
    paths = [Path(__file__), ROOT/'yoyo/evaluation/spike_burst_episode.py', EXP/'PROJECT_PLAN.md']
    paths += [ROOT/'yoyo/evaluation'/f'{n}.py' for n in (
        'spike_burst_progressive', 'spike_burst_replay', 'spike_burst_early_warning',
        'spike_burst_recall_study', 'spike_burst_dataset', 'spike_burst_execution',
        'altseason_research', 'launch_quality_dataset')]
    result = {}
    for p in paths:
        rel = str(p.resolve().relative_to(ROOT))
        if subprocess.check_output(['git','show',f'HEAD:{rel}'],cwd=ROOT) != p.read_bytes():
            raise ValueError('Commit builder first: '+rel)
        result[rel] = old.sha(p)
    return result


def read(name):
    return pd.read_csv(OUT/name, float_precision='round_trip')


def prepare():
    source = pins()
    if OUT.exists() and any(OUT.iterdir()):
        raise ValueError('Refusing overwrite')
    jobs, _, refs, labels = auth.authenticated_prior()
    jobs = [j for j in jobs if j['asset'] not in ('BTC','ETH')]
    OUT.mkdir(parents=True)
    old.write_json(OUT/'prepare_started.json',dict(source_pins=source,config=CONFIG))
    sigs, ctrls, dets, changes, blocked, schedules = [], [], [], [], [], []
    frozen_v2 = pd.read_csv(auth.V2/'signals.csv.gz')
    for number,j in enumerate(jobs):
        f = load_feature(j['features_path'],j['features_sha256'],60,old.END)
        full = f.join(base.progressive_fields(f))
        states = {'v2':base.replay(full,float(j['tick'])),
                  'episode':episode.replay(full,float(j['tick']))}
        lab = labels[labels.instrument.eq(j['instrument'])]
        closes = f.index+old.HOUR
        win = (closes>=old.START)&(closes<old.END)
        dec = {a:np.flatnonzero(s.burst.to_numpy()) for a,s in states.items()}
        expected = frozen_v2.loc[frozen_v2.arm.eq('v2') & frozen_v2.instrument.eq(j['instrument']), 'decision_i'].tolist()
        if [int(i) for i in dec['v2'] if win[i]] != expected:
            raise ValueError('Baseline replay drift '+j['instrument'])
        union = sorted(set(dec['v2'])|set(dec['episode']))
        evaluated = [int(i) for i in union if win[i]]
        matcher = f.copy()
        matcher['history_count'] = np.where(f.ready.eq(True),f.history_count,0)
        controls = match_controls(matcher,union,evaluated,j['instrument'],60,old.START,old.END)
        ctx = {k:j[k] for k in ('instrument','asset','symbol','venue','minutes')}
        for arm,s in states.items():
            matches, detection = old.match_signals(lab,dec[arm],f,s)
            detection['arm'],detection['instrument'] = arm,j['instrument']
            dets.append(detection)
            m = matches.set_index('decision_i').to_dict('index')
            for i in dec[arm]:
                if not win[i]: continue
                eid = old.identity(j['instrument'],arm,int(i))
                sigs.append(dict(ctx,event_id=eid,arm=arm,decision_i=int(i),decision_time=closes[i],
                    route=s.route.iloc[i],relative_volume=f.rv.iloc[i],tr_expansion=f.expansion.iloc[i],
                    signal_close=f.close.iloc[i],signal_atr=f.atr.iloc[i],match_status=m[int(i)]["match_status"],label_i=m[int(i)]["label_i"],lag=m[int(i)]["lag"]))
                for k,ci in enumerate(controls[int(i)]):
                    ctrls.append(dict(ctx,event_id=old.identity(eid,'control',k),matched_event_id=eid,
                        arm=arm,control_number=k,decision_i=int(ci),decision_time=closes[ci]))
        # Rejected candidates are observable at that close, not necessarily
        # former V2 entries because reference state can diverge after rejection.
        es = states['episode']
        for i in np.flatnonzero(es.episode_gate_blocked.to_numpy() & win):
            blocked.append(dict(ctx,decision_i=int(i),decision_time=closes[i],
                prior_episode=es.prior_episode_id.iloc[i],episode_consumed=es.episode_consumed.iloc[i]))
        for i in evaluated:
            a,b=bool(states['v2'].burst.iloc[i]),bool(es.burst.iloc[i])
            changes.append(dict(ctx,decision_i=i,decision_time=closes[i],v2=a,episode=b,
                change='shared' if a and b else 'removed' if a else 'new',
                prior_episode=es.prior_episode_id.iloc[i],episode_consumed=es.episode_consumed.iloc[i]))
        schedules.append(j)
        if number%40==0: print('prepared',number+1,len(jobs),flush=True)
    signal_cols = list(ctx)+['event_id','arm','decision_i','decision_time','route','relative_volume',
        'tr_expansion','signal_close','signal_atr','match_status','label_i','lag']
    control_cols = list(ctx)+['event_id','matched_event_id','arm','control_number','decision_i','decision_time']
    products={'signals.csv.gz':pd.DataFrame(sigs,columns=signal_cols),'controls.csv.gz':pd.DataFrame(ctrls,columns=control_cols),
              'detections.csv.gz':pd.concat(dets,ignore_index=True),'changes.csv.gz':pd.DataFrame(changes),
              'blocked.csv.gz':pd.DataFrame(blocked)}
    for name,t in products.items(): old.write_csv(OUT/name,t)
    shutil.copyfile(auth.V2/'labels.csv.gz',OUT/'labels.csv.gz')
    shutil.copyfile(auth.V2/'exposure.csv.gz',OUT/'exposure.csv.gz')
    old.write_json(OUT/'matching.json',dict(jobs=schedules))
    old._verify_references(refs)
    if pins()!=source: raise ValueError('Source drift')
    old.write_json(OUT/'prepared_manifest.json',dict(status='complete',config=CONFIG,source_pins=source,
        sources=refs,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        artifacts=[old.artifact(OUT/n) for n in list(products)+['labels.csv.gz','exposure.csv.gz','matching.json']]))


def evaluate():
    p = json.loads((OUT/'prepared_manifest.json').read_text())
    if p.get('status')!='complete' or p.get('config')!=CONFIG:
        raise ValueError('Incomplete or changed prepared config')
    if pins()!=p['source_pins']: raise ValueError('Builder drift')
    if (OUT/'evaluation_started.json').exists(): raise ValueError('No repeated evaluation')
    for a in p['artifacts']: old.checked(a['path'],a['sha256'])
    old._verify_references(p['sources'])
    old.write_json(OUT/'evaluation_started.json',dict(prepared_sha=old.sha(OUT/'prepared_manifest.json')))
    signals,controls=read('signals.csv.gz'),read('controls.csv.gz')
    actual,random=[],[]
    for number,j in enumerate(json.loads((OUT/'matching.json').read_text())['jobs']):
        f=load_feature(j['features_path'],j['features_sha256'],60,old.END)
        cache={}
        for t,dest in ((signals,actual),(controls,random)):
            for r in t.loc[t.instrument.eq(j['instrument'])].to_dict('records'):
                i=int(r['decision_i'])
                if i not in cache:
                    cache[i]=dict(simulate_trade(f,i,float(j['tick']),old.END),**auth.forward_outcome(f,i))
                dest.append(dict(r,relative_volume=f.rv.iloc[i],tr_expansion=f.expansion.iloc[i],**cache[i]))
        if number%40==0: print('scored',number+1,flush=True)
    actual,random=pd.DataFrame(actual),pd.DataFrame(random)
    numeric=['net_bp','gross_bp','net_return','net_r','peak_r','relative_volume','tr_expansion']
    for table in (actual,random):
        for col in ['event_id','matched_event_id','arm','asset','decision_time','valid','natural_exit',
                    'censored','forward_known','forward_success','match_status']+numeric:
            if col not in table:
                table[col]=pd.Series(index=table.index,dtype=float if col in numeric else object)
    labels,detections,exposure=read('labels.csv.gz'),read('detections.csv.gz'),read('exposure.csv.gz')
    for t in (actual,random,signals,labels,exposure): t['decision_time']=pd.to_datetime(t.decision_time,utc=True)
    summaries,scores=[],[]
    for arm in ARMS:
        labs=labels.merge(detections[detections.arm.eq(arm)],on=['instrument','event_i'],validate='one_to_one')
        for period,(start,end) in old.period_windows().items():
            a=actual[actual.arm.eq(arm)&actual.decision_time.ge(start)&actual.decision_time.lt(end)]
            c=random[random.matched_event_id.isin(a.event_id)]
            l=labs[labs.decision_time.ge(start)&labs.decision_time.lt(end)]
            positives=l[l.label.eq('positive')]
            row,score=old.trade_summary(a,c)
            known=a[a.forward_known.eq(True)]
            known_c=c[c.forward_known.eq(True)]
            hours=exposure.loc[exposure.decision_time.ge(start)&exposure.decision_time.lt(end),'eligible_bars'].sum()
            row.update(arm=arm,period=period,signals=len(a),alerts_per_day=len(a)/((end-start)/pd.Timedelta(days=1)),
                alerts_per_100_asset_days=2400*len(a)/hours if hours else np.nan,
                positive_events=len(positives),hits_1=int(positives.hit_1.sum()),recall_1=positives.hit_1.mean(),
                event_match_rate=a.match_status.eq('matched').mean(),unknown_match=int(a.match_status.eq('unknown').sum()),
                forward_known=len(known),forward_success_rate=pd.to_numeric(known.forward_success).mean(),
                random_forward_success_rate=pd.to_numeric(known_c.forward_success).mean())
            summaries.append(row)
            if period=='full': scores.extend(dict(arm=arm,**s) for s in score)
    summary=pd.DataFrame(summaries)
    mask=summary.period.eq('full')
    summary.loc[mask,'holm_p']=auth.holm(summary.loc[mask,'permutation_p'].fillna(1).to_numpy())
    products={'trade_events.csv.gz':actual,'trade_controls.csv.gz':random,
              'summary.csv':summary,'score_summary.csv':pd.DataFrame(scores)}
    for name,t in products.items(): old.write_csv(OUT/name,t)
    for a in p['artifacts']: old.checked(a['path'],a['sha256'])
    old._verify_references(p['sources'])
    if pins()!=p['source_pins']: raise ValueError('Source drift')
    old.write_json(OUT/'validation_manifest.json',dict(status='complete',config=CONFIG,
        prepared_sha=old.sha(OUT/'prepared_manifest.json'),source_pins=p['source_pins'],
        artifacts=[old.artifact(OUT/n) for n in products],account_return_not_computed=True))
    print(summary[summary.period.eq('full')].to_json(orient='records',force_ascii=False),flush=True)


def report():
    """Render frozen outcomes, not another scoring pass or parameter search."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from scripts.md_to_html import convert
    validation=json.loads((OUT/'validation_manifest.json').read_text())
    for item in validation['artifacts']: old.checked(item['path'],item['sha256'])
    s=read('summary.csv'); full=s[s.period.eq('full')].set_index('arm')
    a,b=full.loc['v2'],full.loc['episode']
    signals=read('signals.csv.gz'); changes=read('changes.csv.gz'); blocked=read('blocked.csv.gz')
    signals['decision_time']=pd.to_datetime(signals.decision_time,utc=True)
    fig,axes=plt.subplots(1,2,figsize=(12,4),gridspec_kw={'width_ratios':[1,3]})
    axes[0].bar(['V2','One episode'],[a.signals,b.signals],color=['#64748b','#008f82'])
    for i,v in enumerate([a.signals,b.signals]): axes[0].text(i,v,str(int(v)),ha='center',va='bottom')
    axes[0].set_title('61-day long alerts / 278 contracts')
    for arm,col in [('v2','#64748b'),('episode','#008f82')]:
        t=signals[signals.arm.eq(arm)].set_index('decision_time').resample('D').size()
        axes[1].plot(t.index,t.values,label=arm,color=col)
    axes[1].set_title('Alerts per UTC day');axes[1].legend();axes[1].tick_params(axis='x',rotation=25)
    for ax in axes: ax.spines[['top','right']].set_visible(False)
    fig.tight_layout();fig.savefig(OUT/'alert_load.png',dpi=160);plt.close(fig)
    def table(t,cols):
        out=['| '+' | '.join(cols)+' |','|'+'|'.join(['---']*len(cols))+'|']
        for _,r in t.iterrows():
            out.append('| '+' | '.join(str(r[c]) if isinstance(r[c],str) else 'NA' if pd.isna(r[c]) else f'{r[c]:.4f}' for c in cols)+' |')
        return '\n'.join(out)
    count=changes.change.value_counts().to_dict()
    reduction=1-b.signals/a.signals
    text=f'''# SPIKE：频繁信号的定位与单项降噪实测

本轮当前原生TV确认使用 **SPIKE BURST V2**，不是尚未安装的V3。Owner的“无时无刻开单”按频繁箭头理解；本程序是离线信号研究，没有自动下单。V3早预警170条/日的数字不能解释当前TV实例。

## 结果先读

同一密集区限制一次渐进启动后，信号从 **{int(a.signals)} 降至 {int(b.signals)}**，减少 **{reduction:.2%}**；全池每天 **{a.alerts_per_day:.2f} → {b.alerts_per_day:.2f}**。自然平仓净胜率 **{a.natural_win_rate:.2%} → {b.natural_win_rate:.2%}**，事件均值相对匹配随机 **{a.mean_excess_bp:.2f}bp → {b.mean_excess_bp:.2f}bp**。

这项改动只检验“旧密集区被再次使用”这一机制，不能将减少提示等同提高盈利。没有据结果继续调参，没有替换TV、监控或通知。研究版仍不能当成已验证的开单系统。

![Signal burden](../experiments/active/exp-spike-burst-noise-20260910-v4/results/alert_load.png)

## 数据、定义、时序

- 同一278个历史OKX 1H合约，UTC [2026-07-10,2026-09-09)，61天；BTC/ETH沿用原排除。不是当前全部合约，也不是3年验证。
- 原8046个事件锚点，1660个正例、6240个负例、146个未知。标签正例率20.63%；已知样本中21.01%。原标签逐字节保持。标签是首次12根突破后24根内+4ATR先于-2ATR，未要求均线密集，不是密集启动金标。
- 这是本配置第1次消耗holdout，边界仍≥2026-05-04。沿用Owner全日期授权；这些历史已研究过，不能称盲测/独立样本外。没有训练，因此训练val样本、val AUC不适用。
- 所有信号只读当根及之前；全池冻结之后才算结果。只有标签和结果看未来。确认时点为小时收盘；北京时间为UTC+8。
- 当前gap只重置新的密集证据归属；原价格/均线/风险遵循旧连续源合同。真实输入由执行器再次检查完整小时无缺口。

## 为什么旧机制可能重复

原版“参考止损触及”立即释放持有状态；下一根渐进启动仍可使用过去12根任意一次密集证明。关闭上一笔参考，不等于产生新一段蓄势。新机制把既有密集true连续段编号，读取此前12根最新ID，正式信号消费ID，止损不归还。只限制渐进路线，硬爆发路线保持原规则；密集false→true才算新ID。没有额外时间冷却、无新阈值。

完整重放变化：共有{count.get('shared',0)}个相同时间信号，移除{count.get('removed',0)}个旧信号，新增{count.get('new',0)}个不同时间信号；新状态中{len(blocked)}个候选根被挡。被挡候选根不等于移除交易数，因为跳过参考持仓会改变后续可用状态。密集状态边界仍可能抖动，因此不是完美的趋势分段。

## 前后对照

百分比列以0–1表示；bp=万分之一。下面是独立事件结果，不是资金曲线或账户收益。

{table(s[s.period.isin(['full','first31','last30','case_night'])], ['arm','period','signals','alerts_per_day','hits_1','positive_events','recall_1','event_match_rate','forward_success_rate','random_forward_success_rate'])}

原锚点+6匹配率与每个信号后的24根障碍成功率是不同指标；未匹配并不等于亏损，可能只是原24根去重或+6匹配范围外的上涨。旧报告duplicates=0也不证明没有同趋势后续信号。

{table(s[s.period.isin(['full','first31','last30','case_night'])], ['arm','period','valid','natural_exits','natural_win_rate','mean_gross_bp','mean_net_bp','paired_random_net_bp','mean_excess_bp','permutation_p','holm_p'])}

匹配随机：同币×同UTC周×因果ATR百分比桶，最多3个；统一排除两臂当根与此前12根信号。不排除未来信号、不按未来盈亏选对照。新实验重新匹配，因此旧V2报告随机超额数值可能不同；相同旧V2实际事件和收益应相同。Holm两臂全期修正，分期数字仅描述。case_night是手选上涨夜，不当普遍有效证据。

## 单变量基线

量比/TR只描述排序，top-decile为各自指标最高10%，不是获利最高10%；未据此选阈值。descriptive_auc不是训练或验证AUC。

{table(read('score_summary.csv'), ['arm','feature','n','descriptive_auc','top_n','top_gross_bp','top_net_bp','top_win_rate','top_random_bp','top_excess_bp'])}

## 复现命令

```bash
.venv/bin/python -m pytest -q tests/test_spike_burst_episode.py tests/test_spike_burst_noise_study.py tests/test_spike_burst_progressive.py
.venv/bin/python -m yoyo.evaluation.spike_burst_noise_study prepare
.venv/bin/python -m yoyo.evaluation.spike_burst_noise_study evaluate
.venv/bin/python -m yoyo.evaluation.spike_burst_noise_study report
```

先提交准确builder再运行；认证旧features输入。prepare/evaluate拒绝覆盖已存在产物；首次运行使用预注册的空results目录，已完成实验只做哈希核对，不能删除旧结果刷实验。

## 风险与诚实声明

- 止损、次根开盘、2R启动4ATR跟踪、无固定止盈、20bp入场名义往返成本不变；资金费、滑点与冲击未完整建模。没有账户最大回撤结论。
- Python只验证多头；原Pine空头镜像未在此轮验证。原生TV当前版本核对不等于所有用户图表设置相同；本轮没有编译/替换Pine。
- 多币同晚高度相关，不能按每条信号当独立成功证据。曾经看过的数据不是新样本外；不宣称最优参数。
- 80%覆盖率与低噪音是两个目标，旧V3靠普通突破取得高覆盖但未建立交易优势。预警不应该默认带入场框来暗示开单。本轮不将其上线。

## 下一步

若同段去重变化小，问题主要不是重复发送，而是初次突破质量；应另行冻结“压缩形成过程/突破后是否留在区间外”等假设并给误报代价。不要再通过遍历门槛追80%。TV或通知切换要在结果足够支持后明确交付；本轮没有切换。
'''
    md=ROOT/'analysis/p1_spike_burst_noise_20260910.md'
    md.write_text(text)
    # Required project converter, with the actual markdown source base path.
    subprocess.run([str(ROOT/'.venv/bin/python'),'scripts/md_to_html.py',str(md),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    html=ROOT/'analysis/html'/f'{md.stem}.html'
    old.write_json(OUT/'report_manifest.json',dict(validation_sha=old.sha(OUT/'validation_manifest.json'),
        source_sha=old.sha(Path(__file__)),artifacts=[old.artifact(p) for p in (md,html,OUT/'alert_load.png')]))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('prepare','evaluate','report'))
    args=parser.parse_args()
    {'prepare':prepare,'evaluate':evaluate,'report':report}[args.phase]()
