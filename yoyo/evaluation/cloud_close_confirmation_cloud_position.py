"""Test only the confirmed price's position relative to its original V1.7 cloud on frozen wick entries."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as original

EXP = original.ROOT / 'exp-cloud-close-confirmation-cloud-position-20261109-v1'
AUDIT = original.ROOT / 'exp-cloud-close-breakout-historical-20261109-v1'
KEY = ['symbol', 'side', 'entry_ms']


def panel(parents, quotes):
    """Use the original sealed 12H close, EMA20 and EMA50; retired names use their last completed native hour."""
    saved = pd.read_csv(AUDIT/'candidate_audit_full_360.csv.gz', dtype={'symbol':str},
                        usecols=KEY+['arm','is_control','ema20_confirmed','ema50_confirmed','stop_price'])
    saved = saved.loc[saved.arm.eq('reclaim') & ~saved.is_control].set_index(KEY, verify_integrity=True)
    src = pd.read_csv(original.queue.repair.wallet.SOURCE/'signals.csv', dtype={'symbol':str},
                      usecols=['symbol','side','confirmed_bjt','confirmation_close','ema20','ema50'])
    ms = pd.to_datetime(src.confirmed_bjt, utc=True).astype('datetime64[ms, UTC]').astype('int64')
    sealed = src.assign(entry_ms=ms).set_index(KEY, verify_integrity=True)
    rows = []
    for eid,p in parents.iterrows():
        key = p.symbol, int(p.side), int(p.entry_ms)
        r = saved.loc[key]
        if not np.isclose(r.stop_price, p.stop_price, rtol=1e-9, atol=1e-12):
            raise ValueError('Original signal stop mismatch')
        ts, px = quotes(p.symbol)
        t = p.entry_ms-original.H
        i = int(ts.searchsorted(t))
        if i == len(ts) or ts[i] != t:
            raise ValueError('Missing last native hour of the key')
        native = px[i,3]
        if key in sealed.index:
            b = sealed.loc[key]
            close, fast, slow = b.confirmation_close, b.ema20, b.ema50
            basis = 'frozen_signal'
        else:
            close, fast, slow = native, r.ema20_confirmed, r.ema50_confirmed
            basis = 'archived_reclaim'
        if not np.isfinite([close,fast,slow]).all() or (fast-slow)*p.side <= 0:
            raise ValueError('Missing or oppositely directed original cloud')
        diff = np.array([close-fast, close-slow])*p.side
        category = 'beyond_both' if min(diff)>0 else 'opposite_both' if max(diff)<0 else 'inside'
        rows.append(dict(event_id=eid, symbol=p.symbol, side=p.side, entry_ms=p.entry_ms,
                         close=close, ema20=fast, ema50=slow, source=basis, last_h1_close=native,
                         stored_vs_audit_ema_match=np.allclose([fast,slow],[r.ema20_confirmed,r.ema50_confirmed],rtol=1e-8),
                         cloud_position=category))
    return pd.DataFrame(rows).set_index('event_id', verify_integrity=True)


def attribution(features, funds):
    d = funds.join(features.cloud_position, validate='one_to_one')
    if d.cloud_position.isna().any():
        raise ValueError('Funded trade without pre-entry features')
    periods = {}
    split = pd.Timestamp('2025-01-01', tz='UTC').value//10**6
    for name,part in [('all', d), ('through_2024', d[d.exit_bar_ms < split]), ('since_2025', d[d.entry_ms >= split])]:
        top = part.nlargest(10,'cash_pnl').index
        unit = part.nlargest(int(np.ceil(len(part)/10)), 'net_return').index
        periods[name] = {}
        for cat,g in part.groupby('cloud_position'):
            periods[name][cat] = dict(n=len(g), wins=int(g.cash_pnl.gt(0).sum()),
                top_10_cash=len(g.index.intersection(top)), top_decile_unit=len(g.index.intersection(unit)),
                realized_usdt=g.cash_pnl.sum(), mean_net_return_pct=100*g.net_return.mean(),
                tail_rate_pct=100*len(g.index.intersection(unit))/len(g))
    return periods


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed original cloud location study')
    data, market, quotes, parents, caps, multiples = original.account()
    features = panel(parents, quotes)
    features.to_csv(EXP/'known_at_confirmation.csv.gz', index_label='event_id')
    base = pd.read_csv(original.SOURCE/'aligned/funded_median.csv.gz', index_col='event_id')
    cases = {}
    for key,keep in [('beyond',features.cloud_position.eq('beyond_both')),
                     ('complement',features.cloud_position.ne('beyond_both'))]:
        for slip in (0., .0005):
            name = key+('_stress' if slip else '')
            result = original.queue.engine.simulate(data,market,quotes,multiples,slip,EXP,name,
                                                   entry_limits=caps.where(keep,0.))
            if 'metrics' not in result:
                raise ValueError('Unresolved original cash account')
            cases[name] = result
            print(name, result['metrics']['all'], flush=True)
    original.queue.repair.archive.prices.safe_json(EXP/'summary.json', dict(
        reserved=len(parents), funded_baseline=len(base), sources=features.source.value_counts().to_dict(),
        hourly_close_disagreements=int((~np.isclose(features.close,features.last_h1_close,rtol=1e-8,atol=1e-12)).sum()),
        ema_disagreements=int((~features.stored_vs_audit_ema_match).sum()),
        attribution=attribution(features,base), arms=cases))


if __name__ == '__main__':
    main()
