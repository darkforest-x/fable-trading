"""The frame renderer must not rebuild the cross-repository bridge it lost.

Before consolidation, render_frames.py took `fable_root` and `yoyo_root`,
pushed them onto sys.path at call time, and then imported the chart renderer
from whatever that turned up. It had to: the renderer was in another
repository. Now it is here, and the bridge is exactly the kind of thing that
grows back -- someone hits an ImportError, adds a sys.path line, and the module
silently starts rendering with a different repo's pixels than the detector was
trained on.

Static, so it holds without importing cv2 or loading market data.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "yoyo" / "layers" / "l1_detection" / "onset" / "events" / "render_frames.py"


def _tree() -> ast.Module:
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def test_the_module_is_here_to_be_checked() -> None:
    assert MODULE.is_file(), f"{MODULE} is missing"


def _attribute_chains(tree: ast.Module) -> set[str]:
    """Dotted names as written, e.g. sys.path, Path.home -- code only, not prose.

    Substring scanning would flag the module docstring, which names the bridge
    it describes removing. It did, on the first run of this test.
    """
    chains = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parts = [node.attr]
        current = node.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            chains.add(".".join(reversed(parts)))
    return chains


def test_it_never_touches_sys_path() -> None:
    chains = _attribute_chains(_tree())
    assert "sys.path" not in chains, (
        "render_frames.py manipulates sys.path again. That was the "
        "cross-repository bridge; the renderer lives in this repository now."
    )


def test_it_imports_the_canonical_renderer_not_a_forwarding_shim() -> None:
    modules = {
        node.module
        for node in ast.walk(_tree())
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "yoyo.layers.l1_detection.render" in modules, (
        f"expected the canonical renderer import, found {sorted(modules)}"
    )
    legacy = {m for m in modules if m.startswith("src.")}
    assert not legacy, (
        f"render_frames.py imports the legacy forwarding shims {sorted(legacy)}; "
        "migrated code uses the canonical module directly (task book C3.1)"
    )


def test_the_bar_source_is_still_an_argument_not_a_home_directory() -> None:
    """Library code that finds data by guessing is how the root got wrong before."""
    chains = _attribute_chains(_tree())
    guessers = {c for c in chains if c.endswith((".home", ".expanduser", ".cwd"))}
    assert not guessers, f"render_frames.py discovers paths itself via {sorted(guessers)}"
    signature = next(
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef) and node.name == "make_frame_renderer"
    )
    names = [arg.arg for arg in signature.args.args]
    assert "ohlcv_root" in names, f"ohlcv_root must stay explicit, got {names}"
