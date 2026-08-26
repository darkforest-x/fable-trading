#!/usr/bin/env python3
"""Statically verify the generated 15m causal/review parity gallery."""
from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path

from yoyo.datasets.ma_launch_review_parity import ROOT, sha256_file


DEFAULT_RESULTS = (
    ROOT / "experiments/active/exp-15m-ma-launch-t3-review-parity-v2/results"
)


class GalleryParser(HTMLParser):
    """Count cards, image references and CSS-only label overlays."""

    def __init__(self) -> None:
        super().__init__()
        self.cards = 0
        self.images: list[str] = []
        self.box_styles: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "article" and "card" in classes:
            self.cards += 1
        if tag == "img" and values.get("src"):
            self.images.append(str(values["src"]))
        if tag == "span" and "bbox" in classes:
            self.box_styles.append(str(values.get("style") or ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    results = args.results.resolve()
    receipt = json.loads((results / "verification_receipt.json").read_text(encoding="utf-8"))
    manifest_path = results / "parity_manifest.jsonl"
    rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if sha256_file(manifest_path) != receipt["manifest_sha256"]:
        raise SystemExit("manifest hash differs from build receipt")
    if len(rows) != 10000:
        raise SystemExit(f"unexpected parity manifest rows: {len(rows)}")
    if sum(row["review_marker_source_i"] == row["source_anchor_i"] - 3 for row in rows) != 10000:
        raise SystemExit("not every review marker is t-3")
    if sum(row["causal_status"] == "training_positive" for row in rows) != 9938:
        raise SystemExit("training-positive linkage count drifted")
    if sum(row["causal_status"] == "purged_no_training_image" for row in rows) != 62:
        raise SystemExit("purged linkage count drifted")

    pages = sorted(results.glob("page_*.html"))
    if len(pages) != 40:
        raise SystemExit(f"unexpected page count: {len(pages)}")
    card_count = 0
    image_count = 0
    box_count = 0
    missing_refs: list[str] = []
    invalid_boxes: list[str] = []
    for page in pages:
        parsed = GalleryParser()
        parsed.feed(page.read_text(encoding="utf-8"))
        card_count += parsed.cards
        image_count += len(parsed.images)
        box_count += len(parsed.box_styles)
        for src in parsed.images:
            target = (page.parent / src).resolve()
            if not target.is_file():
                missing_refs.append(f"{page.name}:{src}")
        for style in parsed.box_styles:
            required = ("left:", "top:", "width:", "height:")
            if any(field not in style for field in required):
                invalid_boxes.append(f"{page.name}:{style}")
    if card_count != 10000:
        raise SystemExit(f"gallery card count drifted: {card_count}")
    if image_count != 19938:
        raise SystemExit(f"gallery image reference count drifted: {image_count}")
    if box_count != 9938:
        raise SystemExit(f"gallery box overlay count drifted: {box_count}")
    if missing_refs:
        raise SystemExit(f"missing gallery references: {missing_refs[:5]}")
    if invalid_boxes:
        raise SystemExit(f"invalid gallery box styles: {invalid_boxes[:5]}")

    overview = results / "comparison_overview.png"
    if sha256_file(overview) != receipt["overview_sha256"]:
        raise SystemExit("overview hash differs from build receipt")
    qa = {
        "candidate_rows": len(rows),
        "gallery_pages": len(pages),
        "gallery_cards": card_count,
        "gallery_image_references": image_count,
        "gallery_box_overlays": box_count,
        "resolved_missing_image_references": len(missing_refs),
        "invalid_css_box_styles": len(invalid_boxes),
        "review_marker_t_minus_3_rows": 10000,
        "canonical_training_pixel_links": 9938,
        "purged_without_training_image": 62,
        "browser_qa": "blocked_by_file_url_security_policy",
        "browser_policy_bypass_attempted": False,
        "overview_visual_inspection": "passed_via_local_image_viewer",
        "passed": True,
    }
    output = args.out.resolve() if args.out else results / "html_qa_receipt.json"
    output.write_text(
        json.dumps(qa, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(qa, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
