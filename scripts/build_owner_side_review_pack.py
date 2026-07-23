#!/usr/bin/env python3
"""Build an interactive owner long/short review pack from dense_owner_v11.

Owner wants to manually tag each gold box as long / short / skip, then re-run
feature disclosure + causal base rate per side. This script only prepares the
gate — it never fills owner_side.

Outputs under analysis/output/owner_side_review/:
  review_sheet.csv       — one row per independent owner box (~5–6k after MAD)
  items.json             — gallery payload (coords + paths)
  sample_ids.json        — stratified priority sample (default 400)
  previews/*.jpg         — sample overlays (highlighted box)
  gallery.html           — single-card keyboard UI
  README.md              — how to label + which command to run after

Honesty: v11 has ~11.7k images / 5831 positive boxes (YOLO 5-tuple, no side).
Tip-clone sets (v12 htip ~10k+) are NOT the review base — independent owner
boxes only. Holdout cuts are dropped.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_review_pack.py
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_review_pack.py \\
      --sample-n 400 --tag owner_side_review
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.build_crop_pad200_dataset import (  # noqa: E402
    MAX_STORED_MAD,
    boxes_cut_and_spans,
    resolve_win_start,
)
from scripts.build_htip_dataset import (  # noqa: E402
    WINDOW,
    parse_stem,
    read_boxes,
    resolve_series,
)
from scripts.owner_label_feature_verdict import (  # noqa: E402
    HOLDOUT_START,
    WARMUP,
    _find_image,
    _sym_key,
)
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_stem  # noqa: E402
from src.detection.render import make_chart_transform  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import add_features  # noqa: E402

DEFAULT_SRC = PROJECT / "datasets" / "_deprecated_pretip" / "dense_owner_v11"
DEFAULT_OUT = PROJECT / "analysis" / "output" / "owner_side_review"
COLOR_FOCUS = (40, 220, 80)  # BGR green — the box being labeled
COLOR_OTHER = (160, 160, 160)


def list_positive_labels(src: Path) -> list[tuple[Path, str, str, int]]:
    """Light pass: (label_path, stem, symbol_body, n_boxes) for non-empty labels."""
    out: list[tuple[Path, str, str, int]] = []
    for split in ("train", "val"):
        d = src / "labels" / split
        if not d.is_dir():
            continue
        for lbl in sorted(d.glob("*.txt")):
            stem = lbl.stem
            if is_eval_stem(stem):
                continue
            boxes = read_boxes(lbl)
            if not boxes:
                continue
            parsed = parse_stem(stem)
            if not parsed:
                continue
            body, _idx = parsed
            out.append((lbl, stem, body, len(boxes)))
    return out


def pick_stems_for_sample(
    positives: list[tuple[Path, str, str, int]],
    target_boxes: int,
    seed: int,
) -> set[str]:
    """Stratify by symbol until ~target_boxes (counting label box counts)."""
    rng = np.random.default_rng(seed)
    by_sym: dict[str, list[tuple[Path, str, str, int]]] = defaultdict(list)
    for item in positives:
        by_sym[item[2]].append(item)
    for s in by_sym:
        items = by_sym[s]
        items.sort(key=lambda x: -x[3])  # multi-box first
        singles = [x for x in items if x[3] <= 1]
        multis = [x for x in items if x[3] > 1]
        rng.shuffle(singles)
        by_sym[s] = multis + singles
    chosen: set[str] = set()
    n_boxes = 0
    syms = sorted(by_sym)
    while n_boxes < target_boxes and any(by_sym[s] for s in syms):
        rng.shuffle(syms)
        progressed = False
        for s in syms:
            if n_boxes >= target_boxes:
                break
            if not by_sym[s]:
                continue
            _lbl, stem, _body, nb = by_sym[s].pop(0)
            if stem in chosen:
                continue
            chosen.add(stem)
            n_boxes += nb
            progressed = True
        if not progressed:
            break
    return chosen


def extract_boxes(
    src: Path,
    *,
    limit: int = 0,
    series_cache: dict[str, pd.DataFrame],
    with_diag: bool = True,
    allow_stems: set[str] | None = None,
) -> tuple[list[dict], dict]:
    """One row per YOLO box with cut_global, geometry, optional spread_chg8."""
    skips: dict[str, int] = defaultdict(int)
    rows: list[dict] = []
    label_paths: list[Path] = []
    for split in ("train", "val"):
        d = src / "labels" / split
        if d.is_dir():
            label_paths.extend(sorted(d.glob("*.txt")))

    featured_cache: dict[str, pd.DataFrame] = {}

    for lbl in label_paths:
        if limit and len(rows) >= limit:
            break
        stem = lbl.stem
        if allow_stems is not None and stem not in allow_stems:
            continue
        if is_eval_stem(stem):
            skips["eval_stem"] += 1
            continue
        boxes = read_boxes(lbl)
        if not boxes:
            skips["empty"] += 1
            continue
        parsed = parse_stem(stem)
        if not parsed:
            skips["bad_stem"] += 1
            continue
        body, idx = parsed
        if body not in series_cache:
            df = resolve_series(body)
            if df is None:
                df = resolve_series(_sym_key(body))
            series_cache[body] = df
        df = series_cache[body]
        if df is None or len(df) < WINDOW + WARMUP:
            skips["no_series"] += 1
            continue
        enriched_mas = add_mas(df)
        img_path = _find_image(src, stem)
        stored = cv2.imread(str(img_path)) if img_path is not None else None
        if stored is None:
            skips["no_image"] += 1
            if stem.startswith("okx_"):
                skips["okx_no_mad"] += 1
                continue
        try:
            resolved = resolve_win_start(
                len(df), idx, enriched=enriched_mas, stored_img=stored
            )
        except Exception:
            skips["resolve_err"] += 1
            continue
        if resolved is None:
            skips["no_win"] += 1
            continue
        win_mode, win_start, mad = resolved
        if stored is not None and np.isfinite(mad) and mad > MAX_STORED_MAD:
            skips["mad_fail"] += 1
            continue
        if stored is None and stem.startswith("okx_") and win_mode == "end_incl":
            skips["okx_blind_end_incl"] += 1
            continue
        sub = enriched_mas.iloc[win_start : win_start + WINDOW].reset_index(drop=True)
        if len(sub) != WINDOW:
            skips["short_win"] += 1
            continue
        tf = make_chart_transform(sub)
        _cut_local, spans = boxes_cut_and_spans(boxes, tf)
        split = "val" if "/val/" in str(lbl).replace("\\", "/") else "train"
        img_rel = ""
        if img_path is not None:
            try:
                img_rel = str(img_path.relative_to(PROJECT))
            except ValueError:
                img_rel = str(img_path)

        featured = None
        if with_diag:
            if body not in featured_cache:
                times = pd.to_datetime(df["open_time"], utc=True)
                df_tr = df[times < HOLDOUT_START].reset_index(drop=True)
                featured_cache[body] = (
                    add_features(add_indicators(add_mas(df_tr)))
                    if len(df_tr) >= WARMUP + 50
                    else pd.DataFrame()
                )
            featured = featured_cache[body]

        for bi, ((xc, yc, bw, bh), (b0, b1, price_hi, price_lo)) in enumerate(
            zip(boxes, spans)
        ):
            cut_i = win_start + int(b1)
            if cut_i < WARMUP or cut_i >= len(df) - 1:
                skips["cut_oob"] += 1
                continue
            t_i = pd.to_datetime(df["open_time"].iloc[cut_i], utc=True)
            if t_i >= HOLDOUT_START:
                skips["holdout_cut"] += 1
                continue
            width_bars = max(1, int(b1) - int(b0) + 1)
            mid = (float(price_hi) + float(price_lo)) / 2.0
            height_pct = abs(float(price_hi) - float(price_lo)) / max(mid, 1e-12)
            box_id = f"{stem}__b{bi}"
            diag: dict = {
                "spread_chg8": "",
                "fast_spread": "",
                "full_spread": "",
                "ma_spread_pct": "",
            }
            if featured is not None and len(featured):
                tt = pd.to_datetime(featured["open_time"], utc=True)
                hits = np.where(tt == t_i)[0]
                if len(hits):
                    r = featured.iloc[int(hits[0])]
                    for k in diag:
                        v = r.get(k, np.nan)
                        diag[k] = (
                            f"{float(v):.6g}" if pd.notna(v) and np.isfinite(float(v)) else ""
                        )
            rows.append(
                {
                    "box_id": box_id,
                    "symbol": body if body.endswith("_SWAP") else _sym_key(body),
                    "stem": stem,
                    "split": split,
                    "image_path": img_rel,
                    "cut_global": int(cut_i),
                    "cut_time": str(t_i),
                    "bar_b0": int(b0),
                    "bar_b1": int(b1),
                    "width_bars": int(width_bars),
                    "yolo_xc": float(xc),
                    "yolo_yc": float(yc),
                    "yolo_w": float(bw),
                    "yolo_h": float(bh),
                    "box_height_pct": round(float(height_pct), 6),
                    "box_right_frac": round(float((b1 + 0.5) / WINDOW), 4),
                    "win_mode": win_mode,
                    "box_index": bi,
                    "n_boxes_on_image": len(boxes),
                    **diag,
                    "in_sample": 0,
                    "owner_side": "",
                    "owner_note": "",
                }
            )
            if limit and len(rows) >= limit:
                break
    return rows, dict(skips)


def stratified_sample(rows: list[dict], n: int, seed: int) -> list[str]:
    """Prefer symbol diversity + multi-box stems; return box_ids."""
    rng = np.random.default_rng(seed)
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)
    syms = sorted(by_sym)
    if not syms or n <= 0:
        return []
    # Round-robin take until n, shuffle within symbol first.
    pools = {}
    for s in syms:
        items = list(by_sym[s])
        # Multi-box stems first within symbol (harder / more informative).
        items.sort(key=lambda r: (-int(r["n_boxes_on_image"]), r["stem"], r["box_index"]))
        # Light shuffle of singles so we don't always take earliest stems.
        singles = [r for r in items if int(r["n_boxes_on_image"]) <= 1]
        multis = [r for r in items if int(r["n_boxes_on_image"]) > 1]
        rng.shuffle(singles)
        pools[s] = multis + singles
    chosen: list[str] = []
    seen_stem: set[str] = set()
    while len(chosen) < n and any(pools[s] for s in syms):
        rng.shuffle(syms)
        progress = False
        for s in syms:
            if len(chosen) >= n:
                break
            while pools[s]:
                r = pools[s].pop(0)
                # Prefer new stems, but allow 2nd box on same stem for multi.
                if r["stem"] in seen_stem and int(r["n_boxes_on_image"]) <= 1:
                    continue
                chosen.append(r["box_id"])
                seen_stem.add(r["stem"])
                progress = True
                break
        if not progress:
            # Drain leftovers ignoring stem dedupe.
            for s in syms:
                if pools[s] and len(chosen) < n:
                    chosen.append(pools[s].pop(0)["box_id"])
                    progress = True
            if not progress:
                break
    return chosen[:n]


def draw_preview(
    src: Path,
    row: dict,
    out_path: Path,
    *,
    max_w: int = 900,
) -> bool:
    img_path = _find_image(src, row["stem"])
    if img_path is None:
        return False
    img = cv2.imread(str(img_path))
    if img is None:
        return False
    h, w = img.shape[:2]
    # Draw other boxes on the same stem faintly, focus box bright.
    # We only know this box's yolo; re-read label for siblings.
    lbl = src / "labels" / row["split"] / f"{row['stem']}.txt"
    boxes = read_boxes(lbl) if lbl.exists() else []
    focus = int(row["box_index"])
    for i, (xc, yc, bw, bh) in enumerate(boxes):
        x1 = int((xc - bw / 2) * w)
        x2 = int((xc + bw / 2) * w)
        y1 = int((yc - bh / 2) * h)
        y2 = int((yc + bh / 2) * h)
        color = COLOR_FOCUS if i == focus else COLOR_OTHER
        thick = 3 if i == focus else 1
        cv2.rectangle(img, (x1, y1), (x2, y2), color, thick, cv2.LINE_AA)
        if i == focus:
            tag = row["box_id"][-12:]
            cv2.putText(
                img,
                tag,
                (x1, max(18, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )
    if w > max_w:
        scale = max_w / w
        img = cv2.resize(img, (max_w, int(h * scale)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return True


def write_gallery_html(out: Path) -> None:
    """Copy/write interactive single-card gallery (loads items.json + API)."""
    # Kept as a sibling file written by this builder so the pack is self-contained.
    src_html = PROJECT / "scripts" / "_owner_side_gallery.html"
    dst = out / "gallery.html"
    if src_html.exists():
        shutil.copy2(src_html, dst)
    else:
        raise SystemExit(f"missing gallery template: {src_html}")


def write_readme(out: Path, *, n_full: int, n_sample: int, n_images: int, skips: dict) -> None:
    text = f"""# Owner 多空人工审阅包

> 闸门：你手动区分 **long / short / skip**，再跑分边特征 + 因果 base rate。
> 本包**不会**替你填方向。

## 金标来源（读这一段就够）

| 项 | 值 |
|---|---|
| 主源 | `datasets/_deprecated_pretip/dense_owner_v11` |
| 图总数 | {n_images}（含空标背景） |
| **独立 owner 正框** | **{n_full}**（YOLO 仅 class+xywh，**无 side 字段**） |
| 优先小样 | **{n_sample}**（分层抽样，默认先标这个） |
| MAD/错窗等跳过 | `{json.dumps(skips, ensure_ascii=False)}` |

「一万多」若含 tip clone（如 v12 htip），那是克隆集；**审阅请基于本包的独立 owner 框**。

## 怎么开始标（推荐）

在仓库根目录：

```bash
PYTHONPATH=. .venv/bin/python scripts/serve_owner_side_review.py
```

浏览器打开终端打印的地址（默认 http://127.0.0.1:8765/gallery.html）。

### 快捷键

| 键 | 含义 |
|---|---|
| **L** | long（做多手法） |
| **S** | short（做空手法） |
| **K** 或 **X** | skip（看不清 / 不作为本轮样本） |
| **N** | 下一张 |
| **P** | 上一张 |
| **U** | 只看未标注 |
| **1** | 小样模式 |
| **2** | 全量模式 |

点按钮与按键等价。标注后**自动跳下一张**，并立刻写入：

- `reviews.jsonl`（追加）
- `review_sheet.csv` 的 `owner_side` / `owner_note` 列

刷新不丢（服务端落盘 + 浏览器 localStorage 备份）。

### 离线打开（不推荐）

`open analysis/output/owner_side_review/gallery.html` 也能看图，但**无法写盘**；
只能用页内「导出 CSV」下载进度。请优先用上面的 serve 脚本。

## 填完后跑什么

```bash
PYTHONPATH=. .venv/bin/python scripts/owner_side_feature_verdict.py \\
    --sheet analysis/output/owner_side_review/review_sheet.csv \\
    --tag owner_side_feature_verdict
```

未填任何 `owner_side` 时脚本会拒绝运行。

成功线（写在下游 docstring）：**某一边** train 段因果规则 PF@maker ≥ **1.3**
才算该方向有可部署增量。禁止 holdout；只扫 `<2026-05-04`。

## 诚实陷阱

1. **事后 hindsight**：若你看着框**后面的走势**再标多空，标签会被污染；
   尽量按「触发当下能判断的方向」标。最终裁判仍是因果 base rate，不是你的感觉。
2. 诊断列（`spread_chg8` 等）只读，**不要**被它们带节奏去填 side。
3. `skip` 不进下游正样本；宁可 skip 也不要瞎标。
"""
    (out / "README.md").write_text(text, encoding="utf-8")


SHEET_COLS = [
    "box_id",
    "symbol",
    "stem",
    "split",
    "image_path",
    "preview_path",
    "cut_global",
    "cut_time",
    "bar_b0",
    "bar_b1",
    "width_bars",
    "yolo_xc",
    "yolo_yc",
    "yolo_w",
    "yolo_h",
    "box_height_pct",
    "box_right_frac",
    "win_mode",
    "box_index",
    "n_boxes_on_image",
    "spread_chg8",
    "fast_spread",
    "full_spread",
    "ma_spread_pct",
    "in_sample",
    "owner_side",
    "owner_note",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--sample-n", type=int, default=400)
    ap.add_argument(
        "--quick-sample",
        type=int,
        default=0,
        help="Fast path: only extract ~N stratified boxes (skip full walk).",
    )
    ap.add_argument("--limit", type=int, default=0, help="cap boxes (0=all)")
    ap.add_argument("--seed", type=int, default=20260723)
    ap.add_argument("--no-diag", action="store_true", help="skip spread_chg8 etc.")
    ap.add_argument("--skip-previews", action="store_true")
    args = ap.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    prev_dir = out / "previews"
    prev_dir.mkdir(exist_ok=True)

    n_images = sum(1 for _ in (args.src / "images").rglob("*.png")) if args.src.exists() else 0
    print(f"SRC={args.src} images={n_images}")
    series_cache: dict[str, pd.DataFrame] = {}
    allow_stems: set[str] | None = None
    if args.quick_sample and args.quick_sample > 0:
        positives = list_positive_labels(args.src)
        allow_stems = pick_stems_for_sample(positives, args.quick_sample, args.seed)
        print(
            f"quick_sample target_boxes≈{args.quick_sample} "
            f"stems_picked={len(allow_stems)} positives_available={len(positives)}"
        )
    rows, skips = extract_boxes(
        args.src,
        limit=args.limit,
        series_cache=series_cache,
        with_diag=not args.no_diag,
        allow_stems=allow_stems,
    )
    print(f"boxes_extracted={len(rows)} skips={skips}")
    if len(rows) < 10:
        print("ERROR: too few boxes")
        return 1

    if args.quick_sample and args.quick_sample > 0:
        # Entire extracted set IS the priority sample.
        sample_ids = [r["box_id"] for r in rows]
        sample_set = set(sample_ids)
        for r in rows:
            r["in_sample"] = 1
            r["preview_path"] = ""
    else:
        sample_ids = stratified_sample(rows, args.sample_n, args.seed)
        sample_set = set(sample_ids)
        for r in rows:
            r["in_sample"] = 1 if r["box_id"] in sample_set else 0
            r["preview_path"] = ""

    if not args.skip_previews:
        n_ok = 0
        for r in rows:
            if r["box_id"] not in sample_set:
                continue
            rel = f"previews/{r['box_id']}.jpg"
            ok = draw_preview(args.src, r, out / rel)
            if ok:
                r["preview_path"] = rel
                n_ok += 1
        print(f"sample_previews={n_ok}/{len(sample_ids)}")

    # Preserve any existing owner_side if rebuilding.
    sheet_path = out / "review_sheet.csv"
    prior_sides: dict[str, tuple[str, str]] = {}
    if sheet_path.exists():
        old = pd.read_csv(sheet_path, dtype=str).fillna("")
        if "box_id" in old.columns and "owner_side" in old.columns:
            for _, o in old.iterrows():
                side = str(o.get("owner_side", "")).strip().lower()
                if side in ("long", "short", "skip"):
                    prior_sides[str(o["box_id"])] = (
                        side,
                        str(o.get("owner_note", "")),
                    )
    if prior_sides:
        kept = 0
        for r in rows:
            if r["box_id"] in prior_sides:
                r["owner_side"], r["owner_note"] = prior_sides[r["box_id"]]
                kept += 1
        print(f"preserved_prior_sides={kept}")

    df = pd.DataFrame(rows)
    for c in SHEET_COLS:
        if c not in df.columns:
            df[c] = ""
    df = df[SHEET_COLS]
    df.to_csv(sheet_path, index=False)
    df[df["in_sample"] == 1].to_csv(out / "review_sheet_sample.csv", index=False)

    items = []
    for r in rows:
        items.append(
            {
                "box_id": r["box_id"],
                "symbol": r["symbol"],
                "stem": r["stem"],
                "split": r["split"],
                "cut_time": r["cut_time"],
                "cut_global": r["cut_global"],
                "width_bars": r["width_bars"],
                "bar_b0": r["bar_b0"],
                "bar_b1": r["bar_b1"],
                "yolo": [r["yolo_xc"], r["yolo_yc"], r["yolo_w"], r["yolo_h"]],
                "n_boxes_on_image": r["n_boxes_on_image"],
                "box_index": r["box_index"],
                "image_path": r["image_path"],
                "preview_path": r["preview_path"],
                "in_sample": int(r["in_sample"]),
                "spread_chg8": r.get("spread_chg8", ""),
                "fast_spread": r.get("fast_spread", ""),
                "owner_side": r.get("owner_side", ""),
                "owner_note": r.get("owner_note", ""),
            }
        )
    (out / "items.json").write_text(
        json.dumps(items, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "sample_ids.json").write_text(
        json.dumps(sample_ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    meta = {
        "src": str(args.src),
        "n_images": n_images,
        "n_boxes": len(rows),
        "n_sample": len(sample_ids),
        "n_symbols": int(df["symbol"].nunique()),
        "skips": skips,
        "note": (
            "Independent owner boxes from dense_owner_v11 (not tip clones). "
            "YOLO labels have no side field — owner fills owner_side."
        ),
    }
    (out / "pack_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    write_gallery_html(out)
    write_readme(
        out,
        n_full=len(rows),
        n_sample=len(sample_ids),
        n_images=n_images,
        skips=skips,
    )
    # Touch empty reviews.jsonl if missing
    rj = out / "reviews.jsonl"
    if not rj.exists():
        rj.write_text("", encoding="utf-8")

    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"GALLERY={out / 'gallery.html'}")
    print(f"SHEET={sheet_path}")
    print("NEXT: PYTHONPATH=. .venv/bin/python scripts/serve_owner_side_review.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
