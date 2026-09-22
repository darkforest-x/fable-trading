"""Audit a price-axis-only Profit3R rebuild against the immutable prior dataset.

All samples must retain their cohort, split, visible endpoint and x geometry.
Fixed review events are independently regenerated from causal source prefixes;
future outcomes never participate in the image or price-axis calculation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import cv2
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from scripts.windows.train_ma_profit3r import validate_dataset
from yoyo.datasets.ma_profit_dataset import ROOT, event_assets, label_line
from yoyo.datasets.ma_profit_review import _load_until


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('dataset', 'previous', 'plan', 'dataset-plan', 'contract', 'cohort-receipt', 'ledger', 'review-receipt', 'out'):
        p.add_argument('--' + key, required=True, type=Path)
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    if subprocess.check_output(['git', 'status', '--porcelain', '--', relative], cwd=ROOT, text=True).strip():
        raise ValueError('Commit revision auditor before use')
    audit = validate_dataset(a.dataset, a.plan, a.dataset_plan, a.contract, a.cohort_receipt)
    old = {(r['event_id'], r['variant']): r for r in rows(a.previous / 'manifest.jsonl')}
    new_rows = rows(a.dataset / 'manifest.jsonl')
    new = {(r['event_id'], r['variant']): r for r in new_rows}
    assert len(new) == len(new_rows) and old.keys() == new.keys(), 'sample membership drift'
    allowed = {'image_sha256', 'label_sha256', 'box', 'price_scale', 'price_min', 'price_max'}
    image_changes = label_changes = 0
    heights = []
    expected_paths = {r[k] for r in new_rows for k in ('image_path', 'label_path')}
    actual_paths = {str(f.relative_to(a.dataset)) for base, ext in [('images', '*.png'), ('labels', '*.txt')] for f in (a.dataset / base).glob('*/' + ext)}
    assert actual_paths == expected_paths, 'orphan or missing dataset file'
    for key, item in new.items():
        before = old[key]
        assert {k: v for k, v in item.items() if k not in allowed} == {k: v for k, v in before.items() if k not in allowed}, f'lineage/split drift: {key}'
        for path_key, hash_key in [('image_path', 'image_sha256'), ('label_path', 'label_sha256')]:
            assert sha(a.previous / before[path_key]) == before[hash_key], 'prior dataset was mutated'
        if item['box'] is not None:
            for field in ('x0', 'x1', 'cx_norm', 'w_norm'):
                assert item['box'][field] == before['box'][field], f'x geometry drift: {key}'
        image_changes += item['image_sha256'] != before['image_sha256']
        label_changes += item['label_sha256'] != before['label_sha256']
        pixels = cv2.imread(str(a.dataset / item['image_path']))
        ys = np.where(np.any(pixels < 245, axis=2))[0]
        heights.append(int(ys.max() - ys.min() + 1) if len(ys) else 0)
    ledger = {r['event_id']: r for r in rows(a.ledger)}
    review = json.loads(a.review_receipt.read_text())
    ids = list(dict.fromkeys(r['event_id'] for r in review['images']))
    a.out.mkdir(parents=True)
    sheet = Image.new('RGB', (1440, 310 * len(ids)), 'white')
    draw = ImageDraw.Draw(sheet)
    regenerated = []
    for order, event_id in enumerate(ids):
        row = ledger[event_id]
        frame = _load_until(row, pd.Timestamp(row['profit']['decision_close_time_utc']))
        for column, asset in enumerate(event_assets(frame, row, price_scale='visible_range_v1')):
            record = new[(event_id, asset['variant'])]
            assert hashlib.sha256(asset['png']).hexdigest() == record['image_sha256'], 'regenerated pixels disagree'
            assert label_line(asset) == (a.dataset / record['label_path']).read_text(), 'regenerated labels disagree'
            target = a.out / f'{order + 1:02d}_{row["symbol"]}_{asset["variant"]}.png'
            target.write_bytes(asset['png'])
            image = Image.open(target).convert('RGB')
            image.thumbnail((470, 273))
            sheet.paste(image, (column * 480, order * 310 + 24))
            draw.text((column * 480 + 5, order * 310 + 6), f'{row["symbol"]} {row["bar_minutes"]}m {row["direction"]} {asset["variant"]}', fill='black')
            regenerated.append({'event_id': event_id, 'variant': asset['variant'], 'image_sha256': record['image_sha256'], 'label_sha256': record['label_sha256']})
    sheet.save(a.out / 'contact_sheet.png')
    result = {'status': 'passed', 'builder_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'audit': audit, 'previous_manifest_sha256': sha(a.previous / 'manifest.jsonl'),
              'ledger_sha256': sha(a.ledger), 'dataset_plan_sha256': sha(a.dataset_plan),
              'sample_split_endpoint_identity': True, 'original_dataset_files_verified': 2 * len(old),
              'orphan_files': 0, 'changed_images': image_changes, 'changed_labels': label_changes,
              'content_height_px_quantiles': dict(zip(('min', 'q25', 'median', 'q75', 'max'), map(float, np.quantile(heights, [0, .25, .5, .75, 1])))),
              'source_regenerated_views': regenerated, 'production_eligible': False,
              'limitation': 'Pixel geometry, causal source reproduction and dataset integrity only; not model accuracy or owner per-event gold approval.'}
    (a.out / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_regenerated_views'}))


if __name__ == '__main__':
    main()
