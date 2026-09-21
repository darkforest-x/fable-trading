"""Unblind frozen visual judgments; no model fitting or trading simulation.

Inputs are the committed causal chart pack, independently frozen visual labels,
and retrospective outcomes. Outcomes only join after label validation. P/D/B/N
are research interpretations, not owner-confirmed Gold. Matched case-control
frequencies cannot estimate the prospective probability of achieving 10R.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from yoyo.evaluation.spike_v8_six_filters import _committed

CLASSES = ('P', 'D', 'B', 'N', 'U')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_labels(path, receipt_path, expected_ids):
    receipt = json.loads(Path(receipt_path).read_text())
    recorded_hash = receipt.get('sha256', receipt.get('csv_sha256'))
    if recorded_hash != digest(path) or receipt['private_outcomes_read'] is not False:
        raise ValueError('labels are not frozen before outcome access')
    labels = pd.read_csv(path)
    if (labels.visual_id.duplicated().any() or set(labels.visual_id) != set(expected_ids)
            or len(labels) != receipt['rows'] or not labels['class'].isin(CLASSES).all()
            or labels.reason.isna().any()):
        raise ValueError('incomplete or invalid visual labels')
    return labels, receipt


def agreement(left, right):
    left, right = pd.Series(list(left)), pd.Series(list(right))
    if not len(left) or len(left) != len(right):
        raise ValueError('agreement requires equal nonempty paired labels')
    observed = float(left.eq(right).mean())
    chance = sum(float(left.eq(c).mean()*right.eq(c).mean()) for c in CLASSES)
    return dict(n=len(left), exact_agreement=observed,
                kappa=(observed-chance)/(1-chance) if chance < 1 else None,
                strict_p_agreement=float(left.eq('P').eq(right.eq('P')).mean()),
                broad_pd_agreement=float(left.isin(['P','D']).eq(right.isin(['P','D'])).mean()))


def summarize(frame, group_cols):
    rows = []
    for key, group in frame.groupby(group_cols, dropna=False):
        keys = key if isinstance(key, tuple) else (key,)
        row = dict(zip(group_cols, keys))
        row.update(n=len(group), **{c: int(group['class'].eq(c).sum()) for c in CLASSES})
        row.update(strict_p_rate=row['P']/row['n'], broad_pd_rate=(row['P']+row['D'])/row['n'])
        rows.append(row)
    return pd.DataFrame(rows)


def paired_comparison(unique):
    winners = unique.loc[unique.cohort.eq('winner_gt10')]
    controls = unique.loc[unique.cohort.eq('matched_nonwinner')]
    pairs = winners.merge(controls, on='pair_id', suffixes=('_winner','_control'), validate='one_to_one')
    if not pairs.asset_winner.eq(pairs.asset_control).all():
        raise ValueError('matched assets differ')
    rows = []
    for name, classes in [('strict_p', ['P']), ('broad_pd', ['P','D'])]:
        a, b = pairs.class_winner.isin(classes), pairs.class_control.isin(classes)
        win_only, control_only = int((a & ~b).sum()), int((~a & b).sum())
        discordant = win_only+control_only
        delta = a.astype(int)-b.astype(int)
        blocks = delta.groupby(pairs.asset_winner).sum().to_numpy(float)
        rng = np.random.default_rng(921603)
        null = rng.choice([-1,1], size=(20000,len(blocks))) @ blocks
        block_p = (1+int((np.abs(null)>=abs(float(blocks.sum()))).sum()))/(len(null)+1)
        rows.append(dict(metric=name, pairs=len(pairs), winner_hits=int(a.sum()),
                         control_hits=int(b.sum()), difference_pp=100*float(delta.mean()),
                         winner_only=win_only, control_only=control_only,
                         exact_discordant_p=float(binomtest(win_only,discordant).pvalue) if discordant else 1.,
                         asset_block_signflip_p=block_p, asset_blocks=len(blocks)))
    return pd.DataFrame(rows), pairs


def run(pack, review, output):
    if not _committed([Path(__file__), Path('tests/evaluation/test_spike_10r_visual_report.py')]):
        raise ValueError('commit analyzer and tests before unblinding')
    if output.exists():
        raise ValueError('refuse to overwrite unblinding result')
    # Validate all labels and timestamp receipts before reading private outcomes.
    ids = lambda start, end: [f'V{i:04d}' for i in range(start,end+1)]
    astra, af = frozen_labels(review/'astra_labels.csv', review/'astra_freeze.json', ids(1,400))
    luna, lf = frozen_labels(review/'luna_labels.csv', review/'luna_freeze.json', ids(1,16)+ids(401,805))
    plan = json.loads((review/'overlap_plan.json').read_text())
    extra_ids = [f'V{i:04d}' for n in plan['extra_astra_overlap_sheets'] for i in range((n-1)*8+1,n*8+1)]
    overlap, of = frozen_labels(review/'astra_overlap.csv', review/'astra_overlap_freeze.json', extra_ids)
    receipt = json.loads((pack/'receipt.json').read_text())
    for relative, sha in receipt['files'].items():
        if digest(pack/relative) != sha:
            raise ValueError(f'chart pack drift: {relative}')
    now = pd.Timestamp.now(tz='UTC')
    if any(pd.Timestamp(r['frozen_at'])>=now for r in [af,lf,of]):
        raise ValueError('freeze timestamp must precede unblinding')
    primary = pd.concat([astra,luna.loc[~luna.visual_id.isin(ids(1,16))]], ignore_index=True)
    private = pd.read_csv(pack/'private.csv')
    if set(private.visual_id) != set(primary.visual_id):
        raise ValueError('private exposure coverage differs')
    exposed = private.merge(primary,on='visual_id',validate='one_to_one')
    if exposed['repeat'].dtype != bool:
        raise ValueError('repeat flag must be boolean')
    unique = exposed.loc[~exposed['repeat']].copy()
    if unique.event_key.duplicated().any() or len(unique)!=757:
        raise ValueError('unexpected unique candidate coverage')
    if unique.cohort.eq('winner_gt10').sum()!=448 or unique.cohort.eq('matched_nonwinner').sum()!=309:
        raise ValueError('unexpected cohort counts')
    shared = pd.concat([astra.loc[astra.visual_id.isin(ids(1,16))],overlap])
    shared = shared.merge(luna,on='visual_id',suffixes=('_astra','_luna'),validate='one_to_one')
    repeats = exposed.loc[exposed['repeat']].merge(unique,on='event_key',suffixes=('_repeat','_original'),validate='one_to_one')
    repeats['same_reader'] = repeats.reviewer_repeat.eq(repeats.reviewer_original)
    paired, pairs = paired_comparison(unique)
    episode = unique.assign(strict=unique['class'].eq('P'),broad=unique['class'].isin(['P','D'])).groupby(['cohort','episode_id']).agg(n=('visual_id','size'),strict=('strict','mean'),broad=('broad','mean')).reset_index()
    unique['card_path'] = unique.visual_id.map(lambda v: str((pack/'blind/cards'/f'{v}.png').resolve()))
    summary = dict(unblinded_at=now.isoformat(), source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        pack_receipt_sha256=digest(pack/'receipt.json'),label_freezes=dict(astra=af,luna=lf,astra_overlap=of),
        unique_events=len(unique),exposures=len(exposed),cohort_counts=unique.cohort.value_counts().to_dict(),
        time_start=str(unique.available_at.min()),time_end=str(unique.available_at.max()),
        overlap=agreement(shared.class_astra,shared.class_luna),
        hidden_repeats=agreement(repeats.class_original,repeats.class_repeat),
        hidden_repeats_by_reader={str(k):agreement(g.class_original,g.class_repeat) for k,g in repeats.groupby('same_reader')},
        assets_by_cohort=unique.groupby('cohort').asset.nunique().to_dict(),
        episodes=episode.groupby('cohort').agg(episodes=('episode_id','size'),event_count=('n','sum'),equal_episode_strict_rate=('strict','mean'),equal_episode_broad_rate=('broad','mean')).reset_index().to_dict('records'),
        statistical_scope='Exploratory retrospective visual association; outcome sampling and correlated events do not establish prospective prediction or trading edge.',
        training_eligible=False,production_eligible=False)
    output.mkdir(parents=True)
    tables = {'cohort':summarize(unique,['cohort']), 'reader':summarize(unique,['reviewer','cohort']),
              'timeframe':summarize(unique,['timeframe_min','cohort']), 'paired':paired,
              'quarter':summarize(unique,['quarter','cohort']),
              'overlap_labels':shared,'hidden_repeats':repeats,'episodes':episode,'all_candidates':unique}
    for name, table in tables.items():
        table.to_csv(output/f'{name}.csv',index=False)
    summary['files']={str(p.name):digest(p) for p in output.glob('*.csv')}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print(tables['cohort'].to_string(index=False))
    print(paired.to_string(index=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',type=Path,required=True)
    parser.add_argument('--review',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    run(args.pack,args.review,args.output)
