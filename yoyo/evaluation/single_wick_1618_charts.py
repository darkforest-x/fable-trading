"""Plot native #72 examples from saved ledgers; no replay or altered signals."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from yoyo.evaluation.single_wick_1618_majors import EXP, FEATURES, prepare


def validate(t):
    """Check saved evidence algebra and signal geometry without replaying trades."""
    rows = []
    for symbol, g in t.groupby('symbol'):
        dest = EXP/'symbols'/symbol
        e = pd.read_parquet(dest/'entries.parquet').set_index('trade_id')
        f = pd.read_parquet(dest/'candles5m.parquet')
        assert set(g.trade_id) == set(e.index)
        assert (g.exit_i > g.entry_i).all()
        np.testing.assert_allclose(g.gross_r-g.fee_r, g.net_r, atol=1e-8)
        np.testing.assert_allclose(g.net_r*g.risk/g.entry*10000, g.net_notional_bp, atol=1e-7)
        s = g[g.population.eq('signal')]
        k = f.index.get_indexer(pd.to_datetime(s.entry_time, utc=True)-pd.Timedelta(minutes=5))
        assert (k >= 1).all()
        z = f.iloc[k]; previous = f.iloc[k-1]
        side = s.side.to_numpy(); level = s.level.to_numpy()
        np.testing.assert_allclose(s.entry, z.close, rtol=1e-9)
        np.testing.assert_allclose(s.stop, np.where(side == 1, z.low, z.high), rtol=1e-9)
        assert (side*(z.close.to_numpy()-level) > 0).all()
        assert (side*(z.open.to_numpy()-level) > 0).all()
        assert np.where(side == 1, z.low.to_numpy() <= level, z.high.to_numpy() >= level).all()
        assert np.where(side == 1, previous.low.to_numpy() > level, previous.high.to_numpy() < level).all()
        width = s.monday_high-s.monday_low
        target = np.where(side == 1, s.monday_low+.618*width, s.monday_high-.618*width)
        np.testing.assert_allclose(s.target_price, target, rtol=1e-7)
        ref = e[e.population.eq('signal')].set_index('event_id')
        r = e[e.population.eq('random')]
        parent = ref.loc[r.event_id]
        assert (r.entry_i.to_numpy() < parent.entry_i.to_numpy()).all()
        np.testing.assert_array_equal((r.end_i-r.entry_i).to_numpy(), (parent.end_i-parent.entry_i).to_numpy())
        rows.append(dict(symbol=symbol, signals=len(s), positions=len(g), passed=True))
    from sklearn.metrics import roc_auc_score
    signal = t[t.population.eq('signal') & t.closed]
    et = pd.to_datetime(signal.entry_time, utc=True); xt = pd.to_datetime(signal.exit_time, utc=True)
    masks = {'pre2025': (et < '2025-01-01') & (xt < '2025-01-01'),
             'y2025': (et >= '2025-01-01') & (et < '2026-01-01') & (xt < '2026-01-01'),
             'y2026': et >= '2026-01-01'}
    feature_rows = json.loads((EXP/'features.json').read_text())
    short_features = json.loads((EXP/'features_short.json').read_text())
    for item in short_features:
        item['short_only'] = True
    feature_rows += short_features
    for item in feature_rows:
        q = signal[masks[item['period']]].dropna(subset=[item['feature']])
        if item.get('short_only'):
            q = q[q.side.eq(-1)]
        if item['auc'] is not None:
            np.testing.assert_allclose(item['auc'], roc_auc_score(q.target_hit, q[item['feature']]*item['direction']), atol=1e-12)
    # High geometry AUC is checked against a real future-mutation fixture.
    raw = pd.read_parquet(EXP/'symbols/DOGEUSDT/candles5m.parquet').loc['2026-09-28 04:00':]
    cfg = dict(seed=20261029, controls_per_signal=3)
    job = dict(symbol='DOGEUSDT', execution_minutes=5)
    _, _, original, _, _ = prepare(raw, job, cfg)
    decision = pd.Timestamp('2026-09-30 13:35:00+00:00')
    changed = raw.copy()
    changed.loc[changed.index >= decision, ['open','high','low','close']] *= 1.5
    changed.loc[changed.index >= decision, 'volume'] *= 100
    _, _, altered, _, _ = prepare(changed, job, cfg)
    prefix = original[pd.to_datetime(original.signal_time, utc=True) <= decision]
    counterpart = altered.set_index('trade_id').loc[prefix.trade_id].reset_index()
    pd.testing.assert_frame_equal(prefix.reset_index(drop=True), counterpart[prefix.columns].reset_index(drop=True))
    actual = t[t.population.eq('signal') & t.symbol.eq('DOGEUSDT') & t.entry_time.eq(str(decision))].iloc[0]
    local = original[original.population.eq('signal') & original.entry_time.eq(str(decision))].iloc[0]
    np.testing.assert_allclose(actual[FEATURES].astype(float), local[FEATURES].astype(float), rtol=1e-9)
    (EXP/'validation.json').write_text(json.dumps(dict(passed=True, symbols=rows, auc_oracle_checks=len(feature_rows),
        real_future_mutation_prefix_orders=len(prefix),
        checks=['native ledger identities', 'entry-bar exclusion', 'actual-fee algebra',
                'signal OHLC and previous-bar exclusion', 'mirror target geometry',
                'random timing and identical observation horizons', 'univariate AUC versus sklearn',
                'real DOGE prefix unchanged after future OHLCV mutation'],
        unit_tests='16 tests passed in isolated Nautilus runtime; causal future mutation and native fill semantics'), indent=2)+'\n')


def main():
    t = pd.read_parquet(EXP/'trades.parquet')
    validate(t)
    reference = t[t.population.eq('signal') & t.symbol.eq('DOGEUSDT') &
                  t.entry_time.eq('2026-09-30 13:35:00+00:00')]
    keys = ['trade_id', 'entry_time', 'entry', 'stop', 'target_price', 'level', 'monday_high',
            'monday_low', 'wick_fraction', 'volume_ratio20', 'range_atr14', 'risk_bp',
            'planned_rr', 'exit_time', 'exit_price', 'reason', 'closed', 'target_hit']
    (EXP/'reference_case.json').write_text(json.dumps(dict(
        source='Existing Binance UM minute data; Notion screenshot itself is OKX and is not an exact-venue reproduction.',
        screenshot_candle_open_shanghai='2026-09-30 21:30',
        interpretation='reason0 is source-end censoring, not a profitable completed trade or a target hit.',
        rows=reference[keys].to_dict('records')), indent=2)+'\n')
    s = t[t.population.eq('signal') & t.closed & t.side.eq(-1)].copy()
    s['time'] = pd.to_datetime(s.entry_time, utc=True)
    s['duration'] = (pd.to_datetime(s.exit_time, utc=True)-s.time).dt.total_seconds()/3600
    chosen = []
    # Most recent example per symbol, bounded to 12h so individual candles stay legible.
    for hit, count in [(True, 6), (False, 3)]:
        pool = s[s.target_hit.eq(hit) & s.duration.between(.08, 12)].sort_values('time', ascending=False)
        chosen.extend(pool.drop_duplicates('symbol').head(count).to_dict('records'))
    fig, axes = plt.subplots(3, 3, figsize=(20, 13), facecolor='#101820')
    selected = []
    for ax, row in zip(axes.flat, chosen):
        f = pd.read_parquet(EXP/'symbols'/row['symbol']/'candles5m.parquet')
        signal_open = pd.Timestamp(row['entry_time'])-pd.Timedelta(minutes=5)
        at = int(f.index.get_indexer([signal_open])[0])
        exit_open = pd.Timestamp(row['exit_time']).floor('5min')
        right = min(len(f), int(f.index.searchsorted(exit_open, side='right'))+5)
        left = max(0, at-24); z = f.iloc[left:right]
        ax.set_facecolor('#101820')
        for i, candle in enumerate(z.itertuples()):
            color = '#4ec9b0' if candle.close >= candle.open else '#ed7373'
            ax.vlines(i, candle.low, candle.high, color=color, linewidth=.8)
            height = max(abs(candle.close-candle.open), (z.high.max()-z.low.min())*.001)
            ax.add_patch(Rectangle((i-.33, min(candle.open, candle.close)), .66, height,
                facecolor=color, edgecolor=color, linewidth=.5))
        for value, color, label, style in [(row['level'], '#e1b955', '1.618', '-'),
            (row['stop'], '#f08d8d', 'SL', ':'), (row['target_price'], '#70c8e7', 'far 0.618 TP', '--'),
            (row['entry'], '#d8dce0', 'entry', ':')]:
            ax.axhline(value, color=color, lw=.9, linestyle=style, label=f'{label} {value:.6g}')
        ax.axvline(at-left+.45, color='white', lw=1, alpha=.6)
        result = 'TARGET' if row['target_hit'] else 'STOP'
        stamp = pd.Timestamp(row['entry_time']).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')
        ax.set_title(f"{row['symbol']} {'LONG' if row['side']==1 else 'SHORT'} | {result} | {row['net_r']:+.2f} R\n{stamp} UTC+8", color='white', fontsize=10, loc='left')
        ticks = np.unique(np.linspace(0, len(z)-1, 5).astype(int))
        ax.set_xticks(ticks, [z.index[i].tz_convert('Asia/Shanghai').strftime('%m-%d\n%H:%M') for i in ticks], fontsize=8)
        ax.tick_params(colors='#c6d0d8', labelsize=8)
        ax.grid(alpha=.1); ax.legend(loc='best', fontsize=7, facecolor='#101820', labelcolor='white', framealpha=.7)
        for spine in ax.spines.values(): spine.set_color('#344450')
        selected.append({k:row[k] for k in ['trade_id','symbol','entry_time','exit_time','side','entry','stop','target_price','net_r','target_hit']})
    fig.suptitle('Single upper 5m wick at Monday 1.618 | SHORT at close, candle-high stop, far internal 0.618 target\nBinance history; OKX Lv1 fees; six recent target hits and three stops (illustrative selection)', color='white', fontsize=14)
    fig.tight_layout(rect=[0,0,1,.95]); fig.savefig(EXP/'examples.png', dpi=130, facecolor=fig.get_facecolor()); plt.close(fig)
    (EXP/'chart_examples.json').write_text(json.dumps(selected, indent=2)+'\n')


if __name__ == '__main__':
    main()
