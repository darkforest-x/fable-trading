"""Naming the entry must never widen its filter or change its prelabel version."""
from copy import deepcopy
import pytest
from yoyo.review.install_label_studio_queue_entry import title_changes
from yoyo.datasets import label_studio_dataset_union as union


def setup():
    return ({'id':77,'title':union.PREVIOUS_TITLE,'model_version':union.PROTOCOL,
             'show_collab_predictions':True,'evaluate_predictions_automatically':False},
            {'id':44,'project':77,'data':{'title':'本周审核','filters':union.default_filters(),
              'selectedItems':{'all':True,'excluded':[]},'ordering':['tasks:id'],'unrelated':'keep'}})


def test_only_names_change_preserving_filters_selection_and_order():
    project, view = setup();before=deepcopy((project,view));p,v=title_changes(project,view)
    assert p == {'title':union.TITLE}
    assert v['data']=={**view['data'],'title':union.VIEW_TITLE}
    assert (project,view)==before


@pytest.mark.parametrize('field', ['project','view','version','filter','predictions','title'])
def test_foreign_queue_or_user_changes_block_name_patch(field):
    p,v=setup()
    if field=='project':p['id']=76
    elif field=='view':v['id']=50
    elif field=='version':p['model_version']=''
    elif field=='filter':v['data']['filters']={}
    elif field=='predictions':p['show_collab_predictions']=False
    else:p['title']='Owner customized title'
    with pytest.raises(ValueError):title_changes(p,v)
