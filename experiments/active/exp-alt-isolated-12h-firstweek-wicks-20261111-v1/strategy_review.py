"""Reference-photo coverage and touch-matched controls, chosen without trade outcomes."""
import numpy as np
import pandas as pd

from all_contract_touches import DEST
from strategy_signals import CUTOFF, HOUR, OUT, read_raw

RULES = ['first_wick','retest','strict_union','owner_style']
KEY = ['symbol','side','entry_ms']


def add_rules(signals):
    a = signals.copy()
    a['strict_union'] = a.first_wick.eq(1) | a.retest.eq(1)
    a['owner_style'] = (a.rank_12h.eq(1) & a.body_inside.eq(1)) | a.retest.eq(1)
    return a


def photo_coverage(signals):
    photos = pd.read_csv(DEST/'owner_reference/原图15张_OKX与币安逐张对照.csv')
    rows = []
    for _,ref in photos.iterrows():
        s,m = ref['合约'],ref['区间月（纽约）']
        side = 'long' if ref['侧']=='下' else 'short'
        elapsed = ref['币安_轨价起算首次反向20%的小时数']
        row = dict(symbol=s,month=m,side=side)
        if pd.notna(elapsed):
            start = pd.Timestamp(ref['币安_首次触轨小时（北京）'],tz='Asia/Shanghai').value//10**6
            # Only entries available at or before the OPEN of the first H1 bar reaching the target.
            end = start+int(elapsed)*HOUR
            valid = signals[(signals.symbol==s)&(signals.month==m)&(signals.side==side)&
                            signals.entry_ms.between(start,end)]
            for rule in RULES:
                selected = valid[valid[rule].eq(1)]
                row[rule] = bool(len(selected))
                if len(selected):
                    row[rule+'_entry_beijing'] = pd.to_datetime(selected.entry_ms.min(),unit='ms',utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')
        rows.append(row)
    return pd.DataFrame(rows)


def matched_pairs(signals):
    # Ordinary first/second contacts of the same symbol, side and UTC year.
    # No trade contributes more than once within its rule's paired comparison.
    a = signals[signals.rank_12h<=2].copy()
    a['year'] = pd.to_datetime(a.entry_ms,unit='ms',utc=True).dt.year
    pairs = []
    for rule in RULES:
        rng = np.random.default_rng(20261111)
        for _,g in a.groupby(['symbol','side','year','rank_12h'],sort=True):
            is_case = g[rule].eq(1)
            cases,controls = g.index[is_case].to_numpy(),g.index[~is_case].to_numpy()
            n = min(len(cases),len(controls))
            if n:
                selected = rng.choice(cases,n,replace=False)
                matched = rng.choice(controls,n,replace=False)
                pairs.extend((rule,i,j) for i,j in zip(selected,matched))
    p = pd.DataFrame(pairs,columns=['rule','case_i','control_i'])
    return (p.join(signals[KEY].add_prefix('case_'),on='case_i')
             .join(signals[KEY].add_prefix('control_'),on='control_i')
             .drop(columns=['case_i','control_i']))


def price_reference_comparison(signals):
    """Label the same mature post-entry path with two different 20% reference prices.

    Both use the next 1080 H1 highs/lows, starting at the actual entry open.
    These are uncensored price excursions, not fills or strategy returns.
    """
    samples = []
    for symbol,g in signals.groupby('symbol'):
        q = read_raw(symbol)
        q = q[q[:,0]+HOUR<=CUTOFF]
        t = q[:,0]
        for r in g.itertuples():
            i = t.searchsorted(r.entry_ms)
            j = i+45*24-1
            if j>=len(t) or t[i]!=r.entry_ms or t[j]-t[i]!=(j-i)*HOUR:
                continue
            if r.side=='long':
                edge = q[i:j+1,2].max()
                from_rail,from_entry = edge>=r.rail*1.2,edge>=r.entry_price*1.2
            else:
                edge = q[i:j+1,3].min()
                from_rail,from_entry = edge<=r.rail*.8,edge<=r.entry_price*.8
            samples.append((r.Index,from_rail,from_entry))
    cases = pd.DataFrame(samples,columns=['row','rail20','entry20']).set_index('row')
    rows = []
    for name in ['baseline',*RULES]:
        obs = cases[signals.loc[cases.index,name].eq(1)]
        rows.append(dict(rule=name,complete_45d=len(obs),rail20=int(obs.rail20.sum()),
                         entry20=int(obs.entry20.sum()),only_rail20=int((obs.rail20&~obs.entry20).sum())))
    return pd.DataFrame(rows)


def main():
    a = add_rules(pd.read_csv(OUT/'signals.csv.gz'))
    photos = photo_coverage(a)
    photos.to_csv(OUT/'owner_photo_capture.csv',index=False)
    matched = matched_pairs(a)
    matched.to_csv(OUT/'matched_pairs.csv.gz',index=False)
    price_reference_comparison(a).to_csv(OUT/'target_reference.csv',index=False)
    print('source',a[RULES].sum().to_dict(),'reference',photos[RULES].sum().to_dict(),
          'pairs',matched.groupby('rule').size().to_dict())


if __name__=='__main__':
    main()
