"""Keep the viewport-only change outside signal, key and trade semantics."""
from pathlib import Path

PINE = Path(__file__).resolve().parents[2] / "yoyo/evaluation/pine"
OLD = (PINE / "monday_range_v2_12.pine").read_text()
NEW = (PINE / "monday_range_v2_13.pine").read_text()


def executable(source):
    return [
        line.strip() for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]


def test_engines_and_latest_fib_are_unchanged():
    boundary = "// ---- Fibonacci on the latest Monday range"
    assert NEW[NEW.index(boundary):] == OLD[OLD.index(boundary):]


def test_only_retention_setting_changes_before_range_renderer():
    old = OLD[:OLD.index("// ---- Monday rays")]
    new = NEW[:NEW.index("// ---- Monday range history")]
    expected = old.replace("V2.12", "V2.13").replace(
        "优先保留当前区间和射线", "优先保留可见区间和射线"
    )
    expected = "\n".join(
        line for line in expected.splitlines()
        if not line.startswith("int keepWeeks =")
    )
    assert executable(new) == executable(expected)
