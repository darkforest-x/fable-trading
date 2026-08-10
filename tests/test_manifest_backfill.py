"""Guard the manifest backfill against silent drift.

``scripts/backfill_dataset_manifests.py`` re-implements three hash helpers that
originate in ``scripts/build_local_signal_v2_stageb.py``. They must stay
byte-compatible: ``event_id_of`` in particular is what lets
``dense_owner_w20_midbox`` rows join against ``local_signal_v2_stageb`` rows.
If the two ever disagree, the ids stop joining and nothing else would notice.

The builder imports cv2 / pandas / yoyo at module level, so this test lifts the
three function definitions out with ``ast`` and executes only those, rather than
importing the module. Same trick as ``~/yoyo-trading/tests/test_layer_boundaries.py``
(that file moved to the sibling repo in the 2026-08-03 split): enforce the
contract mechanically instead of by agreement.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
BUILDER = PROJECT / "scripts" / "build_local_signal_v2_stageb.py"
BACKFILL = PROJECT / "scripts" / "backfill_dataset_manifests.py"

SHARED_FUNCS = ("event_id_of", "config_hash_of", "sha256_file")


def _extract(path: Path, names: tuple[str, ...]) -> dict:
    """exec just the named top-level functions, with stdlib deps only."""
    tree = ast.parse(path.read_text())
    wanted = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    found = {n.name for n in wanted}
    missing = set(names) - found
    assert not missing, f"{path.name} no longer defines {sorted(missing)}"
    ns: dict = {"hashlib": hashlib, "json": json, "Path": Path}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(path), "exec"), ns)
    return ns


@pytest.fixture(scope="module")
def impls():
    return _extract(BUILDER, SHARED_FUNCS), _extract(BACKFILL, SHARED_FUNCS)


def test_event_id_agrees(impls):
    a, b = impls
    cases = [
        ("0G_USDT_SWAP", 2095, "0G_USDT_SWAP_002130_pad200"),
        ("BTC_USDT_SWAP", 0, "x"),
        ("ETH_USDT_SWAP", 123456, "ETH_USDT_SWAP_123456_pad200"),
    ]
    for sym, anchor, stem in cases:
        assert a["event_id_of"](sym, anchor, stem) == b["event_id_of"](sym, anchor, stem)


def test_event_id_matches_shipped_stageb_manifest(impls):
    """The formula must still reproduce ids already written to disk."""
    man = PROJECT / "datasets" / "local_signal_v2_stageb" / "w20_manifest.json"
    src = PROJECT / "datasets" / "dense_owner_w20_midbox" / "w20_manifest.json"
    if not (man.exists() and src.exists()):
        pytest.skip("datasets not present")
    _a, b = impls
    by_stem = {r["stem"]: r for r in json.loads(src.read_text())}
    rows = json.loads(man.read_text())[:200]
    checked = 0
    for r in rows:
        w = by_stem.get(r["source_stem"])
        if w is None:
            continue
        assert b["event_id_of"](w["symbol"], w["mid_global"], w["stem"]) == r["event_id"]
        checked += 1
    assert checked > 0, "no rows cross-checked"


def test_config_hash_agrees(impls):
    a, b = impls
    kw = {"protocol": "p", "kind": "empty_bg", "seed": 20260807, "nested": [1, 2]}
    assert a["config_hash_of"](**kw) == b["config_hash_of"](**kw)


def test_sha256_agrees(impls, tmp_path):
    a, b = impls
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01" * (1 << 19))  # spans the 1MB read chunk
    assert a["sha256_file"](f) == b["sha256_file"](f) == hashlib.sha256(f.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "stem, kind, sym, w0, win",
    [
        ("0G_USDT_SWAP_001264_w24_hardneg_dense", "dense", "0G_USDT_SWAP", 1264, 24),
        ("BTC_USDT_SWAP_038240_w23_hardneg_dense", "dense", "BTC_USDT_SWAP", 38240, 23),
    ],
)
def test_hardneg_dense_regex(stem, kind, sym, w0, win):
    import importlib.util

    spec = importlib.util.spec_from_file_location("_backfill", BACKFILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m = mod.HARDNEG_DENSE_RE.match(stem)
    assert m, f"{stem} did not parse"
    assert m.group("sym") == sym
    assert int(m.group("w0")) == w0
    assert int(m.group("win")) == win


def test_hardneg_weak_regex():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_backfill", BACKFILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m = mod.HARDNEG_WEAK_RE.match("SPX_USDT_SWAP_004260_w24_hardneg_weak_c0.300")
    assert m
    assert m.group("sym") == "SPX_USDT_SWAP"
    assert float(m.group("conf")) == 0.300


@pytest.mark.parametrize(
    "ds",
    [
        "dense_owner_w20_midbox",
        "local_signal_v2_stageb",
        "local_signal_v2_stageb_strictneg_v2",
    ],
)
def test_shipped_audit_invariants_hold(ds):
    """Every §12.1 invariant recorded in manifest_audit.json must be green."""
    p = PROJECT / "datasets" / ds / "manifest_audit.json"
    if not p.exists():
        pytest.skip(f"{ds}: run scripts/backfill_dataset_manifests.py first")
    rep = json.loads(p.read_text())
    failed = [k for k, v in rep["invariants"].items() if not v]
    assert not failed, f"{ds}: failing invariants {failed}"
    assert rep["counts"]["manifest_rows"] == rep["counts"]["disk_images"]


def test_spec12_timestamp_fields_are_required():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_backfill", BACKFILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert {
        "anchor_timestamp",
        "decision_timestamp",
        "visible_end_timestamp",
        "window_start_timestamp",
        "window_end_timestamp",
    } <= set(mod.REQUIRED_SPEC12)
