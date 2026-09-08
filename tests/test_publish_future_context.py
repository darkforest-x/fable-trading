"""A preview extension may change neither annotation meaning nor image identity."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from yoyo.review import publish_future_context as mod


def xml(future='$future_image', primary='$image', label='空头'):
    return f'''<View><RectangleLabels name="pattern" toName="image"><Label value="{label}"/></RectangleLabels>
    <Choices name="no_box_reason" toName="image"><Choice value="无目标形态"/></Choices>
    <Image name="image" value="{primary}"/><Image name="future" value="{future}"/></View>'''


def test_read_only_future_url_and_presentation_are_allowed():
    before = xml()
    after = xml(mod.URL).replace('<View>', '<View><Header value="说明"/>', 1)
    mod.validate_config(before, after)
    mod.validate_config(after, after)


@pytest.mark.parametrize('change', ['primary', 'label', 'name', 'choice', 'unsafe_future', 'missing_future'])
def test_annotation_primary_image_or_foreign_url_changes_are_rejected(change):
    after = xml(mod.URL)
    if change == 'primary': after = xml(mod.URL, primary='$future_image')
    elif change == 'label': after = xml(mod.URL, label='多头')
    elif change == 'name': after = after.replace('name="pattern"', 'name="new_pattern"')
    elif change == 'choice': after = after.replace('无目标形态', '没有')
    elif change == 'unsafe_future': after = xml('https://example.com/$review_id/image.png')
    else: after = after.replace(f'<Image name="future" value="{mod.URL}"/>', '')
    with pytest.raises(ValueError): mod.validate_config(xml(), after)


@pytest.fixture
def lookup(tmp_path, monkeypatch):
    pack, old = tmp_path/'pack', tmp_path/'old'
    old.mkdir(); (old/'legacy.png').write_bytes(b'legacy')
    monkeypatch.setattr(mod.audit, 'OLD_PACK', old)
    monkeypatch.setattr(mod, 'EXPECTED_COUNTS', {'new_future150': 1, 'legacy_future40_symlink': 1})
    rows, expected = [], {}
    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    for rid, protocol, mode, count in [('a'*24,mod.audit.NEW_PROTOCOL,'new_future150',150),
                                      ('b'*24,mod.audit.OLD_PROTOCOL,'legacy_future40_symlink',40)]:
        path = pack/'images'/rid/'image.png'; path.parent.mkdir(parents=True)
        if count == 150:path.write_bytes(b'new')
        else:path.symlink_to(old/'legacy.png')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({'review_id':rid,'source_protocol_id':protocol,'mode':mode,
            'image_path':f'images/{rid}/image.png','image_sha256':digest,
            'future':{'requested_future_bars':count,'actual_future_bars':count,
                'review_available_at':(start+timedelta(minutes=15*(count+1))).isoformat(),
                'missing_future_reason':None}})
        expected[rid]={'data':{'protocol_id':protocol,'future_image_sha256':digest,
            'future_image':'/data/local-files/?d=label_studio/old/legacy.png'},
            'source_identity':{'main_end_time':start.isoformat()}}
    return pack, rows, expected


def test_complete_lookup_preserves_exact_legacy_link(lookup):
    pack, rows, expected = lookup
    assert mod.verify_lookup(rows, expected, pack) == mod.EXPECTED_COUNTS


@pytest.mark.parametrize('change', ['duplicate','missing','foreign_protocol','traversal','hash','new_symlink',
                                  'legacy_target','time','holdout','count','reason'])
def test_lookup_tampering_fails(lookup, change):
    pack, rows, expected = lookup
    rows = deepcopy(rows)
    if change == 'duplicate': rows.append(deepcopy(rows[0]))
    elif change == 'missing': rows.pop()
    elif change == 'foreign_protocol': rows[0]['source_protocol_id']='other'
    elif change == 'traversal': rows[0]['image_path']='../image.png'
    elif change == 'hash': rows[0]['image_sha256']='0'*64
    elif change == 'new_symlink':
        path=pack/rows[0]['image_path'];path.unlink();path.symlink_to(pack/rows[1]['image_path'])
    elif change == 'legacy_target':
        path=pack/rows[1]['image_path'];path.unlink();path.symlink_to(pack/rows[0]['image_path'])
    elif change == 'time': rows[0]['future']['review_available_at']='2025-06-01T00:00:00+00:00'
    elif change == 'holdout': rows[0]['future']['review_available_at']='2026-05-04T00:15:00+00:00'
    elif change == 'count': rows[0]['future']['actual_future_bars']=151
    else: rows[0]['future']['missing_future_reason']='source_end'
    with pytest.raises(ValueError): mod.verify_lookup(rows, expected, pack)


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'ROOT', tmp_path)
    name = 'yoyo/datasets/review_future_context.py'
    path = tmp_path/name; path.parent.mkdir(parents=True); path.write_bytes(b'frozen-builder')
    meta = tmp_path/'manifest.json'; meta.write_bytes(b'original-metadata')
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *args, **kwargs: b'frozen-builder')
    info = {'files_sha256': {'original': 'a'*64}}
    r = {'protocol_id':mod.PROTOCOL,'status':'ready_for_human_review_future150',
        'requested_future_bars':150,'new_events':1043,'legacy_events':2513,'lookup_images':3556,
        'verified_positive_image_label_pairs':8000,'main_replay_passed':1043,
        'immutable_media_before_sha256':'b'*64,'immutable_media_after_sha256':'b'*64,
        'read_source_metadata_sha256':info['files_sha256'],'source_commit':'c'*40,
        'code_sha256':{name:mod.digest(path.read_bytes())},'source_sha256':{'manifest.json':mod.digest(meta.read_bytes())}}
    r.update({k:False for k in ('holdout_read','training_eligible','production_eligible','new_gold',
        'new_training','new_model_inference','label_studio_mutation_in_builder',
        'future_used_for_training_input','future_used_for_labels')})
    return r, info


def test_completed_receipt_is_grounded_in_current_and_committed_bytes(ready):
    mod.verify_receipt(*ready)


@pytest.mark.parametrize('change', ['unfinished','holdout','partial_replay','different_inputs','different_metadata',
                                  'uncommitted_builder','changed_builder','changed_source','missing_pin'])
def test_incomplete_or_drifted_build_receipts_fail(ready, monkeypatch, change):
    r, info = deepcopy(ready)
    if change == 'unfinished': r['status']='building'
    elif change == 'holdout':r['holdout_read']=True
    elif change == 'partial_replay':r['main_replay_passed']=1042
    elif change == 'different_inputs':r['immutable_media_after_sha256']='d'*64
    elif change == 'different_metadata':r['read_source_metadata_sha256']={}
    elif change == 'uncommitted_builder':monkeypatch.setattr(mod.subprocess,'check_output',lambda *a,**kw:b'old-builder')
    elif change == 'changed_builder':(mod.ROOT/'yoyo/datasets/review_future_context.py').write_bytes(b'changed')
    elif change == 'changed_source':(mod.ROOT/'manifest.json').write_bytes(b'changed')
    else:r['code_sha256']={}
    with pytest.raises(ValueError):mod.verify_receipt(r, info)
