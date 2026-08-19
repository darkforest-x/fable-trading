#!/usr/bin/env python3
"""Build short-window mid-box dataset from Label Studio / pad200 owner boxes.

Owner protocol (2026-08-07):
  1. For each old positive box, take the **middle bar** of that box.
  2. Small signal box = middle ± half bars, half ∈ {2, 3}  → 5 or 7 bars.
  3. Outer window length W ∈ {20..30}, placed at a **random** offset so the
     small box sits fully inside the window (any horizontal position).
  4. Re-render W bars (MA computed on full series, then slice). YOLO label =
     the small box only.

Source of truth for this build: positives in ``datasets/dense_owner_v14_pad200``
(``*_pad200`` stems with non-empty labels). Those already passed pad200 MAD
alignment; we recover the global bar span via re-render MAD vs the stored PNG,
then apply the mid-box protocol on the series.

Usage:
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/build_w20_midbox_dataset.py \\
      --preview 6
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/build_w20_midbox_dataset.py \\
      --limit 0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
# yoyo lives in this repository again (single-repo consolidation, 2026-08).
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from yoyo.data.loader import list_series, load_series  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import (  # noqa: E402
    make_chart_transform,
    render_chart,
)
from src.detection.owner_eval import is_eval_stem, split_of, symbol_of  # noqa: E402

ORIG_WINDOW = 200
WIN_MIN = 20
WIN_MAX = 30
HALF_CHOICES = (2, 3)
MAX_STORED_MAD = 5.0
STEM_RE = re.compile(r"^(?:okx_)?(?P<body>.+?)_(?P<idx>\d{4,8})(?:_pad200)?$")

COLOR_SMALL = (0, 0, 220)  # red-ish BGR for small signal box
COLOR_SRC = (220, 180, 0)  # cyan for source pad200 box


@dataclass
class SampleResult:
    stem: str
    out_stem: str
    symbol: str
    split: str
    mid_global: int
    half: int
    small_bars: tuple[int, int]
    win_len: int
    win_start: int
    small_local: tuple[int, int]
    box_pos_frac: float  # small-box center x in window [0,1]
    stored_mad: float
    end_time: str
    out_img: str
    out_lbl: str


class Skip(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail or reason
        super().__init__(self.detail)


def parse_stem(stem: str) -> tuple[str, int] | None:
    m = STEM_RE.match(stem)
    if not m:
        return None
    return m.group("body"), int(m.group("idx"))


def base_stem(stem: str) -> str:
    return stem[: -len("_pad200")] if stem.endswith("_pad200") else stem


def stable_seed(*parts: object) -> int:
    h = hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()
    return int(h[:8], 16)


def read_boxes(path: Path) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    if not path.exists():
        return boxes
    for line in path.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        boxes.append(tuple(map(float, parts[1:5])))
    return boxes


def bar_from_x(tf, x: float) -> int:
    if tf.n_bars <= 1 or tf.plot_w <= 0:
        return 0
    idx = round((float(x) - tf.left) / tf.plot_w * (tf.n_bars - 1))
    return int(min(max(idx, 0), tf.n_bars - 1))


def series_groups() -> dict[tuple[str, str], list[Path]]:
    """Merge kline_fetched (+ cache if present) under this project's data/."""
    groups: dict[tuple[str, str], list[Path]] = {}
    for d in (PROJECT / "data" / "kline_cache", PROJECT / "data" / "kline_fetched"):
        if not d.is_dir():
            continue
        part = list_series(cache_dir=d, bar="15m")
        for k, paths in part.items():
            groups.setdefault(k, []).extend(paths)
    return groups


_GROUPS: dict[tuple[str, str], list[Path]] | None = None
_SERIES_CACHE: dict[str, pd.DataFrame] = {}


def resolve_series(sym_hint: str) -> pd.DataFrame | None:
    global _GROUPS
    if _GROUPS is None:
        _GROUPS = series_groups()
    if sym_hint in _SERIES_CACHE:
        return _SERIES_CACHE[sym_hint]
    groups = _GROUPS
    # exact
    for (_src, sym), paths in groups.items():
        if sym == sym_hint or sym == f"{sym_hint}_SWAP" or sym.replace("_SWAP", "") == sym_hint:
            df = load_series(paths)
            if len(df) >= ORIG_WINDOW + 50:
                _SERIES_CACHE[sym_hint] = df
                return df
    candidates: list[tuple[str, list[Path]]] = []
    for (_src, sym), paths in groups.items():
        if sym_hint in sym or sym in sym_hint:
            candidates.append((sym, paths))
    candidates.sort(key=lambda x: (0 if x[0].endswith("_USDT_SWAP") else 1, len(x[0])))
    for _sym, paths in candidates[:3]:
        df = load_series(paths)
        if len(df) >= ORIG_WINDOW + 50:
            _SERIES_CACHE[sym_hint] = df
            return df
    return None


def candidate_win_starts(n: int, idx: int, window: int = ORIG_WINDOW) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for mode, start in (
        ("end_incl", idx - window + 1),
        ("start", idx),
        ("end_excl", idx - window),
    ):
        if 0 <= start <= n - window:
            out.append((mode, start))
    seen: set[int] = set()
    uniq: list[tuple[str, int]] = []
    for mode, start in out:
        if start in seen:
            continue
        seen.add(start)
        uniq.append((mode, start))
    return uniq


def box_local_span(box: tuple[float, float, float, float], tf) -> tuple[int, int]:
    xc, yc, w, h = box
    x1 = (xc - w / 2) * tf.width
    x2 = (xc + w / 2) * tf.width
    b0 = bar_from_x(tf, x1)
    b1 = bar_from_x(tf, x2)
    if b1 < b0:
        b0, b1 = b1, b0
    return b0, b1


_GOLDEN: dict | None = None


def golden_pool() -> dict:
    global _GOLDEN
    if _GOLDEN is None:
        path = PROJECT / "data" / "golden_pool.json"
        _GOLDEN = json.loads(path.read_text()) if path.exists() else {}
    return _GOLDEN


def resolve_pad_window(
    stem: str,
    pad_img: np.ndarray,
    boxes: list[tuple[float, float, float, float]],
    enriched: pd.DataFrame,
) -> tuple[int, float, str, list[tuple[int, int]]]:
    """Return (pad_start, mad, mode, local_box_spans) matching stored PNG.

    For ``*_pad200`` stems the stem index points at the *original* 200 window.
    Reconstruct pad_start like build_crop_pad200_dataset: original win +
    golden_pool box right edge → cut → pad_start = cut - 199, then MAD-gate
    against the stored pad200 PNG.
    """
    n = len(enriched)
    parsed = parse_stem(stem)
    if not parsed:
        raise Skip("bad_stem", stem)
    _body, idx = parsed
    stored_f = pad_img.astype(np.float32)
    best: tuple[float, int, str, list[tuple[int, int]]] | None = None

    def _consider(mode: str, start: int, label_boxes: list[tuple[float, float, float, float]]) -> None:
        nonlocal best
        if not (0 <= start <= n - ORIG_WINDOW):
            return
        sub = enriched.iloc[start : start + ORIG_WINDOW].reset_index(drop=True)
        if len(sub) != ORIG_WINDOW:
            return
        rr, _ = render_chart(sub, out_path=None)
        if rr.shape != pad_img.shape:
            return
        mad = float(np.mean(np.abs(stored_f - rr.astype(np.float32))))
        tf = make_chart_transform(sub)
        spans = [box_local_span(b, tf) for b in label_boxes]
        if best is None or mad < best[0]:
            best = (mad, start, mode, spans)

    base = base_stem(stem)
    gp_boxes = golden_pool().get(base) or golden_pool().get(stem)
    # Path A (pad200): reconstruct pad window from original gold + stem index.
    if gp_boxes and stem.endswith("_pad200"):
        for mode, win_start in candidate_win_starts(n, idx, ORIG_WINDOW):
            sub = enriched.iloc[win_start : win_start + ORIG_WINDOW].reset_index(drop=True)
            if len(sub) != ORIG_WINDOW:
                continue
            tf = make_chart_transform(sub)
            rights: list[int] = []
            for box in gp_boxes:
                _b0, b1 = box_local_span(tuple(box), tf)
                rights.append(b1)
            if not rights:
                continue
            cut_global = win_start + int(max(rights))
            pad_start = cut_global - ORIG_WINDOW + 1
            _consider(f"gp_{mode}", pad_start, boxes)

    # Path B: treat stem index as the window currently drawn (orig 200 labels).
    for mode, start in candidate_win_starts(n, idx, ORIG_WINDOW):
        _consider(f"direct_{mode}", start, boxes)

    if best is None:
        raise Skip("no_win_cand", f"{stem} idx={idx} n={n}")
    mad, pad_start, mode, spans = best
    if mad > MAX_STORED_MAD:
        raise Skip("mad_fail", f"{stem} mad={mad:.3f} mode={mode}")
    return pad_start, mad, mode, spans


def yolo_box_from_bars(
    tf,
    win_df: pd.DataFrame,
    b0: int,
    b1: int,
) -> tuple[float, float, float, float] | None:
    """Axis-aligned YOLO box covering bars [b0,b1] using those bars' high/low."""
    n = len(win_df)
    b0 = int(max(0, b0))
    b1 = int(min(n - 1, b1))
    if b1 < b0:
        return None
    seg = win_df.iloc[b0 : b1 + 1]
    hi = float(seg["high"].max())
    lo = float(seg["low"].min())
    if not np.isfinite(hi) or not np.isfinite(lo) or hi <= lo:
        return None
    x1 = tf.x_at(b0) - tf.candle_half_w
    x2 = tf.x_at(b1) + tf.candle_half_w
    y1 = tf.y_at(hi)
    y2 = tf.y_at(lo)
    x1 = float(np.clip(x1, 0, tf.width - 1))
    x2 = float(np.clip(x2, 1, tf.width))
    y1 = float(np.clip(y1, 0, tf.height - 1))
    y2 = float(np.clip(y2, 1, tf.height))
    if x2 - x1 < 4 or abs(y2 - y1) < 4:
        return None
    xc = (x1 + x2) / 2 / tf.width
    yc = (y1 + y2) / 2 / tf.height
    bw = (x2 - x1) / tf.width
    bh = abs(y2 - y1) / tf.height
    return (xc, yc, bw, bh)


def _draw_boxes(img: np.ndarray, boxes, color, thickness: int = 2) -> np.ndarray:
    vis = img.copy()
    h, w = vis.shape[:2]
    for xc, yc, bw, bh in boxes:
        x1 = int((xc - bw / 2) * w)
        x2 = int((xc + bw / 2) * w)
        y1 = int((yc - bh / 2) * h)
        y2 = int((yc + bh / 2) * h)
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
    return vis


def _caption(img: np.ndarray, text: str) -> np.ndarray:
    vis = img.copy()
    cv2.rectangle(vis, (0, 0), (min(vis.shape[1], 900), 36), (255, 255, 255), -1)
    cv2.putText(vis, text, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (20, 20, 20), 2, cv2.LINE_AA)
    return vis


def process_one(
    stem: str,
    lbl_path: Path,
    img_path: Path,
    out_img: Path,
    out_lbl: Path,
    *,
    rng: np.random.Generator,
    draw_box_on_image: bool = False,
    aug_i: int = 0,
) -> SampleResult:
    if is_eval_stem(base_stem(stem)):
        raise Skip("eval_symbol", stem)
    boxes = read_boxes(lbl_path)
    if not boxes:
        raise Skip("empty_label", stem)
    parsed = parse_stem(stem)
    if not parsed:
        raise Skip("bad_stem", stem)
    body, _idx = parsed
    df = resolve_series(body)
    if df is None:
        df = resolve_series(symbol_of(base_stem(stem)))
    if df is None:
        raise Skip("no_series", body)
    enriched = add_mas(df)
    pad_img = cv2.imread(str(img_path))
    if pad_img is None:
        raise Skip("no_image", str(img_path))
    pad_start, mad, _mode, spans = resolve_pad_window(stem, pad_img, boxes, enriched)
    if not spans:
        raise Skip("no_span", stem)
    # Primary = rightmost source box (launch side of the dense zone).
    b0, b1 = max(spans, key=lambda s: s[1])
    mid_local = (b0 + b1) // 2
    mid_global = pad_start + mid_local

    half = int(rng.choice(HALF_CHOICES))
    s0 = mid_global - half
    s1 = mid_global + half
    n = len(enriched)
    if s0 < 0 or s1 >= n:
        raise Skip("small_oob", f"mid={mid_global} half={half} n={n}")

    win_len = int(rng.integers(WIN_MIN, WIN_MAX + 1))
    # Window [w0, w0+win_len-1] must contain [s0, s1].
    w0_lo = max(0, s1 - win_len + 1)
    w0_hi = min(s0, n - win_len)
    if w0_hi < w0_lo:
        raise Skip("no_room", f"win_len={win_len} small={s0}-{s1} n={n}")
    win_start = int(rng.integers(w0_lo, w0_hi + 1))
    win_df = enriched.iloc[win_start : win_start + win_len].reset_index(drop=True)
    if len(win_df) != win_len:
        raise Skip("short_win", stem)

    img, tf = render_chart(win_df, out_path=None)
    loc0 = s0 - win_start
    loc1 = s1 - win_start
    yolo = yolo_box_from_bars(tf, win_df, loc0, loc1)
    if yolo is None:
        raise Skip("empty_yolo", stem)

    out_img.parent.mkdir(parents=True, exist_ok=True)
    out_lbl.parent.mkdir(parents=True, exist_ok=True)
    vis = _draw_boxes(img, [yolo], COLOR_SMALL, 2) if draw_box_on_image else img
    cv2.imwrite(str(out_img), vis)
    out_lbl.write_text(f"0 {yolo[0]:.6f} {yolo[1]:.6f} {yolo[2]:.6f} {yolo[3]:.6f}\n")

    ts = win_df.iloc[-1].get("open_time", win_df.index[-1])
    box_pos = float((loc0 + loc1) / 2 / max(win_len - 1, 1))
    return SampleResult(
        stem=stem,
        out_stem=out_img.stem,
        symbol=body,
        split=split_of(base_stem(stem)),
        mid_global=mid_global,
        half=half,
        small_bars=(s0, s1),
        win_len=win_len,
        win_start=win_start,
        small_local=(loc0, loc1),
        box_pos_frac=box_pos,
        stored_mad=mad,
        end_time=str(ts),
        out_img=str(out_img),
        out_lbl=str(out_lbl),
    )


def iter_positive_pad200(
    src: Path, *, pad200_only: bool = True
) -> list[tuple[str, Path, Path]]:
    """(stem, label, image) for non-empty labels.

    Default ``pad200_only``: only ``*_pad200`` positives (MAD-aligned owner
    boxes). Val originals in v14 are mid-window copies and often fail kline MAD.
    """
    out: list[tuple[str, Path, Path]] = []
    for split in ("train", "val"):
        ld = src / "labels" / split
        if not ld.is_dir():
            continue
        for lbl in sorted(ld.glob("*.txt")):
            if not lbl.read_text().strip():
                continue
            stem = lbl.stem
            if pad200_only and not stem.endswith("_pad200"):
                continue
            img = src / "images" / split / f"{stem}.png"
            if not img.exists():
                for sp in ("train", "val"):
                    alt = src / "images" / sp / f"{stem}.png"
                    if alt.exists():
                        img = alt
                        break
            if img.exists():
                out.append((stem, lbl, img))
    return out


def run_preview(
    src: Path, n: int, out_dir: Path, seed: int, *, pad200_only: bool = True
) -> list[SampleResult]:
    out_dir.mkdir(parents=True, exist_ok=True)
    items = iter_positive_pad200(src, pad200_only=pad200_only)
    rng = np.random.default_rng(seed)
    results: list[SampleResult] = []
    seen_sym: set[str] = set()
    # prefer unique symbols
    order = list(range(len(items)))
    rng.shuffle(order)
    for allow_dup in (False, True):
        for i in order:
            if len(results) >= n:
                break
            stem, lbl, img = items[i]
            sym = symbol_of(base_stem(stem))
            if not allow_dup and sym in seen_sym:
                continue
            if any(r.stem == stem for r in results):
                continue
            out_img = out_dir / f"{stem}_w20.png"
            out_lbl = out_dir / f"{stem}_w20.txt"
            try:
                # per-sample rng from seed+stem for reproducibility
                local = np.random.default_rng(stable_seed(seed, stem))
                res = process_one(
                    stem, lbl, img, out_img, out_lbl,
                    rng=local, draw_box_on_image=True,
                )
            except Skip as e:
                print(f"skip {stem}: {e.reason} {e.detail}")
                continue
            # side-by-side compare with source pad200
            src_im = cv2.imread(str(img))
            src_boxes = read_boxes(lbl)
            left = _caption(
                _draw_boxes(src_im, src_boxes, COLOR_SRC, 2),
                f"SRC pad200  {stem[:40]}",
            )
            right = _caption(
                cv2.imread(str(out_img)),
                f"W{res.win_len} mid±{res.half} local={res.small_local} pos={res.box_pos_frac:.2f}",
            )
            h = max(left.shape[0], right.shape[0])

            def _pad(im):
                if im.shape[0] == h:
                    return im
                o = np.full((h, im.shape[1], 3), 255, np.uint8)
                o[: im.shape[0]] = im
                return o

            cmp = np.hstack([_pad(left), _pad(right)])
            cv2.imwrite(str(out_dir / f"compare_{stem}.png"), cmp)
            results.append(res)
            seen_sym.add(sym)
            print(json.dumps(asdict(res), ensure_ascii=False))
    (out_dir / "preview_summary.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2)
    )
    return results


def run_full(
    src: Path,
    dst: Path,
    *,
    seed: int,
    limit: int,
    augs: int,
    pad200_only: bool = True,
) -> dict:
    items = iter_positive_pad200(src, pad200_only=pad200_only)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
    counts = {"train": 0, "val": 0}
    skip_reasons: dict[str, int] = {}
    manifest: list[dict] = []
    n_done = 0
    for stem, lbl, img in items:
        if limit and n_done >= limit:
            break
        for aug_i in range(max(1, augs)):
            local = np.random.default_rng(stable_seed(seed, stem, aug_i))
            split = split_of(base_stem(stem))
            suffix = "" if aug_i == 0 else f"_a{aug_i}"
            out_stem = f"{base_stem(stem)}_w20{suffix}"
            out_img = dst / "images" / split / f"{out_stem}.png"
            out_lbl = dst / "labels" / split / f"{out_stem}.txt"
            if out_img.exists() and out_lbl.exists():
                counts[split] += 1
                n_done += 1
                continue
            try:
                res = process_one(
                    stem, lbl, img, out_img, out_lbl,
                    rng=local, draw_box_on_image=False, aug_i=aug_i,
                )
            except Skip as e:
                skip_reasons[e.reason] = skip_reasons.get(e.reason, 0) + 1
                continue
            counts[res.split] += 1
            manifest.append(asdict(res))
            n_done += 1
            if n_done % 100 == 0:
                print(f"... {n_done} ok  skips={skip_reasons}")
    (dst / "data.yaml").write_text(
        f"path: {dst.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"names:\n  0: dense_start\n"
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": "mid_bar_pm_2_3_random_window_20_30",
        "src": str(src),
        "out": str(dst),
        "seed": seed,
        "augs": augs,
        "counts": counts,
        "skip_reasons": skip_reasons,
        "n_manifest": len(manifest),
        "win_min": WIN_MIN,
        "win_max": WIN_MAX,
        "half_choices": list(HALF_CHOICES),
    }
    (dst / "w20_summary.json").write_text(json.dumps(summary, indent=2))
    (dst / "w20_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=PROJECT / "datasets" / "dense_owner_v14_pad200")
    ap.add_argument("--out", type=Path, default=PROJECT / "datasets" / "dense_owner_w20_midbox")
    ap.add_argument("--preview", type=int, default=0, help="write N preview pairs and exit")
    ap.add_argument(
        "--preview-dir",
        type=Path,
        default=PROJECT / "analysis" / "output" / "w20_midbox_preview",
    )
    ap.add_argument("--seed", type=int, default=20260807)
    ap.add_argument("--limit", type=int, default=0, help="0 = all positives")
    ap.add_argument("--augs", type=int, default=1, help="random windows per source box")
    ap.add_argument(
        "--include-non-pad200",
        action="store_true",
        help="also process non-pad200 positives (more MAD skips)",
    )
    args = ap.parse_args()
    pad200_only = not args.include_non_pad200

    if args.preview > 0:
        run_preview(
            args.src, args.preview, args.preview_dir, args.seed, pad200_only=pad200_only
        )
        print(f"preview → {args.preview_dir}")
        return 0
    run_full(
        args.src,
        args.out,
        seed=args.seed,
        limit=args.limit,
        augs=args.augs,
        pad200_only=pad200_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
