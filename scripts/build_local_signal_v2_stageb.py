#!/usr/bin/env python3
"""Build Stage-B causal local-signal V2 dataset (Mode C, tip-aligned windows).

Owner auth 2026-08-07 (full V1 handover track): after Stage-A w20 failed P0
(future bars after decision, symbol-hash split, holdout leakage), rebuild a
**separate** dataset that satisfies the hard gates without overwriting
``datasets/dense_owner_w20_midbox``.

Protocol (spec V1 Mode C + Stage B):
  - Reuse pad200-derived anchors from ``w20_manifest.json`` (``mid_global``).
  - ``confirm_delay ∈ {1, 2}``; ``decision = anchor + confirm_delay``.
  - Small box: ``[anchor - 2, decision]`` (box never past decision).
  - Window ends at decision: ``win_start = decision - win_len + 1`` →
    ``visible_end == decision`` (zero future bars).
  - ``win_len ∈ {20..30}`` varies right-side position without future fill.
  - Drop any sample with ``end_time >= 2026-05-04`` (holdout iron rule).
  - Time split on event time + purge gap (default 150 bars × 15m).
  - ``event_id`` = sha1(symbol|anchor_bar|source stem); one crop per event.
  - Easy empty-bg negatives (1:1 target) with timestamps + same split rule.

Does **not** touch ACTIVE / owner_best / main forward_log.

Usage:
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/build_local_signal_v2_stageb.py --preview 8
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/build_local_signal_v2_stageb.py --limit 0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
_YOYO = Path.home() / "yoyo-trading"
for p in (PROJECT, _YOYO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart  # noqa: E402

from scripts.build_w20_midbox_dataset import (  # noqa: E402
    WIN_MAX,
    WIN_MIN,
    resolve_series,
    stable_seed,
    yolo_box_from_bars,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
# Spec §7.3: purge ≥ max window + MA lookback. MA120 + win30 ≈ 150 bars.
PURGE_BARS = 150
BAR_MINUTES = 15
CONFIRM_DELAYS = (1, 2)
BOX_LEFT = 2  # anchor - 2
RENDERER_VERSION = "yoyo.l1_detection.render.render_chart"
PROTOCOL = "local_signal_v2_stageb_mode_c_20260807"
STRICT_NEG_PROTOCOL = "local_signal_v2_stageb_mode_c_strictneg_v2_20260810"
DEFAULT_SRC_MANIFEST = PROJECT / "datasets" / "dense_owner_w20_midbox" / "w20_manifest.json"
DEFAULT_OUT = PROJECT / "datasets" / "local_signal_v2_stageb"
VAL_FRAC = 0.15
NEG_MARGIN = 15
NEG_MAX_TRIES = 80


@dataclass
class PosSample:
    event_id: str
    stem: str
    out_stem: str
    symbol: str
    split: str
    mid_global: int  # anchor_bar
    confirm_delay: int
    half: int  # alias for confirm_delay (audit compatibility)
    decision_bar: int
    small_bars: tuple[int, int]
    win_len: int
    win_start: int
    small_local: tuple[int, int]
    box_pos_frac: float
    stored_mad: float
    start_time: str
    anchor_time: str
    decision_time: str
    visible_end_time: str
    end_time: str
    out_img: str
    out_lbl: str
    config_hash: str
    image_sha256: str
    renderer_version: str
    stage: str
    mode: str
    future_bars: int
    source_stem: str


class Skip(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail or reason
        super().__init__(self.detail)


def event_id_of(symbol: str, anchor_bar: int, source_stem: str) -> str:
    raw = f"{symbol}|{anchor_bar}|{source_stem}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def config_hash_of(**kwargs: object) -> str:
    blob = json.dumps(kwargs, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_source_events(manifest_path: Path) -> list[dict]:
    rows = json.loads(manifest_path.read_text())
    # One row per source stem (event). Stage A had 1 crop each.
    by_stem: dict[str, dict] = {}
    for r in rows:
        by_stem[r["stem"]] = r
    return list(by_stem.values())


def assign_time_splits(
    events: list[dict],
    *,
    val_frac: float = VAL_FRAC,
    purge_bars: int = PURGE_BARS,
) -> dict[str, str]:
    """Map source stem → train|val|drop based on decision end_time.

    Uses Stage-A end_time only as a sort key proxy; Stage-B rebuild will recompute
    end_time from the causal window. Filter holdout after rebuild as well.
    """
    timed: list[tuple[pd.Timestamp, str]] = []
    for e in events:
        t = pd.to_datetime(e.get("end_time"), utc=True, errors="coerce")
        if pd.isna(t):
            continue
        if t >= HOLDOUT_START:
            continue
        timed.append((t, e["stem"]))
    timed.sort(key=lambda x: x[0])
    if not timed:
        return {}
    n_val = max(1, int(round(len(timed) * val_frac)))
    # val = last n_val events; train = earlier, with purge gap before first val
    val_set = {stem for _, stem in timed[-n_val:]}
    first_val_t = timed[-n_val][0]
    purge_delta = timedelta(minutes=purge_bars * BAR_MINUTES)
    train_cutoff = first_val_t - purge_delta
    out: dict[str, str] = {}
    for t, stem in timed:
        if stem in val_set:
            out[stem] = "val"
        elif t <= train_cutoff:
            out[stem] = "train"
        else:
            out[stem] = "drop"  # purge / embargo zone
    return out


def render_positive(
    src: dict,
    *,
    out_img: Path,
    out_lbl: Path,
    rng: np.random.Generator,
    draw_box: bool = False,
    protocol: str = PROTOCOL,
    fixed_window_len: int | None = None,
) -> PosSample:
    symbol = src["symbol"]
    anchor = int(src["mid_global"])
    source_stem = src["stem"]
    df = resolve_series(symbol)
    if df is None:
        raise Skip("no_series", symbol)
    enriched = add_mas(df)
    n = len(enriched)

    confirm_delay = int(rng.choice(CONFIRM_DELAYS))
    decision = anchor + confirm_delay
    s0 = anchor - BOX_LEFT
    s1 = decision
    if s0 < 0 or s1 >= n:
        raise Skip("small_oob", f"anchor={anchor} delay={confirm_delay} n={n}")

    # Always consume the same draw so fixed-window P1 arms preserve every
    # other seeded choice (notably confirm_delay) from the range-window arm.
    sampled_win_len = int(rng.integers(WIN_MIN, WIN_MAX + 1))
    win_len = sampled_win_len if fixed_window_len is None else fixed_window_len
    # Stage B causal: window ends exactly on decision bar.
    win_start = decision - win_len + 1
    if win_start < 0:
        raise Skip("no_room", f"win_len={win_len} decision={decision}")
    # Box must fully sit inside window (left side).
    if s0 < win_start:
        raise Skip("box_left_oob", f"s0={s0} win_start={win_start}")

    win_df = enriched.iloc[win_start : win_start + win_len].reset_index(drop=True)
    if len(win_df) != win_len:
        raise Skip("short_win", source_stem)

    img, tf = render_chart(win_df, out_path=None)
    loc0 = s0 - win_start
    loc1 = s1 - win_start
    yolo = yolo_box_from_bars(tf, win_df, loc0, loc1)
    if yolo is None:
        raise Skip("empty_yolo", source_stem)

    out_img.parent.mkdir(parents=True, exist_ok=True)
    out_lbl.parent.mkdir(parents=True, exist_ok=True)
    if draw_box:
        h, w = img.shape[:2]
        xc, yc, bw, bh = yolo
        x1, x2 = int((xc - bw / 2) * w), int((xc + bw / 2) * w)
        y1, y2 = int((yc - bh / 2) * h), int((yc + bh / 2) * h)
        vis = img.copy()
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 220), 2, cv2.LINE_AA)
        cv2.imwrite(str(out_img), vis)
    else:
        cv2.imwrite(str(out_img), img)
    out_lbl.write_text(f"0 {yolo[0]:.6f} {yolo[1]:.6f} {yolo[2]:.6f} {yolo[3]:.6f}\n")

    start_ts = win_df.iloc[0].get("open_time", win_df.index[0])
    anchor_ts = enriched.iloc[anchor].get("open_time", enriched.index[anchor])
    ts = win_df.iloc[-1].get("open_time", win_df.index[-1])
    start_t = pd.to_datetime(start_ts, utc=True, errors="coerce")
    anchor_t = pd.to_datetime(anchor_ts, utc=True, errors="coerce")
    t = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(start_t) or pd.isna(anchor_t) or pd.isna(t):
        raise Skip("bad_time", f"start={start_ts} anchor={anchor_ts} end={ts}")
    if t >= HOLDOUT_START:
        # Remove just-written files so holdout never lands on disk.
        out_img.unlink(missing_ok=True)
        out_lbl.unlink(missing_ok=True)
        raise Skip("holdout", str(t))

    fut = (win_start + win_len - 1) - decision
    assert fut == 0, f"causal invariant broken fut={fut}"
    box_pos = float((loc0 + loc1) / 2 / max(win_len - 1, 1))
    cfg = config_hash_of(
        protocol=protocol,
        win_len=win_len,
        confirm_delay=confirm_delay,
        box_left=BOX_LEFT,
        stage="B",
        mode="C",
    )
    img_hash = sha256_file(out_img)
    eid = event_id_of(symbol, anchor, source_stem)
    return PosSample(
        event_id=eid,
        stem=source_stem,
        out_stem=out_img.stem,
        symbol=symbol,
        split="",  # filled by caller
        mid_global=anchor,
        confirm_delay=confirm_delay,
        half=confirm_delay,
        decision_bar=decision,
        small_bars=(s0, s1),
        win_len=win_len,
        win_start=win_start,
        small_local=(loc0, loc1),
        box_pos_frac=box_pos,
        stored_mad=float(src.get("stored_mad", 0.0)),
        start_time=str(start_t),
        anchor_time=str(anchor_t),
        decision_time=str(t),
        visible_end_time=str(t),
        end_time=str(t),
        out_img=str(out_img),
        out_lbl=str(out_lbl),
        config_hash=cfg,
        image_sha256=img_hash,
        renderer_version=RENDERER_VERSION,
        stage="B",
        mode="C",
        future_bars=0,
        source_stem=source_stem,
    )


def forbidden_intervals(anchors: list[int], half_max: int = 3) -> list[tuple[int, int]]:
    spans = [(a - half_max - NEG_MARGIN, a + half_max + NEG_MARGIN) for a in anchors]
    spans.sort()
    if not spans:
        return []
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def overlaps(w0: int, w1: int, forb: list[tuple[int, int]]) -> bool:
    for a, b in forb:
        if not (w1 < a or w0 > b):
            return True
    return False


def add_negatives(
    pos_rows: list[dict],
    dst: Path,
    *,
    ratio: float = 1.0,
    seed: int = 20260807,
    time_bounds: dict[str, pd.Timestamp] | None = None,
    protocol: str = PROTOCOL,
    fixed_window_len: int | None = None,
) -> list[dict]:
    by_sym_split: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in pos_rows:
        by_sym_split[(r["symbol"], r["split"])].append(r)

    neg_rows: list[dict] = []
    recorded_stems: set[str] = set()
    for (symbol, split), rows in sorted(by_sym_split.items()):
        target = max(1, int(round(len(rows) * ratio)))
        df = resolve_series(symbol)
        if df is None:
            continue
        enriched = add_mas(df)
        n = len(enriched)
        forb = forbidden_intervals([int(r["mid_global"]) for r in rows])
        got = 0
        attempt = 0
        while got < target and attempt < target * NEG_MAX_TRIES:
            attempt += 1
            rng = np.random.default_rng(stable_seed(seed, "neg", symbol, split, attempt))
            sampled_win_len = int(rng.integers(WIN_MIN, WIN_MAX + 1))
            win_len = sampled_win_len if fixed_window_len is None else fixed_window_len
            if n < win_len + 50:
                break
            # Prefer windows fully before holdout.
            # Sample end bar index such that its timestamp < holdout.
            w0 = int(rng.integers(0, n - win_len + 1))
            w1 = w0 + win_len - 1
            if overlaps(w0, w1, forb):
                continue
            win_df = enriched.iloc[w0 : w0 + win_len].reset_index(drop=True)
            start_ts = win_df.iloc[0].get("open_time", win_df.index[0])
            ts = win_df.iloc[-1].get("open_time", win_df.index[-1])
            start_t = pd.to_datetime(start_ts, utc=True, errors="coerce")
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if pd.isna(start_t) or pd.isna(t) or t >= HOLDOUT_START:
                continue
            if time_bounds is not None and not negative_window_allowed(
                split,
                start_time=start_t,
                end_time=t,
                bounds=time_bounds,
            ):
                continue
            # V2 additionally constrains the full window to frozen time bounds.
            # The legacy path keeps its historical split-label-only behavior.
            out_stem = f"{symbol}_{w0:06d}_w{win_len}_neg"
            out_img = dst / "images" / split / f"{out_stem}.png"
            out_lbl = dst / "labels" / split / f"{out_stem}.txt"
            if time_bounds is not None and out_stem in recorded_stems:
                continue
            if out_img.exists():
                if time_bounds is not None:
                    if not out_lbl.exists():
                        raise RuntimeError(f"existing negative image has no label: {out_img}")
                    neg_rows.append(
                        {
                            "stem": out_stem,
                            "symbol": symbol,
                            "split": split,
                            "win_start": w0,
                            "win_len": win_len,
                            "start_time": str(start_t),
                            "end_time": str(t),
                            "kind": "empty_bg",
                            "out_img": str(out_img),
                            "out_lbl": str(out_lbl),
                            "image_sha256": sha256_file(out_img),
                            "config_hash": config_hash_of(
                                protocol=protocol,
                                kind="empty_bg",
                                negative_split="strict_time",
                            ),
                            "renderer_version": RENDERER_VERSION,
                            "stage": "B",
                        }
                    )
                    recorded_stems.add(out_stem)
                got += 1
                continue
            img, _tf = render_chart(win_df, out_path=None)
            out_img.parent.mkdir(parents=True, exist_ok=True)
            out_lbl.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out_img), img)
            out_lbl.write_text("")  # empty = negative
            neg_rows.append(
                {
                    "stem": out_stem,
                    "symbol": symbol,
                    "split": split,
                    "win_start": w0,
                    "win_len": win_len,
                    "start_time": str(start_t),
                    "end_time": str(t),
                    "kind": "empty_bg",
                    "out_img": str(out_img),
                    "out_lbl": str(out_lbl),
                    "image_sha256": sha256_file(out_img),
                    "config_hash": (
                        config_hash_of(
                            protocol=protocol,
                            kind="empty_bg",
                            negative_split="strict_time",
                        )
                        if time_bounds is not None
                        else config_hash_of(protocol=protocol, kind="empty_bg")
                    ),
                    "renderer_version": RENDERER_VERSION,
                    "stage": "B",
                }
            )
            if time_bounds is not None:
                recorded_stems.add(out_stem)
            got += 1
    return neg_rows


def derive_negative_time_bounds(pos_rows: list[dict]) -> dict[str, pd.Timestamp]:
    """Freeze negative sampling to the actual positive train/val time blocks.

    Inputs are Stage-B positive manifest rows.  The train block ends at the
    latest train decision timestamp.  Validation negative windows must start no
    earlier than the first validation decision and end no later than the last
    validation decision.  This deliberately leaves the positive purge gap empty
    instead of assigning a future-period background window to train.
    """
    frame = pd.DataFrame(
        {
            "split": [r.get("split") for r in pos_rows],
            "end_time": pd.to_datetime(
                [r.get("end_time") for r in pos_rows], utc=True, errors="coerce"
            ),
        }
    ).dropna()
    train = frame.loc[frame["split"] == "train", "end_time"]
    val = frame.loc[frame["split"] == "val", "end_time"]
    if train.empty or val.empty:
        raise ValueError("strict negative split requires non-empty train and val positives")
    train_end = train.max()
    val_start = val.min()
    val_end = val.max()
    if train_end >= val_start:
        raise ValueError(f"positive time split overlaps: train_end={train_end} val_start={val_start}")
    gap_bars = (val_start - train_end).total_seconds() / (BAR_MINUTES * 60)
    if gap_bars < PURGE_BARS:
        raise ValueError(
            f"positive purge gap too small: {gap_bars:.1f} bars < {PURGE_BARS}"
        )
    return {
        "train_end_max": train_end,
        "val_start_min": val_start,
        "val_end_max": val_end,
    }


def negative_window_allowed(
    split: str,
    *,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    bounds: dict[str, pd.Timestamp],
) -> bool:
    """Return whether a negative window is wholly inside its frozen time block."""
    if start_time > end_time or end_time >= HOLDOUT_START:
        return False
    if split == "train":
        return bool(end_time <= bounds["train_end_max"])
    if split == "val":
        return bool(
            start_time >= bounds["val_start_min"]
            and end_time <= bounds["val_end_max"]
        )
    return False


def write_yaml(dst: Path) -> None:
    (dst / "data.yaml").write_text(
        f"path: {dst.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"names:\n  0: dense_start\n"
    )


def select_diverse_preview_events(
    events: list[dict], splits: dict[str, str], *, n: int, seed: int
) -> list[dict]:
    """Choose a deterministic preview with symbol diversity before repeats."""
    candidates = [
        e for e in events if splits.get(e.get("stem")) in {"train", "val"}
    ]
    candidates.sort(
        key=lambda e: stable_seed(seed, "preview", e.get("symbol"), e.get("stem"))
    )
    first_per_symbol: list[dict] = []
    repeats: list[dict] = []
    seen: set[str] = set()
    for event in candidates:
        symbol = str(event.get("symbol"))
        if symbol in seen:
            repeats.append(event)
        else:
            seen.add(symbol)
            first_per_symbol.append(event)
    return (first_per_symbol + repeats)[:n]


def run_preview(
    src_manifest: Path,
    n: int,
    out_dir: Path,
    seed: int,
    *,
    protocol: str = PROTOCOL,
    fixed_window_len: int | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    events = load_source_events(src_manifest)
    splits = assign_time_splits(events)
    results = []
    for e in select_diverse_preview_events(events, splits, n=n, seed=seed):
        local = np.random.default_rng(stable_seed(seed, e["stem"]))
        out_img = out_dir / f"{e['stem']}_stageb.png"
        out_lbl = out_dir / f"{e['stem']}_stageb.txt"
        try:
            res = render_positive(
                e,
                out_img=out_img,
                out_lbl=out_lbl,
                rng=local,
                draw_box=True,
                protocol=protocol,
                fixed_window_len=fixed_window_len,
            )
        except Skip as ex:
            print(f"skip {e['stem']}: {ex.reason} {ex.detail}")
            continue
        res.split = splits[e["stem"]]
        results.append(asdict(res))
        print(json.dumps(results[-1], ensure_ascii=False))
    (out_dir / "preview_summary.json").write_text(json.dumps(results, indent=2))
    print(f"preview → {out_dir} n={len(results)}")


def run_full(
    src_manifest: Path,
    dst: Path,
    *,
    seed: int,
    limit: int,
    neg_ratio: float,
    strict_negative_time_split: bool = False,
    protocol: str = PROTOCOL,
    fixed_window_len: int | None = None,
) -> dict:
    events = load_source_events(src_manifest)
    splits = assign_time_splits(events)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (dst / sub).mkdir(parents=True, exist_ok=True)

    pos_rows: list[dict] = []
    skip_reasons: Counter[str] = Counter()
    n_done = 0
    for e in events:
        if limit and n_done >= limit:
            break
        split = splits.get(e["stem"])
        if split is None:
            skip_reasons["holdout_or_no_time"] += 1
            continue
        if split == "drop":
            skip_reasons["purge_zone"] += 1
            continue
        local = np.random.default_rng(stable_seed(seed, "stageb", e["stem"]))
        out_stem = f"{Path(e['stem']).stem}_stageb"
        # base_stem may already end with _pad200 — keep readable
        out_stem = e["stem"].replace("_pad200", "") + "_stageb"
        out_img = dst / "images" / split / f"{out_stem}.png"
        out_lbl = dst / "labels" / split / f"{out_stem}.txt"
        try:
            res = render_positive(
                e,
                out_img=out_img,
                out_lbl=out_lbl,
                rng=local,
                protocol=protocol,
                fixed_window_len=fixed_window_len,
            )
        except Skip as ex:
            skip_reasons[ex.reason] += 1
            continue
        res.split = split
        d = asdict(res)
        # audit expects small_bars as list
        d["small_bars"] = list(res.small_bars)
        d["small_local"] = list(res.small_local)
        pos_rows.append(d)
        n_done += 1
        if n_done % 100 == 0:
            print(f"... pos {n_done} skips={dict(skip_reasons)}")

    print(f"positives done: {len(pos_rows)}; adding negatives ratio={neg_ratio}")
    time_bounds = derive_negative_time_bounds(pos_rows) if strict_negative_time_split else None
    neg_rows = add_negatives(
        pos_rows,
        dst,
        ratio=neg_ratio,
        seed=seed,
        time_bounds=time_bounds,
        protocol=protocol,
        fixed_window_len=fixed_window_len,
    )

    write_yaml(dst)
    counts = {
        "train": sum(1 for r in pos_rows if r["split"] == "train"),
        "val": sum(1 for r in pos_rows if r["split"] == "val"),
        "train_neg": sum(1 for r in neg_rows if r["split"] == "train"),
        "val_neg": sum(1 for r in neg_rows if r["split"] == "val"),
    }
    # split time ranges
    def _range(rows, split):
        ts = pd.to_datetime([r["end_time"] for r in rows if r["split"] == split], utc=True)
        if len(ts) == 0:
            return {}
        return {"n": int(len(ts)), "min": str(ts.min()), "max": str(ts.max())}

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol,
        "src_manifest": str(src_manifest),
        "out": str(dst),
        "seed": seed,
        "stage": "B",
        "mode": "C",
        "confirm_delays": list(CONFIRM_DELAYS),
        "box_left": BOX_LEFT,
        "win_min": WIN_MIN if fixed_window_len is None else fixed_window_len,
        "win_max": WIN_MAX if fixed_window_len is None else fixed_window_len,
        "fixed_window_len": fixed_window_len,
        "holdout_start": str(HOLDOUT_START),
        "purge_bars": PURGE_BARS,
        "val_frac": VAL_FRAC,
        "split_rule": (
            f"time-ordered events end_time < holdout; last {VAL_FRAC:.0%} → val; "
            f"train ends ≥{PURGE_BARS} bars before first val; purge zone dropped"
        ),
        "is_time_split": True,
        "strict_negative_time_split": strict_negative_time_split,
        "negative_time_bounds": (
            None if time_bounds is None else {k: str(v) for k, v in time_bounds.items()}
        ),
        "counts": counts,
        "skip_reasons": dict(skip_reasons),
        "n_pos_manifest": len(pos_rows),
        "n_neg_manifest": len(neg_rows),
        "time_range": {
            "train": _range(pos_rows, "train"),
            "val": _range(pos_rows, "val"),
        },
        "renderer_version": RENDERER_VERSION,
    }
    (dst / "w20_manifest.json").write_text(json.dumps(pos_rows, indent=2))
    (dst / "w20_neg_manifest.json").write_text(json.dumps(neg_rows, indent=2))
    (dst / "w20_summary.json").write_text(json.dumps(summary, indent=2))
    (dst / "stageb_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src-manifest", type=Path, default=DEFAULT_SRC_MANIFEST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--preview", type=int, default=0)
    ap.add_argument(
        "--preview-dir",
        type=Path,
        default=PROJECT / "analysis" / "output" / "local_signal_v2_stageb_preview",
    )
    ap.add_argument("--seed", type=int, default=20260807)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--neg-ratio", type=float, default=1.0)
    ap.add_argument("--fixed-window-len", type=int, choices=range(WIN_MIN, WIN_MAX + 1))
    args = ap.parse_args()

    if not args.src_manifest.exists():
        print(f"missing source manifest: {args.src_manifest}", file=sys.stderr)
        return 2
    if args.preview > 0:
        run_preview(
            args.src_manifest,
            args.preview,
            args.preview_dir,
            args.seed,
            fixed_window_len=args.fixed_window_len,
        )
        return 0
    run_full(
        args.src_manifest,
        args.out,
        seed=args.seed,
        limit=args.limit,
        neg_ratio=args.neg_ratio,
        fixed_window_len=args.fixed_window_len,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
