"""The old import paths still work, and they resolve to the new tree.

23 modules under src/ are forwarding shims left by the 2026-08-03 four-layer
restructure. Hundreds of call sites in scripts/ and analysis/ still use them,
and this consolidation is not the moment to rewrite those -- but a shim that
silently stops forwarding is worse than no shim, because the old path keeps
importing and starts meaning something else.

Two properties, both cheap and both load-bearing:

  every shim imports without error
  the names it re-exports are the same objects as the canonical module's

Identity, not equality. `is` catches a shim that has quietly grown its own copy
of a function, which is exactly how "one canonical implementation" stops being
true without anyone editing a call site.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FORWARD = re.compile(r"from (yoyo\.[\w.]+) import \*")


def _shims() -> list[tuple[str, str]]:
    """(legacy module, canonical module) for every forwarding shim under src/."""
    found = []
    for path in sorted((REPO / "src").rglob("*.py")):
        match = FORWARD.search(path.read_text(encoding="utf-8", errors="ignore"))
        if not match:
            continue
        legacy = str(path.relative_to(REPO).with_suffix("")).replace("/", ".")
        found.append((legacy, match.group(1)))
    return found


SHIMS = _shims()


def test_the_shim_layer_still_exists():
    assert len(SHIMS) >= 20, (
        f"only {len(SHIMS)} forwarding shims found. If the migration finished and "
        "they were removed, delete this test with them -- do not lower the number."
    )


@pytest.mark.parametrize(("legacy", "canonical"), SHIMS, ids=[s[0] for s in SHIMS])
def test_a_legacy_path_forwards_to_the_canonical_module(legacy: str, canonical: str):
    legacy_module = importlib.import_module(legacy)
    canonical_module = importlib.import_module(canonical)

    exported = [
        name
        for name in dir(canonical_module)
        if not name.startswith("_") and hasattr(legacy_module, name)
    ]
    assert exported, f"{legacy} re-exports nothing from {canonical}"

    diverged = [
        name
        for name in exported
        if getattr(legacy_module, name) is not getattr(canonical_module, name)
    ]
    assert not diverged, (
        f"{legacy} exposes {diverged} as different objects from {canonical}. A shim "
        "that grows its own copy is how a second implementation appears without "
        "anyone editing a call site."
    )


def test_every_canonical_target_lives_in_this_repository():
    """A shim forwarding out of the repo would restore the split it replaced."""
    outside = {}
    for _, canonical in SHIMS:
        module = importlib.import_module(canonical)
        location = Path(module.__file__).resolve()
        if not location.is_relative_to(REPO):
            outside[canonical] = str(location)
    assert not outside, f"shims forward outside this repository: {outside}"
