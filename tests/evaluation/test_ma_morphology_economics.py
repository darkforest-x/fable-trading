"""Trading outcomes must not be interpreted as morphology backgrounds."""
from copy import deepcopy
import pytest
from yoyo.evaluation.ma_morphology_economics import economic_events,summarize,matched_comparison


def row():
    return {'event_id':'x','split':'test','direction':'LONG','bar_minutes':15,'quality_score':.5,
        'profit':{'entry_price':100,'risk_price':1,'gross_r':3,'net_r':2.8,'retained':True,'outcome':'TP'}}


def test_score_does_not_use_profit_or_ground_truth_box():
    ledger=[row()]
    pred=[{'event_id':'x','boxes':[{'class_id':0,'confidence':.6},{'class_id':1,'confidence':.9}]}]
    winner=economic_events(ledger,pred,'test')[0]
    loser=deepcopy(ledger)
    loser[0]['profit'].update(gross_r=-1,net_r=-1.2,retained=False,outcome='SL')
    failed=economic_events(loser,pred,'test')[0]
    assert winner['score']==failed['score']==.6
    assert not any(k in failed for k in ('hit','false_positive_box_count','deployed_any'))
    assert failed['net_bp']==pytest.approx(-120)


def test_incomplete_bank_and_changed_cost_rejected():
    with pytest.raises(ValueError,match='Incomplete'):economic_events([row()],[],'test')
    with pytest.raises(ValueError,match='Incomplete'):economic_events([row(),row()],[{'event_id':'x','boxes':[]}],'test')
    bad=row();bad['profit']['net_r']=2.9
    with pytest.raises(ValueError,match='Cost contract'):economic_events([bad],[{'event_id':'x','boxes':[]}],'test')


def test_constant_scores_are_not_claimed_as_informative_ranking():
    events=economic_events([row()],[{'event_id':'x','boxes':[]}],'test')
    result=summarize(events,'score')
    assert result['ranking_permutation']['status']=='not_identifiable_constant_score'
    assert result['top10']['events']==1
    assert 'detection' not in result


def test_matched_control_adapter_retains_shortages_without_deployment_claim():
    ledger=[row()]
    events=economic_events(ledger,[{'event_id':'x','boxes':[{'class_id':0,'confidence':.7}]}],'test')
    selection={'desired_per_event':5,'shortages':[{'matched_event_id':'x','requested':5,'selected':0,'shortage':5,'reason':'no_safe_control'}]}
    result=matched_comparison(events,ledger,[],[],selection,'test')
    overall=result['overall']
    assert overall['all']['paired_events']==0
    assert overall['all']['unmatched_events']==1
    assert overall['rule_direction_score_at_least_025']['requested_events']==1
    assert 'triggered_candidate_direction' not in overall
    assert 'same_direction_deployed' not in events[0]
