"""Bind the ETH 3m BE research delivery without rerunning historical prices.

Inputs are a completed study output and the accompanying human-reviewed report.
This receipt proves local artifact identity, not production eligibility.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    own = Path(__file__).resolve().relative_to(Path.cwd().resolve())
    subprocess.run(['git', 'cat-file', '-e', f'HEAD:{own}'], check=True)
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', str(own)], check=True)
    meta = json.loads((args.results / 'manifest.json').read_text())
    assert meta['complete'] and meta['status'] == 'complete'
    for name, value in meta['output_sha256'].items():
        assert sha(args.results / name) == value, name
    exp = Path('experiments/active/exp-spike-v8-eth3m-be-20260913-v1')
    paths = [own, Path('yoyo/evaluation/spike_v8_eth3m_be_study.py'),
             Path('tests/evaluation/test_spike_v8_eth3m_be_study.py'),
             Path('analysis/p1_spike_v8_eth3m_be_20260913.md'),
             Path('analysis/html/p1_spike_v8_eth3m_be_20260913.html'),
             Path('docs/learnings/breakeven-fill-counts-need-protection-level-evidence.md'),
             exp / 'config.json', exp / 'PROJECT_PLAN.md', exp / 'holdout_usage.json']
    paths += sorted(p for p in args.results.rglob('*') if p.is_file())
    if 'reused_failed_run' in meta:
        failed = Path(meta['reused_failed_run'])
        assert sha(failed / 'manifest.json') == meta['reused_failed_manifest_sha256']
        paths += [failed / 'manifest.json', failed / 'holdout_read_receipt.json']
    receipt = {'complete': True, 'created_at': datetime.now(timezone.utc).isoformat(),
               'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
               'study_source_commit': meta['source_commit'], 'results': str(args.results),
               'production_eligible': False, 'training_eligible': False,
               'scope': 'ETH 3m only; fixed paired exits and separate serial replay; reused nonblind history',
               'files': [{'path': str(p), 'size_bytes': p.stat().st_size, 'sha256': sha(p)} for p in paths]}
    (exp / 'delivery_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
