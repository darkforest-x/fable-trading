"""Checks for frozen-label integrity and matched visual comparisons."""
import json

import pandas as pd
import pytest

from yoyo.evaluation.spike_10r_visual_report import agreement, digest, frozen_labels, paired_comparison


def test_frozen_labels_reject_edits(tmp_path):
    path = tmp_path/'labels.csv'
    path.write_text('visual_id,class,reason,reviewer\nV0001,P,Compact,astra\n')
    receipt = tmp_path/'freeze.json'
    receipt.write_text(json.dumps(dict(sha256=digest(path),private_outcomes_read=False,rows=1)))
    frozen_labels(path,receipt,['V0001'])
    path.write_text(path.read_text().replace(',P,',',N,'))
    with pytest.raises(ValueError,match='frozen'):
        frozen_labels(path,receipt,['V0001'])


def test_unmatched_winners_do_not_enter_paired_comparison():
    rows = [dict(pair_id='a',asset='X',cohort='winner_gt10',**{'class':'P'}),
            dict(pair_id='a',asset='X',cohort='matched_nonwinner',**{'class':'N'}),
            dict(pair_id='b',asset='Y',cohort='winner_gt10',**{'class':'P'})]
    result, pairs = paired_comparison(pd.DataFrame(rows))
    assert len(pairs)==1
    assert result.iloc[0].winner_hits==1 and result.iloc[0].control_hits==0
    assert result.iloc[0].exact_discordant_p==1


def test_agreement_exposes_borderline_disagreement():
    result = agreement(['P','D','B','N'],['P','B','D','N'])
    assert result['exact_agreement']==.5
    assert result['broad_pd_agreement']==.5
    assert result['strict_p_agreement']==1
    assert result['kappa']==pytest.approx(1/3)
