#!/usr/bin/env python3
"""Collect real tip-window success/fail previews with provisional gold tags (v13).

True tip geometry only: fixed 200-bar window, right edge = tip bar, no future.
NOT pad200 mid-gold remap. Does NOT train / promote / touch holdout / forward_log.

Provisional classes (Owner must confirm — not training GT):
  tip-hit        kept tip-edge box AND dense rule near tip
  tip-miss-dense dense rule near tip, no kept tip-edge box
  tip-noise      kept tip-edge box, dense rule NOT near tip
  tip-empty-ok   no dense near tip, no kept box

Sources:
  1) forward_log signal tips (preferred live distribution)
  2) optional scout: pre-holdout tip windows ranked by MA density
     (class balance only — morphology, not live PnL)

Usage (prefer VPS where klines cover log signal times):
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \\
    scripts/collect_v13_tip_previews.py \\
    --log data/forward_log.csv --limit 32 --conf 0.20 \\
    --scout-dense 8 --scout-empty 8 \\
    --out analysis/output/v13_real_tip_preview
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.auto_label import (  # noqa: E402
    find_dense_segments,
    segment_to_bbox,
)
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.render import MIN_REL_SPAN, render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_WEIGHTS,
    TIP_EDGE_BARS,
    WINDOW,
    _resolve_predict_device,
    load_yolo_model,
    right_edge_to_bar,
)

GREEN = (40, 180, 60)
RED = (40, 40, 220)
ORANGE = (0, 140, 255)
CYAN = (220, 200, 40)  # BGR — rule dense (provisional, not GT)

# Dense segment must end within this many bars of tip to count as tip-dense.
TIP_DENSE_HIT_BARS = 16
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
SCORE_START = pd.Timestamp("2025-06-01", tz="UTC")
TIP_LOOKBACK = 12

CLASS_HELP = {
    "tip-hit": "贴边 KEEP + tip 近端有密集规则段 → 候选正例",
    "tip-miss-dense": "tip 近端有密集，但无贴边 KEEP → 漏检候选",
    "tip-noise": "有贴边 KEEP，但 tip 近端无密集 → 误检候选",
    "tip-empty-ok": "无密集、无 KEEP → 背景对照",
}


def _parse_ts(s: str) -> pd.Timestamp:
    ts = pd.Timestamp(s)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def load_frame(symbol: str) -> pd.DataFrame:
    for (_src, sym), paths in list_series(bar="15m").items():
        if sym == symbol:
            df = load_series(paths)
            if df.empty:
                raise FileNotFoundError(symbol)
            return df
    raise FileNotFoundError(symbol)


def tip_dense_flag(sub: pd.DataFrame) -> tuple[bool, list, float]:
    """Return (tip_dense, segments, mean tip full_spread)."""
    segs = find_dense_segments(sub)
    tip_dense = any(seg.end >= WINDOW - TIP_DENSE_HIT_BARS for seg in segs)
    tip = sub.iloc[-TIP_LOOKBACK:]
    spread = pd.to_numeric(tip["full_spread"], errors="coerce")
    mean_sp = float(spread.mean()) if spread.notna().any() else float("nan")
    return tip_dense, segs, mean_sp


def provisional_class(*, tip_dense: bool, n_kept: int) -> str:
    has_kept = n_kept > 0
    if has_kept and tip_dense:
        return "tip-hit"
    if tip_dense and not has_kept:
        return "tip-miss-dense"
    if has_kept and not tip_dense:
        return "tip-noise"
    return "tip-empty-ok"


def draw_overlay(
    img_path: Path,
    yolo_boxes: list[dict],
    rule_boxes: list[tuple[float, float, float, float]],
    out_path: Path,
) -> None:
    img = cv2.imread(str(img_path))
    if img is None:
        return
    ih, iw = img.shape[:2]
    cv2.line(img, (iw - 2, 0), (iw - 2, ih - 1), RED, 2)
    for xc, yc, w, h in rule_boxes:
        x1, y1 = int((xc - w / 2) * iw), int((yc - h / 2) * ih)
        x2, y2 = int((xc + w / 2) * iw), int((yc + h / 2) * ih)
        cv2.rectangle(img, (x1, y1), (x2, y2), CYAN, 2)
        cv2.putText(
            img,
            "rule",
            (x1, max(16, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            CYAN,
            1,
            cv2.LINE_AA,
        )
    for b in yolo_boxes:
        xc, yc, w, h = b["xc"], b["yc"], b["w"], b["h"]
        x1, y1 = int((xc - w / 2) * iw), int((yc - h / 2) * ih)
        x2, y2 = int((xc + w / 2) * iw), int((yc + h / 2) * ih)
        color = GREEN if b["kept"] else ORANGE
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"{b['conf']:.2f} bar={b['bar_in_win']}{' KEEP' if b['kept'] else ' DROP'}"
        cv2.putText(
            img,
            label,
            (x1, max(16, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def predict_tip_window(
    frame: pd.DataFrame,
    tip_i: int,
    model,
    *,
    conf: float,
    tip_edge_bars: int,
    tmp_png: Path,
) -> tuple[list[dict], list[tuple[float, float, float, float]], dict, Path | None]:
    """Render tip window ending at tip_i; return YOLO boxes, rule boxes, meta."""
    start = tip_i - WINDOW + 1
    if start < 0 or tip_i >= len(frame):
        return [], [], {}, None
    enriched = add_mas(frame)
    sub = enriched.iloc[start : tip_i + 1].reset_index(drop=True)
    if len(sub) != WINDOW:
        return [], [], {}, None
    tip_dense, segs, mean_sp = tip_dense_flag(sub)
    _, tf = render_chart(sub, out_path=tmp_png)
    rule_boxes: list[tuple[float, float, float, float]] = []
    for seg in segs:
        bbox = segment_to_bbox(sub, seg, tf)
        if bbox is not None:
            rule_boxes.append(bbox)
    res = model.predict(
        [str(tmp_png)], conf=conf, verbose=False, device=_resolve_predict_device()
    )[0]
    boxes: list[dict] = []
    min_bar = WINDOW - tip_edge_bars
    if res.boxes is not None:
        xywhn = res.boxes.xywhn.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        for b, c in zip(xywhn, confs):
            xc, yc, w, h = map(float, b[:4])
            bar = right_edge_to_bar(xc, w, tf, n_bars=WINDOW)
            boxes.append(
                {
                    "xc": xc,
                    "yc": yc,
                    "w": w,
                    "h": h,
                    "conf": float(c),
                    "bar_in_win": int(bar),
                    "offset_from_tip": int(WINDOW - 1 - bar),
                    "kept": bar >= min_bar,
                }
            )
    meta = {
        "tip_dense": tip_dense,
        "n_rule_segs": len(segs),
        "mean_tip_full_spread": mean_sp,
        "rule_seg_ends": [int(s.end) for s in segs],
    }
    return boxes, rule_boxes, meta, tmp_png


def scout_candidates(
    *,
    want_dense: int,
    want_empty: int,
    stride: int,
    seed: int,
) -> list[dict]:
    """Pre-holdout tip windows for class balance (true tip geometry, empty labels)."""
    if want_dense <= 0 and want_empty <= 0:
        return []
    rng = random.Random(seed)
    fetch = PROJECT / "data" / "kline_fetched"
    paths = sorted(fetch.glob("okx_*_15m_*.csv"))
    dense_pool: list[dict] = []
    empty_pool: list[dict] = []
    for k, csv_path in enumerate(paths, 1):
        m = re.match(r"okx_(.+)_15m_\d+\.csv$", csv_path.name)
        if not m:
            continue
        sym = m.group(1)
        base = sym.split("_", 1)[0]
        if is_eval_symbol(sym) or is_stockish(sym):
            continue
        df = pd.read_csv(
            csv_path, usecols=["ts", "open", "high", "low", "close", "volume"]
        )
        if len(df) < WINDOW + 150:
            continue
        df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        enriched = add_mas(df)
        ts = enriched["open_time"]
        i0 = max(WINDOW - 1, int(ts.searchsorted(SCORE_START)))
        tips: list[int] = []
        i = i0
        while i < len(enriched):
            if ts.iloc[i] >= HOLDOUT_START:
                break
            tips.append(i)
            i += stride
        if not tips:
            continue
        if len(tips) > 40:
            tips = rng.sample(tips, 40)
        scored: list[tuple[float, int, bool]] = []
        for tip_i in tips:
            sub = enriched.iloc[tip_i - WINDOW + 1 : tip_i + 1].reset_index(drop=True)
            if len(sub) != WINDOW:
                continue
            hi = float(sub["high"].max())
            lo = float(sub["low"].min())
            mid = (hi + lo) / 2.0
            if mid <= 0 or (hi - lo) / mid < MIN_REL_SPAN:
                continue
            tip_dense, _segs, mean_sp = tip_dense_flag(sub)
            rank = (mean_sp if mean_sp == mean_sp else 9.0) - (0.05 if tip_dense else 0.0)
            scored.append((rank, tip_i, tip_dense))
        scored.sort(key=lambda x: x[0])
        used_tips: set[int] = set()
        for rank, tip_i, tip_dense in scored:
            if tip_i in used_tips:
                continue
            used_tips.add(tip_i)
            row = {
                "symbol": sym,
                "signal_time": str(ts.iloc[tip_i]),
                "tip_i_hint": tip_i,
                "source": "scout_preholdout",
                "scout_rank": rank,
                "scout_want_dense": tip_dense,
                "score": "",
                "label": "",
                "outcome": "",
                "detected_at": "",
            }
            if tip_dense:
                dense_pool.append(row)
            else:
                empty_pool.append(row)
            if len(used_tips) >= 3:
                break
        if k % 80 == 0:
            print(
                f"  scout {k}/{len(paths)} dense_pool={len(dense_pool)} "
                f"empty_pool={len(empty_pool)}",
                flush=True,
            )
    dense_pool.sort(key=lambda r: r["scout_rank"])
    empty_pool.sort(key=lambda r: r["scout_rank"], reverse=True)
    seen_sym: Counter = Counter()
    take_dense: list[dict] = []
    take_empty: list[dict] = []
    for r in dense_pool:
        if seen_sym[r["symbol"]] >= 2:
            continue
        take_dense.append(r)
        seen_sym[r["symbol"]] += 1
        if len(take_dense) >= want_dense:
            break
    for r in empty_pool:
        if seen_sym[r["symbol"]] >= 2:
            continue
        take_empty.append(r)
        seen_sym[r["symbol"]] += 1
        if len(take_empty) >= want_empty:
            break
    out = take_dense + take_empty
    print(
        f"scout picked dense={len(take_dense)} empty={len(take_empty)} "
        f"(pools {len(dense_pool)}/{len(empty_pool)})",
        flush=True,
    )
    return out


def write_index_html(out: Path, payload: dict) -> Path:
    rows = [r for r in payload["rows"] if not r.get("error") and r.get("tip_plus", 0) == 0]
    counts = Counter(r.get("provisional_class", "?") for r in rows)
    cards = []
    for i, r in enumerate(rows, 1):
        cls = r.get("provisional_class", "?")
        prev = Path(r["preview"]).name
        meta = (
            f"source={r.get('source')} · tip_dense={r.get('tip_dense')} · "
            f"raw={r.get('n_boxes_raw')} kept={r.get('n_kept')} · "
            f"spread={r.get('mean_tip_full_spread')} · "
            f"log_label={r.get('log_label')} outcome={r.get('log_outcome')}"
        )
        cards.append(
            f"""
<div class="card" data-cls="{html.escape(cls)}">
  <h2>#{i:02d} · <span class="cls">{html.escape(cls)}</span></h2>
  <p class="stem"><code>{html.escape(r.get('symbol',''))} · {html.escape(str(r.get('signal_time','')))}</code></p>
  <p class="why">{html.escape(CLASS_HELP.get(cls, ''))}</p>
  <p class="meta">{html.escape(meta)}</p>
  <figure><img src="{html.escape(prev)}" alt="{html.escape(cls)}"/>
  <figcaption>红竖线=右缘 tip · 青框=密集规则(预标) · 绿=YOLO KEEP · 橙=YOLO DROP</figcaption></figure>
</div>"""
        )
    count_bits = " · ".join(f"{k}={counts.get(k, 0)}" for k in CLASS_HELP)
    body = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<title>真实 tip 成败预标小样</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 24px; background:#0b0f14; color:#e5e7eb; }}
h1 {{ font-size: 1.35rem; }}
.note {{ color:#9ca3af; max-width: 980px; line-height:1.55; }}
.filters button {{ margin: 4px 6px 12px 0; padding:6px 10px; border-radius:6px;
  border:1px solid #374151; background:#111827; color:#e5e7eb; cursor:pointer; }}
.filters button.active {{ border-color:#67e8f9; color:#67e8f9; }}
.card {{ border:1px solid #1f2937; border-radius:10px; padding:16px; margin:18px 0; background:#111827; }}
.card h2 {{ margin:0 0 6px; font-size:1.05rem; }}
.cls {{ color:#67e8f9; }}
.stem code {{ color:#fbbf24; }}
.meta {{ color:#9ca3af; font-size:0.85rem; }}
img {{ width:100%; max-width:1100px; border-radius:6px; background:#000; }}
figcaption {{ font-size:0.8rem; color:#9ca3af; margin-top:4px; }}
.hidden {{ display:none; }}
</style>
</head>
<body>
<h1>真实 tip 成败预标小样（非训练集）</h1>
<p class="note">
目录 <code>{html.escape(str(out.relative_to(PROJECT)))}</code>。
窗=200、右缘=盘口 tip、无后文；<b>不是</b> pad200 中段裁贴右。
预标按「密集规则 ∩ tip_edge KEEP」自动打，<b>需 Owner 目视改判</b>后才可当金标。
未开训、未 promote、未耗 holdout。权重 {html.escape(str(payload.get('weights')))} ·
conf={payload.get('conf')} · tip_edge={payload.get('tip_edge_bars')}。
<br/>tip+0 计数：{html.escape(count_bits)}
</p>
<div class="filters">
  <button class="active" data-f="all">全部</button>
  {''.join(f'<button data-f="{c}">{c}</button>' for c in CLASS_HELP)}
</div>
{''.join(cards)}
<script>
const buttons=[...document.querySelectorAll('.filters button')];
const cards=[...document.querySelectorAll('.card')];
buttons.forEach(b=>b.addEventListener('click',()=>{{
  buttons.forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  const f=b.dataset.f;
  cards.forEach(c=>{{
    c.classList.toggle('hidden', f!=='all' && c.dataset.cls!==f);
  }});
}}));
</script>
</body>
</html>
"""
    path = out / "index.html"
    path.write_text(body, encoding="utf-8")
    return path


def write_review_csv(out: Path, rows: list[dict]) -> Path:
    path = out / "review_sheet.csv"
    tip0 = [r for r in rows if not r.get("error") and r.get("tip_plus", 0) == 0]
    fields = [
        "symbol",
        "signal_time",
        "source",
        "provisional_class",
        "owner_class",
        "owner_note",
        "tip_dense",
        "n_boxes_raw",
        "n_kept",
        "mean_tip_full_spread",
        "log_label",
        "log_outcome",
        "preview",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in tip0:
            w.writerow({k: r.get(k, "") for k in fields if k != "owner_class" and k != "owner_note"} | {
                "owner_class": "",
                "owner_note": "",
            })
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", type=Path, default=PROJECT / "data" / "forward_log.csv")
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--conf", type=float, default=0.20, help="raw floor for preview")
    ap.add_argument("--tip-edge-bars", type=int, default=TIP_EDGE_BARS)
    ap.add_argument(
        "--limit",
        type=int,
        default=32,
        help="max unique forward_log signal tips (plan min≈几十)",
    )
    ap.add_argument(
        "--tip-plus-max",
        type=int,
        default=0,
        help="also render tip+1..N (0 = tip-only gold pack)",
    )
    ap.add_argument("--scout-dense", type=int, default=8, help="pre-holdout tip-dense scouts")
    ap.add_argument("--scout-empty", type=int, default=8, help="pre-holdout empty scouts")
    ap.add_argument("--scout-stride", type=int, default=96)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "analysis" / "output" / "v13_real_tip_preview",
    )
    args = ap.parse_args()

    if not args.weights.exists():
        print(f"weights missing: {args.weights}", file=sys.stderr)
        return 2

    targets: list[dict] = []
    if args.log.exists():
        rows = list(csv.DictReader(args.log.open()))
        seen: set[tuple[str, str]] = set()
        for r in reversed(rows):
            key = (r["symbol"], r["signal_time"])
            if key in seen:
                continue
            seen.add(key)
            targets.append(
                {
                    "symbol": r["symbol"],
                    "signal_time": r["signal_time"],
                    "source": "forward_log",
                    "score": r.get("score", ""),
                    "label": r.get("label", ""),
                    "outcome": r.get("outcome", ""),
                    "detected_at": r.get("detected_at", ""),
                }
            )
            if len(targets) >= args.limit:
                break
        targets = list(reversed(targets))
    else:
        print(f"log missing (scout-only): {args.log}", flush=True)

    scout = scout_candidates(
        want_dense=args.scout_dense,
        want_empty=args.scout_empty,
        stride=args.scout_stride,
        seed=args.seed,
    )
    targets.extend(scout)

    out: Path = args.out if args.out.is_absolute() else PROJECT / args.out
    out.mkdir(parents=True, exist_ok=True)
    tmp_dir = out / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    model = load_yolo_model(args.weights)
    manifest: list[dict] = []
    counts = Counter()

    for rec in targets:
        sym = rec["symbol"]
        st = rec["signal_time"]
        src = rec.get("source", "forward_log")
        print(f"=== [{src}] {sym} {st} ===", flush=True)
        try:
            frame = load_frame(sym)
        except FileNotFoundError as exc:
            counts["error"] += 1
            manifest.append(
                {"symbol": sym, "signal_time": st, "source": src, "error": str(exc)}
            )
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        target_ts = _parse_ts(st)
        hits = (times == target_ts).to_numpy().nonzero()[0]
        if len(hits) == 0:
            signal_i = int((times - target_ts).abs().argmin())
            aligned = False
        else:
            signal_i = int(hits[0])
            aligned = True
        if "tip_i_hint" in rec and src.startswith("scout"):
            signal_i = int(rec["tip_i_hint"])
            aligned = True

        for back in range(0, args.tip_plus_max + 1):
            tip_i = signal_i + back
            if tip_i >= len(frame):
                continue
            stem = (
                f"{sym}_{pd.Timestamp(times.iloc[signal_i]).strftime('%Y%m%d_%H%M')}"
                f"_tipplus{back}"
            )
            if src.startswith("scout"):
                stem = f"scout_{stem}"
            raw_png = tmp_dir / f"{stem}.png"
            boxes, rule_boxes, meta, _ = predict_tip_window(
                frame,
                tip_i,
                model,
                conf=args.conf,
                tip_edge_bars=args.tip_edge_bars,
                tmp_png=raw_png,
            )
            kept = [b for b in boxes if b["kept"]]
            preview = out / f"{stem}.png"
            draw_overlay(raw_png, boxes, rule_boxes, preview)
            pclass = provisional_class(tip_dense=bool(meta.get("tip_dense")), n_kept=len(kept))
            if back == 0:
                counts[pclass] += 1
            entry = {
                "symbol": sym,
                "signal_time": st,
                "source": src,
                "signal_i": signal_i,
                "tip_i": tip_i,
                "tip_plus": back,
                "aligned": aligned,
                "n_boxes_raw": len(boxes),
                "n_kept": len(kept),
                "tip_dense": meta.get("tip_dense"),
                "n_rule_segs": meta.get("n_rule_segs"),
                "mean_tip_full_spread": meta.get("mean_tip_full_spread"),
                "rule_seg_ends": meta.get("rule_seg_ends"),
                "provisional_class": pclass,
                "owner_class": "",
                "boxes": boxes,
                "preview": str(preview.relative_to(PROJECT)),
                "log_score": rec.get("score"),
                "log_label": rec.get("label"),
                "log_outcome": rec.get("outcome"),
                "log_lag_hint": rec.get("detected_at"),
            }
            manifest.append(entry)
            print(
                f"  tip+{back}: raw={len(boxes)} kept={len(kept)} "
                f"dense={meta.get('tip_dense')} class={pclass} -> {preview.name}",
                flush=True,
            )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": str(args.weights),
        "conf": args.conf,
        "tip_edge_bars": args.tip_edge_bars,
        "tip_dense_hit_bars": TIP_DENSE_HIT_BARS,
        "n_forward_targets": sum(1 for t in targets if t.get("source") == "forward_log"),
        "n_scout_targets": sum(1 for t in targets if str(t.get("source", "")).startswith("scout")),
        "counts_tip0_provisional": dict(counts),
        "class_help": CLASS_HELP,
        "note": (
            "provisional_class is auto prelabel for Owner review only; "
            "not training GT. No pad200 remap."
        ),
        "rows": manifest,
    }
    man_path = out / "manifest.json"
    man_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    stats_path = out / "stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "generated_at": payload["generated_at"],
                "counts_tip0_provisional": dict(counts),
                "n_forward": payload["n_forward_targets"],
                "n_scout": payload["n_scout_targets"],
                "n_tip0_previews": sum(
                    1 for r in manifest if not r.get("error") and r.get("tip_plus") == 0
                ),
                "conf": args.conf,
                "tip_edge_bars": args.tip_edge_bars,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    idx = write_index_html(out, payload)
    sheet = write_review_csv(out, manifest)
    print(f"counts_tip0_provisional={dict(counts)}")
    print(f"wrote {man_path}")
    print(f"wrote {stats_path}")
    print(f"wrote {idx}")
    print(f"wrote {sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
