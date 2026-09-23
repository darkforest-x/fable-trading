"""Install existing boxed research exemplars only into a never-edited library.

The manifest records original/revised geometry and confirmation provenance. These
retrospective exemplars are not causal input samples or new training labels.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .images import MAX_TOTAL_IMAGE_BYTES, image_from_bytes
from .schemas import MAX_REFERENCES
from .store import ReferenceRevisionConflict, ResearchStore

DEFAULT_PACK = (Path(__file__).resolve().parents[2] / "experiments" / "active" /
                "exp-spike-gemini-vision-20260923-v1" / "default_references_v3")


def install_default_references(store: ResearchStore, pack: Path = DEFAULT_PACK):
    """Seed revision zero once; preserve edits and deliberately cleared libraries."""
    current = store.get_references()
    if current["revision"] != 0 or current["items"]:
        return current
    manifest = json.loads((pack / "manifest.json").read_text())
    entries = manifest["items"]
    if not 0 < len(entries) <= MAX_REFERENCES:
        raise ValueError("默认参考图数量无效")
    images = []
    for entry in entries:
        filename = entry["file"]
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("默认参考图路径无效")
        path = pack / filename
        if path.is_symlink():
            raise ValueError("默认参考图不能是符号链接")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["source_sha256"]:
            raise ValueError("默认参考图校验失败")
        images.append(image_from_bytes(data, entry["name"]))
    if len({image.sha256 for image in images}) != len(images):
        raise ValueError("默认参考图不能重复")
    if sum(len(image.data) for image in images) > MAX_TOTAL_IMAGE_BYTES:
        raise ValueError("默认参考图合计不能超过 12 MB")
    items = [{"name": image.name, "sha256": image.sha256,
              "image_url": store.put_image(image)} for image in images]
    try:
        return store.replace_references(items, expected_revision=0)
    except ReferenceRevisionConflict:
        return store.get_references()
