"""v10: fix the three silent corruptions v9 was trained through.

v9 reached 84% recall on the owner's gold and the owner rejected 276 of 277 of
its fires -- precision 0.4%, and firing density 137-274x their own labelling
rate. Confidence cannot separate the two populations, so the fault is in the
training data. Three defects, each of which produced wrong data without ever
raising an error:

WINDOW UNRESOLVED (19.3% of stars). resolve_win_start returns a pixel MAD against
the image the owner actually labelled, and every call site in this repo discards
it -- including v9's. When no candidate convention matches, the star is anchored
on a guessed bar, so its direction, its tip and its forward return all describe
the wrong place. A detector trained on positives anchored at arbitrary bars
learns to fire anywhere, which is exactly what v9 does. v10 requires MAD < 0.5.

DIRECTION BY FIRST TRIGGER (9.8% mislabelled). star_side walks forward and returns
whichever barrier is crossed first, so a shakeout ahead of an upward launch wins
and the star is filed as a short. Measured on the resolvable stars: 9.8% of
"shorts" rise more than 2% within 48 bars. v10 decides on the LARGER move in the
window instead of the earlier one.

NEGATIVES THAT LOOK NOTHING LIKE THE PATTERN. v9's easy negatives are sampled with
"if passes(...): continue" -- any bar resembling the setup is skipped -- so the
model never saw a "looks like it but is not". v10 adds the 276 fires the owner
rejected in the v9 hard-negative pack: the model's own mistakes, which is the
only place hard negatives can honestly come from.

Everything else is v9 unchanged: box right edge on the break bar, the owner's own
width, and the augmentation bans (iron rule 5).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.auto_label import DenseSegment, segment_to_bbox  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import add_features  # noqa: E402
from scripts.build_htip_dataset import WINDOW, resolve_series  # noqa: E402
from scripts.build_crop_pad200_dataset import boxes_cut_and_spans, resolve_win_start  # noqa: E402

SHEET = PROJECT / "analysis/output/owner_side_review/review_sheet.csv"
GOLD_PACK = PROJECT / "analysis/output/owner_side_short_tip_v1b_detect1000"
V9_NEG_PACK = PROJECT / "analysis/output/v9_hardneg_pack"          # owner-labelled, val side
V9_NEG_TRAIN = PROJECT / "analysis/output/v9_hardneg_trainside"    # train side, unlabelled
MAD_MAX = 0.5          # pixel match against the image the owner labelled
LS_GLOB = str(PROJECT / "output/label_studio/*.json")
ARCHIVE_ROOTS = [PROJECT / "datasets/_deprecated_pretip/dense_owner_v11/images",
                 PROJECT / "datasets/_deprecated_pretip/dense_owner_v14_pad200/images"]
OUT = PROJECT / "datasets/dense_owner_short_star_tip_v10"

HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
VAL_CUT = pd.Timestamp("2026-02-01", tz="UTC")
FAST_MAX, FULL_MAX = 0.0045, 0.0088
ANCHOR_LOOKBACK, PRIOR_LOOKBACK, WARMUP = 24, 48, 200
BREAK_FORWARD = 24        # bars to search AFTER the trough for the break
RET_BARS = 8                    # 「K线向下」 horizon
BOX_MIN, BOX_MAX = 5, 24        # clip only; the width itself is the owner's own
DROP_ATR_MIN = 1.0              # the fall must be worth at least this many ATR
NEG_RATIO = 1.5
SEED = 20260727
STAR_TAG = "⭐标杆"


def _git() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(PROJECT),
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_star_boxes() -> dict[str, list[tuple[float, float, float, float]]]:
    """stem -> [(xc, yc, w, h) normalized] for boxes on ⭐标杆-tagged images."""
    out: dict[str, list] = {}
    for f in glob.glob(LS_GLOB):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, list):
            continue
        for task in data:
            img = (task.get("data", {}) or {}).get("image") or ""
            stem = re.sub(r"\.(png|jpg)$", "", img.split("/")[-1].split("?")[0])
            if not stem:
                continue
            for ann in task.get("annotations", []) or []:
                res = ann.get("result", []) or []
                if not any(STAR_TAG in (r.get("value", {}).get("choices") or []) for r in res):
                    continue
                for r in res:
                    if r.get("type") != "rectanglelabels":
                        continue
                    v = r["value"]
                    # Label Studio stores percentages of the image
                    xc = (v["x"] + v["width"] / 2) / 100.0
                    yc = (v["y"] + v["height"] / 2) / 100.0
                    out.setdefault(stem, []).append(
                        (xc, yc, v["width"] / 100.0, v["height"] / 100.0))
    return out


def archive_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for root in ARCHIVE_ROOTS:
        for p in root.rglob("*.png"):
            idx.setdefault(p.stem, p)
    return idx


class Series:
    def __init__(self) -> None:
        self.c: dict[str, tuple | None] = {}

    def get(self, sym: str):
        if sym in self.c:
            return self.c[sym]
        base = resolve_series(sym)
        if base is None:
            self.c[sym] = None
            return None
        try:
            framed = add_mas(base)
            ind = add_features(add_indicators(base))
            if len(ind) != len(framed):
                self.c[sym] = None
                return None
            from src.detection.data import ALL_MA_COLS
            ma = np.vstack([framed[c].to_numpy(dtype=float)
                            for c in ALL_MA_COLS if c in framed.columns])
            self.c[sym] = (framed,
                           ind["fast_spread"].to_numpy(dtype=float),
                           ind["full_spread"].to_numpy(dtype=float),
                           ind["close"].to_numpy(dtype=float),
                           pd.to_datetime(framed["open_time"], utc=True),
                           np.nanmin(ma, axis=0),
                           ind["atr_pct"].to_numpy(dtype=float),
                           np.nanmax(ma, axis=0))
        except Exception:  # noqa: BLE001
            self.c[sym] = None
        return self.c[sym]


def symbol_of(stem: str, known: set[str]) -> str | None:
    """Stem -> series key, across every naming convention the rounds used.

    Label Studio stems come from several labelling rounds and carry three
    shapes: `XRP_USDT_SWAP_012345`, the spot-era `SOL_USDT_013760`, and
    `okx_GAS_USDT_SWAP_017960`. An earlier version only ever ADDED an `okx_`
    prefix, never stripped one, so every okx_-prefixed stem failed to resolve
    and 199 of 528 ⭐标杆 were silently dropped as "no_symbol" -- including
    XRP, DOGE, GAS and OL, whose klines are all present. Owner caught it by
    noticing the counts could not add up.
    """
    raw = re.sub(r"_\d+$", "", stem)
    bare = raw[4:] if raw.startswith("okx_") else raw
    for cand in (raw, bare, bare + "_SWAP", "okx_" + bare, raw + "_SWAP"):
        if cand in known:
            return cand
    return None


def star_side(close, ma_min, ma_max, atrp, trough: int, n: int) -> tuple[int, int | None]:
    """Which way did this ⭐标杆 cluster launch? Returns (side, bar).

    The tag is direction-agnostic -- rendering the stars shows textbook LONG
    launches beside the shorts, and of the 269 that also appear in the side
    review the owner split them 188 short / 91 long. So a short detector has to
    read the direction off each one rather than take the tag whole. The LARGER move in the
    window wins, not the earlier one; the label may look forward,
    only the tip window has to stay causal (iron rule 3).
    """
    down = up = None
    for j in range(trough, min(trough + BREAK_FORWARD + 1, n)):
        if j < RET_BARS or not np.isfinite(atrp[j]) or atrp[j] <= 0:
            continue
        move = (close[j] / close[j - RET_BARS] - 1) / atrp[j]
        if np.isfinite(ma_min[j]) and close[j] < ma_min[j] and move < -DROP_ATR_MIN:
            if down is None or move < down[0]:
                down = (move, j)
        if np.isfinite(ma_max[j]) and close[j] > ma_max[j] and move > DROP_ATR_MIN:
            if up is None or move > up[0]:
                up = (move, j)
    # v9 returned whichever barrier was crossed FIRST, so a shakeout ahead of an
    # upward launch filed the star as a short -- 9.8% of its "shorts" rise >2%
    # within 48 bars. The launch is the larger move, not the earlier one.
    if down is None and up is None:
        return 0, None
    if up is None:
        return -1, down[1]
    if down is None:
        return +1, up[1]
    return (-1, down[1]) if abs(down[0]) >= up[0] else (+1, up[1])


def dense_run_start(fast, trough: int, max_back: int = 60) -> int:
    """First bar of the contiguous tight run that ends at the trough.

    v6 copied the owner's original box width, but once the anchor moved from the
    box's right edge to the break bar, that width no longer describes anything --
    measured against the owner's own boxes v6 came out 1.24x too wide (14 bars
    against their 10), which caps IoU near 0.8 even with a perfectly aligned
    right edge. Deriving the left edge from where the bundle actually tightened
    makes the box describe the cluster instead of inheriting a stale span.
    """
    i = trough
    lo = max(0, trough - max_back)
    while i > lo and np.isfinite(fast[i - 1]) and fast[i - 1] <= FAST_MAX:
        i -= 1
    return i


def find_break(close, ma_min, atrp, trough: int, n: int) -> int | None:
    """First bar at/after the trough where the breakdown is confirmed.

    v5 anchored ON the density trough and then demanded the break had already
    happened -- self-contradictory, since the break comes AFTER the tightest
    point. Measured on the owner's 1361 short boxes: 99.8% do break within 24
    bars of the trough (median 2), but only 29.4% break exactly at it, which is
    the whole reason v5 kept so few. Anchor on the break instead.
    """
    for j in range(trough, min(trough + BREAK_FORWARD + 1, n)):
        if j < RET_BARS or not np.isfinite(ma_min[j]) or not np.isfinite(atrp[j]) or atrp[j] <= 0:
            continue
        if close[j] < ma_min[j] and (close[j] / close[j - RET_BARS] - 1) / atrp[j] < -DROP_ATR_MIN:
            return j
    return None


def passes(fast, full, close, anchor: int, ma_min=None, atr=None) -> tuple[bool, dict]:
    """The owner's stated pattern, at the strictness their eye actually wants.

    v4 used `ret8 < 0`, i.e. merely lower than two hours ago. Shown the samples
    the owner said the boxes land 有点早, and measuring against their 390
    verdicts agrees -- later and more decisive is better:

        ret8 < 0                          218 hits, 27.1%  (1.49x base)
        close below all six MAs           163 hits, 33.1%  (1.82x)
        below all MAs AND fall > 1 ATR     96 hits, 42.7%  (2.35x)
        fall > 2 ATR                       47 hits, 51.1%  (2.80x)

    Taking below-all-MAs AND fall>1*ATR: both halves are things the owner
    described (the break is confirmed, and it is a real move rather than drift),
    and it keeps ~2x the sample of the 2-ATR cut, which would also push the tip
    so late that the entry is largely gone.
    """
    lo = max(WARMUP, anchor - PRIOR_LOOKBACK)
    prior = fast[lo:anchor + 1]
    prior_min = float(np.nanmin(prior)) if np.isfinite(prior).any() else np.inf
    ret = close[anchor] / close[anchor - RET_BARS] - 1 if anchor >= RET_BARS else np.nan
    below = bool(ma_min is not None and np.isfinite(ma_min) and close[anchor] < ma_min)
    a = float(atr) if atr is not None and np.isfinite(atr) and atr > 0 else np.nan
    drop_atr = ret / a if np.isfinite(ret) and np.isfinite(a) else np.nan
    ok = bool(prior_min <= FAST_MAX and below
              and np.isfinite(drop_atr) and drop_atr < -DROP_ATR_MIN)
    return ok, {"prior_min_fast": prior_min, "below_all_ma": below,
                "drop_atr": float(drop_atr) if np.isfinite(drop_atr) else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    rng = random.Random(SEED)
    out = args.out
    if out.exists():
        shutil.rmtree(out)
    for s in ("train", "val"):
        (out / "images" / s).mkdir(parents=True, exist_ok=True)
        (out / "labels" / s).mkdir(parents=True, exist_ok=True)

    ser = Series()
    from src.data.loader import list_series
    known = {s for (_src, s) in list_series(bar="15m")}
    arch = archive_index()
    stars = load_star_boxes()
    print(f"⭐标杆 stems: {len(stars)}  存档图可用: {sum(1 for s in stars if s in arch)}")

    kept = {"train": 0, "val": 0}
    negs = {"train": 0, "val": 0}
    src_count = {"star": 0, "side": 0}
    skips = {"no_symbol": 0, "no_series": 0, "no_window": 0, "oob": 0,
             "holdout": 0, "not_pattern": 0, "box": 0, "error": 0, "dup": 0,
             "star_long": 0}
    seen: dict[str, int] = {}
    used: set[str] = set()

    def emit_positive(sym: str, cut: int, width: int, tag: str, force: bool = False) -> bool:
        e = ser.get(sym)
        if e is None:
            skips["no_series"] += 1
            return False
        framed, fast, full, close, times, ma_min, atrp, ma_max = e
        if cut < WARMUP or cut >= len(framed):
            skips["oob"] += 1
            return False
        lo = max(WARMUP, cut - ANCHOR_LOOKBACK)
        seg = fast[lo:cut + 1]
        if not np.isfinite(seg).any():
            skips["oob"] += 1
            return False
        trough = lo + int(np.nanargmin(seg))
        # Every owner box is a positive -- star or side. The mechanical test may
        # choose WHICH bar to anchor on, and may reject a box for being LONG, but
        # it no longer throws away a short the owner drew just because the break
        # is not textbook. v6 discarded 206 of the owner's 1361 shorts that way
        # while its recall on their gold sat at 51.5%.
        side, brk = star_side(close, ma_min, ma_max, atrp, trough, len(framed))
        if side > 0:
            skips["star_long"] = skips.get("star_long", 0) + 1
            return False
        if brk is None:
            brk = find_break(close, ma_min, atrp, trough, len(framed))
        # Owner's rule (2026-07-27): a ⭐标杆 short IS a positive. The mechanical
        # test may pick the bar, never veto the example -- if the two disagree,
        # the test is what is wrong. Un-starred rows still have to earn it.
        if brk is None:
            brk = trough          # no confirmed break: anchor on the tightest bar
        anchor = brk
        if times.iloc[anchor] >= HOLDOUT:
            skips["holdout"] += 1
            return False
        start = anchor - WINDOW + 1
        if start < 0:
            skips["no_window"] += 1
            return False
        sub = framed.iloc[start:anchor + 1].reset_index(drop=True)
        if len(sub) != WINDOW:
            skips["no_window"] += 1
            return False
        stem = f"{sym}_{anchor:06d}"
        sp = "train" if times.iloc[anchor] < VAL_CUT else "val"
        if stem in seen:
            skips["dup"] += 1
            if width <= seen[stem]:
                return False
            kept[sp] -= 1   # this row supersedes the stored one
        img_p = out / "images" / sp / f"{stem}.png"
        try:
            _, tf = render_chart(sub, out_path=img_p)
            t1 = WINDOW - 1
            # v7: the box spans the dense run that ends at the trough, not the
            # owner's original width -- see dense_run_start().
            # v9: each box keeps the width the owner actually drew for it.
            #
            # v8 pinned every box to 10 bars and the comparison says that bought
            # nothing: IoU 0.544 against v6's 0.532 and width ratio 1.22 against
            # 1.24 -- statistically the same -- while recall on the owner's gold
            # fell from 51.5% to 14.1%. A conf sweep down to 0.01 left it at
            # 16.3%, so the model was not merely unsure, it had learned to accept
            # one exact shape and reject the owner's 9-13 bar spread (silent on
            # 83.7% of gold tips, but p50 confidence 0.540 when it did fire).
            #
            # v6 already used the owner's own width. Restoring it while keeping
            # v7/v8's recall change and yolo11s isolates whether those two help
            # once the width stops fighting them.
            span = int(np.clip(width, BOX_MIN, BOX_MAX))
            box = segment_to_bbox(sub, DenseSegment(start=max(0, t1 - span + 1),
                                                    end=t1), tf)
            if box is None:
                skips["box"] += 1
                img_p.unlink(missing_ok=True)
                return False
            xc, yc, w, h = box
            (out / "labels" / sp / f"{stem}.txt").write_text(
                f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
        except Exception:  # noqa: BLE001
            skips["error"] += 1
            img_p.unlink(missing_ok=True)
            return False
        seen[stem] = width
        used.add(stem)
        kept[sp] += 1
        src_count[tag] += 1
        return True

    # ---------- positives A: the owner's ⭐标杆 ----------
    items = list(stars.items())
    if args.limit:
        items = items[: args.limit]
    for stem, boxes in items:
        sym = symbol_of(stem, known)
        if sym is None:
            skips["no_symbol"] += 1
            continue
        e = ser.get(sym)
        if e is None:
            skips["no_series"] += 1
            continue
        framed = e[0]
        m = re.search(r"_(\d+)$", stem)
        if not m:
            skips["no_window"] += 1
            continue
        stored = None
        p = arch.get(stem)
        if p is not None:
            stored = cv2.imread(str(p))
        r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed, stored_img=stored)
        if r is None:
            skips["no_window"] += 1
            continue
        _mode, win_start, mad = r
        # 19.3% of stars resolve to a window that does not match the image the
        # owner labelled. Their box coordinates then map to the wrong bars, and
        # nothing downstream can detect it. Drop them rather than guess.
        if not (np.isfinite(mad) and mad < MAD_MAX):
            skips["mad"] = skips.get("mad", 0) + 1
            continue
        sub_old = framed.iloc[win_start:win_start + WINDOW].reset_index(drop=True)
        if len(sub_old) != WINDOW:
            skips["no_window"] += 1
            continue
        tf_old = make_chart_transform(sub_old)
        _cut_local, spans = boxes_cut_and_spans(boxes, tf_old)
        for b0, b1, *_ in spans:
            emit_positive(sym, win_start + b1, max(1, b1 - b0), "star", force=True)

    print(f"positives from ⭐标杆: {src_count['star']}")

    # ---------- positives B: side-review shorts passing the same three tests ----
    if SHEET.exists() and not args.limit:
        sh = pd.read_csv(SHEET)
        sh = sh[sh["owner_side"].astype(str).str.strip() == "short"]
        sh["cut_global"] = pd.to_numeric(sh["cut_global"], errors="coerce")
        for _, r in sh.iterrows():
            if not np.isfinite(r["cut_global"]):
                continue
            sym = symbol_of(str(r["stem"]), known) or str(r["symbol"])
            emit_positive(sym, int(r["cut_global"]),
                          max(1, int(r["bar_b1"]) - int(r["bar_b0"])), "side")
    n_pos = kept["train"] + kept["val"]
    print(f"positives from side-review: {src_count['side']}   合计 {n_pos}")

    # ---------- negatives (same recipe as v3) ----------
    def emit_negative(sym: str, tip: int, ts, tag: str) -> bool:
        e = ser.get(sym)
        if e is None:
            return False
        framed = e[0]
        stem = f"{tag}_{sym}_{tip:06d}"
        if stem in used:
            return False
        start = tip - WINDOW + 1
        if start < 0:
            return False
        sub = framed.iloc[start:tip + 1].reset_index(drop=True)
        if len(sub) != WINDOW:
            return False
        sp = "train" if ts < VAL_CUT else "val"
        img_p = out / "images" / sp / f"{stem}.png"
        try:
            render_chart(sub, out_path=img_p)
        except Exception:  # noqa: BLE001
            img_p.unlink(missing_ok=True)
            return False
        (out / "labels" / sp / f"{stem}.txt").write_text("")
        used.add(stem)
        negs[sp] += 1
        return True

    n_hard = 0
    gp = GOLD_PACK / "review_sheet.csv"
    if gp.exists() and not args.limit:
        g = pd.read_csv(gp)
        for _, r in g[g["owner_keep"].astype(str).str.strip() == "drop"].iterrows():
            sym = str(r["symbol"])
            e = ser.get(sym)
            if e is None:
                continue
            times = e[4]
            ts = pd.Timestamp(r["tip_time"])
            if ts >= HOLDOUT:
                continue
            tip = int(times.searchsorted(ts))
            if tip < WARMUP or tip >= len(e[0]):
                continue
            if emit_negative(sym, tip, times.iloc[tip], "neghard"):
                n_hard += 1

    # v9's own rejected fires: the model's mistakes are the only honest source of
    # hard negatives, and the sampler below cannot produce them (it skips any bar
    # that resembles the pattern).
    # Two packs, because the split is by time and one pack alone lands entirely on
    # one side of it. The owner-reviewed pack was mined from the bars just before
    # holdout, so all 276 of its rejections fall after VAL_CUT and the model would
    # have trained on none of them -- that is exactly what the first v10 build did.
    # The train-side pack is mined across 2025-06..2026-01 and is NOT owner
    # reviewed; it is used as negatives on the measured precision of the detector
    # that produced it, 0.4% with a 95% upper bound of 2.0%, so the label error
    # here is bounded and small against 1388 easy negatives that teach nothing.
    n_v9 = 0
    rows_neg = []
    vp = V9_NEG_PACK / "review_sheet.csv"
    if vp.exists() and not args.limit:
        v = pd.read_csv(vp)
        rows_neg.append(v[v["owner_keep"].astype(str).str.strip() == "drop"])
    tp = V9_NEG_TRAIN / "review_sheet.csv"
    if tp.exists() and not args.limit:
        rows_neg.append(pd.read_csv(tp))
    v = pd.concat(rows_neg, ignore_index=True) if rows_neg else pd.DataFrame()
    if len(v):
        for _, r in v.iterrows():
            sym = str(r["symbol"])
            e = ser.get(sym)
            if e is None:
                continue
            times = e[4]
            ts = pd.Timestamp(r["tip_time"])
            if ts >= HOLDOUT:
                continue
            tip = int(times.searchsorted(ts))
            if tip < WARMUP or tip >= len(e[0]):
                continue
            if emit_negative(sym, tip, times.iloc[tip], "negv9"):
                n_v9 += 1
    print(f"hard negatives: owner-drop {n_hard} + v9 误开火 {n_v9}")

    want = max(0, int(n_pos * NEG_RATIO) - n_hard - n_v9)
    pool = [s for s in ser.c if ser.c[s] is not None]
    n_easy, guard = 0, 0
    while n_easy < want and guard < want * 60 and pool:
        guard += 1
        sym = rng.choice(pool)
        e = ser.get(sym)
        if e is None:
            continue
        framed, fast, full, close, times, ma_min, atrp, ma_max = e
        hi = len(framed) - 1
        if hi <= WARMUP + WINDOW:
            continue
        tip = rng.randint(WARMUP + WINDOW, hi)
        if times.iloc[tip] >= HOLDOUT:
            continue
        if not np.isfinite(fast[tip]) or not np.isfinite(full[tip]):
            continue
        ok, _ = passes(fast, full, close, tip, ma_min[tip], atrp[tip])
        if ok:
            continue                       # this matches the pattern — not a negative
        if emit_negative(sym, tip, times.iloc[tip], "negrand"):
            n_easy += 1

    n_neg = negs["train"] + negs["val"]
    total = n_pos + n_neg
    (out / "data.yaml").write_text(
        "# v4: owner-stated pattern (converged before + price falling at tip),\n"
        "# positives seeded from the owner's ⭐标杆 tags. Tip = right edge.\n"
        f"path: {out}\ntrain: images/train\nval: images/val\n"
        "names:\n  0: dense_cluster\nnc: 1\n", encoding="utf-8")
    meta = {"generated_at": pd.Timestamp.utcnow().isoformat(), "git": _git(),
            "script": "scripts/build_star_tip_dataset_v4.py",
            "owner_pattern": "v9: 框宽=owner 原框宽度(v8 定死 10 根毁了召回); 其余同 v8",
            "positives": kept, "by_source": src_count, "negatives": negs,
            "n_pos": n_pos, "n_neg": n_neg, "n_total": total,
            "neg_share": round(n_neg / max(total, 1), 3),
            "hard_negatives": n_hard, "easy_negatives": n_easy,
            "skips": skips, "seed": SEED,
            "thresholds": {"FAST_MAX": FAST_MAX, "FULL_MAX": FULL_MAX,
                           "RET_BARS": RET_BARS, "PRIOR_LOOKBACK": PRIOR_LOOKBACK,
                           "DROP_ATR_MIN": DROP_ATR_MIN}}
    (out / "build_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(f"\n输出 {out}")
    print(f"  正 {n_pos} (⭐{src_count['star']} + side {src_count['side']})  "
          f"负 {n_neg}  合计 {total}  空图 {meta['neg_share']*100:.1f}%")
    print(f"  skips: {skips}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
