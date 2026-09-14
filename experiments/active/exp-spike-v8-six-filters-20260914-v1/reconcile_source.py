"""Reconcile V1 source claims from its frozen ledger, not a new strategy run."""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v8_six_filters import CATALOG, _catalog_map, stock_class

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / 'source_audit'
SHA = 'b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578'
SOURCE = ROOT / f'experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/immutable_replay_ledger/covered_trade_ledger.{SHA}.csv.gz'


def main():
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SHA
    f = pd.read_csv(SOURCE)
    censored = f.censored.astype(str).str.lower().map({'true': True, 'false': False})
    assert censored.notna().all()
    f = f.loc[~censored].copy()
    assert np.isfinite(f.net_r).all()
    f['entry_time'] = pd.to_datetime(f.entry_time, utc=True)
    f['exit_time'] = pd.to_datetime(f.exit_time, utc=True)
    catalog = _catalog_map()
    f['stock_class'] = [stock_class(dict(venue=r.venue, symbol=r.symbol, asset=r.asset), catalog)[0] for r in f.itertuples()]
    clock = f.entry_time
    china = clock.dt.tz_convert('Asia/Shanghai')
    masks = {
        'all_closed': pd.Series(True, index=f.index),
        'rv_gt50': f.volume_ratio > 50,
        'joint_rv_gt50_tratr_gt10': (f.volume_ratio > 50) & (f.tr_atr_expansion > 10),
        'risk_gt30pct': f.risk_fraction_at_entry > .30,
        'usdc_base': f.asset.eq('USDC'),
        'stock_linked_all': f.stock_class.eq('stock_linked'),
        'h00_utc': clock.dt.hour.eq(0),
        'sunday_utc': clock.dt.dayofweek.eq(6),
        'h00_asia_shanghai': china.dt.hour.eq(0),
        'sunday_asia_shanghai': china.dt.dayofweek.eq(6),
        'holding_lt4h_NONCAUSAL': (f.exit_time - f.entry_time).dt.total_seconds() < 14400,
    }
    rows = []
    for policy, mask in masks.items():
        for scope, include in [('source_with_daily', pd.Series(True, index=f.index)), ('30m_1h_4h', f.timeframe_min.isin([30,60,240]))]:
            g = f.loc[mask & include]
            rows.append(dict(policy=policy, scope=scope, rows=len(g), wins=int((g.net_r > 0).sum()),
                win_rate=float((g.net_r > 0).mean()) if len(g) else None,
                sum_net_r=float(g.net_r.sum()), static_removed_negative_r=-float(g.net_r.sum()),
                realized_ge10=int((g.net_r >= 10).sum()), stock_unknown=int(g.stock_class.eq('unknown').sum())))
    OUT.mkdir(exist_ok=False)
    pd.DataFrame(rows).to_csv(OUT/'source_claims.csv', index=False)
    f.loc[masks['stock_linked_all']].groupby(['venue','symbol','stock_class']).net_r.agg(['size','sum']).to_csv(OUT/'stock_attribution.csv')
    (OUT/'receipt.json').write_text(json.dumps(dict(source=str(SOURCE.relative_to(ROOT)), source_sha256=SHA,
        catalog=str(CATALOG), catalog_sha256=hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
        source_closed=len(f), source_read='saved outcome attribution only;not counterfactual filter profit',
        warning='Sunday/hour definitions fixed here do not resolve unspecified source clocks;Bonferroni p belongs to joint-zone source claim and has not been reproduced',
        outputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file()}),indent=2))
    print(pd.DataFrame(rows).query("scope=='source_with_daily'").to_string(index=False))


if __name__ == '__main__':
    main()
