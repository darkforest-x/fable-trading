"""Protect the local LS runtime bundle and its rollback provenance."""
import pytest

from yoyo.review import install_label_studio_shortcut as shortcut


def test_install_is_idempotent_and_restores_vendor_bytes(tmp_path):
    bundle = tmp_path / "main.js"
    original = b"window.vendor = true;\n//# sourceMappingURL=main.js.map"
    bundle.write_bytes(original)
    state = tmp_path / "state"
    first = shortcut.install(bundle, state)
    assert bundle.read_bytes().startswith(original + shortcut.MARKER)
    assert shortcut.install(bundle, state) == first
    restored = shortcut.install(bundle, state, restore=True)
    assert restored["restored"] and bundle.read_bytes() == original


def test_foreign_runtime_edit_is_not_overwritten(tmp_path):
    bundle = tmp_path / "main.js"
    bundle.write_bytes(b"vendor();")
    state = tmp_path / "state"
    shortcut.install(bundle, state)
    foreign = bundle.read_bytes() + b"\notherFix();"
    bundle.write_bytes(foreign)
    for restore in [False, True]:
        with pytest.raises(ValueError, match="drift"):
            shortcut.install(bundle, state, restore=restore)
        assert bundle.read_bytes() == foreign


def test_damaged_backup_blocks_restore(tmp_path):
    bundle = tmp_path / "main.js"
    bundle.write_bytes(b"vendor();")
    state = tmp_path / "state"
    result = shortcut.install(bundle, state)
    (state / result["backup_name"]).write_bytes(b"wrong")
    with pytest.raises(ValueError, match="Backup hash"):
        shortcut.install(bundle, state, restore=True)


def test_updated_source_requires_restore_and_can_then_upgrade(tmp_path, monkeypatch):
    bundle = tmp_path / "main.js"
    bundle.write_bytes(b"vendor();")
    source = tmp_path / "handler.js"
    source.write_bytes(b"versionOne();")
    monkeypatch.setattr(shortcut, "SOURCE", source)
    state = tmp_path / "state"
    first = shortcut.install(bundle, state)
    source.write_bytes(b"versionTwo();")
    with pytest.raises(ValueError, match="source changed"):
        shortcut.install(bundle, state)
    shortcut.install(bundle, state, restore=True)
    assert bundle.read_bytes() == b"vendor();"
    upgraded = shortcut.install(bundle, state)
    assert upgraded["source_sha256"] != first["source_sha256"]
    assert bundle.read_bytes().endswith(b"versionTwo();\n")


def test_unowned_marker_and_wrong_target_fail_closed(tmp_path):
    bundle = tmp_path / "main.js"
    bundle.write_bytes(b"vendor();/* FABLE_LS_RIGHT_CLICK_SUBMIT_V2 */")
    with pytest.raises(ValueError, match="no matching receipt"):
        shortcut.install(bundle, tmp_path / "unowned")
    bundle.write_bytes(b"vendor();")
    state = tmp_path / "state"
    shortcut.install(bundle, state)
    second = tmp_path / "different.js"
    second.write_bytes(b"vendor();")
    with pytest.raises(ValueError, match="different bundle"):
        shortcut.install(second, state)


def test_receipt_write_failure_does_not_change_live_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "main.js"
    bundle.write_bytes(b"vendor();")
    write = shortcut.atomic_write

    def fail_receipt(path, content):
        if path.name == "installation.json":
            raise OSError("disk failure")
        write(path, content)

    monkeypatch.setattr(shortcut, "atomic_write", fail_receipt)
    with pytest.raises(OSError):
        shortcut.install(bundle, tmp_path / "state")
    assert bundle.read_bytes() == b"vendor();"


def test_prepared_receipt_recovers_without_handler_source(tmp_path, monkeypatch):
    bundle = tmp_path / "main.js"
    bundle.write_bytes(b"vendor();")
    state = tmp_path / "state"
    write = shortcut.atomic_write

    def fail_bundle(path, content):
        if path == bundle:
            raise OSError("interrupted install")
        write(path, content)

    monkeypatch.setattr(shortcut, "atomic_write", fail_bundle)
    with pytest.raises(OSError):
        shortcut.install(bundle, state)
    monkeypatch.setattr(shortcut, "atomic_write", write)
    monkeypatch.setattr(shortcut, "SOURCE", tmp_path / "missing-source.js")
    assert shortcut.install(bundle, state, restore=True)["restored"]
    assert bundle.read_bytes() == b"vendor();"
