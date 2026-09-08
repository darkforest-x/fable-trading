"""Read-only diagnostics of the frozen first 72 submitted HL2 review answers.

Geometry comes from exported owner answers, never from future prices. OHLCV is
read through the approved bounded prefix reader; the original main and separate
150-bar reference must replay before descriptive paths are calculated. Neither
shape judgments nor terminal returns are promoted to labels by this module.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import subprocess

import cv2
import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.datasets import owner_review_export as export
from yoyo.datasets import review_future_context as future
from yoyo.datasets.ma_launch_owner_grade_a_hl2 import with_hl2_mas
from yoyo.layers.l1_detection.render import ChartTransform

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-yolo-dataset-consolidation-20260908-v1'
PREREG = EXP / 'annotation_audit_preregistration.json'
SNAPSHOT = ROOT / 'output/offline_tasks/owner_review_exports/20260908T154048654620Z'
OUT = ROOT / 'output/offline_tasks/owner_annotation_audit_20260908'
PACK = ROOT / 'datasets/grade_a_hl2_review_20260908_v1'
FUTURE = ROOT / 'datasets/owner_review_future150_20260908_v1'


def readl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def bar_centers(box, tf):
    """One-based candle centers inside a box, using the renderer's integer x."""
    left = box['x'] * tf.width / 100
    right = (box['x'] + box['width']) * tf.width / 100
    return [i + 1 for i in range(tf.n_bars) if left <= tf.x_at(i) <= right]


def geometry(owner, proposal, transform):
    tf = ChartTransform(**transform)
    return {
        'dx_bars': (owner['x'] - proposal['x']) * tf.width / 100 / (tf.plot_w / (tf.n_bars - 1)),
        'dy_px': (owner['y'] - proposal['y']) * tf.height / 100,
        'dw_px': (owner['width'] - proposal['width']) * tf.width / 100,
        'dh_px': (owner['height'] - proposal['height']) * tf.height / 100,
        'class_changed': owner['label'] != proposal['label'],
        'owner_bar_centers': bar_centers(owner, tf),
        'proposal_bar_centers': bar_centers(proposal, tf),
    }


def clipping(box, transform, candles):
    """Count vertical clipping for candles whose centers lie inside the box.

    This diagnoses geometry under the existing whole-candle envelope convention;
    it is not a classifier of morphology or a claim about owner intent.
    """
    tf = ChartTransform(**transform)
    top = box['y'] * tf.height / 100
    bottom = (box['y'] + box['height']) * tf.height / 100
    body, wick = [], []
    for number in bar_centers(box, tf):
        candle = candles[number - 1]
        if (min(tf.y_at(candle[k]) for k in ('open', 'close')) < top - 1
                or max(tf.y_at(candle[k]) for k in ('open', 'close')) > bottom + 1):
            body.append(number)
        if tf.y_at(candle['high']) < top - 1 or tf.y_at(candle['low']) > bottom + 1:
            wick.append(number)
    return {'body_outside': body, 'wick_outside': wick}


def draw_card(main, reference, row):
    """Create a diagnostic copy; source PNGs and annotations remain untouched."""
    left = main.copy()
    for box, color in [(row['proposal'], (110, 110, 110)), (row['owner'], (240, 100, 20))]:
        xy = [round(box['x'] * 12.8), round(box['y'] * 7.42),
              round((box['x'] + box['width']) * 12.8), round((box['y'] + box['height']) * 7.42)]
        cv2.rectangle(left, tuple(xy[:2]), tuple(xy[2:]), color, 3)
    tf = ChartTransform(**row['chart_transform'])
    card = np.full((840, 2560, 3), 255, np.uint8)
    card[58:800, :1280] = left
    card[58:800, 1280:] = reference
    title = f"TASK {row['task_id']}  {row['direction']}  {row['symbol']} | {row['status']}"
    cv2.putText(card, title, (12, 27), cv2.FONT_HERSHEY_SIMPLEX, .7, (20, 20, 20), 2)
    cv2.putText(card, 'GRAY: original proposal   BLUE: submitted box | numbers: main-canvas candles',
                (12, 50), cv2.FONT_HERSHEY_SIMPLEX, .52, (40, 40, 40), 1)
    for i in range(tf.n_bars):
        x = tf.x_at(i)
        cv2.putText(card, str(i + 1), (max(0, x - 9), 827), cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 0, 0), 1)
    ft = ChartTransform(**row['future_transform'])
    for k in [1, 5, 10, 20, 40, 100, 150]:
        x = 1280 + ft.x_at(tf.n_bars - 1 + k)
        cv2.putText(card, '+' + str(k), (min(2510, x - 10), 827), cv2.FONT_HERSHEY_SIMPLEX, .53, (0, 0, 0), 1)
    return card


def build():
    prereg = json.loads(PREREG.read_text())
    if subprocess.check_output(['git', 'branch', '--show-current'], text=True, cwd=ROOT).strip() != 'main':
        raise ValueError('main required')
    code_paths = [str(Path(__file__).relative_to(ROOT)), str(PREREG.relative_to(ROOT)),
                  'tests/test_review_annotation_audit.py',
                  'yoyo/datasets/review_future_context.py', 'yoyo/datasets/owner_review_export.py',
                  'yoyo/datasets/fifteen_minute_launch_candidates.py',
                  'yoyo/datasets/ma_launch_owner_grade_a_hl2.py',
                  'yoyo/layers/l1_detection/render.py', 'yoyo/contracts/holdout.py']
    for path in code_paths:
        if (ROOT / path).read_bytes() != subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT):
            raise ValueError('commit builder before build: ' + path)
    code = {p: export.sha(ROOT / p) for p in code_paths}
    for name, digest in prereg['source_sha256'].items():
        if export.sha(ROOT / name) != digest:
            raise ValueError('frozen source changed: ' + name)
    expected, old_mapping, _ = export.read_sources()
    raw = json.loads((SNAPSHOT / 'raw.json').read_text())
    mapping = export.verify_tasks(raw['tasks'], expected, old_mapping)
    export.verify_predictions(raw['predictions'], expected, mapping)
    answers = [a for a in readl(SNAPSHOT / 'answers.jsonl')
               if a['protocol_id'] == export.NEW_PROTOCOL and a['record_kind'] == 'annotation']
    answers.sort(key=lambda a: a['task_id'])
    if [a['task_id'] for a in answers] != prereg['task_ids']:
        raise ValueError('cohort changed')
    meta = {r['review_id']: r for r in readl(PACK / 'manifest.jsonl')}
    refs = {r['review_id']: r for r in readl(FUTURE / 'manifest.jsonl')}
    # Gate the complete cohort before touching any image or OHLCV value.
    groups = defaultdict(list)
    for a in answers:
        rid = a['review_id']; m = meta[rid]; f = refs[rid]
        if mapping[rid] != a['task_id'] or f['mode'] != 'new_future150':
            raise ValueError('identity mismatch')
        if (pd.Timestamp(m['main_end_time']) + future.manual.BAR > HOLDOUT_START
                or pd.Timestamp(f['future']['review_available_at']) > HOLDOUT_START
                or f['future']['actual_future_bars'] != 150):
            raise ValueError('unauthorized or incomplete reference')
        rep = next(r for r in m['lineage'] if r['is_representative'])
        groups[m['source_path']].append((a, m, f, rep))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'cards').mkdir(exist_ok=True)
    rows, audits, unchanged = [], {}, {}
    for source, group in groups.items():
        bound = future.source_read_end([g[3] for g in group])
        frame, audit = future.manual.read_preholdout_prefix(ROOT / source, end_exclusive=bound)
        if audit['holdout_ohlcv_rows_materialized'] != 0:
            raise ValueError('holdout materialized')
        audits[source] = {**audit, 'end_exclusive': bound.isoformat()}
        frame = with_hl2_mas(frame)
        for a, m, f, rep in group:
            image = PACK / m['asset_roles']['image']; ref = FUTURE / f['image_path']
            for path, digest in [(image, m['assets'][m['asset_roles']['image']]), (ref, f['image_sha256'])]:
                if export.sha(path) != digest:
                    raise ValueError('image changed')
                unchanged[str(path.relative_to(ROOT))] = digest
            future.replay_main(frame, rep, m['chart_transform'], image.read_bytes())
            replay, info = future.render_future(frame, rep)
            if hashlib.sha256(replay).hexdigest() != f['image_sha256'] or info != f['future']:
                raise ValueError('future replay mismatch')
            proposal = export.rectangle(expected[a['review_id']]['prediction']['result'][0])
            if len(a['normalized_boxes']) != 1:
                raise ValueError('this audit expects one raw submitted rectangle')
            owner = a['normalized_boxes'][0]
            window, _ = future.bounded_future_window(frame, rep)
            main_n = m['chart_transform']['n_bars']
            anchor = float(window.iloc[main_n - 1]['close'])
            core_end = rep['source_core_end_i'] - rep['window_start_i']
            core_anchor = float(window.iloc[core_end]['close'])
            horizons = [3, 5, 10, 20, 40, 100, 150]
            row = {'task_id': a['task_id'], 'review_id': a['review_id'], 'status': a['status'],
                   'choices': a['choices'], 'direction': m['direction'], 'symbol': Path(source).stem,
                   'submitted_at': a['raw_answer']['updated_at'], 'source_path': source,
                   'main_start_time': m['main_start_time'], 'main_end_time': m['main_end_time'],
                   'proposal': proposal, 'owner': owner, 'chart_transform': m['chart_transform'],
                   'future_transform': f['future']['chart_transform'],
                   'source_core_bars': [rep['source_core_start_i'] - rep['window_start_i'] + 1, core_end + 1],
                   'geometry': geometry(owner, proposal, m['chart_transform']),
                   'future_close_change_pct_from_input_end': {str(k): 100 * (float(window.iloc[main_n-1+k]['close'])/anchor - 1) for k in horizons},
                   'close_change_pct_from_original_core_end': {str(k): 100 * (float(window.iloc[core_end+k]['close'])/core_anchor - 1) for k in horizons},
                   'main_candles': window.iloc[:main_n][['open_time','open','high','low','close']].assign(open_time=lambda x:x.open_time.astype(str)).to_dict('records'),
                   'future_candles': window.iloc[main_n:][['open_time','open','high','low','close']].assign(open_time=lambda x:x.open_time.astype(str)).to_dict('records')}
            row['clipping'] = {kind: clipping(row[kind], m['chart_transform'], row['main_candles'])
                               for kind in ('owner', 'proposal')}
            card = draw_card(cv2.imread(str(image)), cv2.imread(str(ref)), row)
            cv2.imwrite(str(OUT / 'cards' / f"{a['task_id']}.png"), card)
            rows.append(row)
        print(f'validated {len(rows)}/72', flush=True)
    rows.sort(key=lambda r:r['task_id'])
    for path, digest in unchanged.items():
        assert export.sha(ROOT / path) == digest
    for p, digest in code.items():
        assert export.sha(ROOT / p) == digest
    changed = [r for r in rows if any(abs(r['geometry'][k]) > 1e-5 for k in ('dx_bars','dy_px','dw_px','dh_px')) or r['geometry']['class_changed']]
    # Null: pairing another answer to a different proposal should not preserve
    # dimensions nearly as often as pairing to its actual inherited proposal.
    def same_size(a, b):
        return all(abs(a[k] - b[k]) < 1e-6 for k in ('width','height'))
    rng = np.random.default_rng(20260908)
    null = [sum(same_size(changed[i]['owner'], changed[j]['proposal']) for i,j in enumerate(rng.permutation(len(changed)))) for _ in range(10000)]
    observed = sum(same_size(r['owner'],r['proposal']) for r in changed)
    summary = {'tasks':len(rows), 'status':dict(Counter(r['status'] for r in rows)),
               'changed_geometry_or_class':len(changed), 'same_width_and_height_among_changed':observed,
               'unchanged_width_count':sum(abs(r['geometry']['dw_px']) < 1e-5 for r in changed),
               'unchanged_height_count':sum(abs(r['geometry']['dh_px']) < 1e-5 for r in changed),
               'dx_bars_median':float(np.median([r['geometry']['dx_bars'] for r in changed])),
               'right_shifted':sum(r['geometry']['dx_bars'] > 1e-5 for r in changed),
               'class_changed':sum(r['geometry']['class_changed'] for r in rows),
               'kept_boxes_clipping': {kind: {part:sum(bool(r['clipping'][kind][part]) for r in rows if r['status']=='owner_boxes')
                                              for part in ('body_outside','wick_outside')} for kind in ('owner','proposal')},
               'size_pairing_null':{'observed':observed,'permutations':10000,'seed':20260908,'mean':float(np.mean(null)), 'max':int(max(null)), 'p_plus_one':float((1+sum(x>=observed for x in null))/10001)},
               'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip(),
               'source_sha256':code,'input_sha256':prereg['source_sha256'],
               'image_files_verified_before_after':len(unchanged), 'main_replays':72,'future_replays':72,
               'holdout_read':False, 'label_studio_writes':0, 'training_eligible':False,'new_gold':False,
               'geometry_is_not_semantic_accuracy':True}
    (OUT/'rows.json').write_bytes(export.blob(rows))
    (OUT/'summary.json').write_bytes(export.blob(summary))
    (OUT/'source_audits.json').write_bytes(export.blob(audits))
    (OUT/'immutable_media.json').write_bytes(export.blob(unchanged))
    for page in range(24):
        cards = [cv2.imread(str(OUT/'cards'/f"{r['task_id']}.png")) for r in rows[page*3:page*3+3]]
        cv2.imwrite(str(OUT/f'page_{page+1:02d}.png'), np.vstack(cards))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    build()
