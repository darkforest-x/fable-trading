"""Shared owner-taste F1 evaluation + eval/val split membership (single source
of truth).

Four queue scripts grew their own copies of the F1 loop and they were one tweak
away from drifting apart. Import this instead:

    from src.detection.owner_eval import evaluate_owner_f1
    best, sweep = evaluate_owner_f1("runs/.../best.pt", "datasets/dense_owner_v4")

Matching rule (identical to all published numbers so far): greedy IoU>=0.30
per GT box against unused predictions; F1 sweep over confidences.

2026-07-16: the same drift happened again, to the split rules. `is_eval` had
grown SIX copies across scripts, and — worse — the name meant two different
things: some took a stem and split it internally, others required the caller to
split first. Passing the wrong one does not raise; it hashes
"BTC_USDT_SWAP_001234" instead of "BTC_USDT_SWAP", silently deciding eval
membership at random. Meanwhile scripts/build_owner_dataset.py had no filter at
all and is the likely origin of dense_owner_v7h's 596 leaked eval images, which
invalidated the decisive A/B.

So the split rules live here now, with names that make the argument type
impossible to get wrong: is_eval_symbol takes a SYMBOL, is_eval_stem takes a
STEM. Do not re-copy them.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

# The frozen eval ruler is the LIST in datasets/owner_eval_frozen/MANIFEST.json
# (47 symbols / 464 images, materialized 2026-07-16), not a live hash rule.
# It was a hash rule (sha1 % 7 == 0) before, and that broke twice in one day:
# stems come in two spellings ("okx_BCH_USDT_SWAP_012340" vs "BCH_USDT_SWAP"),
# the rule hashed the raw spelling, and BCH_USDT_SWAP ended up on BOTH sides of
# the eval line -- 18 images in the ruler, 1 sibling in training. A ruler whose
# membership shifts when someone edits a parsing function is not frozen; the
# hash now only classifies symbols the manifest has never heard of (new fetches).
EVAL_MOD = 7
VAL_MOD = 5
_MANIFEST = Path(__file__).resolve().parents[2] / "datasets/owner_eval_frozen/MANIFEST.json"


def symbol_of(stem: str) -> str:
    """Image stem -> normalized symbol, tolerant of both stem spellings.

    "okx_BCH_USDT_SWAP_012340" -> "BCH_USDT_SWAP"
    "BCH_USDT_SWAP_012340"     -> "BCH_USDT_SWAP"
    "BCH_USDT_SWAP"            -> "BCH_USDT_SWAP"  (no index to strip)
    """
    s = re.sub(r"^okx_", "", stem)
    return re.sub(r"_\d+$", "", s)


@lru_cache(maxsize=1)
def _manifest_symbols() -> frozenset[str]:
    if _MANIFEST.exists():
        return frozenset(json.loads(_MANIFEST.read_text())["symbols"])
    return frozenset()


def is_eval_symbol(sym: str) -> bool:
    """Is this SYMBOL in the frozen eval ruler? Manifest first, hash fallback.

    The fallback only fires for symbols absent from every dataset the manifest
    was built from -- i.e. genuinely new fetches -- so it can never move an
    existing symbol across the line.
    """
    sym = symbol_of(sym)  # normalize even if a raw spelling sneaks in
    manifest = _manifest_symbols()
    if manifest:
        return sym in manifest
    return int(hashlib.sha1(sym.encode()).hexdigest(), 16) % EVAL_MOD == 0


def is_eval_stem(stem: str) -> bool:
    """Is this image STEM's symbol in the frozen eval ruler?"""
    return is_eval_symbol(symbol_of(stem))


def split_of(stem: str) -> str:
    """train/val for a stem, split by SYMBOL so no symbol straddles the split.

    Deliberately still hash-based (it is a working split, not a frozen ruler),
    but on the NORMALIZED symbol so both spellings land on the same side.
    """
    return "val" if int(hashlib.sha1(symbol_of(stem).encode()).hexdigest(), 16) % VAL_MOD == 0 else "train"


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0]-a[2]/2, a[1]-a[3]/2, a[0]+a[2]/2, a[1]+a[3]/2
    bx1, by1, bx2, by2 = b[0]-b[2]/2, b[1]-b[3]/2, b[0]+b[2]/2, b[1]+b[3]/2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2]*a[3] + b[2]*b[3] - inter
    return inter / union if union > 0 else 0.0


def _load_txt(p: Path):
    if not p.exists():
        return []
    return [tuple(map(float, l.split()[1:]))
            for l in p.read_text().splitlines() if len(l.split()) == 5]


def evaluate_owner_f1(weights: str | Path, dataset_dir: str | Path,
                      confs=(0.15, 0.2, 0.3, 0.4), iou_thr: float = 0.30,
                      split: str = "val") -> tuple[dict, list[dict]]:
    """Return (best_row, sweep_rows); rows: conf/f1/p/r/tp/fp/fn."""
    from ultralytics import YOLO  # heavyweight import kept local
    dataset_dir = Path(dataset_dir)
    vi, vl = dataset_dir / "images" / split, dataset_dir / "labels" / split
    model = YOLO(str(weights))
    images = sorted(vi.glob("*.png"))
    sweep = []
    for conf in confs:
        tp = fp = fn = 0
        for img in images:
            gt = _load_txt(vl / (img.stem + ".txt"))
            res = model.predict(str(img), conf=conf, verbose=False)[0]
            preds = ([tuple(map(float, b)) for b in res.boxes.xywhn.cpu().numpy()]
                     if res.boxes is not None else [])
            used = set()
            for g in gt:
                m = next((k for k, p in enumerate(preds)
                          if k not in used and _iou(g, p) >= iou_thr), None)
                if m is None:
                    fn += 1
                else:
                    used.add(m)
                    tp += 1
            fp += len(preds) - len(used)
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        sweep.append({"conf": conf, "f1": round(f1, 3), "p": round(prec, 3),
                      "r": round(rec, 3), "tp": tp, "fp": fp, "fn": fn})
    best = max(sweep, key=lambda r: r["f1"])
    return best, sweep
