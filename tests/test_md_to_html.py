"""Regression tests for the dependency-free project report renderer."""

from scripts.md_to_html import convert


def test_convert_joins_wrapped_paragraphs_and_list_items() -> None:
    rendered = convert(
        """A paragraph that is
wrapped in the source.

- One item that is
  wrapped in the source.
- Second item.
"""
    )

    assert "<p>A paragraph that is wrapped in the source.</p>" in rendered
    assert "<li>One item that is wrapped in the source.</li>" in rendered
    assert "<li>Second item.</li>" in rendered
    assert rendered.count("<ul>") == 1
    assert rendered.count("</ul>") == 1


def test_convert_renders_ordered_lists_without_breaking_following_blocks() -> None:
    rendered = convert(
        """1. First step that is
   wrapped in the source.
2. Second step.

## Next heading

| A | B |
|---|---|
| 1 | 2 |
"""
    )

    assert "<ol>" in rendered
    assert "<li>First step that is wrapped in the source.</li>" in rendered
    assert "<li>Second step.</li>" in rendered
    assert "</ol>" in rendered
    assert "<h2>Next heading</h2>" in rendered
    assert "<table>" in rendered
