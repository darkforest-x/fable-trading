"""Hash the canonical asset-ranking delivery; do not recalculate trade results."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path('experiments/active/exp-spike-v1-v8-asset-ranking-20260914-v1')


def main() -> None:
    files = []
    for directory in ['results/full_v1', 'interpretation/full_v2', 'exclusion']:
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file())
    files.extend(ROOT/p for p in ['config.json', 'PROJECT_PLAN.md', 'holdout_receipt.json', 'traits_plan.md', 'leaderboard_receipt.json'])
    files.extend(Path(p) for p in [
        'analysis/p1_spike_v1_v8_asset_ranking_20260914.md',
        'analysis/html/p1_spike_v1_v8_asset_ranking_20260914.html',
        'analysis/html/p1_spike_v1_v8_asset_leaderboard_20260914.html',
        'yoyo/evaluation/spike_v1_v8_asset_ranking.py',
        'yoyo/evaluation/spike_asset_exclusion_study.py',
        'yoyo/evaluation/spike_asset_rank_interpretation.py',
        'yoyo/evaluation/spike_asset_ranking_view.py',
        'yoyo/evaluation/spike_asset_rank_delivery.py',
        'tests/evaluation/test_spike_v1_v8_asset_ranking.py',
        'tests/evaluation/test_spike_asset_exclusion_study.py',
    ])
    ledger = ROOT/'results/full_v1/normalized_ledger.csv.gz'
    if hashlib.sha256(ledger.read_bytes()).hexdigest() != 'ec1a0793794ae196a7505371a2a4920aaa652be5950dd621ae4a0a0a0322e826':
        raise ValueError('Canonical normalized ledger changed')
    receipts = []
    for file in sorted(set(files)):
        receipts.append({'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'bytes': file.stat().st_size})
    (ROOT/'delivery_receipt.json').write_text(json.dumps({
        'experiment_id': ROOT.name, 'generator_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'canonical_interpretation': 'interpretation/full_v2', 'holdout': 'owner-authorized reused history; nonblind',
        'no_live_changes': True, 'browser_visual_verification': False,
        'browser_limitation': 'Local-file navigation blocked by browser security policy; data and JavaScript syntax checked offline.',
        'files': receipts}, indent=2))
    print(f'Canonical delivery: {len(receipts)} files')


if __name__ == '__main__':
    main()
