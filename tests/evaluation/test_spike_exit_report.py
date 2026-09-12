"""Report checks preserve clustered units and fail without complete artifacts."""
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.spike_exit_report import grouped_effect,build

def test_same_policy_cluster_null_and_asset_concentration(tmp_path):
    a=pd.DataFrame([dict(stream_key=f'{v}-{asset}',cohort='v6_both',policy='baseline',timeframe_min=60,risk_fraction=.01,notional_cap='1.0',period='validation',valid=True,asset=asset,net_return=ret) for v in ['okx','gate'] for asset,ret in [('A',.2),('B',-.1)]])
    selection=pd.DataFrame([dict(timeframe_min=60,cohort='v6_both',policy='baseline')])
    row=grouped_effect(a,selection,tmp_path).iloc[0]
    assert row.paired_streams==4 and row.asset_clusters==2
    assert row.paired_mean==0 and row.ci_low==0 and row.ci_high==0
    assert row.permutation_p==1 and row.bh_q==1
    assert row.top_positive_asset=='A' and row.top_positive_share==1
    assert row.return_mean_excluding_top_asset==pytest.approx(-.1)

def test_incomplete_artifacts_cannot_generate_final_report(tmp_path):
    (tmp_path/'post_manifest.json').write_text('{"complete":false,"streams":3}')
    (tmp_path/'engine_manifest.json').write_text('{"complete":false}')
    with pytest.raises(ValueError,match='complete scope'):
        build(tmp_path,tmp_path,tmp_path/'must-not-exist.md')
    assert not (tmp_path/'must-not-exist.md').exists()
