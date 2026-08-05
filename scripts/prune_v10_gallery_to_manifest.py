#!/usr/bin/env python3
"""Keep only manifest cards in v10_yolo_5d_gallery; drop orphan PNGs; rebuild HTML.

Does not re-run YOLO. Conf-band filters come from write_html in scan script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import scripts.scan_v10_yolo_5d_gallery as g  # noqa: E402

OUT = PROJECT / "analysis" / "output" / "v10_yolo_5d_gallery"
MANIFEST = OUT / "manifest.json"
IMG = OUT / "images"


def main() -> int:
    if not MANIFEST.is_file():
        raise SystemExit(f"missing {MANIFEST}")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cards: list[dict] = list(data["cards"])
    keep = {c["rel_img"].split("/")[-1] for c in cards}
    if not IMG.is_dir():
        raise SystemExit(f"missing {IMG}")

    disk = list(IMG.glob("*.png"))
    orphans = [p for p in disk if p.name not in keep]
    missing = keep - {p.name for p in disk}
    print(
        f"manifest={len(cards)} disk={len(disk)} keep={len(keep)} "
        f"orphans={len(orphans)} missing={len(missing)}",
        flush=True,
    )
    if missing:
        print("WARN missing images for manifest cards:", sorted(missing)[:10], flush=True)

    for p in orphans:
        p.unlink()
    print(f"deleted orphans={len(orphans)}", flush=True)

    # drop partial/legacy index noise if present
    partial = OUT / "partial.html"
    if partial.is_file():
        partial.unlink()
        print("deleted partial.html", flush=True)

    days = int(data.get("days", 5))
    conf = float(data.get("conf", 0.3))
    g.write_html(cards, OUT / "index.html", days=days, conf=conf)
    print(
        f"DONE index.html cards={len(cards)} conf_bands={ [b[0] for b in g.CONF_BANDS] }",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
