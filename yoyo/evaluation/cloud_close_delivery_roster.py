"""Check funded and matched rosters against the archived trading lives of Binance contracts."""
import json
from collections import Counter

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_complete as study


def check_rosters(paths, rank_paths, dates):
    """Apply known end times only to admissions and slot release, without pricing forced exits."""
    counts = Counter()
    for symbol, path in rank_paths.items():
        with np.load(path) as quotes:
            clocks = quotes['rank_times']
            counts.update(map(int, clocks[clocks < dates.get(symbol, np.inf)]))
    events = paths[paths.tp_r.eq(0)].copy()
    horizon = (events.symbol + 'USDT').map(dates).fillna(np.inf)
    membership = events.entry_ms.map(counts).fillna(0)
    available = events.entry_ms.lt(horizon) & membership.ge(10)
    clean = events.loc[available].copy()
    horizon = horizon.loc[available]
    # The existing matcher releases a slot after the exit hour, including a
    # delivery exactly on an H1 boundary. Intrahour exits retain that hour.
    exit_hour = (horizon // study.archive.native.wallet.HOUR) * study.archive.native.wallet.HOUR
    crosses = clean.exit_bar_ms.ge(exit_hour)
    clean.loc[crosses, 'exit_bar_ms'] = exit_hour[crosses].astype('int64')
    real = clean[~clean.is_control]
    controls = clean[clean.is_control & clean.parent_id.isin(real.parent_id)]
    paired = real[real.parent_id.isin(controls.parent_id)]
    baseline = pd.read_csv(study.EXP / 'funded_tenth.csv.gz')
    evidence = dict(expired_real_entries=int((~available & ~events.is_control).sum()),
                    expired_control_entries=int((~available & events.is_control).sum()),
                    market_gate_losses=int(membership.lt(10).sum()))
    crosses_ids = set(clean.loc[crosses, 'event_id'])
    for name, pool in [('actual', real), ('matched_actual', paired), ('control', controls)]:
        funded = study.archive.native.wallet.admit(pool, 9)[0]
        needing_price = funded[funded.event_id.isin(crosses_ids)]
        evidence[name] = dict(funded=len(funded),
                              possible_delivery_event_ids=needing_price.event_id.tolist())
        if name == 'actual':
            evidence[name]['same_parent_ids_as_frozen'] = set(funded.event_id) == set(baseline.event_id)
            if len(needing_price) or not evidence[name]['same_parent_ids_as_frozen']:
                raise ValueError('Frozen actual wallet requires repricing on dated deliverable contracts')
    return evidence


def main():
    out = study.EXP / 'delivery_rosters.json'
    if out.exists():
        raise FileExistsError(out)
    original = pd.read_csv(study.EXP / 'original_contract_classification.csv').set_index('symbol')
    end = original.archive_deliveryDate_ms.copy()
    same = original.current_onboardDate_ms.eq(original.archive_onboardDate_ms)
    end.loc[same] = original.loc[same, ['archive_deliveryDate_ms', 'current_deliveryDate_ms']].min(axis=1)
    dates = end.to_dict()
    extra = set(json.loads((study.archive.EXP / 'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((study.EXP / 'summary.json').read_text())['new_archive_cohorts']['priced'])
    paths = study.archive.pool(extra)
    repaired = set(study.archive.native.patch.affected_tickers())
    rank_paths = {}
    for ticker in set(original.index) | extra:
        base = (study.archive.CACHE / 'symbols' if ticker in extra else
                study.archive.native.WORK if ticker in repaired else study.archive.venue.WORK)
        rank_paths[ticker] = base / ticker / 'quotes.npz'
    exact = check_rosters(paths, rank_paths, dates)
    evidence = pd.read_csv(study.EXP / 'contract_lifetime_evidence.csv.gz', low_memory=False)
    olds = evidence[evidence.row_type.eq('extra29_archive_audit')].set_index('symbol')
    if len(olds) != 29 or olds.last_positive_volume_15m_close_utc.isna().any():
        raise ValueError('Missing old-archive last-trade bounds')
    last_trade = pd.to_datetime(olds.last_positive_volume_15m_close_utc, utc=True).astype('datetime64[ms, UTC]')
    sensitivity = check_rosters(paths, rank_paths, {**dates, **last_trade.astype('int64').to_dict()})
    result = dict(exchange_dated_contracts=exact,
                  also_truncate_29_at_end_of_last_positive_volume_bar=sensitivity,
                  scope='This is an admission/slot and custody-clock check. Last traded-bar close on the 29 older '
                        'contracts is a sensitivity proxy. Control and matched returns are pending where an actual '
                        'delivery price or event order is needed; no synthetic settlement fills.')
    out.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
