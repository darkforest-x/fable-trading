"""Package strict-wick and body-reclaim subsets without rendering again.

Owner requested every matching image. Keep all images, and make the stricter
pure-wick set independently accessible; no future outcome is used to choose it.
"""
import json
from zipfile import ZipFile,ZIP_STORED
import pandas as pd
from yoyo.evaluation.top20_12h_reclaims import EXP,dump
from yoyo.evaluation.top20_12h_reclaims_charts import OUT,KINDS


def run():
    delivery=json.loads((OUT/'delivery.json').read_text());assert 'kind_archives' not in delivery
    index=pd.read_csv(OUT/'index.csv',float_precision='round_trip')
    chinese=pd.DataFrame({'币种':index.symbol,'K线开盘时间_北京':index.signal_open,'确认收盘时间_北京':index.signal_close,
        '形态':index.kind.map(KINDS),'方向':index.side.map({1:'下刺收回',-1:'上刺收回'}),'1.618价位':index.level,
        '开盘':index.open,'最高':index.high,'最低':index.low,'收盘':index.close,
        '1天反向变化_百分比':index.return_pct_2,'3天反向变化_百分比':index.return_pct_6,'7天反向变化_百分比':index.return_pct_14,
        '3天内再收在线外':index.level_lost_6,'3天内再破针尖':index.wick_broken_6,
        '旧挂牌历史':index.before_current_listTime,'图片':index.relative_path})
    chinese.to_csv(OUT/'中文索引.csv',index=False,encoding='utf-8-sig');archives=[]
    for kind,g in index.groupby('kind'):
        path=OUT/f'{KINDS[kind]}_{len(g)}张.zip'
        with ZipFile(path,'w',ZIP_STORED) as z:
            for r in g.itertuples():z.write(OUT/r.relative_path,r.relative_path)
            z.writestr('中文索引.csv',chinese.loc[g.index].to_csv(index=False).encode('utf-8-sig'))
            for name in ['config.json','summary.json','control_diagnostics.json','same_cohort_control_check.json']:
                z.write(EXP/name,name)
        with ZipFile(path) as z:
            assert z.testzip() is None
            assert sum(n.endswith('.png') for n in z.namelist())==len(g)
        archives.append(dict(kind=kind,charts=len(g),path=str(path),bytes=path.stat().st_size))
    receipt=json.loads((EXP/'input_receipt.json').read_text())
    from pathlib import Path
    for source in receipt['sources']:
        stat=Path(source['source']).stat();assert(stat.st_size,stat.st_mtime_ns)==(source['bytes'],source['mtime_ns'])
    pure=index[index.kind.eq('pure_wick')&index.return_pct_6.notna()]
    extreme=pure.low.where(pure.side.eq(1),pure.high)
    within=pure.side*(pure.close-extreme)/pure.close*100
    delivery.update(kind_archives=archives,chinese_index=str(OUT/'中文索引.csv'),all_source_stats_unchanged=True,
        price_time_boundary=dict(events=len(pure),median_signal_extreme_to_close_pct_of_close=float(within.median()),
            median_subsequent_72h_directional_pct=float(pure.return_pct_6.median()),
            meaning='Distances normalized by signal close;the inside-signal-bar move is already over at confirmation and is never added to subsequent price changes.'))
    dump(OUT/'delivery.json',delivery)
    print(json.dumps(archives,ensure_ascii=False),flush=True)


if __name__=='__main__':run()
