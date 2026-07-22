#!/usr/bin/env python3
"""Crop-after-box + left-pad to fixed 200 bars (Owner plan A).

Protocol (NOT H-TIP true_tip re-anchor):
  1. From an owner-labeled 200-bar window, map GT box right edge → cut bar.
  2. Drop every bar AFTER cut (no future context).
  3. Take ``[cut - 199, cut]`` from the series so the canvas stays 200 bars
     (live bar width). If history is shorter than 200, skip the sample.
  4. Remap the *same* gold box onto the new bar span (dense MA zone stays;
     box sits near the right edge because the window ends at cut).

Stem index (dense_owner_v11 is a MIX):
  - round8/9-style stems: numeric suffix = window END bar (inclusive)
    → ``win_start = idx - 199`` (``end_incl``).
  - older / ``okx_*`` stems: numeric suffix = window START bar
    → ``win_start = idx`` (``start``).
Blindly preferring ``end_incl`` (bulk with MAD off) remaps gold boxes onto
the wrong OHLC for ~all ``okx_*`` positives — Owner sees "框不对". When the
stored PNG is available, candidates MUST be disambiguated by pixel MAD vs
re-render (default on; ``--no-mad-gate`` only for emergency resume).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_crop_pad200_dataset.py --preview 4
  PYTHONPATH=. .venv/bin/python scripts/build_crop_pad200_dataset.py \\
      --src datasets/dense_owner_v11 --out datasets/dense_owner_v14_pad200 \\
      --limit 0   # only after Owner says go
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_stem, symbol_of  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from scripts.build_htip_dataset import (  # noqa: E402
    WINDOW,
    bar_from_x,
    parse_stem,
    read_boxes,
    resolve_series,
)

PREVIEW_DIR = PROJECT / "analysis" / "output" / "v13_tiponly_preview"
TRY_DIR = PROJECT / "analysis" / "output" / "pad200_try"

# BGR: cyan = original Owner GT; green = remapped pad200 box
COLOR_ORIG_GT = (220, 180, 0)
COLOR_PAD_BOX = (0, 200, 0)

# Box-OHLC alignment gate after remap (same global bars → same closes).
MIN_BOX_CLOSE_CORR = 0.999
MAX_BOX_CLOSE_REL_ERR = 1e-6
# Stored PNG vs re-render: reject archive≠kline drift (LINK-class).
MAX_STORED_MAD = 5.0


@dataclass
class Pad200Result:
    stem: str
    symbol: str
    win_start: int
    cut_local: int
    cut_global: int
    pad_start: int
    n_bars: int
    box_bars_orig: tuple[int, int]
    box_bars_new: tuple[int, int]
    end_time: str
    out_img: str
    out_lbl: str
    compare_img: str = ""
    box_close_corr: float = float("nan")
    box_close_max_rel_err: float = float("nan")
    win_index_mode: str = ""
    stored_mad: float = float("nan")


def _price_at(tf, y_px: float) -> float:
    span = max(tf.price_max - tf.price_min, 1e-12)
    return tf.price_max - (float(y_px) - tf.top) / tf.plot_h * span


def _time_tag(ts) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.strftime("%Y%m%d_%H%M")


def _draw_yolo_boxes(
    img: np.ndarray,
    boxes: list[tuple[float, float, float, float]],
    color: tuple[int, int, int],
    thickness: int = 2,
) -> np.ndarray:
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
    cv2.rectangle(vis, (0, 0), (min(vis.shape[1], 520), 36), (255, 255, 255), -1)
    cv2.putText(
        vis, text, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA
    )
    return vis


def _find_image(src: Path, stem: str) -> Path | None:
    for split in ("train", "val"):
        p = src / "images" / split / f"{stem}.png"
        if p.exists():
            return p
    return None


def _candidate_win_starts(n: int, idx: int) -> list[tuple[str, int]]:
    """Stem-index conventions used across owner packs.

    Order is only a fallback when no stored PNG is available. v11 mixes
    end-inclusive (round8/9) and start-index (``okx_*`` / older); MAD vs the
    archived PNG is required to pick the right one. ``find_window_start`` in
    build_htip_dataset prefers start and mis-aligns round8/9-style stems.
    """
    out: list[tuple[str, int]] = []
    for mode, start in (
        ("end_incl", idx - WINDOW + 1),
        ("start", idx),
        ("end_excl", idx - WINDOW),
    ):
        if 0 <= start <= n - WINDOW:
            out.append((mode, start))
    # dedupe starts, keep first mode name
    seen: set[int] = set()
    uniq: list[tuple[str, int]] = []
    for mode, start in out:
        if start in seen:
            continue
        seen.add(start)
        uniq.append((mode, start))
    return uniq


def resolve_win_start(
    n: int,
    idx: int,
    *,
    enriched: pd.DataFrame | None = None,
    stored_img: np.ndarray | None = None,
) -> tuple[str, int, float] | None:
    """Pick window start; if stored PNG given, choose min pixel-MAD vs re-render.

    Returns ``(mode, win_start, mad)``. ``mad`` is NaN when no stored PNG.
    """
    cands = _candidate_win_starts(n, idx)
    if not cands:
        return None
    if stored_img is None or enriched is None:
        mode, start = cands[0]
        return mode, start, float("nan")
    best: tuple[float, str, int] | None = None
    stored_f = stored_img.astype(np.float32)
    for mode, start in cands:
        sub = enriched.iloc[start : start + WINDOW].reset_index(drop=True)
        if len(sub) != WINDOW:
            continue
        rr, _ = render_chart(sub, out_path=None)
        if rr.shape != stored_img.shape:
            continue
        mad = float(np.mean(np.abs(stored_f - rr.astype(np.float32))))
        if best is None or mad < best[0]:
            best = (mad, mode, start)
    if best is None:
        mode, start = cands[0]
        return mode, start, float("nan")
    return best[1], best[2], best[0]


def box_close_alignment(
    orig_closes: np.ndarray,
    pad_closes: np.ndarray,
) -> tuple[float, float]:
    """Return (corr, max_rel_err) for remapped box close sequences."""
    a = np.asarray(orig_closes, dtype=np.float64).ravel()
    b = np.asarray(pad_closes, dtype=np.float64).ravel()
    if a.size == 0 or a.size != b.size:
        return float("nan"), float("inf")
    if a.size == 1:
        denom = max(abs(a[0]), 1e-12)
        err = abs(a[0] - b[0]) / denom
        return (1.0 if err <= MAX_BOX_CLOSE_REL_ERR else 0.0), float(err)
    if np.allclose(a, a[0]) and np.allclose(b, b[0]):
        denom = max(abs(a[0]), 1e-12)
        err = float(np.max(np.abs(a - b)) / denom)
        return (1.0 if err <= MAX_BOX_CLOSE_REL_ERR else 0.0), err
    corr = float(np.corrcoef(a, b)[0, 1])
    denom = np.maximum(np.abs(a), 1e-12)
    max_rel = float(np.max(np.abs(a - b) / denom))
    return corr, max_rel


def assert_box_close_aligned(orig_closes: np.ndarray, pad_closes: np.ndarray) -> tuple[float, float]:
    corr, max_rel = box_close_alignment(orig_closes, pad_closes)
    if not (np.isfinite(corr) and corr >= MIN_BOX_CLOSE_CORR and max_rel <= MAX_BOX_CLOSE_REL_ERR):
        raise AssertionError(
            f"pad200 box OHLC mismatch: corr={corr:.6f} max_rel_err={max_rel:.3e} "
            f"(need corr>={MIN_BOX_CLOSE_CORR} and max_rel<={MAX_BOX_CLOSE_REL_ERR})"
        )
    return corr, max_rel


def _hstack_compare(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Side-by-side; pad heights if needed (should match render size)."""
    h = max(left.shape[0], right.shape[0])

    def _pad(im: np.ndarray) -> np.ndarray:
        if im.shape[0] == h:
            return im
        out = np.full((h, im.shape[1], 3), 255, dtype=np.uint8)
        out[: im.shape[0]] = im
        return out

    return np.hstack([_pad(left), _pad(right)])


def boxes_cut_and_spans(
    boxes: list[tuple[float, float, float, float]],
    tf_old,
) -> tuple[int, list[tuple[int, int, float, float]]]:
    """Return cut_local (max right edge) and per-box (b0, b1, price_hi, price_lo)."""
    spans: list[tuple[int, int, float, float]] = []
    rights: list[int] = []
    for xc, yc, w, h in boxes:
        x1 = (xc - w / 2) * tf_old.width
        x2 = (xc + w / 2) * tf_old.width
        y1 = (yc - h / 2) * tf_old.height
        y2 = (yc + h / 2) * tf_old.height
        b0 = bar_from_x(tf_old, x1)
        b1 = bar_from_x(tf_old, x2)
        if b1 < b0:
            b0, b1 = b1, b0
        # image y grows downward → smaller y_px is higher price
        price_hi = _price_at(tf_old, min(y1, y2))
        price_lo = _price_at(tf_old, max(y1, y2))
        spans.append((b0, b1, price_hi, price_lo))
        rights.append(b1)
    return int(max(rights)), spans


def remap_gold_boxes(
    spans: list[tuple[int, int, float, float]],
    *,
    win_start: int,
    pad_start: int,
    tip_tf,
    pad_df: pd.DataFrame | None = None,
) -> list[tuple[float, float, float, float]]:
    """Map original gold boxes into pad200 window by bar span + price levels.

    Does NOT force-anchor to tip fire and does not rebuild from MA bundle
    (that is the H-TIP ``boxes_to_tip`` path Owner rejected).

    If the original absolute price band falls outside the pad window's
    visible scale (common when left-pad changes the chart min/max), fall
    back to the high/low of the same bars in ``pad_df``.
    """
    out: list[tuple[float, float, float, float]] = []
    pmax = float(tip_tf.price_max)
    pmin = float(tip_tf.price_min)
    for b0, b1, price_hi, price_lo in spans:
        g0, g1 = win_start + b0, win_start + b1
        t0 = g0 - pad_start
        t1 = g1 - pad_start
        if t1 < 0 or t0 >= WINDOW:
            continue
        t0 = int(max(0, t0))
        t1 = int(min(WINDOW - 1, t1))
        if t1 < t0:
            continue
        hi, lo = float(price_hi), float(price_lo)
        if hi < lo:
            hi, lo = lo, hi
        if hi < pmin or lo > pmax:
            if pad_df is None:
                continue
            seg = pad_df.iloc[t0 : t1 + 1]
            hi = float(seg["high"].max())
            lo = float(seg["low"].min())
        else:
            hi = min(hi, pmax)
            lo = max(lo, pmin)
            if hi <= lo:
                if pad_df is None:
                    continue
                seg = pad_df.iloc[t0 : t1 + 1]
                hi = float(seg["high"].max())
                lo = float(seg["low"].min())
        nx1 = tip_tf.x_at(t0) - tip_tf.candle_half_w
        nx2 = tip_tf.x_at(t1) + tip_tf.candle_half_w
        ny1 = tip_tf.y_at(hi)
        ny2 = tip_tf.y_at(lo)
        nx1 = float(np.clip(nx1, 0, tip_tf.width - 1))
        nx2 = float(np.clip(nx2, 1, tip_tf.width))
        ny1 = float(np.clip(ny1, 0, tip_tf.height - 1))
        ny2 = float(np.clip(ny2, 1, tip_tf.height))
        if nx2 - nx1 < 4 or abs(ny2 - ny1) < 4:
            continue
        xc = (nx1 + nx2) / 2 / tip_tf.width
        yc = (ny1 + ny2) / 2 / tip_tf.height
        bw = (nx2 - nx1) / tip_tf.width
        bh = abs(ny2 - ny1) / tip_tf.height
        out.append((xc, yc, bw, bh))
    return out


def process_pad200(
    stem: str,
    lbl_path: Path,
    out_img: Path,
    out_lbl: Path,
    *,
    draw_preview: bool = False,
    orig_img_path: Path | None = None,
    compare_path: Path | None = None,
) -> Pad200Result | None:
    """Render one pad200 sample. Returns None if skipped (short history, etc.)."""
    if is_eval_stem(stem):
        return None
    boxes = read_boxes(lbl_path)
    if not boxes:
        return None
    parsed = parse_stem(stem)
    if not parsed:
        return None
    body, idx = parsed
    df = resolve_series(body)
    if df is None:
        df = resolve_series(symbol_of(stem))
    if df is None:
        return None
    n = len(df)
    # MA on full series then slice — same as round8/9 renderers (not add_mas on 200 alone).
    enriched = add_mas(df)
    stored_img = None
    if orig_img_path is not None and orig_img_path.exists():
        stored_img = cv2.imread(str(orig_img_path))
    resolved = resolve_win_start(n, idx, enriched=enriched, stored_img=stored_img)
    if resolved is None:
        return None
    win_mode, win_start, stored_mad = resolved
    if (
        stored_img is not None
        and np.isfinite(stored_mad)
        and stored_mad > MAX_STORED_MAD
    ):
        print(
            f"SKIP high MAD {stem}: stored_mad={stored_mad:.3f} > {MAX_STORED_MAD}",
            flush=True,
        )
        return None
    sub = enriched.iloc[win_start : win_start + WINDOW].reset_index(drop=True)
    if len(sub) != WINDOW:
        return None
    tf_old = make_chart_transform(sub)
    cut_local, spans = boxes_cut_and_spans(boxes, tf_old)
    cut_global = win_start + cut_local
    pad_start = cut_global - WINDOW + 1
    # Strict: never stretch a short sequence. Skip if history < 200.
    if pad_start < 0 or cut_global >= n:
        return None
    pad_sub = enriched.iloc[pad_start : cut_global + 1].reset_index(drop=True)
    if len(pad_sub) != WINDOW:
        return None

    # Primary (rightmost) span for OHLC gate + reporting.
    b0, b1, _, _ = spans[0]
    for sb0, sb1, _, _ in spans:
        if sb1 == cut_local:
            b0, b1 = sb0, sb1
            break
    t0 = win_start + b0 - pad_start
    t1 = win_start + b1 - pad_start
    orig_closes = sub.iloc[b0 : b1 + 1]["close"].to_numpy(dtype=np.float64)
    pad_closes = pad_sub.iloc[t0 : t1 + 1]["close"].to_numpy(dtype=np.float64)
    try:
        corr, max_rel = assert_box_close_aligned(orig_closes, pad_closes)
    except AssertionError as exc:
        print(f"SKIP align fail {stem}: {exc}", flush=True)
        return None

    img, tip_tf = render_chart(pad_sub, out_path=None)
    new_boxes = remap_gold_boxes(
        spans,
        win_start=win_start,
        pad_start=pad_start,
        tip_tf=tip_tf,
        pad_df=pad_sub,
    )
    if not new_boxes:
        return None

    # Sanity: window must be exactly WINDOW bars (no stretch path).
    assert tip_tf.n_bars == WINDOW, tip_tf.n_bars

    out_img.parent.mkdir(parents=True, exist_ok=True)
    out_lbl.parent.mkdir(parents=True, exist_ok=True)
    # Preview compare: boxes only — no red cut line (Owner request).
    pad_vis = _draw_yolo_boxes(img, new_boxes, COLOR_PAD_BOX, thickness=2)
    if draw_preview:
        cv2.imwrite(str(out_img), pad_vis)
    else:
        cv2.imwrite(str(out_img), img)

    lines = "".join(f"0 {a:.6f} {b:.6f} {c:.6f} {d:.6f}\n" for a, b, c, d in new_boxes)
    out_lbl.write_text(lines)

    compare_out = ""
    if compare_path is not None and orig_img_path is not None and orig_img_path.exists():
        orig = stored_img if stored_img is not None else cv2.imread(str(orig_img_path))
        if orig is not None:
            left = _caption(
                _draw_yolo_boxes(orig, boxes, COLOR_ORIG_GT, thickness=2),
                f"ORIG GT  box_bars={b0}-{b1}  cut={cut_local}  mode={win_mode}",
            )
            right = _caption(
                pad_vis,
                f"PAD200  box_bars={t0}-{t1}  n={WINDOW}  corr={corr:.4f}",
            )
            compare_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(compare_path), _hstack_compare(left, right))
            compare_out = str(compare_path)

    ts = pad_sub.iloc[-1].get("open_time", pad_sub.index[-1])
    return Pad200Result(
        stem=stem,
        symbol=body,
        win_start=win_start,
        cut_local=cut_local,
        cut_global=cut_global,
        pad_start=pad_start,
        n_bars=WINDOW,
        box_bars_orig=(b0, b1),
        box_bars_new=(int(t0), int(t1)),
        end_time=str(ts),
        out_img=str(out_img),
        out_lbl=str(out_lbl),
        compare_img=compare_out,
        box_close_corr=corr,
        box_close_max_rel_err=max_rel,
        win_index_mode=win_mode,
        stored_mad=stored_mad,
    )


def _find_label(src: Path, stem: str) -> Path | None:
    for split in ("train", "val"):
        p = src / "labels" / split / f"{stem}.txt"
        if p.exists() and read_boxes(p):
            return p
    return None


def run_preview(src: Path, n: int, out_dir: Path) -> list[Pad200Result]:
    """Pick ``n`` positives that pass corr+MAD gates; prefer unique symbols."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Pad200Result] = []
    seen_sym: set[str] = set()
    # Prefer known good dense samples first, then scan train.
    preferred = [
        "HOME_USDT_SWAP_005530",
        "0G_USDT_SWAP_002130",  # MAD=0 vs stored; LINK_USDT_001160 archive≠current kline
    ]
    train_imgs = sorted((src / "images" / "train").glob("*.png"))
    stems = preferred + [p.stem for p in train_imgs if p.stem not in preferred]

    # Pass 1: unique symbols; pass 2: fill remaining slots if needed.
    for allow_dup_sym in (False, True):
        if len(results) >= n:
            break
        for stem in stems:
            if len(results) >= n:
                break
            if any(r.stem == stem for r in results):
                continue
            lbl = _find_label(src, stem)
            if lbl is None:
                continue
            orig_img = _find_image(src, stem)
            if orig_img is None:
                continue
            parsed = parse_stem(stem)
            if not parsed:
                continue
            body, _ = parsed
            sym_key = body.split("_")[0]
            if not allow_dup_sym and stem not in preferred and sym_key in seen_sym:
                continue
            slot = len(results) + 1
            tmp_img = out_dir / f"_tmp_pad200_{stem}.png"
            tmp_lbl = out_dir / f"_tmp_pad200_{stem}.txt"
            compare_path = out_dir / f"fixed_compare_10_{slot:02d}_{stem}.png"
            res = process_pad200(
                stem,
                lbl,
                tmp_img,
                tmp_lbl,
                draw_preview=True,
                orig_img_path=orig_img,
                compare_path=compare_path,
            )
            if res is None:
                tmp_img.unlink(missing_ok=True)
                tmp_lbl.unlink(missing_ok=True)
                compare_path.unlink(missing_ok=True)
                continue
            tag = _time_tag(res.end_time)
            final_img = out_dir / f"pad200_after_box_{slot}_{sym_key}_{tag}.png"
            final_lbl = out_dir / f"pad200_after_box_{slot}_{sym_key}_{tag}.txt"
            tmp_img.replace(final_img)
            tmp_lbl.replace(final_lbl)
            res.out_img = str(final_img)
            res.out_lbl = str(final_lbl)
            res.compare_img = str(compare_path)
            results.append(res)
            seen_sym.add(sym_key)
            mad_s = (
                f"{res.stored_mad:.3f}" if np.isfinite(res.stored_mad) else "nan"
            )
            print(
                f"preview {slot}: {stem} mode={res.win_index_mode} "
                f"n_bars={res.n_bars} cut_local={res.cut_local} "
                f"cut_global={res.cut_global} box_orig={res.box_bars_orig} "
                f"box_new={res.box_bars_new} corr={res.box_close_corr:.6f} "
                f"max_rel={res.box_close_max_rel_err:.3e} mad={mad_s} "
                f"end={res.end_time} compare={compare_path.name}",
                flush=True,
            )
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=PROJECT / "datasets" / "dense_owner_v11")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="full dataset root; omit unless Owner approved bulk build",
    )
    ap.add_argument("--preview", type=int, default=0, help="write N preview PNGs and exit")
    ap.add_argument(
        "--preview-dir",
        type=Path,
        default=TRY_DIR,
        help="preview/compare output dir (default: analysis/output/pad200_try)",
    )
    ap.add_argument("--limit", type=int, default=0, help="max train clones (0=all); needs --out")
    ap.add_argument(
        "--resume",
        action="store_true",
        help="continue into existing --out (skip stems that already have *_pad200.png or skip log)",
    )
    ap.add_argument(
        "--mad-gate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="disambiguate stem index via stored PNG MAD (default ON; "
        "required for v11 okx_*=start vs round8=end_incl mix). "
        "Use --no-mad-gate only for emergency resume without PNGs.",
    )
    args = ap.parse_args()

    if not args.src.exists():
        print(f"src missing: {args.src}", file=sys.stderr)
        return 2

    if args.preview > 0:
        results = run_preview(args.src, args.preview, args.preview_dir)
        readme = args.preview_dir / "README_pad200.txt"
        lines = [
            "pad200 try — crop after GT box right edge + left-pad to 200",
            "Protocol: cut at box right edge; window = [cut-199, cut]; n_bars MUST be 200.",
            "Stem index: v11 MIX — round8-style=end_incl, okx_*=start; ALWAYS MAD vs stored PNG.",
            "fixed_compare_10_*: LEFT = stored orig + Owner GT (cyan); RIGHT = pad200 + remapped (green).",
            "Gate: remapped box close corr >= 0.999 and max rel err <= 1e-6; stored MAD <= 5.",
            "No red cut line on preview compares. Does NOT overwrite dense_owner_v11.",
            "",
        ]
        for i, r in enumerate(results, 1):
            lines.append(Path(r.compare_img).name if r.compare_img else Path(r.out_img).name)
            lines.append(
                f"  {r.stem}  mode={r.win_index_mode}  n_bars={r.n_bars}  "
                f"cut_local={r.cut_local}  cut_global={r.cut_global}  "
                f"pad_start={r.pad_start}  box_orig={r.box_bars_orig}  "
                f"box_new={r.box_bars_new}  corr={r.box_close_corr}  "
                f"max_rel={r.box_close_max_rel_err}  mad={r.stored_mad}  "
                f"end={r.end_time}"
            )
        readme.write_text("\n".join(lines) + "\n")
        print(json.dumps([asdict(r) for r in results], indent=2, default=str))
        if len(results) < args.preview:
            print(
                f"WARNING: only {len(results)}/{args.preview} previews "
                f"(need more long-history positives)",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.out is None:
        print("Pass --preview N for previews, or --out DIR after Owner approval.", file=sys.stderr)
        return 2

    # Full build skeleton (not run unless --out given).
    dst = args.out
    if dst.exists() and not args.resume:
        print(f"refusing to clobber existing out: {dst}", file=sys.stderr)
        return 2
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
    # val: copy originals unchanged (same frozen ruler as v11/v12/v13)
    import shutil
    import gc

    n_val = 0
    for img in sorted((args.src / "images" / "val").glob("*.png")):
        dest = dst / "images" / "val" / img.name
        if not dest.exists():
            shutil.copy2(img, dest)
        lbl = args.src / "labels" / "val" / f"{img.stem}.txt"
        if lbl.exists():
            dl = dst / "labels" / "val" / lbl.name
            if not dl.exists():
                shutil.copy2(lbl, dl)
        n_val += 1

    already_ok = {p.name.replace("_pad200.png", "") for p in (dst / "images" / "train").glob("*_pad200.png")}
    skip_log = dst / "pad200_skip.log"
    already_skip: set[str] = set()
    if skip_log.exists():
        for line in skip_log.read_text(encoding="utf-8").splitlines():
            stem0 = line.split("\t", 1)[0].strip()
            if stem0:
                already_skip.add(stem0)

    n_ok = len(already_ok)
    n_skip = len(already_skip)
    n_bg = 0
    skip_fh = skip_log.open("a" if args.resume else "w", encoding="utf-8")
    print(
        f"bulk start resume={args.resume} mad_gate={args.mad_gate} "
        f"already_ok={n_ok} already_skip={n_skip}",
        flush=True,
    )
    for img in sorted((args.src / "images" / "train").glob("*.png")):
        stem = img.stem
        lbl = args.src / "labels" / "train" / f"{stem}.txt"
        if not lbl.exists() or not read_boxes(lbl):
            # empty-label backgrounds: copy as-is (no pad200 re-render)
            dest = dst / "images" / "train" / img.name
            if not dest.exists():
                shutil.copy2(img, dest)
            if lbl.exists():
                dl = dst / "labels" / "train" / lbl.name
                if not dl.exists():
                    shutil.copy2(lbl, dl)
            n_bg += 1
            continue
        if stem in already_ok or stem in already_skip:
            continue
        out_img = dst / "images" / "train" / f"{stem}_pad200.png"
        out_lbl = dst / "labels" / "train" / f"{stem}_pad200.txt"
        # Default: MAD vs stored PNG (v11 mixes end_incl and start). Blind
        # end_incl remaps okx_* gold onto the wrong OHLC. gc every N samples
        # to keep 16GB machines alive (jetsam risk if MAD left off for RAM).
        orig = img if args.mad_gate else None
        try:
            res = process_pad200(
                stem,
                lbl,
                out_img,
                out_lbl,
                draw_preview=False,
                orig_img_path=orig,
            )
        except Exception as exc:  # noqa: BLE001 — keep bulk build alive on one bad stem
            n_skip += 1
            skip_fh.write(f"{stem}\texc\t{exc}\n")
            skip_fh.flush()
            print(f"SKIP exc {stem}: {exc}", flush=True)
            gc.collect()
            continue
        if res is None:
            n_skip += 1
            skip_fh.write(f"{stem}\n")
            skip_fh.flush()
            print(f"SKIP {stem}", flush=True)
            continue
        n_ok += 1
        if n_ok % 20 == 0:
            gc.collect()
        if n_ok % 50 == 0:
            print(
                f"  pad200_ok={n_ok} skip={n_skip} bg_copied={n_bg}",
                flush=True,
            )
        if args.limit and n_ok >= args.limit:
            break
    skip_fh.close()

    (dst / "data.yaml").write_text(
        f"path: {dst.resolve()}\ntrain: images/train\nval: images/val\n"
        f"names:\n  0: dense_cluster\n"
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": "crop_after_box_pad200",
        "src": str(args.src),
        "out": str(dst),
        "window": WINDOW,
        "train_pad200": n_ok,
        "train_skip": n_skip,
        "train_bg_copied": n_bg,
        "val_orig": n_val,
        "empty_bg_policy": "copy_as_is",
        "val_policy": "copy_orig_unchanged",
        "mad_gate": bool(args.mad_gate),
        "win_index_default": "mad_vs_stored_png" if args.mad_gate else "end_incl_fallback",
        "skip_log": str(skip_log),
        "note": "v11 stem index is MIXED; mad_gate=false corrupts okx_* pad200 boxes",
    }
    (dst / "pad200_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(
        f"DONE pad200_ok={n_ok} skip={n_skip} bg_copied={n_bg} val={n_val}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
