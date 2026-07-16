"""Regenerate round8 chunk3+4 only — anti-dup sampling.

Fixes owner feedback (2026-07-17): original chunk3/4 felt like re-labelling
the same coins. Root cause was no per-symbol cap and no exclusion of bases
already hammered in round6/7 (even when stems/files differed).

Rules for the replacement 1000 tasks (2 x 500):
  1. Never reuse stems already in round8 chunk1/2 (in-progress labelling).
  2. Prefer bases NEVER labelled in r6/r7; hard-exclude bases with >= MIN_HIST
     labels (default 5) in those exports.
  3. At most MAX_PER_BASE windows per base coin (default 2).
  4. Stride within this pack: no two selected windows of same symbol closer
     than MIN_GAP bars (default 400 = 2 windows).
  5. Drop windows whose wall-clock range overlaps any historical labelled
     window of the same base by >= OVERLAP_BARS bars (default 50).
  6. Prelabel with owner_best.pt + IoU dedup (same as round8).

Writes:
  output/label_studio/tasks_round8_chunk3_v2.json
  output/label_studio/tasks_round8_chunk4_v2.json

Does NOT touch chunk1/2 task files. Import as new LS projects
  round8_chunk3_v2 / round8_chunk4_v2
so the empty bad projects can be ignored/deleted by owner.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/make_round8_chunk34_v2.py
  PYTHONPATH=. .venv/bin/python scripts/make_round8_chunk34_v2.py --smoke 40
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402

WINDOW = 200
# Zero content-overlap with any already-selected (or chunk1/2) window of same symbol.
MIN_GAP = 200
MAX_PER_BASE = 5          # never/light-hist bases (new coins)
MAX_PER_BASE_HEAVY = 3    # bases already labelled a lot in r6/r7
MAX_PER_BASE_IN_C12 = 3   # bases already in chunk1/2 → at most +3 more (gap-enforced)
HEAVY_HIST = 8
N_TOTAL = 1000
N_CHUNKS = 2
UNCERTAIN_LO, UNCERTAIN_HI = 0.15, 0.45
PRELABEL_CONF = 0.20
IOU_DEDUP = 0.30
OVERLAP_BARS = 50
SEED = 20260717

OUT_DIR = PROJECT / "datasets/dense_2026h1/images/train"
LS_DIR = PROJECT / "output/label_studio"
KLINE = PROJECT / "data/kline_fetched"


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1, bx2, by2 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    u = a[2] * a[3] + b[2] * b[3] - inter
    return inter / u if u > 0 else 0.0


def parse_stem(stem: str) -> tuple[str | None, int | None]:
    m = re.match(r"(.+)_(\d+)$", stem or "")
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def base_coin(stem_or_sym: str) -> str:
    s = re.sub(r"_\d+$", "", stem_or_sym or "")
    s = s.replace("okx_", "")
    s = s.replace("_USDT_SWAP", "").replace("_USDT", "")
    return s


def stems_from_json(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  skip {path.name}: {exc}", flush=True)
        return []
    if isinstance(data, dict):
        data = data.get("tasks") or []
    out = []
    for t in data:
        if not isinstance(t, dict):
            continue
        d = t.get("data") or {}
        s = d.get("stem")
        if not s:
            img = str(d.get("image") or "")
            s = Path(img.split("?")[0]).stem
        if s and "local-files" not in s:
            out.append(s)
    return out


def load_used_chunk12() -> tuple[set[str], Counter, dict]:
    used: set[str] = set()
    for name in ("tasks_round8_chunk1.json", "tasks_round8_chunk2.json"):
        p = LS_DIR / name
        if p.exists():
            used |= set(stems_from_json(p))
    base_counts = Counter(base_coin(s) for s in used)
    # symbol -> bar indices already in chunk1/2 (for MIN_GAP against new picks)
    sym_idx: dict[str, list[int]] = defaultdict(list)
    for s in used:
        sym, idx = parse_stem(s)
        if sym and idx is not None:
            sym_idx[sym].append(idx)
    return used, base_counts, dict(sym_idx)


def load_hist_base_counts() -> Counter:
    """How many times each base coin appears in r6/r7 exports (any stem form)."""
    counts: Counter = Counter()
    patterns = [
        "export_round6_all.json",
        "export_round6_halfhalf_chunk*.json",
        "export_round7_all.json",
        "export_round7_chunk*.json",
    ]
    seen_files: set[str] = set()
    for pat in patterns:
        for p in LS_DIR.glob(pat):
            if p.name in seen_files:
                continue
            seen_files.add(p.name)
            for s in stems_from_json(p):
                counts[base_coin(s)] += 1
    return counts


def load_hist_intervals_by_base() -> dict[str, list[tuple[int, int]]]:
    """base -> list of (t_start_ms, t_end_ms) for historical labelled windows."""
    intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    stems: list[str] = []
    for pat in ("export_round6_all.json", "export_round7_all.json"):
        p = LS_DIR / pat
        if p.exists():
            stems.extend(stems_from_json(p))
    # also chunk exports if all missing
    if not stems:
        for p in LS_DIR.glob("export_round7_chunk*.json"):
            stems.extend(stems_from_json(p))

    ts_cache: dict[str, "pd.Series | None"] = {}

    def ts_for(sym: str):
        if sym in ts_cache:
            return ts_cache[sym]
        cands = list(KLINE.glob(f"okx_{sym}_15m_*.csv"))
        if not cands:
            # try without SWAP / with SWAP
            alt = sym.replace("_USDT_SWAP", "_USDT") if "_SWAP" in sym else sym + "_SWAP"
            if not alt.endswith("_USDT") and "_USDT" not in alt:
                alt = f"{sym}_USDT"
            cands = list(KLINE.glob(f"okx_{alt}_15m_*.csv"))
            if not cands and not sym.endswith("_USDT_SWAP"):
                cands = list(KLINE.glob(f"okx_{sym}_USDT_SWAP_15m_*.csv"))
                if cands:
                    sym = cands[0].name[len("okx_") :].split("_15m_")[0]
        if not cands:
            ts_cache[sym] = None
            return None
        try:
            df = pd.read_csv(cands[0], usecols=["ts"])
            ts_cache[sym] = df["ts"].astype("int64")
        except Exception:
            ts_cache[sym] = None
        return ts_cache[sym]

    for s in stems:
        sym, idx = parse_stem(s)
        if sym is None or idx is None:
            continue
        # messy stems like okx_DOOD_USDT_SWAP_033160
        if sym.startswith("okx_"):
            sym = sym[4:]
        ts = ts_for(sym)
        if ts is None or idx >= len(ts) or idx < 10:
            continue
        w = min(WINDOW, idx + 1)
        t0 = int(ts.iloc[idx - w + 1])
        t1 = int(ts.iloc[idx])
        intervals[base_coin(s)].append((t0, t1))
    return intervals


def candidate_pool(used: set[str]) -> list[str]:
    """All dense_2026h1 stems not in chunk1/2, eval/stockish filtered."""
    out = []
    for p in sorted(OUT_DIR.glob("*.png")):
        stem = p.stem
        if stem in used:
            continue
        sym, idx = parse_stem(stem)
        if sym is None or idx is None:
            continue
        if is_eval_symbol(sym) or is_stockish(sym):
            continue
        out.append(stem)
    return out


def window_ms(stem: str, ts_cache: dict) -> tuple[int, int] | None:
    sym, idx = parse_stem(stem)
    if sym is None or idx is None or idx < WINDOW - 1:
        return None
    if sym not in ts_cache:
        cands = list(KLINE.glob(f"okx_{sym}_15m_*.csv"))
        if not cands:
            ts_cache[sym] = None
        else:
            try:
                ts_cache[sym] = pd.read_csv(cands[0], usecols=["ts"])["ts"].astype("int64")
            except Exception:
                ts_cache[sym] = None
    ts = ts_cache[sym]
    if ts is None or idx >= len(ts):
        return None
    return int(ts.iloc[idx - WINDOW + 1]), int(ts.iloc[idx])


def overlaps_hist(stem: str, hist_iv: dict, ts_cache: dict) -> bool:
    b = base_coin(stem)
    ivs = hist_iv.get(b) or []
    if not ivs:
        return False
    w = window_ms(stem, ts_cache)
    if w is None:
        return False
    t0, t1 = w
    thr_ms = OVERLAP_BARS * 15 * 60 * 1000
    for ht0, ht1 in ivs:
        o0, o1 = max(t0, ht0), min(t1, ht1)
        if o1 >= o0 and (o1 - o0) >= thr_ms:
            return True
    return False


def select_stems(
    pool: list[str],
    hist_counts: Counter,
    hist_iv: dict,
    rng: random.Random,
    chunk12_base: Optional[Counter] = None,
    chunk12_sym_idx: Optional[dict] = None,
) -> list[str]:
    """Diversity-first: hard ban time-overlap + gap vs chunk1/2; soft prefer new bases."""
    ts_cache: dict = {}
    c12 = chunk12_base or Counter()
    c12_idx = chunk12_sym_idx or {}
    cleaned = []
    n_overlap = 0
    n_near_c12 = 0
    for stem in pool:
        if overlaps_hist(stem, hist_iv, ts_cache):
            n_overlap += 1
            continue
        sym, idx = parse_stem(stem)
        if sym and idx is not None and any(abs(idx - j) < MIN_GAP for j in c12_idx.get(sym, [])):
            n_near_c12 += 1
            continue
        cleaned.append(stem)
    print(
        f"  after filters: {len(cleaned)} (time-overlap -{n_overlap}, "
        f"near-chunk12 -{n_near_c12})",
        flush=True,
    )

    tiers: list[list[str]] = [[], [], []]
    for stem in cleaned:
        n = hist_counts.get(base_coin(stem), 0)
        if n == 0:
            tiers[0].append(stem)
        elif n < HEAVY_HIST:
            tiers[1].append(stem)
        else:
            tiers[2].append(stem)
    print(
        f"  tiers: never={len(tiers[0])} light(<{HEAVY_HIST})={len(tiers[1])} "
        f"heavy(>={HEAVY_HIST})={len(tiers[2])}",
        flush=True,
    )

    def cap_for(base: str) -> int:
        if c12.get(base, 0) >= 1:
            return MAX_PER_BASE_IN_C12
        n = hist_counts.get(base, 0)
        if n >= HEAVY_HIST:
            return MAX_PER_BASE_HEAVY
        return MAX_PER_BASE

    def take_diverse(cands: list[str], budget: int, already: list[str]) -> list[str]:
        by_base: dict[str, list[str]] = defaultdict(list)
        for s in cands:
            by_base[base_coin(s)].append(s)
        for b in by_base:
            by_base[b].sort(key=lambda s: parse_stem(s)[1] or 0)
            thinned = []
            last_idx = -10**9
            for s in by_base[b]:
                idx = parse_stem(s)[1] or 0
                if idx - last_idx >= MIN_GAP:
                    thinned.append(s)
                    last_idx = idx
            rng.shuffle(thinned)
            by_base[b] = thinned

        bases = list(by_base.keys())
        rng.shuffle(bases)
        picked: list[str] = []
        per_base: Counter = Counter(base_coin(s) for s in already)
        per_sym_idx: dict[str, list[int]] = defaultdict(list)
        # seed with chunk1/2 indices so new picks stay away from them
        for sym, idxs in c12_idx.items():
            per_sym_idx[sym].extend(idxs)
        for s in already:
            sym, idx = parse_stem(s)
            if sym and idx is not None:
                per_sym_idx[sym].append(idx)

        progress = True
        while len(picked) + len(already) < budget and progress:
            progress = False
            rng.shuffle(bases)
            for b in bases:
                if len(picked) + len(already) >= budget:
                    break
                if per_base[b] >= cap_for(b):
                    continue
                remain = by_base[b]
                chosen = None
                for s in remain:
                    sym, idx = parse_stem(s)
                    if idx is None:
                        continue
                    if any(abs(idx - j) < MIN_GAP for j in per_sym_idx.get(sym, [])):
                        continue
                    chosen = s
                    break
                if chosen is None:
                    continue
                by_base[b].remove(chosen)
                picked.append(chosen)
                per_base[b] += 1
                sym, idx = parse_stem(chosen)
                per_sym_idx[sym].append(idx)
                progress = True
        return picked

    selected: list[str] = []
    for tier in tiers:
        if len(selected) >= N_TOTAL:
            break
        selected.extend(take_diverse(tier, N_TOTAL, selected))
    print(
        f"  selected {len(selected)} "
        f"(unique bases {len({base_coin(s) for s in selected})})",
        flush=True,
    )
    bc = Counter(base_coin(s) for s in selected)
    print(f"  max per base: {max(bc.values()) if bc else 0}; "
          f"bases with >=2: {sum(1 for c in bc.values() if c >= 2)}", flush=True)
    never = sum(1 for s in selected if hist_counts.get(base_coin(s), 0) == 0)
    heavy = sum(1 for s in selected if hist_counts.get(base_coin(s), 0) >= HEAVY_HIST)
    print(f"  never-hist bases windows: {never}; heavy-hist windows: {heavy}", flush=True)
    return selected


def prelabel(stems: list[str]) -> list[tuple[str, list, float]]:
    from ultralytics import YOLO

    model = YOLO(str(PROJECT / "models/owner_best.pt"))
    scored = []
    t0 = time.time()
    for k, stem in enumerate(stems, 1):
        path = OUT_DIR / f"{stem}.png"
        if not path.exists():
            scored.append((stem, [], 0.0))
            continue
        r = model.predict(str(path), conf=0.10, verbose=False)[0]
        cand = []
        if r.boxes is not None and len(r.boxes):
            for b, c in zip(r.boxes.xywhn.cpu().numpy(), r.boxes.conf.cpu().numpy()):
                cand.append((float(c), tuple(map(float, b[:4]))))
        cand.sort(reverse=True)
        keep = []
        for c, box in cand:
            if c >= PRELABEL_CONF and not any(iou(box, kb) >= IOU_DEDUP for kb in keep):
                keep.append(box)
        top = cand[0][0] if cand else 0.0
        scored.append((stem, keep, top))
        if k % 200 == 0:
            print(f"  prelabel {k}/{len(stems)}  {k/(time.time()-t0):.1f}/s", flush=True)
    return scored


def write_chunks(scored: list[tuple[str, list, float]], rng: random.Random) -> list[Path]:
    unc = [s for s in scored if UNCERTAIN_LO <= s[2] <= UNCERTAIN_HI]
    rest = [s for s in scored if not (UNCERTAIN_LO <= s[2] <= UNCERTAIN_HI)]
    rng.shuffle(unc)
    rng.shuffle(rest)
    # keep diversity: already selected set is diverse; just interleave unc preference
    n_unc = min(len(unc), len(scored) // 2)
    take = unc[:n_unc] + rest[: len(scored) - n_unc]
    rng.shuffle(take)
    take = take[:N_TOTAL]
    # split available into N_CHUNKS as evenly as possible
    n = len(take)
    paths = []
    for i in range(N_CHUNKS):
        a = (i * n) // N_CHUNKS
        b = ((i + 1) * n) // N_CHUNKS
        chunk = take[a:b]
        if not chunk:
            print(f"  skip empty chunk{i+3}_v2", flush=True)
            continue
        tasks = []
        for stem, boxes, _ in chunk:
            tasks.append({
                "data": {
                    "image": f"/data/local-files/?d=dense_2026h1/images/train/{stem}.png",
                    "stem": stem,
                    "split": "train",
                },
                "predictions": [{
                    "model_version": "owner_v9_chain_dedup_v2",
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
        out = LS_DIR / f"tasks_round8_chunk{i+3}_v2.json"
        out.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
        n_pre = sum(1 for t in tasks if t["predictions"][0]["result"])
        print(f"  wrote {out.name}: {len(tasks)} tasks ({n_pre} prelabelled)", flush=True)
        paths.append(out)
    return paths


def audit(paths: list[Path], used12: set[str], hist_counts: Counter) -> None:
    stems = []
    for p in paths:
        stems.extend(stems_from_json(p))
    print("\n=== AUDIT v2 pack ===", flush=True)
    print(f"  n={len(stems)} unique={len(set(stems))}", flush=True)
    print(f"  ∩ chunk1/2: {len(set(stems) & used12)}", flush=True)
    bc = Counter(base_coin(s) for s in stems)
    print(f"  unique bases={len(bc)} max/base={max(bc.values()) if bc else 0}", flush=True)
    heavy = sum(1 for s in stems if hist_counts.get(base_coin(s), 0) >= HEAVY_HIST)
    print(f"  windows on heavy-hist bases (>={HEAVY_HIST}): {heavy}", flush=True)
    never = sum(1 for s in stems if hist_counts.get(base_coin(s), 0) == 0)
    print(f"  windows on never-labelled bases: {never}", flush=True)
    # gap check
    by_sym: dict[str, list[int]] = defaultdict(list)
    for s in stems:
        sym, idx = parse_stem(s)
        if sym and idx is not None:
            by_sym[sym].append(idx)
    close = 0
    for idxs in by_sym.values():
        idxs = sorted(idxs)
        for a, b in zip(idxs, idxs[1:]):
            if b - a < MIN_GAP:
                close += 1
    print(f"  same-symbol pairs with gap < {MIN_GAP}: {close}", flush=True)
    print(f"  top bases: {bc.most_common(10)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", type=int, default=None, help="only N stems end-to-end")
    args = ap.parse_args()

    rng = random.Random(SEED)
    t0 = time.time()
    print("Loading chunk1/2 + history…", flush=True)
    used12, c12_base, c12_sym_idx = load_used_chunk12()
    hist_counts = load_hist_base_counts()
    print(f"  chunk1/2 stems: {len(used12)} bases: {len(c12_base)}", flush=True)
    print(f"  hist bases: {len(hist_counts)} (heavy>={HEAVY_HIST}: "
          f"{sum(1 for c in hist_counts.values() if c >= HEAVY_HIST)})", flush=True)

    print("Loading hist wall-clock intervals…", flush=True)
    hist_iv = load_hist_intervals_by_base()
    print(f"  bases with intervals: {len(hist_iv)}", flush=True)

    print("Building candidate pool from dense_2026h1…", flush=True)
    pool = candidate_pool(used12)
    print(f"  candidates: {len(pool)}", flush=True)
    if args.smoke:
        rng.shuffle(pool)
        pool = pool[: max(args.smoke * 5, args.smoke)]

    selected = select_stems(
        pool, hist_counts, hist_iv, rng,
        chunk12_base=c12_base, chunk12_sym_idx=c12_sym_idx,
    )
    if args.smoke:
        selected = selected[: args.smoke]
    if len(selected) < (N_TOTAL if not args.smoke else args.smoke):
        print(f"WARNING: only {len(selected)} stems after filters "
              f"(wanted {N_TOTAL if not args.smoke else args.smoke})", flush=True)
    if not selected:
        raise SystemExit("no stems selected — check pool / filters")

    print(f"Prelabelling {len(selected)} images…", flush=True)
    scored = prelabel(selected)
    paths = write_chunks(scored, rng)
    audit(paths, used12, hist_counts)
    print(f"\nDONE in {(time.time() - t0)/60:.1f} min", flush=True)
    print("Import:\n"
          "  PYTHONPATH=. python3 scripts/ls_auto_import.py round8_chunk3_v2 "
          "output/label_studio/tasks_round8_chunk3_v2.json\n"
          "  PYTHONPATH=. python3 scripts/ls_auto_import.py round8_chunk4_v2 "
          "output/label_studio/tasks_round8_chunk4_v2.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
