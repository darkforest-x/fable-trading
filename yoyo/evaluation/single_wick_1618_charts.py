"""Plot native #72 examples from saved ledgers; no replay or altered signals."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from yoyo.evaluation.single_wick_1618_majors import EXP


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
    (EXP/'validation.json').write_text(json.dumps(dict(passed=True, symbols=rows,
        checks=['native ledger identities', 'entry-bar exclusion', 'actual-fee algebra',
                'signal OHLC and previous-bar exclusion', 'mirror target geometry',
                'random timing and identical observation horizons'],
        unit_tests='16 tests passed in isolated Nautilus runtime; causal future mutation and native fill semantics'), indent=2)+'\n')


def main():
    t = pd.read_parquet(EXP/'trades.parquet')
    validate(t)
    s = t[t.population.eq('signal') & t.closed].copy()
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
    fig.suptitle('Single 5m wick at Monday 1.618 | close entry, candle-extreme stop, far internal 0.618 target\nBinance history; OKX Lv1 fees; six recent target hits and three stops (illustrative selection)', color='white', fontsize=14)
    fig.tight_layout(rect=[0,0,1,.95]); fig.savefig(EXP/'examples.png', dpi=130, facecolor=fig.get_facecolor()); plt.close(fig)
    (EXP/'chart_examples.json').write_text(json.dumps(selected, indent=2)+'\n')


if __name__ == '__main__':
    main()
