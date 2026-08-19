"""Lineage claims must be checkable, and the two known ways they lie must fail."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from yoyo.artifacts.lineage import (
    ArtifactDigest,
    LineageError,
    RunManifest,
    canonical_json_sha256,
    compare_reproduction,
    digest_file,
)

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _manifest(**overrides) -> RunManifest:
    base = dict(
        artifact_id="a1",
        protocol_version="p1",
        generator_path="tools/build_thing.py",
        generator_commit="a" * 40,
        config_sha256="b" * 64,
        generated_at=NOW,
        builder_committed_at=NOW - timedelta(hours=1),
    )
    base.update(overrides)
    return RunManifest(**base)


def test_a_complete_manifest_validates():
    assert _manifest().manifest_sha256()


def test_an_empty_identifier_is_refused():
    with pytest.raises(LineageError, match="generator_commit cannot be empty"):
        _manifest(generator_commit="")


def test_a_malformed_config_hash_is_refused():
    with pytest.raises(LineageError, match="SHA-256"):
        _manifest(config_sha256="short")


def test_a_naive_timestamp_is_refused():
    with pytest.raises(LineageError, match="timezone-aware"):
        _manifest(generated_at=datetime(2026, 8, 19, 12, 0))


def test_an_artifact_older_than_its_builder_is_refused():
    """The failure that states itself badly: everything looks complete."""
    manifest = _manifest(
        generated_at=NOW - timedelta(days=2), builder_committed_at=NOW
    )
    with pytest.raises(LineageError, match="before its builder"):
        manifest.assert_builder_landed_first()


def test_the_ordering_check_cannot_pass_by_omission():
    with pytest.raises(LineageError, match="without both"):
        _manifest(builder_committed_at=None).assert_builder_landed_first()


def test_a_correctly_ordered_build_passes():
    assert _manifest().assert_builder_landed_first() is None


def test_reproducibility_is_recorded_per_axis():
    manifest = _manifest(reproducibility={"content": True, "split_assignment": False})
    payload = manifest.to_dict()["reproducibility"]
    assert payload["content"] is True
    assert payload["split_assignment"] is False
    assert payload["sample_set"] is None  # not claimed, not assumed


def test_an_unknown_reproducibility_axis_is_refused():
    with pytest.raises(LineageError, match="unknown reproducibility axes"):
        _manifest(reproducibility={"vibes": True})


def test_the_w20_midbox_shape_of_failure_is_representable():
    """2635/2635 images identical AND 405 samples in the wrong split."""
    manifest = _manifest(
        reproducibility={"content": True, "sample_set": True, "split_assignment": False}
    )
    payload = manifest.to_dict()["reproducibility"]
    assert payload["content"] is True and payload["split_assignment"] is False


def test_comparison_reports_every_set_separately():
    original = [ArtifactDigest("a.png", "1" * 64, 10), ArtifactDigest("b.png", "2" * 64, 10)]
    rebuilt = [ArtifactDigest("a.png", "1" * 64, 10), ArtifactDigest("b.png", "3" * 64, 10),
               ArtifactDigest("c.png", "4" * 64, 10)]
    result = compare_reproduction(original, rebuilt)
    assert result["identical"] == ["a.png"]
    assert result["differing"] == ["b.png"]
    assert result["added_by_rebuild"] == ["c.png"]
    assert result["missing_from_rebuild"] == []


def test_canonical_json_refuses_non_finite_values():
    with pytest.raises(ValueError):
        canonical_json_sha256({"x": float("nan")})


def test_canonical_json_ignores_key_order():
    assert canonical_json_sha256({"a": 1, "b": 2}) == canonical_json_sha256({"b": 2, "a": 1})


def test_digesting_a_missing_file_is_refused(tmp_path: Path):
    with pytest.raises(LineageError, match="does not exist"):
        digest_file(tmp_path / "nope.bin")


def test_digesting_a_real_file_reports_size_and_hash(tmp_path: Path):
    target = tmp_path / "x.bin"
    target.write_bytes(b"hello")
    digest = digest_file(target)
    assert digest.size_bytes == 5
    assert len(digest.sha256) == 64
