"""PEPE 1H software regression, without fitting, future labels or PnL.

Read one SHA-authenticated SPIKE feature artifact. V4 consumes only its causal
OHLCV, 20/60/120 averages, MD/SB and prior rolling statistics. V5 consumes each
closed bar and the frozen V4 parent. Compare the broken committed gate with the
repair, retain source-event provenance, and test native TradingView observations.
Dates select report rows only; they never select or change detector parameters.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_dataset import load_feature
from yoyo.evaluation.spike_burst_early_warning import detect as legacy_detect
from yoyo.evaluation.spike_burst_v5_structure import detect

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/active/exp-spike-v5-pepe-1h-repair-20260910-v1'
SOURCE = ROOT / 'experiments/active/exp-spike-burst-validation-20260910-v1/results/features/75d10ad8568817c52dd030fd1e6a.pkl.gz'
SOURCE_SHA = '031d3e57bb6081002e3ce4484e999b90ca7759322e6e78da8d9d61173248d07a'
BROKEN_COMMIT = 'b8bf1aa2b6a025e0a216a0d859661118c4438d22'
GATE = 'yoyo/evaluation/spike_burst_v5_structure.py'
PINE = 'yoyo/evaluation/pine/spike_burst_v5.pine'
BUILDER = 'yoyo/evaluation/spike_v5_pepe_regression.py'
PLAN = str((EXPERIMENT / 'PROJECT_PLAN.md').relative_to(ROOT))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_old_gate():
    source = subprocess.check_output(['git', 'show', BROKEN_COMMIT + ':' + GATE], cwd=ROOT)
    module = types.ModuleType('_spike_v5_broken_frozen')
    sys.modules[module.__name__] = module
    exec(compile(source, BROKEN_COMMIT + ':' + GATE, 'exec'), module.__dict__)
    return module.detect


def main():
    pins = {}
    for rel in (GATE, PINE, BUILDER, PLAN,
                'yoyo/evaluation/spike_burst_early_warning.py',
                'yoyo/evaluation/spike_burst_progressive.py'):
        committed = subprocess.check_output(['git', 'show', 'HEAD:' + rel], cwd=ROOT)
        if committed != (ROOT / rel).read_bytes():
            raise ValueError('Commit exact builder/implementation/plan before replay: ' + rel)
        pins[rel] = sha(ROOT / rel)
    frame = load_feature(SOURCE, SOURCE_SHA, 60, pd.Timestamp('2026-09-09T00:00Z'))
    old = legacy_detect(frame)
    supplied = frame[['open', 'high', 'low', 'close', 'md', 'sb', 'atr', 'ropeHigh', 'ready']].copy()
    supplied['legacy_confirmed'] = old.confirmed.astype(bool)
    supplied['legacy_parent_high'] = old.frozen_parent_high
    supplied['legacy_parent_low'] = [
        float(old.prog_prior_low.iloc[int(parent)]) if np.isfinite(parent) else np.nan
        for parent in old.parent_i
    ]
    supplied['data_gap'] = False
    supplied['confirmed'] = True
    broken = frozen_old_gate()(supplied)
    fixed = detect(supplied)
    out = supplied.copy()
    out['bar_open_beijing'] = frame.index.tz_convert('Asia/Shanghai').astype(str)
    out['bar_close_beijing'] = (frame.index + pd.Timedelta(hours=1)).tz_convert('Asia/Shanghai').astype(str)
    out['full_body_above_six'] = frame[['open', 'close']].min(axis=1).gt(frame.ropeHigh)
    out['md_rising'] = frame.md.gt(frame.md.shift())
    out['broken_v5'] = broken.confirmed
    out['repaired_v5'] = fixed.confirmed
    out['legacy_i'] = fixed.legacy_i
    out['gate_reason'] = fixed.why_pending
    for col in ('rv', 'expansion', 's20', 'e20', 's60', 'e60', 's120', 'e120'):
        out[col] = frame[col]
    out['dense_hits'] = old.prog_dense_hits

    # Native TV snapshots were read at these exact 1H bar-open timestamps.
    probes = [
        ('2026-08-19T12:00Z', [2.600e-6, 2.628e-6, 2.600e-6, 2.616e-6], True, False, 8),
        ('2026-08-19T15:00Z', [2.650e-6, 2.765e-6, 2.642e-6, 2.691e-6], False, True, 11),
        ('2026-08-19T16:00Z', [2.691e-6, 2.727e-6, 2.685e-6, 2.714e-6], False, True, 12),
    ]
    checks = []
    for stamp, prices, legacy, body, density in probes:
        row = out.loc[pd.Timestamp(stamp)]
        checks.append(dict(bar_open_utc=stamp,
            ohlc=bool(np.allclose(row[['open','high','low','close']].to_numpy(dtype=float), prices, rtol=0, atol=1e-15)),
            native_legacy=bool(row.legacy_confirmed) == legacy,
            native_body=bool(row.full_body_above_six) == body,
            native_density=float(row.dense_hits) == density))
    if not all(all(v for k, v in check.items() if k != 'bar_open_utc') for check in checks):
        raise AssertionError('Native TV regression mismatch: ' + json.dumps(checks))

    start, stop = pd.Timestamp('2026-08-14T00:00Z'), pd.Timestamp('2026-09-09T00:00Z')
    reviewed = out.loc[(out.index >= start) & (out.index < stop)]
    final = reviewed.loc[reviewed.repaired_v5].copy()
    linked = []
    for stamp, row in final.iterrows():
        origin_i = int(row.legacy_i)
        origin = out.iloc[origin_i]
        if not bool(origin.legacy_confirmed) or out.index[origin_i] > stamp:
            raise AssertionError('Final event lacks causal V4 provenance')
        linked.append(dict(final_open_beijing=row.bar_open_beijing,
            final_close_beijing=row.bar_close_beijing,
            legacy_open_beijing=origin.bar_open_beijing,
            wait_bars=int((stamp - out.index[origin_i]) / pd.Timedelta(hours=1)),
            close=float(row.close)))

    # Required positive regression: preserve the original Aug19 candidate and
    # confirm at the actual first completion, not its historical candidate bar.
    required = out.loc[pd.Timestamp('2026-08-19T16:00Z')]
    assert bool(required.repaired_v5) and not bool(required.broken_v5)
    assert out.index[int(required.legacy_i)] == pd.Timestamp('2026-08-19T12:00Z')
    assert not bool(out.loc[pd.Timestamp('2026-08-19T12:00Z')].repaired_v5)
    prefix = supplied.loc[:pd.Timestamp('2026-08-19T16:00Z')]
    pd.testing.assert_frame_equal(detect(prefix), fixed.loc[prefix.index])

    output = EXPERIMENT / 'results'
    output.mkdir(parents=True, exist_ok=True)
    trace = output / 'pepe_1h_trace.csv.gz'
    reviewed.to_csv(trace, index_label='bar_open_utc', compression='gzip')
    receipt = dict(schema='spike-v5-pepe-1h-repair-regression-v1',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_path=str(SOURCE.relative_to(ROOT)), source_sha256=SOURCE_SHA, source_pins=pins,
        source_rows=len(frame), source_first=str(frame.index[0]), source_last=str(frame.index[-1]),
        evaluated_bar_open_utc=[str(start), str(stop)], evaluated_bars=len(reviewed),
        counts=dict(v4=int(reviewed.legacy_confirmed.sum()), broken_v5=int(reviewed.broken_v5.sum()),
                    repaired_v5=int(reviewed.repaired_v5.sum()),
                    same_bar=sum(r['wait_bars']==0 for r in linked), delayed=sum(r['wait_bars']>0 for r in linked)),
        native_probes=checks, final_events=linked, prefix_passed=True,
        trace_sha256=sha(trace), repair_configuration=2, holdout_consumption=1, economic_evaluation=False,
        limitations='Known-history software regression; not false-positive accuracy, profitability or blind OOS.')
    (output / 'regression.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: receipt[k] for k in ('counts','native_probes','prefix_passed')},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
