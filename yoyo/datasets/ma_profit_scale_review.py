"""Review the price-axis floor on frozen training events without altering inputs.

Only core-start minus nine bars through core-end plus five bars are visible.
HL2 SMA/EMA 20/60/120 use the same 1,200-bar causal support as the dataset.
The comparison changes price bounds only; no labels or training files are written.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import cv2
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from yoyo.datasets.ma_profit_dataset import ROOT, add_hl2_mas, _recolor_candles
from yoyo.datasets.ma_profit_review import _load_until
from yoyo.datasets.fifteen_minute_launch_candidates import sha256_file
from yoyo.layers.l1_detection import render


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--review-receipt', type=Path, required=True)
    parser.add_argument('--event-id', action='append', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    rows = {r['event_id']: r for r in map(json.loads, args.ledger.read_text().splitlines())}
    copies = {r['event_id']: r for r in json.loads(args.review_receipt.read_text())['images'] if r['variant'] == 'A'}
    args.out.mkdir(parents=True)
    sheet = Image.new('RGB', (1280, 410 * len(args.event_id)), 'white')
    draw = ImageDraw.Draw(sheet)
    records = []
    for index, event_id in enumerate(args.event_id):
        row, original = rows[event_id], copies[event_id]
        frame = _load_until(row, pd.Timestamp(row['profit']['decision_close_time_utc']))
        times = pd.to_datetime(frame.open_time, utc=True)
        start = int(np.flatnonzero(times == pd.Timestamp(row['core_start_time']))[-1])
        end = int(np.flatnonzero(times == pd.Timestamp(row['core_end_time']))[-1])
        support = start - 11 - 1200
        causal = add_hl2_mas(frame.iloc[support:end + 6].reset_index(drop=True))
        window = causal.iloc[start - 9 - support:].reset_index(drop=True)
        old, tf = render.render_chart(window)
        old = _recolor_candles(old)
        original_path = ROOT / original['copy']
        assert sha256_file(original_path) == original['sha256']
        assert np.array_equal(old, cv2.imread(str(original_path))), 'baseline pixels drift'
        values = pd.concat([window.low, window.high, *[window[c] for c in render.ALL_MA_COLS]]).dropna()
        low, high = float(values.min()), float(values.max())
        span = max(high - low, 1e-9)
        fitted = replace(tf, price_min=low - .06 * span, price_max=high + .06 * span)
        new, _ = render.render_chart(window, fixed_transform=fitted)
        new = _recolor_candles(new)
        stem = f'{index + 1:02d}_{row["symbol"]}_{row["bar_minutes"]}m'
        for column, (name, image) in enumerate((('current', old), ('visible_range_fit', new))):
            target = args.out / f'{stem}_{name}.png'
            assert cv2.imwrite(str(target), image)
            view = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            view.thumbnail((640, 371))
            sheet.paste(view, (column * 640, index * 410 + 30))
            draw.text((column * 640 + 12, index * 410 + 8), f'{stem} | {name}', fill='black')
        records.append({'event_id': event_id, 'visible_bars': len(window), 'original_sha256': original['sha256'],
                        'baseline_pixels_equal': True, 'visible_end_utc': str(window.open_time.iloc[-1]),
                        'old_price_span': tf.price_max - tf.price_min,
                        'fitted_price_span': fitted.price_max - fitted.price_min,
                        'vertical_magnification': (tf.price_max - tf.price_min) / (fitted.price_max - fitted.price_min)})
    sheet.save(args.out / 'comparison.png')
    receipt = {'builder_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               'ledger_sha256': sha256_file(args.ledger), 'training_eligible': False,
               'production_eligible': False, 'changed': 'price axis only; visible candles and MA support unchanged',
               'limitation': 'Diagnostic alternative only. Local fitting also magnifies MA separation; this is not an accepted training or inference policy.',
               'events': records}
    (args.out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
