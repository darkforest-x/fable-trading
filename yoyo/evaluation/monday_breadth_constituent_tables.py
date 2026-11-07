"""Compact, bilingual-research CSV view of the 7 frozen breadth clocks."""
from pathlib import Path
import pandas as pd

ACTIVE = Path(__file__).resolve().parents[2]/'experiments/active'
OUT = ACTIVE/'exp-monday-breadth-constituent-six-day-20261108-v1'
MAJOR = ACTIVE/'exp-monday-breadth-six-day-excursions-20261108-v1'
BANDS = ['1','2-4','5-9','10-24','25-34','35-49','50-99','100-199','200+']


def main():
    m = pd.read_csv(MAJOR/'summary.csv',dtype={'band':str})
    m = m[(m.period=='all')&m.band.isin(BANDS)]
    s = pd.read_csv(OUT/'summary.csv',dtype={'band':str}).query("period=='all'").copy()
    keys = ['minutes','band','continued']
    d = s[keys+['signals','eligible_members','net_n','peak_n','win_pct','mean_net_pct','mean_peak_pct',
               'median_peak_pct','max_peak_pct','peak_ge10_pct','paired_n','matched_excess_pp']].copy()
    d.columns = keys+['信号K线数','初始山寨币次数','到期可评估笔数','峰值可评估笔数','山寨_到期胜率%',
                     '山寨_平均到期净收益%','山寨_平均最大浮盈%','山寨_最大浮盈中位数%','山寨_单笔最高浮盈%',
                     '山寨_最高浮盈达到10%占比','山寨_有同币对照笔数','山寨_相对匹配对照净收益差_百分点']
    for asset in ['BTC','ETH','SOL']:
        cols = ['n','win_pct','mean_net_pct','mean_peak_pct','median_peak_pct','max_peak_pct']
        z=m[m.asset.eq(asset)][keys+cols].copy()
        z.columns=keys+[asset+'_'+x for x in ['到期可评估笔数','到期胜率%','平均到期净收益%','平均最大浮盈%','最大浮盈中位数%','单笔最高浮盈%']]
        d=d.merge(z,on=keys,how='left',validate='one_to_one')
    d['band']=pd.Categorical(d.band,categories=BANDS,ordered=True)
    d=d.sort_values(keys); d['continued']=d.continued.map({True:'有',False:'无'})
    d.rename(columns={'minutes':'初始窗口_分钟','band':'初始触及币数档','continued':'随后30分钟有无新币'},inplace=True)
    d.to_csv(OUT/'七种周期_六天结果.csv',index=False,encoding='utf-8-sig',float_format='%.4f')


if __name__=='__main__':
    main()
