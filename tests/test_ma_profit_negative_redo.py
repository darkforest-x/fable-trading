"""Adversarial checks for the negative-population repair, not training quality."""
import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.datasets import ma_profit_dataset as old
from yoyo.datasets import ma_profit_negative_redo as redo


def frame(n=1600):
    x = pd.Series(range(n), dtype=float)
    price = 100+x*.0001+(x%7)*.01
    return pd.DataFrame({"open_time": pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC"),
        "open": price, "high": price+.03, "low": price-.04, "close": price+.015, "volume": 1.})


def event(f, c, eid, retained=False, outcome="SL"):
    return {"event_id": eid, "cluster_id": eid, "canonical_asset": "X", "symbol": "X_USDT_SWAP", "direction": "LONG", "bar_minutes": 15,
            "source_path": "unused", "source_sha256": "unused", "split": "train", "purge_reason": "", "core_start_time": f.open_time.iloc[c-3].isoformat(),
            "core_end_time": f.open_time.iloc[c].isoformat(), "profit": {"retained": retained, "outcome": "TP" if retained else outcome,
            "decision_close_time_utc": (f.open_time.iloc[c]+pd.Timedelta(minutes=90)).isoformat(),
            "label_window_end_utc": (f.open_time.iloc[c]+pd.Timedelta(hours=13, minutes=30)).isoformat()}}


def test_future_and_outcome_magnitude_do_not_change_negative_pixels():
    f = frame(); r = event(f, 1350, "neg")
    before = redo.negative_assets(f, r)
    changed = f.copy()
    changed.loc[1356:, ["open", "high", "low", "close"]] *= 100
    after = redo.negative_assets(changed, r)
    assert before == after
    r2 = copy.deepcopy(r); r2["profit"].update(outcome="TIMEOUT", net_r=1.2, gross_r=1.4)
    assert redo.negative_assets(f, r2) == before
    win = copy.deepcopy(r); win["profit"].update(retained=True, outcome="TP")
    parent = old.event_assets(f, win, "visible_range_v1")
    assert [a["png"] for a in before] == [a["png"] for a in parent]
    assert [a["candidate_box"] for a in before] == [a["box"] for a in parent]
    assert all(a["visible"]["decision_at_utc"] == r["profit"]["decision_close_time_utc"] for a in before)


def test_unknown_invalid_or_winner_cannot_become_training_negatives():
    f = frame()
    for outcome in ("UNKNOWN", "INVALID", "TP"):
        r = event(f, 1350, "bad", outcome=outcome)
        with pytest.raises(redo.NegativeRedoError, match="only resolved"):
            redo.negative_assets(f, r)
    winner = event(f, 1350, "winner", retained=True)
    assert not redo.eligible(winner)


def test_longest_view_not_only_core_obeys_asset_protection_and_split():
    f = frame(); r = event(f, 1350, "negative")
    # Anchor is outside the core but inside B2's eleven-bar left context.
    anchor = pd.Timestamp(r["core_start_time"])-pd.Timedelta(minutes=150)
    chosen, rejected = redo.select_rows([r], {"X": [(anchor, anchor)]}, "2026-01-01T00:00:00Z")
    assert not chosen and rejected[0]["reason"] == "positive_or_owner_protection"
    assert len(redo.select_rows([r], {"Y": [(anchor, anchor)]}, "2026-01-01T00:00:00Z")[0]) == 1
    assert not redo.select_rows([r], {}, r["profit"]["label_window_end_utc"])[0]
    duplicate = copy.deepcopy(r)
    with pytest.raises(redo.NegativeRedoError, match="duplicate"):
        redo.select_rows([r, duplicate], {}, "2026-01-01T00:00:00Z")


def test_missing_known_warmup_is_not_silently_rendered():
    f = frame(); r = event(f, 1350, "negative")
    missing = f.drop(index=1200).reset_index(drop=True)
    with pytest.raises(redo.NegativeRedoError, match="known input gap"):
        redo.negative_assets(missing, r)


@pytest.fixture
def built(tmp_path, monkeypatch):
    f=frame(); source=tmp_path/'source.csv'; f.to_csv(source,index=False)
    pos,neg=event(f,1250,'pos',True),event(f,1400,'neg')
    for r in (pos,neg): r.update(source_path=str(source),source_sha256=redo.sha256(source))
    parentroot=tmp_path/'parent';oldledger=tmp_path/'old.jsonl';redo.write_rows(oldledger,[pos])
    oldplan=tmp_path/'oldplan.json';redo.write_json(oldplan,{'events_sha256':redo.sha256(oldledger),'render':{'price_scale':'visible_range_v1'},'training_contract_sha256':'test','selection_receipt_sha256':'test'})
    monkeypatch.setattr(old,'_committed',lambda _: 'test-committed')
    monkeypatch.setattr(old,'_cohort_controls',lambda *_: ({},[]))
    old.build(oldplan,oldledger,parentroot)
    pool=tmp_path/'pool.jsonl';redo.write_rows(pool,[pos,neg])
    refs=tmp_path/'refs.json';redo.write_json(refs,{'events':[]})
    manual=tmp_path/'manual.jsonl';redo.write_rows(manual,[])
    files={'old_manifest':parentroot/'manifest.jsonl','old_summary':parentroot/'summary.json','old_ledger':oldledger,'candidate_ledger':pool,'reference_exclusion':refs,'manual_rows':manual}
    plan=tmp_path/'plan.json';redo.write_json(plan,{'schema_version':1,'experiment_id':'exp-ma-profit3r-negatives-20260922-v2','owner_authorization':{'training_authorized':True,'promote':False,'live_money':False},'training_eligible':False,'production_eligible':False,'inputs':{k:{'path':str(p),'sha256':redo.sha256(p)} for k,p in files.items()},'negative_policy':{'population':'all_resolved_parent_train_nonwinners','protection_hours':4,'ratio_cap':None},'parent_dataset_root':str(parentroot),'expected_positive_train_events':1,'splits':{'train_end_exclusive':'2026-01-01T00:00:00Z'}})
    selection=tmp_path/'selection';redo.select(plan,selection)
    output=tmp_path/'redo';redo.build(plan,selection,output)
    return plan,selection,output,parentroot


def test_old_assets_and_labels_survive_and_loader_really_has_negatives(built):
    plan,selection,out,parentroot=built
    a=redo.audit(plan,out,selection)
    assert a['actual_label_counts']['A/train/negative']=={'images':1,'events':1}
    assert a['actual_label_counts']['B/train/negative']=={'images':2,'events':1}
    for r in redo.rows(parentroot/'manifest.jsonl'):
        for k in ('image_path','label_path'): assert (out/r[k]).read_bytes()==(parentroot/r[k]).read_bytes()
    with pytest.raises(FileExistsError): redo.build(plan,selection,out)


def test_negative_file_with_positive_box_is_rejected_even_with_rehashed_manifest(built):
    plan,selection,out,_=built
    manifest=redo.rows(out/'manifest.jsonl')
    r=next(x for x in manifest if x.get('negative_kind'))
    (out/r['label_path']).write_text('0 0.5 0.5 0.2 0.2\n')
    r['label_sha256']=redo.sha256(out/r['label_path'])
    redo.write_rows(out/'manifest.jsonl',manifest)
    summary=redo.read_json(out/'summary.json');summary['manifest_sha256']=redo.sha256(out/'manifest.jsonl');redo.write_json(out/'summary.json',summary)
    with pytest.raises(redo.NegativeRedoError,match='actual empty label'):
        redo.audit(plan,out,selection)


def test_loader_cannot_omit_negatives_that_exist_in_manifest(built):
    plan,selection,out,_=built
    paths=(out/'train_A.txt').read_text().splitlines();(out/'train_A.txt').write_text(paths[0]+'\n')
    with pytest.raises(redo.NegativeRedoError,match='loader lists'):
        redo.audit(plan,out,selection)
