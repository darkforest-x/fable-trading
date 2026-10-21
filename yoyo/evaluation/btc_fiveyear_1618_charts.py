"""Render all 25 frozen BTC Fib 1.618 sweep20 + MTF trades as review images.

Reuse the five-year native outcome ledger and existing 60-bar landscape
renderer. The touch candle, prior-20 extreme and Monday/Fib grid use only
touch-time history; future candles are review context, never new features.
Every selected signal remains present, including distinct arrows sharing
one touch/entry. Actual entry/exit fills and original 20bp fees are unchanged.
Static black 2880x1620 PNGs, four contact sheets and a ZIP are the requested
delivery surface. Yellow highlights the whole touch candle and matched key;
red/green encode trade direction, with written labels and signed outcomes.
"""
import argparse
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/active/exp-range-v231-fiveyear-filters-20261021-v1'
DEST = ROOT / 'artifacts/btc_fiveyear_1618_25_charts_20261021'
CODE = ['yoyo/evaluation/btc_fiveyear_1618_charts.py',
        'yoyo/evaluation/btc_annual_signal_landscape.py']


def prepare():
    import numpy as np
    import pandas as pd
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals
    from yoyo.evaluation.pepe_range_exits import aggregate
    from yoyo.evaluation.monday_range_sweep_reference import sweep_mask

    cfg = json.loads((SOURCE / 'config.json').read_text())
    read = lambda p: pd.read_csv(p, float_precision='round_trip')
    gate = read(SOURCE / 'BTC/admission.csv').set_index('pair_id')
    matches = read(SOURCE / 'BTC/signal_matches.csv').set_index('pair_id')
    ledger = read(SOURCE / 'BTC/combined_signal_details.csv')
    chosen = ledger[ledger.key_group.astype(str) == '1.618'].sort_values(['entry_i', 'pair_id'])
    assert len(chosen) == 25 and chosen.pair_id.is_unique
    assert (chosen.net_r > 1e-9).sum() == 11
    expected = read(SOURCE / 'by_key.csv').query("symbol == 'BTC' and key == '1.618'").iloc[0]
    assert abs(chosen.net_r.sum() - expected.net_r) < 1e-8
    candles_path = ROOT / cfg['source'] / 'BTC/candles.csv.gz'
    five = read(candles_path)
    five.index = pd.DatetimeIndex(pd.to_datetime(five.pop('ts'), unit='ms', utc=True)
                                  + pd.Timedelta(minutes=5)).as_unit('ns')
    q = aggregate(five, 15)
    opened = q.index - pd.Timedelta(minutes=15)
    arrows = signals(q).to_numpy()
    grid, names, weeks, _ = monday_levels(q, cfg['ratios'], cfg['timezone'])
    sweeps = {side: sweep_mask(q, side) for side in (-1, 1)}
    charts, rows = [], []
    for number, t in enumerate(chosen.itertuples(), 1):
        m, g = matches.loc[t.pair_id], gate.loc[t.pair_id]
        i, j, e = (int(m[k]) for k in ['signal_i', 'touch_i', 'entry_i'])
        side = int(t.side)
        assert arrows[i] == side and sweeps[side][j]
        assert g.window_both and g.paired_arrow_was_mtf
        assert q.index[e] == pd.Timestamp(t.entry_time) == pd.Timestamp(m.entry_time)
        assert five.index[int(t.entry_i)] == q.index[e]
        assert opened[i] == pd.Timestamp(m.signal_time) and opened[j] == pd.Timestamp(m.touch_time)
        assert q.index[i] == pd.Timestamp(g.arrow_close)
        assert q.close.iloc[e] == t.entry == m.entry and t.stop == m.stop
        assert abs(grid[j, names.index(m.key)] - m.level) < 1e-8
        b = q.iloc[j]
        bodylo, bodyhi = min(b.open, b.close), max(b.open, b.close)
        wick = bodylo - b.low if side == 1 else b.high - bodyhi
        depth = m.level - b.low if side == 1 else b.high - m.level
        assert wick > 0 and .1 - 1e-8 <= depth / wick <= 1
        assert (b.low <= m.level < bodylo if side == 1 else bodyhi < m.level <= b.high)
        assert t.stop == (b.low if side == 1 else b.high)
        prior20 = float(q.low.iloc[j-20:j].min() if side == 1 else q.high.iloc[j-20:j].max())
        lo = max(0, min(min(i, j) - 20, len(q) - 60))
        hi = lo + 60
        assert lo <= min(i, j, e) <= max(i, j, e) < hi
        assert np.all(np.diff(q.index[lo:hi].asi8) == pd.Timedelta(minutes=15).value)
        exit_time = pd.Timestamp(t.exit_time).tz_convert('Asia/Shanghai')
        assert opened[lo] <= exit_time <= q.index[hi-1]
        exitbar = five.iloc[int(t.exit_i)]
        assert exitbar.low - .1 < t.exit_price < exitbar.high + .1
        if int(t.reason) == 7:
            assert abs(t.exit_price - exitbar.close) < 1e-8
        risk = side * (t.entry - t.stop)
        assert abs(t.gross_r - side * (t.exit_price - t.entry) / risk) < 1e-8
        assert abs(t.fee_r - .002 * t.entry / risk) < 1e-8
        pivot = None
        if np.isfinite(t.pivot_price):
            pt = pd.Timestamp(t.pivot_open_time).tz_convert('Asia/Shanghai')
            pk = int(opened.get_indexer([pt])[0])
            assert pk >= 0
            assert abs(q.iloc[pk]['high' if side == 1 else 'low'] - t.pivot_price) < 1e-8
            pivot = dict(time=pt.isoformat(), price=float(t.pivot_price), reference_r=float(t.pivot_reference_r))
        r = dict(version='V2.31', number=number, total=25, pair_id=int(t.pair_id),
            side=side, signal_i=i, touch_i=j, entry_i=e,
            signal_time=m.signal_time, touch_time=m.touch_time, entry_bar_time=m.entry_bar_time,
            entry_time=m.entry_time, entry=float(t.entry), stop=float(t.stop),
            key=m.key, level=float(m.level), other_number=None, other_version=None,
            monday_date=str(weeks[j]), monday_low=float(grid[j, 0]), monday_high=float(grid[j, 1]),
            wick=float(wick), depth=float(depth), penetration=float(depth/wick),
            bars_before=i-lo, bars_after=hi-i-1,
            mtf_arrow_time=pd.Timestamp(g.arrow_close).tz_convert('Asia/Shanghai').isoformat(),
            review_group='扫20根＋多周期 · 1.618', prior20=prior20, separate_entry_callout=True,
            candles=[dict(index=k, time=opened[k].tz_convert('Asia/Shanghai').isoformat(),
                ny_day=int(opened[k].tz_convert(cfg['timezone']).dayofweek), arrow=int(arrows[k]),
                **{c: float(q.iloc[k][c]) for c in ['open', 'high', 'low', 'close']}) for k in range(lo, hi)],
            levels=[dict(key=name, price=float(grid[j, k])) for k, name in enumerate(names)] +
                   [dict(key='prior20:low' if side == 1 else 'prior20:high', price=prior20)],
            exit=dict(time=exit_time.isoformat(), price=float(t.exit_price), reason=int(t.reason),
                gross_r=float(t.gross_r), net_r=float(t.net_r), outside_window=False, pivot=pivot))
        charts.append(r)
        rows.append(dict(number=number, pair_id=int(t.pair_id), side=side, signal_time=m.signal_time,
            touch_time=m.touch_time, entry_time=m.entry_time, entry=t.entry, stop=t.stop,
            exit_time=exit_time.isoformat(), exit_price=t.exit_price, reason=int(t.reason),
            key=m.key, level=m.level, prior20=prior20, gross_r=t.gross_r, fee_r=t.fee_r, net_r=t.net_r))
    DEST.mkdir(exist_ok=True)
    payload = dict(charts=charts, source=str(SOURCE.relative_to(ROOT)), data=str(candles_path.relative_to(ROOT)),
        selection='All BTC key_group=1.618 combined signal rows, ordered by entry time then pair_id',
        count=25, net_wins=11, net_losses=14, net_r=float(chosen.net_r.sum()),
        unique_touch_entry_pairs=int(chosen[['side', 'touch_time', 'entry_time']].drop_duplicates().shape[0]),
        chart_contract=__doc__, builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())
    (DEST / 'chart_data.json').write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    pd.DataFrame(rows).to_csv(DEST / 'trade_index.csv', index=False)
    print(json.dumps({k: payload[k] for k in ['count', 'net_wins', 'net_losses', 'net_r', 'unique_touch_entry_pairs']}), flush=True)


def render():
    from yoyo.evaluation.btc_annual_signal_landscape import render as draw, FONT, stamp
    from PIL import Image, ImageDraw, ImageFont
    data = json.loads((DEST / 'chart_data.json').read_text())
    draw(destination=DEST, source_data=data, trade_review=True)
    manifest = json.loads((DEST / 'manifest.json').read_text())
    assert len(manifest) == 25
    font = ImageFont.truetype(FONT, 26)
    overview = []
    for start in range(0, 25, 8):
        chunk = manifest[start:start+8]
        contact = Image.new('RGB', (1600, ((len(chunk)+1)//2)*492+70), '#0B0D11')
        ink = ImageDraw.Draw(contact)
        ink.text((24, 18), f'BTC · Fib 1.618 · 扫20根＋多周期  #{start+1:02d}–#{start+len(chunk):02d} / 25', font=font, fill='#D9DADB')
        for offset, row in enumerate(chunk):
            r = data['charts'][start+offset]
            assert {r['key'], '0:up', '0:down'} <= set(row['labeled_levels'])
            with Image.open(DEST / row['file']) as im:
                assert im.size == (2880, 1620)
                contact.paste(im.resize((800, 450), Image.Resampling.LANCZOS), ((offset%2)*800, 70+(offset//2)*492))
            caption = f"#{r['number']:02d}  {stamp(r['entry_time'], '%Y-%m-%d %H:%M')}  {'多' if r['side']==1 else '空'}  净 {r['exit']['net_r']:+.2f}R"
            ink.text(((offset%2)*800+22, 70+(offset//2)*492+451), caption, font=font,
                     fill='#36C9A3' if r['exit']['net_r'] > 0 else '#F36C7A')
        name = f'overview_{start//8+1:02d}.jpg'
        contact.save(DEST / name, quality=95)
        overview.append(name)
    archive = DEST / 'BTC_1618_25_sweep20_MTF_60bars.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for row in manifest:
            z.write(DEST / row['file'], Path(row['file']).name)
        z.write(DEST / 'trade_index.csv', 'trade_index.csv')
    with zipfile.ZipFile(archive) as z:
        assert len([n for n in z.namelist() if n.endswith('.png')]) == 25 and z.testzip() is None
    receipt = dict(count=25, net_wins=11, net_losses=14, net_r=data['net_r'],
        unique_touch_entry_pairs=data['unique_touch_entry_pairs'], all60bars=True, all_fills_visible=True,
        dimensions=[2880, 1620], both_monday_bounds=True, named_matched_key=True, prior20_extreme=True,
        actual_fill_prices=True, original_fees=True, zip=archive.name, overview=overview,
        bytes=archive.stat().st_size, training_eligible=False, production_eligible=False)
    (DEST / 'validation.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'render'])
    args = parser.parse_args()
    for path in CODE:
        subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *CODE], cwd=ROOT).returncode:
        raise RuntimeError('Commit renderer before generation')
    globals()[args.mode]()
