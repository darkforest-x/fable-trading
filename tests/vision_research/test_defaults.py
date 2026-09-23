"""Default exemplars must not overwrite owner edits or hide source corruption."""

import hashlib
import io
import json

from PIL import Image
import pytest

from yoyo.vision_research.defaults import DEFAULT_PACK, install_default_references
from yoyo.vision_research.store import ResearchStore
from yoyo.vision_research.images import image_from_bytes
from yoyo.vision_research.pattern_rules import reference_note


def make_pack(path):
    path.mkdir()
    stream = io.BytesIO()
    Image.new("RGB", (80, 60), "navy").save(stream, "PNG")
    data = stream.getvalue()
    (path / "sample.png").write_bytes(data)
    (path / "manifest.json").write_text(json.dumps({"items": [{
        "file": "sample.png", "name": "Original core box",
        "source_sha256": hashlib.sha256(data).hexdigest(),
    }]}))
    return path


def test_seed_once_preserves_edits_and_intentional_empty_library(tmp_path):
    pack = make_pack(tmp_path / "pack")
    store = ResearchStore(tmp_path / "runtime")
    seeded = install_default_references(store, pack)
    assert len(seeded["items"]) == 1 and seeded["revision"] == 1
    assert install_default_references(store, pack) == seeded
    edited = store.replace_references([{**seeded["items"][0], "name": "Owner edit"}], 1)
    assert install_default_references(store, pack) == edited
    cleared = store.replace_references([], edited["revision"])
    assert install_default_references(store, pack) == cleared


def test_corrupt_pack_leaves_library_untouched(tmp_path):
    pack = make_pack(tmp_path / "pack")
    (pack / "sample.png").write_bytes(b"changed source")
    store = ResearchStore(tmp_path / "runtime")
    with pytest.raises(ValueError, match="校验失败"):
        install_default_references(store, pack)
    assert store.get_references() == {"items": [], "revision": 0}
    assert not list(store.images.iterdir())


def test_shipped_defaults_retain_source_pixels_and_provenance(tmp_path):
    manifest = json.loads((DEFAULT_PACK / "manifest.json").read_text())
    store = ResearchStore(tmp_path / "runtime")
    result = install_default_references(store)
    assert len(result["items"]) == len(manifest["items"]) >= 2
    for entry, saved in zip(manifest["items"], result["items"]):
        assert entry["box_semantics"] == "core_wicks_and_six_moving_averages"
        assert entry["sample_owner_geometry_confirmed"] is False
        assert entry["time_boundary"] == "retrospective_reference_only"
        with Image.open(DEFAULT_PACK / entry["file"]) as original:
            with Image.open(store.image_path(saved["sha256"] + ".png")) as normalized:
                assert original.convert("RGB").tobytes() == normalized.tobytes()


def test_reference_descriptions_match_the_shipped_pack_and_confirmation_level():
    manifest = json.loads((DEFAULT_PACK / "manifest.json").read_text())
    for entry in manifest["items"]:
        normalized = image_from_bytes((DEFAULT_PACK / entry["file"]).read_bytes(), entry["name"])
        note = reference_note(normalized.sha256)
        assert note, f"Missing reference guidance for {entry['name']}"
        assert f"{entry['core_bars']}根" in note
        assert "框坐标仍待逐样本确认" in note
        assert ("原样本形态曾获Owner认可" in note) is entry["owner_semantic_verdict"]
        assert ("尚未获Owner逐样本认可" in note) is not entry["owner_semantic_verdict"]
    assert reference_note("0" * 64) == ""
