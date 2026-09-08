"""A historical review archive cannot turn missing/conflicting work into Gold."""
from copy import deepcopy
import pytest
from yoyo.datasets import historical_review_inventory as inventory
from yoyo.datasets.owner_review_export import OLD_PROTOCOL


def example(status='unanswered'):
    old = [{'review_id': 'r1', 'box_id': 'b1', 'symbol': 'AAA_USDT_SWAP', 'source_path': 'data/okx_AAA.csv',
            'owner_side': 'short', 'original_split': 'train', 'exact_star': True}]
    event = {'review_id': 'r1', 'task_id': 1, 'status': status, 'event_conflict': status == 'event_conflict'}
    quality = [{'review_id': 'r1', 'task_id': 1, 'audit_flags': [], 'negative_event_conflicts': []}]
    answer = {'review_id': 'r1', 'task_id': 1, 'protocol_id': OLD_PROTOCOL, 'record_kind': 'annotation',
              'annotation_id': 8, 'effective_answer': True, 'effective_boxes': []}
    answers = [] if status == 'unanswered' else [answer]
    return old, [event], answers, quality


def test_unanswered_and_star_are_not_positive_or_background():
    result = inventory.inventory(*example())
    row = result['rows'][0]
    assert row['disposition'] == 'archive_pending_shape_screen'
    assert row['effective_boxes'] == []
    assert row['old_star_provenance_only'] is True
    assert not row['training_eligible'] and not row['new_gold']
    assert not row['current_default_queue_member']


def test_preserves_owner_boxes_without_claiming_gold_or_copying_labels():
    args = example('owner_boxes')
    args[2][0]['effective_boxes'] = [{'label': '多头', 'x': 35, 'y': 20, 'width': 30, 'height': 10}]
    before = deepcopy(args)
    row = inventory.inventory(*args)['rows'][0]
    assert row['historical_owner_side'] == 'short'
    assert row['effective_boxes'] == args[2][0]['effective_boxes']
    assert row['disposition'] == 'retain_owner_boxes_pending_quality'
    assert args == before
    assert row['annotation_ids'] == [8]


@pytest.mark.parametrize('status', ['owner_no_target', 'owner_no_target_with_inherited_proposal'])
def test_explicit_no_target_remains_empty(status):
    row = inventory.inventory(*example(status))['rows'][0]
    assert row['disposition'] == 'retain_no_target_pending_background_checks'
    assert row['effective_boxes'] == []


def test_multiple_conflicting_answers_are_preserved_without_effective_labels():
    args = example('event_conflict')
    args[2][0]['effective_boxes'] = [{'label': '多头'}]
    args[2].append({**args[2][0], 'annotation_id': 9, 'effective_boxes': [{'label': '空头'}]})
    row = inventory.inventory(*args)['rows'][0]
    assert row['annotation_ids'] == [8, 9]
    assert row['effective_boxes'] == []
    assert row['disposition'] == 'quarantine_conflicting_answer'


def test_single_answer_conflict_not_collapsed_to_negative():
    row = inventory.inventory(*example('conflict_needs_review'))['rows'][0]
    assert row['disposition'] == 'quarantine_conflicting_answer'


def test_draft_has_no_effective_label():
    args = example('draft_only')
    args[2][0].update(record_kind='draft', draft_id=55, effective_answer=False)
    del args[2][0]['annotation_id']
    row = inventory.inventory(*args)['rows'][0]
    assert row['draft_ids'] == [55] and row['annotation_ids'] == []
    assert row['disposition'] == 'preserve_draft'


def test_duplicate_identity_rejected():
    args = example()
    args[0].append(args[0][0].copy())
    with pytest.raises(ValueError, match='Duplicate identity'):
        inventory.inventory(*args)


@pytest.mark.parametrize('field', ['task', 'protocol', 'coverage', 'quality_task', 'quality_members'])
def test_identity_and_status_drift_fail_closed(field):
    args = example('owner_boxes')
    if field == 'task': args[2][0]['task_id'] = 2
    elif field == 'protocol': args[2][0]['protocol_id'] = 'another_protocol'
    elif field == 'coverage': args[2].clear()
    elif field == 'quality_task': args[3][0]['task_id'] = 2
    else: args[3].clear()
    with pytest.raises(ValueError):
        inventory.inventory(*args)


def test_negative_flags_remain_separate_from_owner_status():
    args = example('owner_boxes')
    args[3][0].update(audit_flags=['existing_negative_window_conflict'],
                      negative_event_conflicts=['negative:abc'])
    result = inventory.inventory(*args)
    assert result['rows'][0]['answer_status'] == 'owner_boxes'
    assert result['negative_event_review_holds'] == ['negative:abc']
    assert not result['policy']['automatic_label_transfer']


def test_hl2_statuses_are_not_adopted_by_historical_candidates():
    args = example()
    args[1].append({'review_id': 'new_hl2', 'task_id': 2, 'status': 'owner_boxes', 'event_conflict': False})
    result = inventory.inventory(*args)
    assert result['population'] == 1
    assert result['rows'][0]['answer_status'] == 'unanswered'


def test_equivalent_repeated_answers_are_one_event_box_set_not_two_targets():
    args = example('owner_boxes')
    args[2][0]['effective_boxes'] = [{'label': '多头', 'x': 35, 'y': 20, 'width': 30, 'height': 10}]
    args[2].append({**deepcopy(args[2][0]), 'annotation_id': 9})
    row = inventory.inventory(*args)['rows'][0]
    assert row['annotation_ids'] == [8, 9] and row['effective_answer_count'] == 2
    assert row['effective_boxes'] == args[2][0]['effective_boxes']
    assert len(row['effective_boxes']) == 1


def test_disagreeing_boxes_cannot_claim_equivalent_event_status():
    args = example('owner_boxes')
    args[2][0]['effective_boxes'] = [{'label': '多头', 'x': 35}]
    args[2].append({**deepcopy(args[2][0]), 'annotation_id': 9, 'effective_boxes': [{'label': '空头', 'x': 35}]})
    with pytest.raises(ValueError, match='disagree'):
        inventory.inventory(*args)
