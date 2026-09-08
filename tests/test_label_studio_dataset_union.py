"""Protect live Owner work while appending a different proposal family."""
from copy import deepcopy
from contextlib import nullcontext
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs

import pytest

from yoyo.datasets import label_studio_dataset_union as mod


def task(rid, protocol):
    return {'data': {'review_id': rid, 'protocol_id': protocol}, 'predictions': [{
        'model_version': protocol, 'result': [{'id': rid, 'type': 'rectanglelabels',
        'from_name': 'pattern', 'to_name': 'image', 'original_width': 1280,
        'original_height': 742, 'value': {'x': 10, 'y': 20, 'width': 30,
        'height': 40, 'rectanglelabels': ['空头']}}]}]}


@pytest.fixture
def server(tmp_path, monkeypatch):
    old = [task('old1', mod.historical.PROTOCOL), task('old2', mod.historical.PROTOCOL)]
    new = [task('new1', mod.PROTOCOL), task('new2', mod.PROTOCOL)]
    for name, tasks in (('old', old), ('pack', new)):
        directory = tmp_path/name
        directory.mkdir()
        (directory/'tasks.json').write_text(json.dumps(tasks))
    (tmp_path/'pack/admin').mkdir()
    (tmp_path/'pack/admin/build_receipt.json').write_text(json.dumps({'tasks_sha256': mod.sha(tmp_path/'pack/tasks.json')}))
    config = tmp_path/'config.xml'
    config.write_text('<View/>')
    for key, value in {'PACK':tmp_path/'pack', 'OLD_PACK':tmp_path/'old', 'CONFIG':config,
        'STATE':tmp_path/'state', 'EXPECTED_OLD_COUNT':2, 'EXPECTED_NEW_COUNT':2,
        'OLD_TASK_SHA':mod.sha(tmp_path/'old/tasks.json')}.items():
        monkeypatch.setattr(mod, key, value)
    monkeypatch.setattr(mod.historical, 'CONFIG', config)
    monkeypatch.setattr(mod, 'source_identity', lambda: {'source_commit':'test'})
    monkeypatch.setattr(mod, 'image_resources', lambda *_: [])
    monkeypatch.setattr(mod.base, 'session', lambda: ('opener', 'token'))
    monkeypatch.setattr(mod.base, 'document_root', lambda *_: tmp_path)
    state = SimpleNamespace(project={'id':77, 'title':mod.historical.TITLE, 'label_config':'<View/>',
        'model_version':mod.historical.PROTOCOL, 'show_collab_predictions':True,
        'evaluate_predictions_automatically':False}, view={'id':44, 'project':77,
        'data':{'title':'Default', 'filters':{'conjunction':'and', 'items':[]}, 'hiddenColumns':['tasks:data.secret']}},
        rows=[], predictions=[], calls=[], concurrent_edit=False, stores=[])
    for i,t in enumerate(old):
        state.rows.append({'id':100+i, 'data':t['data'], 'total_predictions':1,
            'total_annotations':1 if i==0 else 0, 'annotations':[{'id':7,'result':['real-owner']} ] if i==0 else [],
            'drafts':[{'id':8,'result':['unsaved-owner']} ] if i==1 else []})
        state.predictions.append({'task':100+i, 'id':500+i, **t['predictions'][0]})

    def pages(sess,path):
        if path.startswith('/api/storages/localfiles?'):return deepcopy(state.stores)
        if path.startswith('/api/predictions?'):
            return deepcopy(state.predictions)
        if path.startswith('/api/tasks?'):
            if 'query=' in path or 'view=44' in path:
                return [{'id':r['id']} for r in state.rows if r['data']['protocol_id']==mod.PROTOCOL]
            return deepcopy(state.rows)
        raise AssertionError(path)

    def api(sess,method,path,payload=None):
        state.calls.append((method,path,deepcopy(payload)))
        if method=='GET' and path=='/api/projects/77/': return deepcopy(state.project)
        if method=='GET' and path=='/api/dm/views/44/': return deepcopy(state.view)
        if method=='POST' and path=='/api/projects/validate/': return {}
        if method=='POST' and path=='/api/storages/localfiles/validate':return {}
        if method=='POST' and path=='/api/storages/localfiles':
            state.stores.append(deepcopy(payload));return {'id':len(state.stores),**payload}
        if method=='POST' and path.startswith('/api/projects/77/import?'):
            ids=[]
            for t in payload:
                assert set(t)=={'data','predictions'}
                tid=100+len(state.rows)
                state.rows.append({'id':tid,'data':deepcopy(t['data']),'total_predictions':1,
                    'total_annotations':0,'annotations':[],'drafts':[]})
                state.predictions.append({'task':tid,'id':tid+500,**deepcopy(t['predictions'][0])})
                ids.append(tid)
            if state.concurrent_edit:
                state.rows[0]['annotations'][0]['result']=['owner-concurrent-update']
            return {'task_count':len(payload),'task_ids':ids}
        if method=='PATCH' and path=='/api/projects/77/':
            state.project.update(deepcopy(payload));return deepcopy(state.project)
        if method=='PATCH' and path=='/api/dm/views/44/':
            state.view.update(deepcopy(payload));return deepcopy(state.view)
        raise AssertionError(f'Forbidden write or unexpected call: {method} {path}')

    monkeypatch.setattr(mod.base,'api',api)
    monkeypatch.setattr(mod.base,'_pages',pages)
    return state


def test_append_reentry_preserves_owner_work_and_original_predictions(server):
    before=deepcopy((server.rows,server.predictions))
    first=mod.deliver()
    second=mod.deliver()
    assert first['imported']==2 and second['imported']==0
    assert first['task_ids']==second['task_ids']
    assert server.rows[:2]==before[0] and server.predictions[:2]==before[1]
    assert server.project['model_version']==mod.PROTOCOL
    assert server.view['data']['hiddenColumns']==['tasks:data.secret']
    assert all('/annotations' not in p and '/draft' not in p and not (m=='PATCH' and '/tasks' in p)
               for m,p,_ in server.calls)


def test_concurrent_owner_update_is_not_replaced(server):
    server.concurrent_edit=True
    mod.deliver()
    assert server.rows[0]['annotations'][0]['result']==['owner-concurrent-update']


@pytest.mark.parametrize('corrupt',[False,True])
def test_served_asset_validation_precedes_any_task_or_queue_change(server,monkeypatch,corrupt):
    blob=b'frozen-image'
    url='/data/local-files/?d=label_studio/pack/input_images/new1.png'
    monkeypatch.setattr(mod,'image_resources',lambda *_:[('new1','image',url,hashlib.sha256(blob).hexdigest())])
    def open_image(*_,**__):
        assert server.stores
        assert not any('/import' in p or m=='PATCH' for m,p,_ in server.calls)
        return nullcontext(SimpleNamespace(status=200,read=lambda:b'drift' if corrupt else blob))
    monkeypatch.setattr(mod.base,'session',lambda:(SimpleNamespace(open=open_image),'token'))
    if corrupt:
        with pytest.raises(ValueError):mod.deliver()
        assert not any('/import' in p or m=='PATCH' for m,p,_ in server.calls)
    else:
        assert mod.deliver()['imported']==2


@pytest.mark.parametrize('defect',['task_data','prediction','missing_old','duplicate','foreign_version','view_filter','selection'])
def test_drift_stops_before_mutation(server,defect):
    if defect=='task_data':server.rows[0]['data']['caption']='edited'
    elif defect=='prediction':server.predictions[0]['result'][0]['value']['x']=11
    elif defect=='missing_old':server.rows.pop()
    elif defect=='duplicate':server.rows.append(deepcopy(server.rows[0]))
    elif defect=='foreign_version':server.project['model_version']='foreign'
    elif defect=='view_filter':server.view['data']['filters']={'custom':'owner'}
    elif defect=='selection':server.view['data']['selectedItems']={'all':False,'included':[100]}
    with pytest.raises(ValueError):mod.deliver()
    assert not any(m=='PATCH' or '/import' in p for m,p,_ in server.calls)


def test_annotation_payload_cannot_be_imported():
    bad=task('n',mod.PROTOCOL);bad['annotations']=[]
    with pytest.raises(ValueError):mod.expected_tasks([], [bad])


def test_future_image_cannot_receive_detection():
    bad=task('n',mod.PROTOCOL);bad['predictions'][0]['result'][0]['to_name']='future'
    with pytest.raises(ValueError):mod.expected_tasks([], [bad])
