"""ETH-only causal feature groups, without new labels, entries or price replays.

Source: registered BTC/ETH 5m snapshots from the repeated-1.618 study. Every
feature ends at the closed submission bar before the touch. Trend efficiency
uses close differences over48/288 bars. Expansion compares last12 ranges/log
returns to preceding288 (shift12). Approach uses last12 candles, adjacent3-bar
returns and last48 high/low overlaps with the already known Monday extension.
BTC context uses synchronous5m log returns: trailing2016-bar beta/correlation
and48-bar cumulative returns. Candle volume imbalance is an OHLCV proxy,
not aggressive order-flow data. Missing warmup stays NaN, never backfilled.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_eth_repeat_data import read_frame, base_features, dump
from yoyo.evaluation.major_1618_v2_data import label_data

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-eth-1618-features-training-20261031-v1'
PRIOR = ROOT / 'experiments/active/exp-btc-eth-xau-1618-models-20261031-v2'


def config():
    return json.loads((EXP / 'config.json').read_text())


def read(name):
    return pd.read_csv(EXP / name, float_precision='round_trip')


def feature_series(eth, btc):
    """Closed OHLCV only; windows and shifts are documented in module docstring."""
    assert eth.index.equals(btc.index), 'BTC/ETH close clocks must match exactly'
    c = eth.close
    change = c.diff()
    returns = np.log(c).diff()
    br = np.log(btc.close).diff()
    atr = base_features(eth).atr
    tr = pd.concat([eth.high-eth.low, (eth.high-c.shift()).abs(),
                    (eth.low-c.shift()).abs()], axis=1).max(axis=1)
    out = pd.DataFrame(index=eth.index)
    for n, name in [(48, '4h'), (288, '24h')]:
        out[f'g_efficiency_{name}'] = (c-c.shift(n)).abs()/change.abs().rolling(n).sum().replace(0, np.nan)
    out['g_range_expansion'] = tr.rolling(12).mean()/tr.shift(12).rolling(288).mean().replace(0, np.nan)
    out['g_vol_expansion'] = returns.rolling(12).std()/returns.shift(12).rolling(288).std().replace(0, np.nan)
    out['approach_efficiency'] = (c-c.shift(12))/change.abs().rolling(12).sum().replace(0, np.nan)
    out['approach_acceleration'] = (c-2*c.shift(3)+c.shift(6))/atr
    out['candle_volume_imbalance'] = ((np.sign(c-eth.open)*eth.volume).rolling(12).sum()
                                     /eth.volume.rolling(12).sum().replace(0, np.nan))
    beta = returns.rolling(2016).cov(br)/br.rolling(2016).var().replace(0, np.nan)
    out['g_beta_7d'] = beta.clip(-5, 5)
    out['g_correlation_7d'] = returns.rolling(2016).corr(br)
    out['btc_return_4h'] = br.rolling(48).sum()*100
    out['eth_residual_4h'] = (returns.rolling(48).sum()-out.g_beta_7d*br.rolling(48).sum())*100
    return out.replace([np.inf, -np.inf], np.nan)


def event_features(events, eth, btc):
    """Use event submission_i, side and fixed entry; no outcome columns read."""
    base = feature_series(eth, btc)
    indices = events.submission_i.astype(int).to_numpy()
    assert (indices >= 0).all() and (indices < len(eth)).all()
    assert (pd.to_datetime(events.submission_time, utc=True).to_numpy()
            == eth.index[indices].to_numpy()).all()
    out = base.iloc[indices].copy()
    out.index = events.index
    side = events.side.to_numpy()
    for dest, src in [('g_approach_efficiency_1h', 'approach_efficiency'),
                      ('g_approach_acceleration_15m', 'approach_acceleration'),
                      ('g_candle_volume_imbalance_1h', 'candle_volume_imbalance')]:
        out[dest] = -side*out[src]
    out['g_btc_return_4h'] = side*out.btc_return_4h
    out['g_eth_residual_4h'] = side*out.eth_residual_4h
    occupancies = []
    for i, entry in zip(indices, events.entry):
        window = eth.iloc[max(0, i-47):i+1]
        occupancies.append(float(((window.low <= entry) & (window.high >= entry)).mean()))
    out['g_level_occupancy_4h'] = occupancies
    names = sum(config()['feature_groups'].values(), [])
    return out[names]


def prepare():
    raw = label_data(include_xau=False)
    d = raw[raw.symbol.eq('ETH')].copy()
    eth, btc = read_frame('ETH'), read_frame('BTC')
    events = d.drop_duplicates('event_id').copy()
    features = event_features(events, eth, btc)
    features['event_id'] = events.event_id
    d = d.merge(features, on='event_id', validate='many_to_one')
    assert len(d) == len(raw[raw.symbol.eq('ETH')])
    d.to_csv(EXP / 'data.csv.gz', index=False)
    receipt = dict(rows=len(d), events=int(d.event_id.nunique()),
                   complete_events=int(d[d.complete].event_id.nunique()),
                   weeks=int(d.week.nunique()), source=str(PRIOR),
                   features=sum(config()['feature_groups'].values(), []),
                   missing_by_feature=features.isna().sum().astype(int).to_dict(),
                   baseline_equal_week_weight_already_present=True,
                   snapshot_first=str(eth.index[0]), snapshot_last=str(eth.index[-1]),
                   new_downloads=0, new_native_positions=0,
                   builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())
    dump(EXP / 'data_receipt.json', receipt)
    print(receipt, flush=True)


if __name__ == '__main__':
    prepare()
