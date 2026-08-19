"""Every file the ledger calls a DIRECT_PORT must still be the source's bytes.

Task book section 11.2 asks for byte parity and says explicitly that "looks the
same" does not count. This is that check, applied to the whole migration at
once rather than to a sample: the ledger recorded a source SHA-256 for every
file that crossed a repository boundary, so re-hashing the destinations turns
"we copied it faithfully" into something the suite re-derives on every run.

It also survives the source repositories being archived, which a test that
imported them would not.

An ADAPT_AND_PORT entry is expected to differ -- that is what the decision
means -- so those are checked the other way: the destination must match the
hash recorded at the time of adaptation, which catches a later edit that nobody
recorded.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "reports" / "consolidation" / "migration_ledger.jsonl"


def _entries() -> list[dict]:
    if not LEDGER.is_file():
        return []
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


ENTRIES = _entries()
COPIED = [
    e for e in ENTRIES
    if e["decision"] in ("DIRECT_PORT", "ADAPT_AND_PORT", "HISTORICAL_REPORT")
]


def test_the_ledger_exists_and_is_not_empty():
    assert ENTRIES, f"{LEDGER} is missing or empty -- nothing below checks anything"


def test_every_entry_can_name_its_source():
    """Task book section 11.1: source identity."""
    incomplete = [
        e.get("destination_path") or e.get("source_path")
        for e in ENTRIES
        if not (e.get("source_repo") and e.get("source_commit") and e.get("source_path"))
    ]
    assert not incomplete, f"ledger entries without full provenance: {incomplete}"


@pytest.mark.parametrize(
    "entry",
    COPIED or [None],
    ids=lambda e: (e["destination_path"] if e else "<empty ledger>"),
)
def test_a_ported_file_still_has_its_recorded_hash(entry):
    if entry is None:
        pytest.skip("no copied entries in the ledger yet")
    destination = REPO / entry["destination_path"]
    assert destination.is_file(), (
        f"{entry['destination_path']} is in the migration ledger but not on disk"
    )
    actual = _sha256(destination)
    assert actual == entry["destination_sha256"], (
        f"{entry['destination_path']} has changed since it was ported "
        f"({entry['decision']} from {entry['source_repo']}@{entry['source_commit'][:12]}). "
        "If the edit was intended, re-record it in the ledger; if it was not, this is "
        "the drift the ledger exists to catch."
    )


def test_direct_ports_are_byte_identical_to_their_source():
    """The claim DIRECT_PORT makes, restated from the recorded hashes.

    HISTORICAL_REPORT carries the same claim: a migrated report whose bytes
    changed is a rewritten conclusion.
    """
    mismatched = [
        e["destination_path"]
        for e in ENTRIES
        if e["decision"] in ("DIRECT_PORT", "HISTORICAL_REPORT")
        and e["source_sha256"] != e["destination_sha256"]
    ]
    assert not mismatched, (
        f"{mismatched} are recorded as DIRECT_PORT but their source and destination "
        "hashes differ. DIRECT_PORT means the bytes; those belong under ADAPT_AND_PORT."
    )


def test_adaptations_really_are_adaptations():
    """An ADAPT entry whose bytes match the source is a mislabelled DIRECT_PORT."""
    identical = [
        e["destination_path"]
        for e in ENTRIES
        if e["decision"] == "ADAPT_AND_PORT" and e["source_sha256"] == e["destination_sha256"]
    ]
    assert not identical, (
        f"{identical} are recorded as ADAPT_AND_PORT but nothing changed. Record them "
        "as DIRECT_PORT so the distinction keeps meaning something."
    )


def test_reference_only_entries_were_not_copied_in():
    """REFERENCE_ONLY means the bytes stayed where they were."""
    for entry in ENTRIES:
        if entry["decision"] != "REFERENCE_ONLY":
            continue
        assert entry.get("destination_path") is None, (
            f"{entry['source_path']} is REFERENCE_ONLY but records a destination"
        )
        assert entry.get("reference_pointer"), (
            f"{entry['source_path']} is REFERENCE_ONLY with no pointer to where it lives"
        )


def test_no_ported_file_exceeds_the_size_ceiling_without_a_reason():
    """Task book section 3.6: large products stay out of git."""
    ceiling = 2 * 1024 * 1024
    oversized = [
        (e["destination_path"], e["size_bytes"])
        for e in COPIED
        if e.get("size_bytes", 0) > ceiling
    ]
    assert not oversized, (
        f"{oversized} were committed despite exceeding {ceiling} bytes; they belong in "
        "artifacts/registry.yaml as a pointer, not in git"
    )
