"""Reject unrecognized recovery failures and preserve complete stream lineage."""
import json
import pandas as pd
import pytest

from yoyo.evaluation.spike_v131_rule_recover import EXPECTED_ERROR,recovery_symbols,compose
from yoyo.evaluation.spike_v131_excursions import provenance


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


def test_recovery_refuses_other_failure_causes(tmp_path):
    write(tmp_path/'receipt.json',dict(complete=False,errors=[dict(symbol='X',error='AssertionError(raw parity)')]))
    with pytest.raises(AssertionError):recovery_symbols(tmp_path)


def test_compose_requires_exact_failed_subset_and_retains_per_stream_source(tmp_path):
    primary=tmp_path/'primary';repair=tmp_path/'repair';source=tmp_path/'market.csv';source.write_text('input')
    common=dict(config=dict(timeframes=[15]),manifest={},metadata={},btc={})
    pi=common|dict(symbols=['A','B'],subset=False,builder_commit='724682b4f8-original')
    ri=common|dict(symbols=['B'],subset=True,builder_commit='repair-builder')
    write(primary/'identity.json',pi);write(repair/'identity.json',ri)
    receipts={}
    for root,symbol in [(primary,'A'),(repair,'B')]:
        folder=root/'streams'/symbol;folder.mkdir(parents=True)
        pd.DataFrame(dict(cohort=['signal'])).to_csv(folder/'15m.csv.gz',index=False)
        r=dict(symbol=symbol,input=provenance(source),detectors_and_features_recomputed=True,
               streams=[dict(timeframe_min=15,rows=1)])
        write(folder/'receipt.json',r);receipts[symbol]=r
    write(primary/'receipt.json',dict(complete=False,errors=[dict(symbol='B',error=EXPECTED_ERROR)],receipts=[receipts['A']]))
    write(repair/'receipt.json',dict(complete=True,errors=[],receipts=[receipts['B']]))
    bad=ri|dict(symbols=['A']);write(repair/'identity.json',bad)
    with pytest.raises(AssertionError):compose(primary,repair,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()
    write(repair/'identity.json',ri);compose(primary,repair,tmp_path/'final')
    final=json.loads((tmp_path/'final/receipt.json').read_text())
    assert final['complete'] and len(final['receipts'])==2
    assert final['receipts'][0]['generated_in']==str(primary/'streams/A')
    assert final['receipts'][1]['generation_commit']=='repair-builder'
    assert not json.loads((primary/'receipt.json').read_text())['complete']
