"""Price failed rallies into the bearish V1.7 EMA cloud on a historical cross-section."""
import gc
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_breadth_branch as breadth

wallet = breadth.wallet
engine = breadth.archive.native.engine
EXP = wallet.ROOT/'experiments/active/exp-cloud-close-weak-rally-branch-20261109-v1'
CACHE = wallet.ROOT/'data/research/cloud_close_weak_rally_branch'
STEP = breadth.STEP


def sources():
    directory = pd.read_csv(wallet.SOURCE/'input_coverage.csv')
    out = {}
    for r in directory.itertuples():
        parent = wallet.TOP if r.cohort=='top20_reused' else wallet.SOURCE
        folder = parent/'inputs'/r.symbol
        out[r.symbol] = (folder/'bars1h.csv.gz',folder/'bars12h.csv.gz',wallet.END)
    cfg = json.loads((breadth.UNIVERSE/'config.json').read_text())
    for n in cfg['notices']:
        for s in n['symbols']:
            f = breadth.archive.prices.CACHE/'series'/(s+'-USDT-SWAP')/'bars1h.csv.gz'
            out[s] = (f,None,pd.Timestamp(n['delivery_from_utc']).value//1_000_000)
    if len(out)!=360:
        raise ValueError('Documented historical universe changed')
    return out


def frames(source):
    h,bar,end = source
    hourly = pd.read_csv(h,index_col='time',float_precision='round_trip').loc[:end-1,breadth.archive.FIELDS]
    bars = (pd.read_csv(bar,index_col='time',float_precision='round_trip')[breadth.archive.FIELDS]
            if bar else breadth.archive.native.twelve_hour(hourly))
    return hourly,bars.loc[:end-STEP]


def momentum(close):
    """30-day simple return from exactly 60 consecutive 12H close intervals up to signal close."""
    ok = close.index.to_series().diff(60).eq(60*STEP)
    return close.div(close.shift(60)).sub(1).where(ok)


def candidates(symbol,bars,btc,side):
    group = bars.index.to_series().diff().ne(STEP).cumsum()
    fast = bars.close.groupby(group).transform(lambda c:c.ewm(span=20,adjust=False,min_periods=50).mean())
    slow = bars.close.groupby(group).transform(lambda c:c.ewm(span=50,adjust=False,min_periods=50).mean())
    relative = momentum(bars.close).sub(btc.reindex(bars.index))
    aligned = (fast-slow).mul(side).gt(0)
    strong = relative.mul(side).gt(0)
    if side<0:
        retest = aligned&bars.high.ge(fast)&bars.close.lt(fast)
        breaks = bars.close.lt(bars.low.shift(1))
    else:
        retest = aligned&bars.low.le(fast)&bars.close.gt(fast)
        breaks = bars.close.gt(bars.high.shift(1))
    confirmed = retest.shift(1,fill_value=False)&aligned&strong&breaks&bars.index.to_series().diff().eq(STEP)
    opportunity = {}
    for key in bars.index[aligned&strong]:
        w = breadth.ny(key)
        if w['monday_end']<=key and key+STEP<=w['expires'] and wallet.START<=key+STEP<wallet.END:
            opportunity.setdefault(w['week'],[]).append(int(key))
    pairs = []
    for week,options in opportunity.items():
        actual = [t for t in options if confirmed.at[t] and t-STEP>=breadth.ny(t)['monday_end']]
        if not actual:
            continue
        key = actual[0]
        later = next((t for t in options if t>key and not confirmed.at[t]),None)
        stop = (max(bars.high.at[key],bars.high.at[key-STEP]) if side<0 else min(bars.low.at[key],bars.low.at[key-STEP]))
        pairs.append((week,key,float(stop),later))
    return pairs,relative


def path_book(symbol,source,btc,side):
    h,bars = frames(source)
    plans,relative = candidates(symbol,bars,btc,side)
    if not plans:
        return None
    q,clock,_ = engine._prepare_symbol(bars,h)
    parents = []
    ranks = {}
    for week,original,stop,control in plans:
        parent = f'{symbol}|{week}'
        risk_pct = None
        for key,is_control in [(original,False),(control,True)]:
            if key is None:
                continue
            stamp = key+STEP
            i = int(clock.searchsorted(stamp))
            if i==len(clock) or clock[i]!=stamp:
                break
            open_px = float(q[i,0])
            if not is_control:
                risk_pct = side*(1-stop/open_px)
                if risk_pct<=0:
                    break
            barrier = open_px*(1-side*risk_pct) if is_control else stop
            idx,reason = engine._safe_path_end(i,side,q,clock)
            event = parent+('|later' if is_control else '|confirmed')
            parents.append(dict(event_id=event,parent_id=parent,symbol=symbol,week=week,side=side,
                is_control=is_control,source_row_number=0,entry_i=i,entry_ms=stamp,confirmation_ms=stamp,
                key_open_ms=key,entry_price=open_px,stop_price=barrier,risk_distance=side*(open_px-barrier),
                risk_fraction=risk_pct,end_i=idx,path_upper_bound_reason=reason))
            ranks[event] = -side*relative.at[key]
    rows,_ = engine._simulate_symbol(parents,q,clock,(0.,),96)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return None
    if not frame.entry_status.eq('executed').all():
        raise ValueError(f'Invalid native short fills for {symbol}')
    frame['entry_order'] = frame.event_id.map(ranks)
    return frame


def main(*,long=False):
    directory = EXP.parent/'exp-cloud-close-strong-pullback-branch-20261109-v1' if long else EXP
    cache = CACHE.parent/'cloud_close_strong_pullback_branch' if long else CACHE
    side = 1 if long else -1
    if (directory/'summary.json').exists():
        raise FileExistsError('Completed weak-rally short study')
    src = sources()
    _,btc = frames(src['BTC'])
    ret = momentum(btc.close)
    cache.mkdir(parents=True,exist_ok=True)
    books = []
    for number,(s,f) in enumerate(sorted(src.items())):
        if s=='BTC':
            continue
        dest = cache/(s+'.csv.gz')
        absent = cache/(s+'.empty')
        if not dest.exists() and not absent.exists():
            x = path_book(s,f,ret,side)
            if x is None:
                absent.touch()
            else:
                tmp = dest.with_suffix('.tmp.gz')
                x.to_csv(tmp,index=False)
                tmp.replace(dest)
        if dest.exists():
            books.append(pd.read_csv(dest,dtype={'symbol':str},float_precision='round_trip'))
        if number%50==0:
            print('priced native markets',number,flush=True)
    book = pd.concat(books,ignore_index=True)
    book.to_csv(directory/'native_paths.csv.gz',index=False)
    orig = book[~book.is_control]
    ctrl = book[book.is_control]
    paired = orig[orig.parent_id.isin(ctrl.parent_id)]
    @lru_cache(maxsize=None)
    def quotes(symbol):
        f,_,end = src[symbol]
        h = pd.read_csv(f,usecols=['time',*breadth.archive.FIELDS],float_precision='round_trip')
        h = h[h.time.lt(end)]
        return h.time.to_numpy(),h[breadth.archive.FIELDS].to_numpy()

    out = {}
    for label,events in [('full',orig),('paired',paired),('later',ctrl)]:
        data = wallet.prepare(events,9,quote_loader=quotes)
        for slip in ([0.,.0005] if label=='full' else [0.]):
            name = 'full_5bp' if slip else label
            table,eq = wallet.account(data,slip=slip)
            if not np.isclose(1000+table.cash_pnl.sum(),eq.nav.iat[-1],atol=.005):
                raise ValueError('Native weak-rally wallet did not conserve cash')
            table.to_csv(directory/f'funded_{name}.csv.gz',index=False)
            if label=='full':
                eq.to_csv(directory/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
            warnings = table[table.exit_reason.isin(breadth.archive.GAP_REASONS)].copy()
            out[name] = dict(metrics={p:wallet.summarize(table,eq,*dates) for p,dates in breadth.pair.periods.PERIODS.items()},
                       data_exits=warnings.exit_reason.value_counts().to_dict(),unquoted_holding_hours=data['gap_marks'],
                       pre_boundary_tail_exit_count=int((warnings.exit_reason.eq('series_tail_close')&
                            warnings.exit_bar_ms.lt(wallet.END-wallet.HOUR)).sum()))
            print(name,out[name]['metrics']['all'],flush=True)
        del data
        gc.collect()
    (directory/'summary.json').write_text(json.dumps(dict(side=side,real_opportunities=len(orig),matched=len(paired),
        results=out,scope='360-name historical OKX source with original data-gap and end-of-series prices; incomplete funding and any relevant administrative settlement must be examined before interpreting a promotion.'),
        indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    import argparse
    cli = argparse.ArgumentParser()
    cli.add_argument('--long',action='store_true')
    main(long=cli.parse_args().long)
