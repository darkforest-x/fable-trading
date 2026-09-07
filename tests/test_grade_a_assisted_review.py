"""Evidence gates for future-only review and assisted-answer semantics."""
from datetime import datetime, timezone
from types import SimpleNamespace
import json
import pytest
from yoyo.datasets import grade_a_assisted_review as a


def tf(n=18,lo=100,hi=120):
    return SimpleNamespace(width=1280,height=742,left=12,top=12,plot_w=1256,plot_h=718,n_bars=n,price_min=lo,price_max=hi)


def test_box_transport_preserves_bar_and_price_coordinates():
    source,target=tf(),tf(58,80,160)
    box=[.12,.2,.4,.65];result=a.transport_box(box,source,target)
    for idx in (0,2):
        oldbar=(box[idx]*source.width-source.left)/source.plot_w*17
        newbar=(result[idx]*target.width-target.left)/target.plot_w*57
        assert newbar==pytest.approx(oldbar)
    for idx in (1,3):
        oldprice=source.price_max-(box[idx]*source.height-source.top)/source.plot_h*20
        newprice=target.price_max-(result[idx]*target.height-target.top)/target.plot_h*80
        assert newprice==pytest.approx(oldprice)
    assert a.transport_box(box,source,source)==pytest.approx(box)


@pytest.mark.parametrize('box',[[0,0,0,1],[0,.8,.5,.2],[0,0,1.01,1],[0,float('nan'),1,1],[True,0,1,1],None])
def test_invalid_box_not_silently_clipped(box):
    with pytest.raises(ValueError):a.validate_box(box)


def test_future_guard_checks_end_and_close_before_source_loading():
    good={'window_start_time':'2025-10-24T03:15:00Z','window_end_time':'2025-10-24T07:30:00Z','source_month':'2025-10'}
    assert a.check_target(good)==datetime(2025,10,24,17,30,tzinfo=timezone.utc)
    bad={**good,'window_start_time':'2026-05-03T12:00:00Z','window_end_time':'2026-05-03T16:15:00Z','source_month':'2026-05'}
    with pytest.raises((ValueError,AssertionError)):a.check_target(bad)


def make_pack(tmp_path):
    a.old.dump(tmp_path/'public/manifest.json',{'schema_version':2,'pack_id':'qa','protocol_id':a.PROTOCOL_ID,'items':[{'review_id':rid,'future_bars':40} for rid in ('a','b')]})
    a.old.jsonl(tmp_path/'admin/lineage.jsonl',[{'review_id':'a','is_primary':True,'repeat_of_review_id':None},{'review_id':'b','is_primary':False,'repeat_of_review_id':'a'}])
    answer={'review_id':'a','verdict':'REJECT','reason':None,'note':'QA_ONLY','corrected_direction':None,'corrected_box':None,'future_bars_seen':40,'answered_at':'2026-09-07T10:00:00Z'}
    return {'schema_version':2,'pack_id':'qa','protocol_id':a.PROTOCOL_ID,'manifest_sha256':a.old.sha(tmp_path/'public/manifest.json'),'exported_at':'2026-09-07T10:00:00Z','answers':[answer,{**answer,'review_id':'b'}]}


def test_rejecting_proposal_is_not_a_negative_label(tmp_path):
    export=make_pack(tmp_path);s=a.score(export,tmp_path)
    assert s['primary_verdicts']=={'REJECT':1} and s['explicit_no_target']==0
    assert s['repeat_agreement']==1 and s['assisted_repeat_kappa'] is None
    assert s['training_eligible'] is False and s['labels_mutated'] is False
    export['answers'][0]['reason']='NO_TARGET'
    assert a.score(export,tmp_path)['explicit_no_target']==1
    export['answers'][0]['answered_at']=None
    assert a.score(export,tmp_path)['primary_completed']==0


@pytest.mark.parametrize('kind',['old_schema','wrong_protocol','wrong_manifest','foreign','duplicate','missing','context','timestamp','accept_correction','uncertain_reason','no_target_box','saved_without_verdict'])
def test_incompatible_answers_fail_before_writing(tmp_path,kind):
    export=make_pack(tmp_path);r=export['answers'][0]
    if kind=='old_schema':export['schema_version']=1
    elif kind=='wrong_protocol':export['protocol_id']='blind'
    elif kind=='wrong_manifest':export['manifest_sha256']='0'*64
    elif kind=='foreign':r['review_id']='foreign'
    elif kind=='duplicate':export['answers'][1]['review_id']='a'
    elif kind=='missing':r.pop('note')
    elif kind=='context':r['future_bars_seen']=20
    elif kind=='timestamp':r['answered_at']='2026-02-30T10:00:00Z'
    elif kind=='accept_correction':r.update(verdict='ACCEPT',corrected_direction='SHORT')
    elif kind=='uncertain_reason':r.update(verdict='UNCERTAIN',reason='WRONG_BOX')
    elif kind=='no_target_box':r.update(reason='NO_TARGET',corrected_box=[.1,.2,.4,.6])
    else:r['verdict']=None
    with pytest.raises((ValueError,KeyError)):a.save_snapshot(export,tmp_path)
    assert not (tmp_path/'answers').exists()


def test_backups_preserve_previous_complete_answers_and_drafts(tmp_path):
    export=make_pack(tmp_path);first=a.save_snapshot(export,tmp_path)
    export['answers'][0].update(answered_at=None,note='new draft')
    export['exported_at']='2026-09-07T10:00:01Z'
    second=a.save_snapshot(export,tmp_path)
    assert first['saved_answers']==2 and second['saved_answers']==1
    assert first['filename']!=second['filename']
    assert json.loads((tmp_path/'answers'/first['filename']).read_text())['answers'][0]['answered_at'] is not None


def test_old_input_hash_drift_is_fatal(tmp_path,monkeypatch):
    monkeypatch.setattr(a,'ROOT',tmp_path)
    f=tmp_path/'input.png';f.write_bytes(b'original');before={'input.png':a.old.sha(f)}
    a.verify_preserved(before)
    f.write_bytes(b'future included by mistake')
    with pytest.raises(ValueError):a.verify_preserved(before)


def test_stale_request_cannot_replace_newer_progress(tmp_path):
    export=make_pack(tmp_path);a.save_snapshot(export,tmp_path)
    export['exported_at']='2026-09-07T09:59:59Z'
    with pytest.raises(ValueError,match='stale'):a.save_snapshot(export,tmp_path)
    export['exported_at']='2026-09-07T10:00:00Z';export['answers'][0]['note']='different'
    with pytest.raises(ValueError,match='stale'):a.save_snapshot(export,tmp_path)
    assert len(list((tmp_path/'answers').glob('answers_*.json')))==1


@pytest.mark.parametrize('kind',['duplicate','wrong_id','bad_repeat'])
def test_lineage_drift_cannot_silently_change_summary(tmp_path,kind):
    export=make_pack(tmp_path);path=tmp_path/'admin/lineage.jsonl';rows=a.read_lines(path)
    if kind=='duplicate':rows.append(rows[0])
    elif kind=='wrong_id':rows[1]['review_id']='foreign'
    else:rows[1]['repeat_of_review_id']='b'
    a.old.jsonl(path,rows)
    with pytest.raises(ValueError,match='lineage'):a.score(export,tmp_path)
