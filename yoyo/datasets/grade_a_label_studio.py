"""Import the Owner-requested full event review and sampled candidates into LS.

The 2026-09-07 request covers original inputs with separate human future
context. No model annotations, predictions, training writes, or holdout reads
are performed. The existing local Label Studio server serves reports/, so
relative pack links preserve the physical dataset layout and image hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from yoyo.datasets import grade_a_assisted_review as assisted

ROOT = assisted.ROOT
EXPERIMENT = ROOT / 'experiments/active/exp-15m-grade-a-labelstudio-manual-20260907-v1'
RESULTS = EXPERIMENT / 'results'
CONFIG = ROOT / 'configs/labelstudio/grade_a_manual_future40.xml'
FULL_PACK = ROOT / 'datasets/grade_a_manual_events_20260907_v1'
TITLES = {
    'full': 'YOLO 完整训练集 · 4172 事件手工标注 · 未来40根',
    'candidates': 'YOLO 补充候选 · 240 样本＋36复核 · 未来40根',
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path: Path, value: object) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() != text:
        raise ValueError(f'refusing to overwrite a different artifact: {path}')
    path.write_text(text)


def build_candidates() -> Path:
    """Reuse all 276 frozen image pairs without exposing machine proposals."""
    for path in [Path(__file__).resolve(), CONFIG]:
        rel = str(path.relative_to(ROOT))
        if subprocess.check_output(['git', 'show', 'HEAD:' + rel], cwd=ROOT) != path.read_bytes():
            raise ValueError(f'commit source before building: {rel}')
    receipt = json.loads((assisted.RESULTS / 'build_receipt.json').read_text())
    pack = assisted.PACK
    for rel, key in [('public/manifest.json', 'manifest_sha256'),
                     ('review_future_only/manifest.json', 'future_manifest_sha256'),
                     ('admin/lineage.jsonl', 'lineage_sha256')]:
        if sha(pack / rel) != receipt[key]:
            raise ValueError(f'candidate identity drift: {rel}')
    manifest = json.loads((pack / 'public/manifest.json').read_text())
    future = {r['review_id']: r for r in json.loads((pack / 'review_future_only/manifest.json').read_text())['items']}
    lineage = {r['review_id']: r for r in assisted.read_lines(pack / 'admin/lineage.jsonl')}
    tasks = []
    for item in manifest['items']:
        key = item['review_id']; origin = pack / 'public' / item['image']
        reference = pack / item['future_image']; meta = future[key]
        assisted.old.assert_pre_holdout(assisted.old.instant(meta['review_available_at']), what='candidate context')
        if sha(origin) != lineage[key]['input_copy_sha256'] or sha(reference) != meta['sha256']:
            raise ValueError(f'candidate image changed: {key}')
        base = '/data/local-files/?d=label_studio/' + pack.name + '/'
        image_url = base + 'public/' + item['image']
        tasks.append({'data': {
            'review_id': key, 'protocol_id': 'manual_from_blank_future40_v1',
            'image': image_url, 'future_image': base + item['future_image'],
            'context_image': image_url,
            'image_sha256': sha(origin), 'future_image_sha256': sha(reference),
            'context_image_sha256': sha(origin),
            'caption': '后续 40 根（10 小时）。有形态就选方向画框；没有就点“无目标形态”；不确定可选“拿不准”。',
        }})
    if len(tasks) != 276 or len({t['data']['review_id'] for t in tasks}) != 276:
        raise ValueError('candidate membership changed')
    target = RESULTS / 'candidate_tasks.json'
    write_once(target, tasks)
    return target


def link_pack(pack: Path) -> None:
    """Expose a dedicated read-only-by-convention image pack to local LS."""
    if not pack.is_dir():
        raise ValueError(f'pack is not built: {pack}')
    directory = ROOT / 'reports/label_studio'
    directory.mkdir(parents=True, exist_ok=True)
    link = directory / pack.name
    if link.is_symlink() and link.resolve() == pack.resolve():
        return
    if link.exists() or link.is_symlink():
        raise ValueError(f'refusing to replace existing path: {link}')
    link.symlink_to(Path('../../datasets') / pack.name, target_is_directory=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build-candidates', 'import'])
    parser.add_argument('--which', choices=['full', 'candidates'], default='full')
    args = parser.parse_args()
    if args.command == 'build-candidates':
        print(build_candidates())
        return
    from yoyo.datasets.label_studio_import import import_blank_project
    pack = FULL_PACK if args.which == 'full' else assisted.PACK
    tasks = FULL_PACK / 'tasks.json' if args.which == 'full' else RESULTS / 'candidate_tasks.json'
    link_pack(pack)
    result = import_blank_project(TITLES[args.which], tasks, CONFIG,
                                  RESULTS / f'{args.which}_import_receipt.json')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
