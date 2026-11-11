"""One first watch per contract/month/side, retaining later touches in the same price path.

Select comparison examples on the second reclaim's prefix only. Peer cohorts are
defined before the next bar: same side, adjacent or separated at the same number
of bars, no interim close outside. For each reference choose the closest OTHER
contract by equal mean absolute percentile-rank distances across the observable
features. Peers may succeed or fail. No future information selects the peers.
"""
from pathlib import Path
import sys
import pandas as pd
import numpy as np

from replay import BAR,HOUR,OUT,HERE,KEY,SOURCE,DEST,ROUNDTRIP,save
from census12 import OUT as OLD
from strategy_signals import OUT as SIGNALS

REFERENCE=[('1000PEPEUSDT','2025-06','long'),('XAGUSDT','2026-05','short'),
           ('1000SHIBUSDT','2021-12','long'),('FARTCOINUSDT','2026-09','short')]
TKEY=KEY+['touch_ms']
GROUP=['symbol','month','side']
TRANSLATE={'下轨':'long','上轨':'short','下轨做多':'long','上轨做空':'short','lower':'long','upper':'short'}


def local_ms(a):
    return pd.to_datetime(a).dt.tz_localize('Asia/Shanghai').astype('datetime64[ns, UTC]').astype('int64')//1_000_000


def sources(rows):
    event_index=pd.MultiIndex.from_frame(rows[TKEY])
    group_index=pd.MultiIndex.from_frame(rows[GROUP])
    counts=[]
    def include(label, frame, group=False):
        cols=GROUP if group else TKEY
        keys=pd.MultiIndex.from_frame(frame[cols])
        idx=group_index if group else event_index
        misses=~keys.isin(idx)
        if any(misses): raise ValueError((label,frame.loc[misses,cols].head()))
        rows[label]=idx.isin(keys)
        counts.append(dict(分组=label,原始数量=len(frame),独立键=len(keys.unique()),单位='合约月方向' if group else '触轨日',
                           已对应=int((~misses).sum())))
    paths=[
      ('长针205','12小时长影线_局部单针_穿轨不足5pct_再碰前反转至少20pct.csv','触轨小时（北京时间）'),
      ('形态509','12h单针与二次试线_去重复盘样本.csv','触轨小时（北京）'),
      ('双试49','首根长影线_两根碰轨后先反转20pct.csv','首触小时（北京）'),
      ('首触2033','首触确认机会_45天内20pct先于5pct止损.csv','触价小时（北京）'),
      ('首触达标6912','原口径6912首触达20pct组合交叉核对.csv','触价小时（北京）'),
      ('首针与二试候选','12h首针收回与二次试线_入场候选.csv','触轨小时（北京）'),
      ('历史反转组7210','45天反转20pct_按合约月份方向合并.csv',None),
      ('原单针全形态','isolated_12h.csv','touch_hour_Beijing'),
      ('原单针七日20','reversals_20pct.csv','touch_hour_Beijing')]
    for name,file,col in paths:
        src=pd.read_csv(OLD/file)
        key=src.iloc[:,0:2].copy()
        key.columns=['symbol','month']
        side='side' if 'side' in src else '方向' if '方向' in src else '触价轨道（下轨做多／上轨做空）'
        key['side']=src[side].map(TRANSLATE)
        if col: key['touch_ms']=local_ms(src[col])
        include(name,key,group=col is None)
    for name,path in [('原12H信号',SIGNALS/'signals.csv.gz'),('原1H信号',SIGNALS/'confirm_1h/signals.csv.gz')]:
        include(name,pd.read_csv(path,usecols=TKEY))
    counts=pd.DataFrame(counts)
    counts.to_csv(HERE/'source_counts.csv',index=False,encoding='utf-8-sig')
    return counts.分组.tolist()


def peers():
    tape=pd.read_csv(OUT/'bar_tape.csv.gz',**ROUNDTRIP).sort_values(GROUP+['time_ms'])
    keys=[]
    for item, bars in tape.groupby(GROUP,sort=False):
        touches=np.flatnonzero(bars.touched_active)
        if len(touches)<2 or touches[0]!=0: continue
        first,second=touches[:2]
        prefix=bars.iloc[:second+1]
        if not (prefix.close_pct>0).all(): continue
        a,b=prefix.iloc[0],prefix.iloc[-1]
        keys.append(dict(zip(GROUP,item),time_ms=b.time_ms,gap=int(second-first),
                   first_close=a.close_pct,second_close=b.close_pct,second_wick=b.wick_range_pct,
                   second_tip=b.low_pct,extreme=max(a.high_pct,b.high_pct),h1_streak=b.ending_inside_streak))
    data=pd.DataFrame(keys).set_index(GROUP)
    save(data.reset_index(),'two_reclaims_at_second_close')
    observed=['first_close','second_close','second_wick','second_tip','extreme','h1_streak']
    picks=[]
    for ref in REFERENCE:
        example=data.loc[ref]
        pool=data[(data.index.get_level_values('side')==ref[2])&data.gap.eq(example.gap)].copy()
        ranks=pool[observed].rank(pct=True)
        delta=ranks.sub(ranks.loc[ref]).abs().mean(axis=1)
        check=delta[delta.index.get_level_values('symbol')!=ref[0]]
        other=check.sort_values(kind='stable').index[0]
        choices=[('参照',ref),('相似对照',other)]
        if other[1]>=ref[1]:
            earlier=check[check.index.get_level_values('month')<ref[1]]
            if len(earlier):
                choices.append(('更早月份对照',earlier.sort_values(kind='stable').index[0]))
        for kind,key in choices:
            picks.append(dict(example=ref[0],role=kind,symbol=key[0],month=key[1],side=key[2],
                              **data.loc[key].to_dict()))
    pd.DataFrame(picks).to_csv(HERE/'peer_decisions.csv',index=False,encoding='utf-8-sig')


def label_group(event,bars):
    if pd.isna(event.first_confirmation_ms):
        return event.path_type
    if event.path_type=='失守后重新收回并确认':
        return '曾收在线外，另一次收回后确认'
    until=bars[bars.time_ms<=event.first_confirmation_ms]
    positions=np.flatnonzero(until.touched_active)
    if len(positions)<=1: return '首触后直接确认'
    if len(positions)==2: return '连续两根试线后确认' if np.diff(positions)[0]==1 else '隔根二试后确认'
    return '至少三根试线后确认'


def next_two_closes(row, bars):
    future=bars[bars.time_ms>row.time_ms]
    window=future[future.time_ms<=row.time_ms+2*BAR]
    hit=window[(window.close_pct>row.extreme)|(window.close_pct<0)].head(1)
    if len(hit): return '失守' if hit.iloc[0].close_pct<0 else '推进'
    return '拉锯' if len(window)==2 else '不足24H'


def first_resolution(row,bars):
    """Observe the first later close through the original rail or the two touching bars' favorable extreme."""
    for b in bars.itertuples():
        if b.time_ms<=row.time_ms: continue
        if b.close_pct<0 or b.close_pct>row.extreme:
            state='失守' if b.close_pct<0 else '推进'
            return state,(b.time_ms-row.time_ms)/HOUR,b.close_pct
    return '观察结束仍未决',None,None


def summarize():
    r=pd.read_csv(OUT/'reviewed.csv.gz',**ROUNDTRIP).merge(pd.read_csv(OUT/'45day_path.csv.gz',**ROUNDTRIP),
                                                         on='id',validate='one_to_one')
    tags=sources(r)
    save(r,'reviewed_with_sources')
    first=r.sort_values('touch_ms').drop_duplicates(GROUP).copy()
    assert len(first)==12564
    tape=pd.read_csv(OUT/'bar_tape.csv.gz',**ROUNDTRIP)
    lookup={k:g.sort_values('time_ms') for k,g in tape.groupby(GROUP,sort=False)}
    candidates=pd.read_csv(OUT/'two_reclaims_at_second_close.csv.gz',**ROUNDTRIP)
    candidates['二试类型']=np.where(candidates.gap==1,'连续两根','两根之间有间隔')
    candidates['其后24小时']=[next_two_closes(x,lookup[(x.symbol,x.month,x.side)]) for x in candidates.itertuples()]
    paths=[first_resolution(x,lookup[(x.symbol,x.month,x.side)]) for x in candidates.itertuples()]
    candidates[['当月后续','二试后首个结果小时数','判定收盘距轨%']]=pd.DataFrame(paths,index=candidates.index)
    save(candidates,'two_reclaims_followup')
    pd.crosstab(candidates['二试类型'],candidates['其后24小时']).to_csv(HERE/'two_reclaims_counts.csv',encoding='utf-8-sig')
    stalled=candidates[candidates['其后24小时']=='拉锯']
    pd.crosstab(stalled['二试类型'],stalled['当月后续']).to_csv(HERE/'sideways_followup_counts.csv',encoding='utf-8-sig')
    first['shape']=[label_group(x,lookup.get((x.symbol,x.month,x.side))) for x in first.itertuples()]
    n=r.groupby(GROUP).size()
    first['touch_days']=n.reindex(pd.MultiIndex.from_frame(first[GROUP])).values
    first['observed20']=first.first_extreme20_hours.notna()
    first['decisive45']=first.observed20|first.observed_hours_45.eq(1080)
    first['confirm_before20hour']=(first.first_confirmation_ms<=first.first_extreme20_hour_open_ms)
    first['confirmed_before_first_loss']=(first.first_confirmation_ms.notna()&
                                          (first.first_outside_ms.isna()|(first.first_confirmation_ms<first.first_outside_ms)))
    save(first,'group_first_watch')
    summary=first.groupby('shape').agg(组数=('id','size'),已观测到触轨后20=('observed20','sum'),
        路径足以判断45天20=('decisive45','sum'),确认早于20所在小时=('confirm_before20hour','sum'),
        触线到确认小时中位数=('hours_to_confirmation','median'),确认时距轨百分比中位数=('confirmation_close_pct','median'))
    summary.to_csv(HERE/'shape_counts.csv',encoding='utf-8-sig')
    cols={'symbol':'合约','month':'纽约区间月','side':'轨道','underlying':'标的类别','beijing_touch':'首次观察小时_北京',
          'rail':'1.618价格','touch_days':'同轨触价日数','shape':'首轮走势','first_close_state':'首根12H表现',
          'first_reclaim_ms':'首次收回_北京','first_confirmation_ms':'首次确认_北京','confirmation_close_pct':'确认时距轨%',
          'first_outside_ms':'首次收在线外_北京','best_h1_close_pct':'原轨有效期内最好1H收盘距轨%',
          'first_extreme20_hours':'首次触轨小时结束后第几小时触及20%','peak_after_touch_pct':'45天已观察盘中最大有利幅度%',
          'worst_before_20_hour_pct':'达20所在小时前最深不利幅度%','observed_hours_45':'已观察小时数',
          'path_end':'原轨观察终点','历史反转组7210':'原7210组选中'}
    exp=first[list(cols)].copy()
    for name in ('first_reclaim_ms','first_confirmation_ms','first_outside_ms'):
        exp[name]=pd.to_datetime(exp[name],unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
    exp.side=exp.side.map({'long':'下轨','short':'上轨'})
    exp.rename(columns=cols).sort_values(['合约','纽约区间月','轨道']).to_csv(OUT/'按合约月方向复盘.csv',index=False,
                                                                           encoding='utf-8-sig',float_format='%.9g')
    pp=pd.read_csv(HERE/'peer_decisions.csv',**ROUNDTRIP)
    for i,row in pp.iterrows():
        bars=lookup[(row.symbol,row.month,row.side)]
        future=bars[bars.time_ms>row.time_ms].sort_values('time_ms')
        pp.loc[i,'next_24h']=next_two_closes(row,bars)
        if len(future):
            pp.loc[i,'best_later_12h_close_pct']=future.close_pct.max()
            pp.loc[i,'next_12h_close_pct']=future.iloc[0].close_pct
            loss=future[future.close_pct<0].head(1)
            pp.loc[i,'hours_to_loss']=(loss.iloc[0].time_ms-row.time_ms)/HOUR if len(loss) else None
        pp.loc[i,'closes_to_expiry']=len(future)
    pp.to_csv(HERE/'peer_followup.csv',index=False,encoding='utf-8-sig')
    print(summary.to_string())


if __name__=='__main__':
    if sys.argv[1]=='peers': peers()
    elif sys.argv[1]=='summarize': summarize()
    else: raise ValueError(sys.argv[1])
