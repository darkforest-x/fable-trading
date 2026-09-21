"""Re-score immutable V4 and V5 serial paths at both strict net-R targets.

This is retrospective evaluation only. It never changes scores, admissions,
trade paths, R denominators, costs or market inputs. Input receipts and leaf
hashes are checked before use; V4/V5 have identical execution clocks and arms.
The two-by-two design separates easier success criteria from new model value.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import pandas as pd
from pandas.testing import assert_frame_equal

from yoyo.evaluation import spike_10r_model_study as old
from yoyo.evaluation import spike_5r_model_study as new
from yoyo.evaluation import spike_10r_search as s
from yoyo.evaluation.spike_v8_six_filters import _committed


def load_serial(folder):
    receipt = new.verify_output(folder, 'receipt.json')
    pins = receipt['stream_receipts']
    if len(pins) != 3531:
        raise ValueError('incomplete serial coverage')
    for key, sha in pins.items():
        root = folder/'streams'/key
        if s.digest(root/'completion.json') != sha:
            raise ValueError('serial completion drift')
        leaf = json.loads((root/'completion.json').read_text())
        for name, expected in leaf['files'].items():
            if s.digest(root/name) != expected:
                raise ValueError('serial leaf drift')
    frame = pd.read_csv(folder/'trades.csv.gz')
    for col in ('entry_time', 'exit_time', 'available_at'):
        frame[col] = pd.to_datetime(frame[col], utc=True)
    for col in ('valid_entry', 'censored'):
        frame[col] = new.strict_bool(frame[col])
    return frame


def assert_control_parity(previous, current):
    """Target relabelling cannot alter non-model execution or economics."""
    controls = ['original_all', 'prior_v21_risk_decile',
                *[new.policy_name('risk', q) for q in new.COVERAGES]]
    frames = [f.loc[f.rule.isin(controls)].sort_values(['rule', 'event_key']).reset_index(drop=True)
              for f in (previous, current)]
    assert_frame_equal(*frames, check_exact=True)


def run(dataset, output):
    deps = list(dict.fromkeys(new.dependencies()+old.dependencies()+[Path(__file__),
        Path('tests/evaluation/test_spike_5r_target_comparison.py')]))
    if not _committed(deps):
        raise ValueError('commit comparison builder before analysis')
    if output.exists():
        raise ValueError('refuse to overwrite target comparison')
    _, controls, _ = s.load_candidates(dataset)
    rows, groups, pins, frames = [], [], {}, []
    for trained_target, exp in ((10, old.EXP), (5, new.EXP)):
        folder = exp/'serial_v1'
        frame = load_serial(folder)
        frames.append(frame)
        pins[str(folder/'receipt.json')] = s.digest(folder/'receipt.json')
        for evaluation_target, study in ((5, new), (10, old)):
            table = study.comparison(frame, controls, None, serial_mode=True)
            table['trained_target_r'] = trained_target
            table['evaluation_target_r'] = evaluation_target
            rows.append(table)
        part = frame.loc[frame.available_at.ge(new.START) & frame.valid_entry & ~frame.censored].copy()
        part['month'] = part.available_at.dt.strftime('%Y-%m')
        for dimension in ('month', 'timeframe_min', 'venue', 'asset'):
            for (rule, value), g in part.groupby(['rule', dimension]):
                groups.append(dict(trained_target_r=trained_target, rule=rule,
                    dimension=dimension, value=str(value), closed=len(g),
                    gt5=int(g.net_r.gt(5).sum()), gt10=int(g.net_r.gt(10).sum()),
                    win_rate=float(g.net_r.gt(0).mean()),
                    mean_net_bp=float(g.net_return.mean()*1e4)))
    assert_control_parity(*frames)
    output.mkdir(parents=True)
    pd.concat(rows, ignore_index=True).to_csv(output/'target_comparison.csv', index=False)
    pd.DataFrame(groups).to_csv(output/'serial_groups.csv', index=False)
    new.receipt_files(output, 'receipt.json', dict(input_receipts=pins,
        dependencies={str(p):s.digest(p) for p in deps},
        dataset_receipt_sha256=s.digest(dataset/'receipt.json'),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(), nonmodel_path_parity=True,
        production_eligible=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.dataset, args.output)
