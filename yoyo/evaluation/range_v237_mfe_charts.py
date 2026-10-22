"""Initial-stop MFE diagnostic for frozen V2.37 confirmation-close entries.

Reuse the existing excursions implementation and cached native fixed-target
ledgers. Future 5m OHLC is an outcome only: inspect entry_i+1 through the first
initial-stop touch or end_i. There is no TP, BE, ATR, H1 or time-based exit.
Exclude the entire stop-touch candle from the conservative main histogram;
including its favorable extreme gives an explicit upper bound. These are
gross hypothetical excursions, not executable returns or portfolio results.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_v237_twoyear import close_frame, read

SOURCE = ROOT / 'experiments/active/exp-range-v237-fixed-exits-20261022-v1'
DEST = SOURCE / 'mfe'
BINS = [0, .5, 1, 2, 3, 5, 10, 20, np.inf]
LABELS = ['0–<0.5', '0.5–<1', '1–<2', '2–<3', '3–<5', '5–<10', '10–<20', '≥20']


def calculate():
    from yoyo.evaluation.btc_range_wick_review import excursions
    cfg = json.loads((SOURCE / 'config.json').read_text())
    price_root = ROOT / cfg['source']
    source_cfg = json.loads((price_root / 'config.json').read_text())
    hist, summaries, checks, stamps = [], [], [], []
    DEST.mkdir(exist_ok=True)
    for spec in cfg['symbols']:
        symbol = spec['symbol']
        base = read(SOURCE / symbol / 'candidates.csv')
        occ = read(SOURCE / symbol / 'occurrences.csv')
        frame = close_frame(read(price_root / symbol / 'candles.csv.gz'))
        bars = frame[['open', 'high', 'low', 'close']].to_numpy()
        assert occ['mode'].eq('causal').all()
        assert base.pair_id.is_unique and (base.end_i > base.entry_i).all()
        assert frame.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all()
        assert (base.risk > 0).all() and base.end_i.eq(len(frame)-1).all()
        np.testing.assert_allclose(base.entry, bars[base.entry_i, 3], atol=1e-8, rtol=0)
        np.testing.assert_allclose(base.risk, base.side*(base.entry-base.stop), atol=1e-8, rtol=0)
        for key in ['entry_i', 'end_i', 'entry', 'stop', 'side', 'risk']:
            np.testing.assert_array_equal(occ[key], occ.pair_id.map(base.set_index('pair_id')[key]))
        base['trade_id'] = base.pair_id.astype(str)
        unique = base[['pair_id', 'trade_id']].merge(
            excursions(bars, base), on='trade_id', validate='one_to_one')
        unique['outcome_i'] = np.where(unique.initial_stop_hit, unique.stop_i, base.end_i)
        unique['observed_minutes'] = (unique.outcome_i-base.entry_i)*5
        assert (unique.mfe_upper_r >= unique.mfe_r).all()
        assert unique.mfe_r.ge(0).all()
        # Independently executed native TP hits must lie inside OHLC MFE bounds.
        # Tick-aligned 1/2/3R levels have already been verified by this study.
        for target in [1, 2, 3]:
            for path in ['OHLC', 'OLHC']:
                ledger_path = SOURCE / symbol / 'native' / f'tp{target}_{path}' / 'trades.csv'
                ledger = read(ledger_path).merge(unique, on='pair_id', validate='one_to_one', suffixes=('', '_mfe'))
                assert len(ledger) == len(base)
                hit = ledger.reason.eq(4)
                assert (~(ledger.mfe_r >= target+1e-7) | hit).all()
                assert (~hit | (ledger.mfe_upper_r >= target-1e-7)).all()
                checks.append(dict(symbol=symbol, target_r=target, path=path,
                                   native_positions=len(ledger), bound_parity=True))
        joined = occ.merge(unique.drop(columns='trade_id'), on='pair_id', validate='many_to_one')
        assert len(joined) == len(occ)
        joined.to_csv(DEST / f'{symbol}_mfe.csv.gz', index=False)
        for (lane, population), group in joined.groupby(['lane', 'population']):
            values = group.mfe_r
            record = dict(symbol=symbol, lane=int(lane), population=population, n=len(group),
                median_r=float(values.median()), mean_r=float(values.mean()),
                p75_r=float(values.quantile(.75)), p90_r=float(values.quantile(.9)), max_r=float(values.max()),
                initial_stop_hit=int(group.initial_stop_hit.sum()),
                censored_at_data_end=int((~group.initial_stop_hit).sum()),
                ambiguous_mfe_n=int((group.mfe_upper_r > values+1e-8).sum()),
                median_observed_minutes=float(group.observed_minutes.median()),
                max_observed_days=float(group.observed_minutes.max()/1440),
                reach={str(t):dict(lower_n=int(values.ge(t-1e-8).sum()),
                    upper_n=int(group.mfe_upper_r.ge(t-1e-8).sum())) for t in [1, 2, 3, 5, 10, 20]})
            summaries.append(record)
            for field in ['mfe_r', 'mfe_upper_r']:
                counts, _ = np.histogram(group[field], BINS)
                assert counts.sum() == len(group)
                hist.extend(dict(symbol=symbol, lane=int(lane), population=population,
                    measure=field, bin=label, n=int(n), percent=100*float(n)/len(group))
                    for label, n in zip(LABELS, counts))
        stamps.extend(file_stamp(p) for p in [SOURCE/symbol/'candidates.csv',
            SOURCE/symbol/'occurrences.csv', price_root/symbol/'candles.csv.gz'])
    table = pd.DataFrame(hist)
    table.to_csv(DEST / 'histogram.csv', index=False)
    result = dict(source=str(SOURCE.relative_to(ROOT)), start=source_cfg['start'], end=source_cfg['end'],
        definition=__doc__, rows=summaries, native_bound_checks=checks, inputs=stamps,
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        production_eligible=False, training_eligible=False)
    dump(DEST / 'summary.json', result)
    return table, result


def render(hist, result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties

    font = FontProperties(fname='/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
    plt.rcParams.update({'font.family': font.get_name(), 'axes.unicode_minus': False,
        'font.size': 12, 'axes.spines.top': False, 'axes.spines.right': False,
        'axes.spines.left': False, 'axes.edgecolor': '#DAE0E8'})
    fig, axes = plt.subplots(2, 2, figsize=(17, 11), sharey=True)
    colors = {'BTC': '#206BD4', 'ETH': '#6B50B8'}
    h = hist[hist.measure == 'mfe_r']
    y_max = max(40, h.percent.max()+9)
    for row, symbol in enumerate(['BTC', 'ETH']):
        for col, lane in enumerate([5, 15]):
            ax = axes[row, col]
            meta = next(r for r in result['rows'] if r['symbol']==symbol and r['lane']==lane and r['population']=='signal')
            for pop, offset, color, name in [('signal', -.19, colors[symbol], '确认后入场'),
                ('random', .19, '#CBD2DC', '匹配随机')]:
                data = h[(h.symbol==symbol)&(h.lane==lane)&(h.population==pop)]
                x = np.arange(len(data))
                ax.bar(x+offset, data.percent, .35, color=color, label=name, zorder=3)
                if pop == 'signal':
                    for xx, n, pct in zip(x+offset, data.n, data.percent):
                        ax.text(xx, pct+.7, f'{n}\n{pct:.1f}%', ha='center', fontsize=9,
                                color=colors[symbol], linespacing=1.4)
            ax.set_xticks(x, LABELS, fontsize=10)
            ax.set_xlabel('最大浮盈区间（R）', labelpad=9, color='#566477')
            ax.set_ylabel('占该组全部信号（%）', color='#566477')
            ax.grid(axis='y', alpha=.18, zorder=0); ax.set_axisbelow(True)
            ax.tick_params(axis='both', length=0, pad=7); ax.set_ylim(0, y_max)
            ax.set_title(f'{symbol}  ·  {lane}min  ·  {meta["n"]:,} 笔', loc='left',
                         fontsize=17, fontweight='bold', pad=43)
            ax.text(0, 1.035, f'中位数 {meta["median_r"]:.2f}R   |   75%分位 {meta["p75_r"]:.2f}R'
                    f'   |   ≥3R {meta["reach"]["3"]["lower_n"]/meta["n"]:.1%}',
                    transform=ax.transAxes, color='#566477', fontsize=11)
            if row == 0 and col == 0:
                ax.legend(loc='upper right', frameon=False, fontsize=10)
    fig.suptitle('最大浮盈 R 分布', x=.055, y=.97, ha='left', fontsize=27, fontweight='bold', color='#16263B')
    fig.text(.055, .923, 'V2.37 · 确认后入场  |  2024-10-22—2026-10-22  |  BTC / ETH 永续合约', fontsize=13, color='#566477')
    fig.text(.055, .892, '仅保留初始止损；取消止盈、保本及跟踪退出。柱上：笔数 / 占比；各区间互斥，合计 100%。', fontsize=12, color='#566477')
    fig.text(.055, .055, '1R = 入场价与初始止损价之差。统计至首次触及初始止损或数据截止；不设 2 小时上限。', fontsize=11, color='#566477')
    fig.text(.055, .029, '主图保守排除止损触发的整根 5min K 线；浮盈未扣费，是历史最大空间，不是实际平仓收益。', fontsize=11, color='#566477')
    fig.tight_layout(rect=[.025, .085, .99, .875], h_pad=2.5, w_pad=2)
    fig.savefig(DEST/'mfe_histogram.png', dpi=180, facecolor='white')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--calculate-only', action='store_true')
    parser.add_argument('--render-only', action='store_true')
    args = parser.parse_args()
    assert not (args.calculate_only and args.render_only)
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', __file__], cwd=ROOT).returncode:
        raise RuntimeError('Commit chart builder before generating artifacts')
    if args.render_only:
        hist, result = read(DEST/'histogram.csv'), json.loads((DEST/'summary.json').read_text())
    else:
        hist, result = calculate()
    if not args.calculate_only:
        render(hist, result)
    print(json.dumps([r for r in result['rows'] if r['population']=='signal'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
