"""Expose locked rules, comparable realized returns and strict-control evidence in a single Chinese CSV."""
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_selection as search

FOLLOW={'any':'不限','zero':'0','one_to_four':'1–4','five_to_nine':'5–9','ten_plus':'≥10',
    'at_most_four':'≤4','ratio_le_25pct':'新增≤初始的25%','ratio_25_to_100pct':'初始的25%<新增≤初始的100%',
    'ratio_gt_100pct':'新增>初始币数','front_only':'仅前半段有新增','back_only':'仅后半段有新增',
    'slowing':'后半段新增速度减慢','accelerating':'后半段新增速度加快'}
CONF={'none':'不限','fresh_cluster':'最近一笔初始首触距收盘≤5分钟','prior_shock_6h':'前6小时有≥25币同向冲击',
    'no_prior_shock_24h':'最近一次≥25币同向冲击至少在24小时前','reclaim_lt25':'收回下轨的币<25%',
    'reclaim_25to75':'收回下轨的币占25%至不足75%','reclaim_ge50':'至少50%收回下轨','reclaim_ge75':'至少75%收回下轨',
    'alt_move_negative':'初始山寨币观察期涨幅中位数<0','alt_move_0to2':'初始山寨币观察期涨幅中位数在0%至不足2%',
    'alt_move_ge2':'初始山寨币观察期涨幅中位数≥2%','btc_move_negative':'BTC观察期下跌',
    'btc_move_0to1':'BTC观察期涨幅在0%至不足1%','btc_move_ge1':'BTC观察期涨幅≥1%'}
ROLES={'dynamic_primary':'动态退出首选','time_exit_winner':'固定持有首选','retention':'山寨保留浮盈候选',
       'case_target':'指定事件目标候选','case_runner':'指定事件分批留仓候选','fee_only_case':'指定事件仅扣手续费候选'}


def count_label(name):
    if name.startswith('share_'):return '≥过去28天有触轨记录币种的'+name.split('_')[1]+'%'
    lo,hi=map(int,name[1:].split('_'))
    if hi>=100000:return f'≥{lo} 个'
    return f'{lo} 个' if lo==hi else f'{lo}–{hi} 个'


def exit_label(r):
    if r.role=='time_exit_winner':return f'{r.hold_hours}小时到期市价退出'
    h=int(r.hold_hours);sl=int(r.stop_pct)
    parts=[f'初始止损{sl}%' if sl else '无初始止损']
    if r.kind.startswith('btc_funded_'):
        if r.kind.endswith('partial'):parts += [f'按已付资金费更新净1万点目标，预留一次市价手续费后卖{r.take_fraction*100:g}%',f'余仓回撤{r.width:g}%跟踪' if r.width else '余仓持至到期']
        elif r.kind.endswith('floor'):parts+=['达到含资金费的净1万点后，按已完成K线抬高保护价']
        else:parts+=['按已付资金费更新1万净点限价']
    else:
        if r.take_fraction>0:parts.append(f'涨{r.take_pct:g}%卖{r.take_fraction*100:g}%')
        if r.kind=='percent_trail':parts.append(f'涨{r.activate_pct:g}%后从已收盘最高价回撤{r.width:g}%退出')
        elif r.kind.startswith('atr_'):parts.append(f'涨{r.activate_pct:g}%后跟踪已收盘最高价下方{r.width:g}倍{r.kind[4:]} ATR(14)')
        elif r.kind=='profit_lock':parts.append(f'涨{r.activate_pct:g}%后锁住已出现涨幅的{r.width*100:g}%')
        elif r.kind.startswith('trend_'):parts.append(f'涨{r.activate_pct:g}%后，已收盘{r.kind[6:]}价格低于EMA{r.width:g}退出')
    parts.append(f'最迟{h}小时平仓')
    return '，'.join(parts)


def compact():
    out=search.OUT;control=out/'controls'
    selectors={'timed':search.SEARCH,'dynamic':study.OUT}
    funding=pd.read_csv(study.OUT/'finalists_funding_2026.csv').set_index('candidate_id')
    drawdown=pd.read_csv(study.OUT/'marked_risk_summary_2026.csv').set_index('candidate_id')
    res=[]
    for typ,directory in selectors.items():
        r=pd.read_csv(directory/'locked_rules.csv')
        ev=pd.read_csv(directory/'locked_evaluation.csv')
        name='fixed_validity.csv' if typ=='timed' else 'dynamic_validity.csv'
        validity=pd.read_csv(control/name).set_index('candidate_id')
        past=pd.read_csv(control/'past_only'/name).set_index('candidate_id')
        for row in r[r.role.isin(ROLES)].itertuples():
            k=row.candidate_id
            if typ=='timed':fold=ev[(ev.candidate_id==k)&(ev.epoch==3)]
            else:fold=ev[(ev.candidate_id==k)&ev.period.eq('3')]
            if not len(fold):continue
            stat=fold.iloc[0];v=validity.loc[k] if k in validity.index else {}
            fd=funding.loc[k] if k in funding.index else {}
            risk=drawdown.loc[k] if k in drawdown.index else {}
            res.append({'候选ID':k,'标的':row.asset,'用途':ROLES[row.role],'初始窗口分钟':row.minutes,
                '初始触轨范围':count_label(row.breadth),'观察分钟':row.wait_min,'新增币条件':FOLLOW[row.diffusion],
                '价格或时序条件':CONF[row.confirmation],'退出办法':exit_label(row),'2026独立笔数':stat.n,
                '2026胜率%':stat.win,'2026主口径每笔均收%':stat['mean'],'主口径含资金费':k.endswith('_funded_BTC'),
                '去最佳月每笔均收%':stat.exmonth,'扣资金费可核笔数':fd.get('evaluated',np.nan),
                '扣资金费每笔均收%':fd.get('after_funding',np.nan),'扣资金费胜率%':fd.get('win_pct',np.nan),
                '每笔最大浮盈均值%':stat.get('avg_peak',np.nan),'实现收益占浮盈%':stat.get('retained_pct',np.nan),
                '退出时点复利回撤%':stat.dd,'5分钟收盘盯市回撤%':risk.get('marked_close_drawdown_pct',np.nan),
                '可配对事件':v.get('n_pairs',0),'同币随机均收%':v.get('paired_random',np.nan),
                '同组信号均收%':v.get('paired_actual',np.nan),'相对随机均收差_百分点':v.get('paired_edge',np.nan),
                '严格多重校正p':v.get('p_holm',np.nan),'满足全部检验':v.get('pass',False),
                '仅排除已发生冲击时配对数':past.loc[k,'n_pairs'] if k in past.index else 0,
                '仅排除已发生冲击时相对随机差_百分点':past.loc[k,'paired_edge'] if k in past.index else np.nan})
    df=pd.DataFrame(res)
    df.to_csv(out/'锁定参数与2026核验.csv',index=False,encoding='utf-8-sig')
    return df


def wave_comparison():
    out=search.OUT
    rules=pd.read_csv(study.OUT/'second_wave_requests/locked_rules.csv').set_index('asset')
    timeline=pd.read_csv(study.OUT/'second_wave_random_exit_timing.csv').set_index('asset')
    funding=pd.read_csv(study.OUT/'second_wave_funding.csv').set_index('asset')
    chosen=pd.read_csv(study.OUT/'second_wave_chosen.csv').set_index('asset')
    strict=pd.read_csv(out/'controls/second_wave/second_wave_validity.csv').set_index('asset')
    past=pd.read_csv(out/'controls/second_wave/past_only/second_wave_validity.csv').set_index('asset')
    rows=[]
    for asset,r in rules.iterrows():
        t=timeline.loc[asset];f=funding.loc[asset];c=chosen.loc[asset]
        rows.append({'标的':asset,'初始窗口分钟':r.minutes,'初始触轨范围':count_label(r.breadth),'观察分钟':r.wait_min,
            '入场前新增币条件':FOLLOW[r.diffusion],'入场后检查小时':r.checkpoints,
            '退出判定':'入场后不同新增下轨币数达到初始币数，在检查时点退出，否则持六天',
            '2026独立笔数':t.n,'提前退出次数':t.early,'扣费胜率%':c.win3,'扣费六天均收%':t.no_early_exit,
            '扣费二次扩散退出均收%':t.actual_net,'相对六天改善_百分点':c.edge3,
            '含资金费配对笔数':f.covered,'含资金费六天均收%':f.fixed_funded,'含资金费二次扩散均收%':f.wave_funded,
            '含资金费二次扩散胜率%':f.wave_win,'六天单币最大浮盈均值%':f.mean_member_peak,
            '同季度随机提前退出均收%':t.random_early_net,'随机退出双侧p':t.p_two_sided,
            '同币严格对照可配对数':strict.loc[asset,'n_pairs'],'同币严格对照差_百分点':strict.loc[asset,'paired_edge'],
            '仅排除已发生冲击时匹配数':past.loc[asset,'n_pairs'],'仅排除已发生冲击时相对随机差_百分点':past.loc[asset,'paired_edge'],
            '去最佳月扣费均收%':t.remove_best_month,'单次最差扣费收益%':t.worst_event,'满足严格检验':strict.loc[asset,'pass']})
    pd.DataFrame(rows).to_csv(out/'二次扩散退出对照.csv',index=False,encoding='utf-8-sig')


def point_targets():
    events=pd.read_parquet(study.OUT/'funded_case_check/locked_events.parquet')
    e=events[events.candidate_id.eq('case_runner_funded_BTC')].copy()
    e['入场北京时间']=e.decision.dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
    e['六天最高毛浮盈点数']=e.peak_pct*e.entry_open/100
    e['实际净赚至少一万点']=e.funded_points.ge(10000)
    e['实现对毛浮盈之比%']=np.where(e['六天最高毛浮盈点数']>0,100*e.funded_points/e['六天最高毛浮盈点数'],np.nan)
    names={'initial':'初始首触币数','new_coins':'观察期新增币数','entry_open':'BTC买入价','exit_vwap':'加权卖出价',
           'funding_points':'资金费点数','funded_points':'扣手续费和资金费后实现点数','funded_pct':'最终净收益%'}
    cols=['入场北京时间',*names.keys(),'六天最高毛浮盈点数','实现对毛浮盈之比%','实际净赚至少一万点']
    e.sort_values('decision')[cols].rename(columns=names).to_csv(search.OUT/'BTC一万点方案_全部53笔.csv',index=False,encoding='utf-8-sig')


if __name__=='__main__':
    compact()
    point_targets()
    wave_comparison()
