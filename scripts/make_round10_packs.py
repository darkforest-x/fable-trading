"""Round-10 active-learning packs — zero overlap with any prior owner labels.

Why now: owner_v11_chain frozen-F1 0.658 still sits on the rising limb of the
label curve (v6 0.595 → v9 0.627 → v10 0.645 → v11 0.658). Live trading does
not *block* on more labels, but another 1–1.5k high-value corrections remains
positive EV before saturation. Batch size 1200 (4×300) matches a realistic
owner week without the burnout of another 3000 dump.

Triple non-repeat guards (same spirit as round9, tightened):
  1. exact stem ∉ golden_pool;
  2. bar index not within WINDOW of any already-labelled window of the same symbol;
  3. is_eval_stem / stockish excluded;
  4. stems already present in any previous tasks_round*.json skipped as belt-and-suspenders.

Source reservoirs (already rendered, no new chart render required):
  datasets/dense_2025h2, dense_2026h1  (leftovers after rounds 8–9)

Prelabels: models/owner_best.pt (v11_chain). Half uncertain conf∈[0.15,0.45].

Usage:
  PYTHONPATH=. .venv/bin/python scripts/make_round10_packs.py
  PYTHONPATH=. .venv/bin/python scripts/make_round10_packs.py --count 600 --chunks 2
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.owner_eval import is_eval_stem  # noqa: E402

ROUND = 10
WINDOW = 200
DEFAULT_COUNT = 1200
DEFAULT_CHUNKS = 4
UNCERTAIN_LO, UNCERTAIN_HI = 0.15, 0.45
PRELABEL_CONF = 0.20
IOU_DEDUP = 0.30
RESERVOIRS = (
    PROJECT / "datasets/dense_2025h2",
    PROJECT / "datasets/dense_2026h1",
)
STEM_RE = re.compile(r"^(?P<sym>.+)_(?P<idx>\d{4,})$")


def labelled_index(pool: dict) -> dict[str, list[int]]:
    by: dict[str, list[int]] = defaultdict(list)
    for stem in pool:
        m = STEM_RE.match(stem)
        if m:
            by[m.group("sym")].append(int(m.group("idx")))
    for sym in by:
        by[sym].sort()
    return by


def prior_task_stems() -> set[str]:
    """Every stem ever shipped as a Label Studio pack task."""
    out: set[str] = set()
    root = PROJECT / "output/label_studio"
    if not root.exists():
        return out
    for path in root.glob("tasks_round*.json"):
        try:
            tasks = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(tasks, list):
            continue
        for t in tasks:
            stem = (t.get("data") or {}).get("stem")
            if stem:
                out.add(str(stem))
    return out


def iou(a, b) -> float:
    ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2
    ax2, ay2 = a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2
    bx2, by2 = b[0] + b[2] / 2, b[1] + b[3] / 2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    u = a[2] * a[3] + b[2] * b[3] - inter
    return inter / u if u > 0 else 0.0


def collect_candidates(
    pool: set[str],
    labelled: dict[str, list[int]],
    prior_tasks: set[str],
) -> list[tuple[str, Path, str]]:
    """(stem, img_path, reservoir_name) with all non-repeat guards."""
    out: list[tuple[str, Path, str]] = []
    seen_local: set[str] = set()
    for res in RESERVOIRS:
        for split in ("train", "val"):
            idir = res / "images" / split
            if not idir.exists():
                continue
            for img in idir.glob("*.png"):
                stem = img.stem
                if stem in pool or stem in prior_tasks or stem in seen_local:
                    continue
                if is_eval_stem(stem):
                    continue
                m = STEM_RE.match(stem)
                if not m:
                    continue
                sym, idx = m.group("sym"), int(m.group("idx"))
                # guard 2: window proximity to any labelled bar of same symbol
                if any(abs(idx - j) < WINDOW for j in labelled.get(sym, [])):
                    continue
                seen_local.add(stem)
                out.append((stem, img, res.name))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT)
    ap.add_argument("--chunks", type=int, default=DEFAULT_CHUNKS)
    ap.add_argument("--seed", type=int, default=20260719)
    ap.add_argument("--score-pool", type=int, default=None,
                    help="how many candidates to YOLO-score before picking (default count*5)")
    args = ap.parse_args()

    pool_raw = json.loads((PROJECT / "data/golden_pool.json").read_text())
    pool = set(pool_raw.keys())
    labelled = labelled_index(pool_raw)
    prior = prior_task_stems()
    print(
        f"golden_pool={len(pool)} prior_task_stems={len(prior)} "
        f"labelled_symbols={len(labelled)}",
        flush=True,
    )

    candidates = collect_candidates(pool, labelled, prior)
    print(f"eligible unlabeled (triple-guard): {len(candidates)}", flush=True)
    if len(candidates) < args.count:
        raise SystemExit(f"only {len(candidates)} eligible < requested {args.count}")

    rng = random.Random(args.seed)
    rng.shuffle(candidates)
    score_n = args.score_pool or min(len(candidates), max(args.count * 5, 4000))
    to_score = candidates[:score_n]
    print(f"scoring {len(to_score)} with owner_best …", flush=True)

    from ultralytics import YOLO

    weights = PROJECT / "models/owner_best.pt"
    if not weights.exists():
        raise SystemExit(f"missing {weights}")
    model = YOLO(str(weights))
    scored: list[tuple[str, Path, str, list[tuple[float, float, float, float]], float]] = []
    t0 = time.time()
    for k, (stem, img, res_name) in enumerate(to_score, 1):
        r = model.predict(str(img), conf=0.10, verbose=False)[0]
        cand: list[tuple[float, tuple]] = []
        if r.boxes is not None and len(r.boxes):
            for b, c in zip(r.boxes.xywhn.cpu().numpy(), r.boxes.conf.cpu().numpy()):
                cand.append((float(c), tuple(map(float, b[:4]))))
        cand.sort(reverse=True)
        keep: list[tuple] = []
        for c, box in cand:
            if c >= PRELABEL_CONF and not any(iou(box, kb) >= IOU_DEDUP for kb in keep):
                keep.append(box)
        top = cand[0][0] if cand else 0.0
        scored.append((stem, img, res_name, keep, top))
        if k % 500 == 0:
            print(f"  scored {k}/{len(to_score)}  {k/(time.time()-t0):.1f}/s", flush=True)

    unc = [s for s in scored if UNCERTAIN_LO <= s[4] <= UNCERTAIN_HI]
    rest = [s for s in scored if not (UNCERTAIN_LO <= s[4] <= UNCERTAIN_HI)]
    rng.shuffle(unc)
    rng.shuffle(rest)
    n_unc = min(len(unc), args.count // 2)
    take = unc[:n_unc] + rest[: args.count - n_unc]
    rng.shuffle(take)
    if len(take) < args.count:
        raise SystemExit(f"after scoring only {len(take)} < {args.count}")
    take = take[: args.count]
    print(
        f"pack: uncertain {n_unc} + rest {len(take)-n_unc} = {len(take)} "
        f"(unc pool was {len(unc)})",
        flush=True,
    )

    # Final safety: re-check no stem in pool / prior / self-dup
    final = []
    used: set[str] = set()
    for item in take:
        stem = item[0]
        assert stem not in pool, stem
        assert stem not in prior, stem
        assert stem not in used, stem
        assert not is_eval_stem(stem), stem
        m = STEM_RE.match(stem)
        assert m
        idx = int(m.group("idx"))
        sym = m.group("sym")
        assert not any(abs(idx - j) < WINDOW for j in labelled.get(sym, [])), stem
        used.add(stem)
        final.append(item)

    out_dir = PROJECT / "output/label_studio"
    out_dir.mkdir(parents=True, exist_ok=True)
    per = -(-len(final) // args.chunks)
    manifest = {
        "round": ROUND,
        "count": len(final),
        "chunks": args.chunks,
        "seed": args.seed,
        "weights": str(weights.relative_to(PROJECT)),
        "guards": [
            "stem_not_in_golden_pool",
            "stem_not_in_prior_tasks_round_json",
            f"bar_index_gap>={WINDOW}_vs_labelled_same_symbol",
            "not_is_eval_stem",
        ],
        "stems": [s[0] for s in final],
        "by_reservoir": {},
    }
    for stem, img, res_name, boxes, top in final:
        manifest["by_reservoir"][res_name] = manifest["by_reservoir"].get(res_name, 0) + 1

    for i in range(args.chunks):
        chunk = final[i * per : (i + 1) * per]
        if not chunk:
            continue
        tasks = []
        for stem, img, res_name, boxes, top in chunk:
            # path relative to datasets/ for Label Studio local-files
            rel = img.relative_to(PROJECT / "datasets")
            tasks.append({
                "data": {
                    "image": f"/data/local-files/?d={rel.as_posix()}",
                    "stem": stem,
                    "split": img.parent.name,
                    "reservoir": res_name,
                    "round": ROUND,
                },
                "predictions": [{
                    "model_version": "owner_v11_chain",
                    "score": float(top),
                    "result": [{
                        "type": "rectanglelabels",
                        "from_name": "label",
                        "to_name": "image",
                        "original_width": 1280,
                        "original_height": 742,
                        "value": {
                            "x": (cx - w / 2) * 100,
                            "y": (cy - h / 2) * 100,
                            "width": w * 100,
                            "height": h * 100,
                            "rectanglelabels": ["dense_cluster"],
                        },
                    } for cx, cy, w, h in boxes],
                }],
            })
        out = out_dir / f"tasks_round{ROUND}_chunk{i+1}.json"
        out.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
        n_pre = sum(1 for t in tasks if t["predictions"][0]["result"])
        print(f"  {out.name}: {len(tasks)} tasks ({n_pre} with prelabels)", flush=True)

    man_path = out_dir / f"round{ROUND}_manifest.json"
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest → {man_path} stems={len(manifest['stems'])}", flush=True)
    print(f"reservoirs {manifest['by_reservoir']}", flush=True)
    print("ROUND10_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
