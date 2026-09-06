"""Saved-only V21 descriptives using the statistical-analysis skill utility.

Run with existing system Python, not main strategy dependencies. No fitting,
outlier removal, new market I/O, alternative p-value selection or repricing.
Shapiro statistics are descriptive only: serial/reused opportunities are not
IID; primary inference remains the frozen calendar-month block procedure.
"""
from pathlib import Path
import argparse
import hashlib
import json
import runpy
import subprocess

import numpy as np
import pandas as pd
import scipy

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'experiments/active/exp-btcusdtp-1h-external-breadth-preholdout-20260906-v21'
UTILITY = Path('/Users/zhangzc/.codex/skills/statistical-analysis/scripts/assumption_checks.py')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=E/'results/statistical_diagnostics.json')
    args = parser.parse_args()
    relative = Path(__file__).resolve().relative_to(ROOT).as_posix()
    if subprocess.check_output(['git','show','HEAD:'+relative], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError('Commit diagnostics builder before generating evidence')
    if args.out.exists():
        raise ValueError('Preserve earlier diagnostics')
    summary = json.loads((E/'results/summary.json').read_text())
    if not summary['outcomes_read']:
        raise ValueError('No diagnostic outcome access before support pass')
    utility = runpy.run_path(str(UTILITY))
    result = {'utility_sha256': hashlib.sha256(UTILITY.read_bytes()).hexdigest(),
        'versions': {'numpy': np.__version__, 'pandas': pd.__version__, 'scipy': scipy.__version__},
        'generated_at': pd.Timestamp.now(tz='UTC').isoformat(), 'outliers_removed': 0,
        'primary_inference_changed': False, 'iid_assumption_claimed': False, 'groups': {}}
    for name in ('case_delta', 'excess_delta'):
        path = E/'results'/(name+'.csv.gz')
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != summary['output_hashes'][path.name]:
            raise ValueError('Saved outcome bytes changed')
        frame = pd.read_csv(path)
        x = frame.difference.dropna().to_numpy(float)*1e4
        normal = utility['check_normality'](x, name=name, plot=False)
        outliers = utility['detect_outliers'](x, name=name, plot=False)
        result['groups'][name] = {'input_sha256': sha, 'total': len(frame), 'n': len(x),
            'unknown': int(frame.difference.isna().sum()), 'mean_bp': float(x.mean()),
            'median_bp': float(np.median(x)), 'sd_bp': float(np.std(x, ddof=1)),
            'q1_bp': float(np.quantile(x,.25)), 'q3_bp': float(np.quantile(x,.75)),
            'min_bp': float(x.min()), 'max_bp': float(x.max()),
            'shapiro_W': float(normal['statistic']), 'shapiro_p_descriptive': float(normal['p_value']),
            'iqr_outliers_n_all_retained': int(outliers['n_outliers'])}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
