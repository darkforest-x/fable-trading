"""Four authoritative documents, no fifth, and none of them pointing at nothing.

The consolidation's root cause was five repositories each maintaining a
HANDOFF.md that called itself the current truth. Merging them fixed the
repositories; nothing yet stops the same thing happening inside one repository,
where it looks like diligence rather than fragmentation.

So the division is written down in docs/DOC_MAP.md and checked here:

    HANDOFF.md          current truth        changes every round
    CLAUDE.md/AGENTS.md the iron rules       changes rarely
    ROADMAP.md          phases and gates     changes when a phase moves
    PROJECT_CHARTER.md  structure and flow   barely changes

The path check matters more than it looks. A charter that says "put X in
yoyo/contracts/" after that directory has been renamed is worse than no
charter: it is confidently wrong, and the reader has no way to tell which of
its claims still hold.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHARTER = REPO / "docs" / "PROJECT_CHARTER.md"

AUTHORITATIVE = {
    "HANDOFF.md": "current truth",
    "CLAUDE.md": "iron rules",
    "AGENTS.md": "iron rules, kept identical to CLAUDE.md",
    "ROADMAP.md": "phases and gates",
    "docs/PROJECT_CHARTER.md": "structure and flow",
    "docs/DOC_MAP.md": "index",
}

#: Things that look like paths but are not: placeholders, GitHub slugs, drive
#: letters, and the bulk archive, which lives in the main working tree rather
#: than in the worktree this suite may be running from.
NOT_A_PATH = re.compile(r"[<>*]|^https?:|^[A-Z]:|^darkforest-x/|pXX|archive/consolidated")


@pytest.mark.parametrize(("rel", "role"), sorted(AUTHORITATIVE.items()))
def test_each_authoritative_document_exists(rel: str, role: str):
    assert (REPO / rel).is_file(), f"{rel} is missing -- it holds {role}"


def test_the_iron_rules_are_identical_in_both_copies():
    """CLAUDE.md and AGENTS.md must not drift; two rule sets is no rule set.

    Everything below the title line, which scripts/hooks/pre-commit also
    ignores: the files are the same document under two names because Claude
    Code reads one and Codex the other, so only the heading may differ.
    """
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8").split("\n", 1)[1]
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8").split("\n", 1)[1]
    assert claude == agents, (
        "CLAUDE.md and AGENTS.md have diverged. They are the same document under "
        "two names because different agents read different filenames; when they "
        "differ, whichever one you read is the wrong one."
    )


def test_the_charter_names_the_division_it_is_part_of():
    text = CHARTER.read_text(encoding="utf-8")
    for rel in ("HANDOFF.md", "CLAUDE.md", "ROADMAP.md"):
        assert rel in text, (
            f"the charter does not say what {rel} is for, so a reader cannot tell "
            "which document to put the next thing in"
        )


def test_every_path_the_charter_names_exists():
    """A charter with dead paths is confidently wrong, which is worse than silent."""
    text = CHARTER.read_text(encoding="utf-8")
    referenced = {
        match
        for match in re.findall(r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_./-]*)`", text)
        if not NOT_A_PATH.search(match)
    }
    missing = sorted(
        rel for rel in referenced
        if not (REPO / rel).exists() and not (REPO / rel.rstrip("/")).exists()
    )
    assert not missing, (
        f"the charter names paths that do not exist: {missing}. Either the tree "
        "moved and the charter did not, or the charter describes an intention "
        "rather than the repository."
    )


def test_the_charter_defers_rather_than_restating_the_iron_rules():
    """Duplicated rules drift. The charter points; CLAUDE.md states.

    Checked by looking for the rule *numbers*: a charter that starts numbering
    iron rules has begun keeping a second copy of them.
    """
    text = CHARTER.read_text(encoding="utf-8")
    assert "CLAUDE.md" in text
    numbered = re.findall(r"铁律\s*\d+", text)
    assert not numbered, (
        f"the charter cites iron rules by number ({numbered}); numbers drift when "
        "CLAUDE.md is renumbered. Describe the constraint and point at CLAUDE.md."
    )


def test_no_fifth_document_claims_to_be_the_current_truth():
    """The failure mode that produced this consolidation, one directory down."""
    claimants = []
    for path in sorted(REPO.glob("*.md")) + sorted((REPO / "docs").glob("*.md")):
        rel = str(path.relative_to(REPO))
        if rel in AUTHORITATIVE:
            continue
        head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
        if "当前真相" in head:
            claimants.append(rel)
    assert not claimants, (
        f"{claimants} declare 当前真相 in their opening. There is one, and it is at "
        "the top of HANDOFF.md."
    )
