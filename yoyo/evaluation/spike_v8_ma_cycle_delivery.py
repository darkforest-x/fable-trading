"""Bind the completed MA-cycle report, charts and diagnostics to local bytes.

Large CSV, PNG and embedded HTML files remain local. This manifest is the small
versioned receipt; it does not certify profitability or eligibility for trading.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

EXP = Path('experiments/active/exp-spike-v8-ma-cycle-20260913-v1')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    source = Path(__file__).resolve().relative_to(Path.cwd().resolve())
    if (subprocess.run(['git','cat-file','-e',f'HEAD:{source}'],capture_output=True).returncode
        or subprocess.run(['git','diff','--quiet','HEAD','--',str(source)]).returncode):
        raise ValueError('Commit delivery builder first')
    raw = EXP/'results/full_v1'
    manifest = json.loads((raw/'manifest.json').read_text())
    assert manifest['complete'] and manifest['official']
    assert manifest['events'] == 113295 and manifest['scoring_closed'] == 94180
    assert manifest['streams_scanned'] == 3531
    for name, expected in json.loads((raw/'receipt.json').read_text()).items():
        assert sha(raw/name) == expected, name
    for folder in ('summary_v2','charts_v2'):
        meta = json.loads((EXP/'results'/folder/'manifest.json').read_text())
        for name, expected in meta.get('files',meta.get('charts',{})).items():
            assert sha(EXP/'results'/folder/name) == expected, name
    report = Path('analysis/p1_spike_v8_ma_cycle_20260913.md')
    html = Path('analysis/html/p1_spike_v8_ma_cycle_20260913.html')
    assert report.is_file() and html.is_file()
    paths = [report,html,source,
             Path('yoyo/evaluation/spike_v8_ma_cycle_study.py'),
             Path('yoyo/evaluation/spike_v8_ma_cycle_summary.py'),
             Path('yoyo/evaluation/spike_v8_ma_cycle_charts.py'),
             Path('tests/evaluation/test_spike_v8_ma_cycle_study.py'),
             Path('docs/learnings/market-phase-labels-and-trade-references-have-separate-clocks.md'),
             Path('docs/learnings/state-machine-progress-is-not-market-phase-ground-truth.md'),
             EXP/'config.json',EXP/'PROJECT_PLAN.md',EXP/'holdout_receipt.json']
    paths += sorted(p for p in (EXP/'results').rglob('*') if p.is_file())
    receipt = dict(created_at=datetime.now(timezone.utc).isoformat(),complete=True,
                   source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                   data_use='authorized configuration-specific holdout-era use 1; reused nonblind history',
                   streams=3531,events=113295,scoring_closed=94180,
                   training_eligible=False,production_eligible=False,
                   scope='Fixed causal state diagnostic and independent same-entry comparisons; no changed V8 execution or account',
                   files=[dict(path=str(p),size_bytes=p.stat().st_size,sha256=sha(p)) for p in paths])
    (EXP/'delivery_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    main()
