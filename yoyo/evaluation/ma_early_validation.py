"""Frozen-checkpoint diagnostics on early-v6 held-out images, never training.

Adapted from the repository's Ultralytics 8.4.89 padding diagnosis. The only
difference between the two validator arms is pad=0.5 versus pad=0. Images and
labels are copied into an isolated cache workspace; original assets stay fixed.
The old v6A and new early-v6 checkpoints see identical evaluation images.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
EXP = Path('experiments/active/exp-ma-morphology-v6-threeview-20260925-v1')
PLAN = EXP / 'early_evaluation_plan_20260926.json'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(output: Path, device: str):
    import torch
    from ultralytics import YOLO
    from scripts.research.diagnose_ma_morphology_padding import RecordingValidator, AlignedValidator
    from yoyo.datasets.ma_morphology_training_package import check_environment

    class PersistPredictions:
        def finalize_metrics(self):
            super().finalize_metrics()
            (self.save_dir / 'raw_predictions.json').write_text(json.dumps(self.jdict))

    class DefaultValidator(PersistPredictions, RecordingValidator):
        pass

    class PadZeroValidator(PersistPredictions, AlignedValidator):
        pass

    env = check_environment(cuda_required=device == '0')
    plan = json.loads((ROOT / PLAN).read_text())
    dataset = ROOT / plan['dataset']
    assert sha(dataset / 'manifest.jsonl') == plan['manifest_sha256']
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    rows = [json.loads(l) for l in (dataset / 'manifest.jsonl').open()]
    rows = [r for r in rows if r['split'] in ('val', 'test') and r['variant'] == 'P9']
    pools = {}
    for split in ('val', 'test'):
        for pool in ('reference', 'grade_a_challenge'):
            key = split + '_' + pool
            selected = [r for r in rows if r['split'] == split and r['evaluation_pool'] == pool]
            pools[key] = selected
            for r in selected:
                for kind in ('image', 'label'):
                    src = dataset / r[kind + '_path']
                    assert sha(src) == r[kind + '_sha256']
                    folder = 'images' if kind == 'image' else 'labels'
                    dst = output / 'data' / folder / key / src.name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
            yaml = output / (key + '.yaml')
            yaml.write_text('path: ' + json.dumps(str((output / 'data').resolve())) + '\n'
                + f'train: images/{key}\nval: images/{key}\ntest: images/{key}\n'
                + 'names: [dense_launch_long, dense_launch_short]\n')
    torch.set_num_threads(4)
    results = {'status': 'running', 'plan_sha256': sha(ROOT / PLAN), 'environment': env,
               'runs': {}, 'production_eligible': False}
    for model_name, binding in plan['models'].items():
        weight = ROOT / binding['path']
        assert sha(weight) == binding['sha256']
        for pad_name, validator in [('pad05', DefaultValidator), ('pad0', PadZeroValidator)]:
            for pool, selected in pools.items():
                name = f'{model_name}_{pad_name}_{pool}'
                metrics = YOLO(str(weight)).val(validator=validator,
                    data=str(output / (pool + '.yaml')), split='val', imgsz=1280,
                    batch=8, device=device, conf=.001, iou=.7, max_det=300,
                    rect=True, workers=0, augment=False, plots=False, verbose=False,
                    save_json=True, project=str(output / 'runs'), name=name, exist_ok=False)
                folder = Path(metrics.diagnostic_save_dir)
                results['runs'][name] = {'images': len(selected), 'metrics': metrics.results_dict,
                    'geometry': json.loads((folder / 'geometry.json').read_text()),
                    'predictions_sha256': sha(folder / 'raw_predictions.json'),
                    'no_positive_targets': not any(r['class_id'] is not None for r in selected)}
                (output / 'validation.json').write_text(json.dumps(results, indent=2) + '\n')
                print('VALIDATION ' + json.dumps({name: results['runs'][name]}), flush=True)
    results['status'] = 'complete'
    (output / 'validation.json').write_text(json.dumps(results, indent=2) + '\n')
    return results


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', default='cpu')
    a = p.parse_args()
    run(a.output.resolve(), a.device)
