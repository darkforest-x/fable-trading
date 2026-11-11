"""Summarize realized native or horizon-variant fills, with paired controls."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DEST = HERE.parents[2]/'data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1/h4_strategy'
CUT = int(pd.Timestamp('2025-01-01T00:00Z').timestamp()*1000)
MAIN = ['上轨守住','下轨重新收高']


def intervals(frame):
    return [('全期',frame),('2025年前',frame[frame.exit_ms<CUT]),('2025年起',frame[frame.entry_ms>=CUT])]


def bootstrap(delta, month):
    totals = pd.DataFrame({'delta':delta,'month':month}).groupby('month').delta.agg(['sum','size'])
    rng = np.random.default_rng(20261112)
    draw = rng.integers(0,len(totals),size=(3000,len(totals)))
    samples = totals['sum'].to_numpy()[draw].sum(axis=1)/totals['size'].to_numpy()[draw].sum(axis=1)
    return np.quantile(samples,[.025,.975])


def summarize(path, length):
    frame = pd.read_csv(path)
    closed = frame[frame.exit_reason!='cutoff_mark']
    results = []
    for period,subset in intervals(closed):
        for group in ['主策略',*MAIN,'下轨未重新收高']:
            cases = subset[subset.group.isin(MAIN) if group=='主策略' else subset.group.eq(group)]
            actual = cases[cases['sample']=='signal']
            reference = cases[cases['sample']=='random']
            pairs = actual.merge(reference,on='pair_id',validate='one_to_one',suffixes=('_s','_r'))
            delta = pairs.net_pct_s-pairs.net_pct_r
            lower,upper = bootstrap(delta,pairs.source_month_s)
            wins = actual.net_pct > 0
            loss = -actual.loc[~wins,'net_pct'].sum()
            results.append(dict(最长小时=length,时期=period,组=group,结清数=len(actual),
                胜率_pct=100*wins.mean(),单笔净收益均值_pct=actual.net_pct.mean(),
                盈利因子=actual.loc[wins,'net_pct'].sum()/loss,匹配对数=len(pairs),
                配对信号均值_pct=pairs.net_pct_s.mean(),配对随机均值_pct=pairs.net_pct_r.mean(),
                配对差_pp=delta.mean(),月抽样差下界_pp=lower,月抽样差上界_pp=upper,
                止损数=actual.exit_reason.eq('stop').sum(),止盈数=actual.exit_reason.eq('target').sum(),
                到期数=actual.exit_reason.eq('timeout').sum(),
                持仓期间最大浮盈中位数_pct=actual.mfe_pct.median(),
                持仓期间最大浮亏幅度中位数_pct=actual.mae_pct.median()))
    return pd.DataFrame(results)


if __name__=='__main__':
    length=int(sys.argv[1])
    inputs={168:'fills.csv',72:'fills_72h.csv'}
    summary=summarize(DEST/inputs[length],length)
    summary.to_csv(HERE/f'trade_summary_{length}h.csv',index=False,encoding='utf-8-sig')
    print(summary[summary.组=='主策略'].round(3).to_string(index=False))
