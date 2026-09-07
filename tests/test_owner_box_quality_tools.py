"""Synthetic gates and actual installed SDK controls; never use market images."""
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import textwrap

import pytest

from yoyo.datasets import owner_box_quality_tools as audit


SDK_PYTHON = Path.home() / ".local/share/fable-trading/venvs/dataset-audit/bin/python"


def row(index=0):
    rid = f"{index:024x}"
    roles = {key: f"{folder}/{rid}.png" for key, folder in audit.ROLES.items()}
    return {"review_id": rid, "box_id": f"owner-{index}__b0", "owner_side": "long" if index % 2 else "short",
            "training_eligible": False, "production_eligible": False, "new_gold": False,
            "sample_owner_geometry_confirmed": False, "future_values_used_for_proposal": False,
            "main_canvas_role": "review_only_not_a_training_input", "asset_roles": roles,
            "assets": {path: "a"*64 for path in roles.values()},
            "main_start_i": 200, "main_end_i": 219, "main_start_time": "2025-01-01T00:00:00Z",
            "main_end_time": "2025-01-01T04:45:00Z", "chart_transform": {"width": 1280, "height": 742, "n_bars": 20},
            "proposal": {"x0": 200.0, "x1": 600.0, "y0": 250.0, "y1": 510.0},
            "alias_candidate_group": "same-interval", "original_window_dependency_id": "same-W200",
            "exact_star": index == 0}


def test_metadata_retains_aliases_and_direction_without_opening_images(tmp_path, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("metadata validation opened image bytes")
    monkeypatch.setattr(Path, "read_bytes", forbid)
    records = audit.validate_metadata([row(0), row(1)], tmp_path, expected_count=2)
    assert len(records) == 2 and {r["owner_side"] for r in records} == {"long", "short"}
    assert len({r["review_id"] for r in records}) == 2
    assert {r["alias_candidate_group"] for r in records} == {"same-interval"}
    assert all(r["annotation_role"] == "unconfirmed_geometry_proposal" for r in records)


@pytest.mark.parametrize("field,value", [
    ("main_start_time", "2026-05-04T00:00:00Z"),
    ("main_end_time", "2026-05-04T00:00:00Z"),
    ("main_end_time", "2025-01-01T04:30:00Z"),
    ("main_end_time", "2025-01-01T04:45:00"),
    ("owner_side", "skip"), ("future_values_used_for_proposal", True),
    ("sample_owner_geometry_confirmed", True), ("training_eligible", True),
])
def test_bad_metadata_rejected_before_any_image_open(tmp_path, monkeypatch, field, value):
    good, bad = row(0), row(1)
    bad[field] = value
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("opened an image"))
    with pytest.raises(ValueError):
        audit.validate_metadata([good, bad], tmp_path, expected_count=2)


def test_last_preholdout_bar_close_is_allowed(tmp_path):
    r = row()
    r["main_end_time"] = (audit.HOLDOUT_START-timedelta(minutes=15)).isoformat()
    r["main_start_time"] = (audit.HOLDOUT_START-timedelta(minutes=300)).isoformat()
    assert len(audit.validate_metadata([r], tmp_path, expected_count=1)) == 1


@pytest.mark.parametrize("role", ["future_image", "original_image", "comparison_image"])
def test_nonmain_role_cannot_enter_selected_image(tmp_path, role):
    r = row()
    r["asset_roles"]["image"] = r["asset_roles"][role]
    with pytest.raises(ValueError, match="role"):
        audit.validate_metadata([r], tmp_path, expected_count=1)


def test_symlink_cannot_redirect_main_to_future(tmp_path):
    r = row()
    main = tmp_path / r["asset_roles"]["image"]
    future = tmp_path / r["asset_roles"]["future_image"]
    main.parent.mkdir(parents=True)
    main.symlink_to(future)
    with pytest.raises(ValueError, match="symlink"):
        audit.validate_metadata([r], tmp_path, expected_count=1)


@pytest.mark.parametrize("box", [
    {"x0": -1}, {"x1": 1281}, {"y0": float("nan")}, {"y1": float("inf")},
    {"x1": 100}, {"y0": 600},
])
def test_bad_proposal_geometry_fails(tmp_path, box):
    r = row()
    r["proposal"].update(box)
    with pytest.raises(ValueError, match="geometry"):
        audit.validate_metadata([r], tmp_path, expected_count=1)


def test_duplicate_identity_and_missing_role_fail(tmp_path):
    with pytest.raises(ValueError, match="identity"):
        audit.validate_metadata([row(), row()], tmp_path, expected_count=2)
    r = row()
    del r["asset_roles"]["future_image"]
    with pytest.raises(ValueError, match="four"):
        audit.validate_metadata([r], tmp_path, expected_count=1)


def test_formal_source_gate_runs_before_manifest_or_images(monkeypatch):
    def uncommitted():
        raise ValueError("uncommitted source")
    monkeypatch.setattr(audit, "source_identity", uncommitted)
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("read before gate"))
    with pytest.raises(ValueError, match="uncommitted"):
        audit.run()


@pytest.mark.skipif(not SDK_PYTHON.exists(), reason="dedicated optional SDK environment unavailable")
def test_actual_sdk_controls_and_no_deletion(tmp_path):
    """Real duplicate + distinct image controls, real Datumaro malformed Bbox.

    The dedicated environment intentionally has no pytest; invoke its installed
    SDKs through a subprocess. All six images are created from synthetic pixels.
    """
    manifest = tmp_path / "synthetic.json"
    manifest.write_text(json.dumps([row(i) for i in range(6)]))
    script = textwrap.dedent('''
        import json, sys
        from pathlib import Path
        from PIL import Image, ImageDraw
        import datumaro as dm
        from datumaro.plugins.validators import DetectionValidator
        from yoyo.datasets import owner_box_quality_tools as a
        root = Path(sys.argv[1])
        rows = json.loads((root/'synthetic.json').read_text())
        for i,r in enumerate(rows):
            p = root/r['asset_roles']['image']; p.parent.mkdir(parents=True, exist_ok=True)
            # Rows 0/1 are an intentional byte-identical image alias. Others differ.
            k = max(0, i-1)
            im = Image.new('RGB',(1280,742),(15+k*23,20+k*11,30+k*7))
            d = ImageDraw.Draw(im)
            d.rectangle((30+k*110,45+k*50,140+k*110,600-k*35), fill=(220,150,75+k*20))
            im.save(p)
            r['assets'][r['asset_roles']['image']] = a.sha256(p)
        records = a.validate_metadata(rows,root,expected_count=6)
        a.verify_images(records)
        before = {r['image']:a.sha256(Path(r['image'])) for r in records}
        report = a.sdk_audit(records,root/'sdk_results')
        assert report['preserved_count']==6 and report['deleted_count']==0 and report['labels_changed_count']==0
        assert report['groups']['exact_duplicates']==[[rows[0]['review_id'],rows[1]['review_id']]]
        assert all(not r['issues'] or r['retained'] for r in report['rows'])
        assert len(report['rows'])==6 and report['datumaro']['format_roundtrip_count']==6
        assert report['datumaro']['validation_summary']['errors']==0
        assert before=={r['image']:a.sha256(Path(r['image'])) for r in records}
        assert not any((root/folder).exists() for role,folder in a.ROLES.items() if role!='image')
        # A real negative-length box must be flagged by Datumaro itself.
        broken = dm.Dataset.from_iterable([
            dm.DatasetItem(id='bad',media=dm.Image.from_file(records[0]['image'],size=(742,1280)),
                           annotations=[dm.Bbox(80,90,-20,40,label=0,id=1)]),
            dm.DatasetItem(id='good',media=dm.Image.from_file(records[1]['image'],size=(742,1280)),
                           annotations=[dm.Bbox(80,90,20,40,label=1,id=1)])],categories=list(a.SIDES))
        invalid = DetectionValidator(**a.VALIDATOR_CONFIG).validate(broken)
        assert invalid['summary']['errors']>0, invalid
        report['synthetic_bad_box_validation'] = a.jsonable(invalid['summary'])
        a.write_json(root/'synthetic_control_result.json',report)
        # Correct metadata SHA is required again if the main image is replaced.
        Path(records[2]['image']).write_bytes(b'corrupted')
        try: a.verify_images(records)
        except ValueError as error: assert 'SHA mismatch' in str(error)
        else: raise AssertionError('bad SHA accepted')
    ''')
    completed = subprocess.run([str(SDK_PYTHON), "-c", script, str(tmp_path)], cwd=audit.ROOT,
                               text=True, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((tmp_path / "synthetic_control_result.json").read_text())
    assert report["versions"]["datumaro"] == "1.12.0"
    assert report["versions"]["cleanvision"] == "0.3.7"
    assert report["synthetic_bad_box_validation"]["errors"] > 0
