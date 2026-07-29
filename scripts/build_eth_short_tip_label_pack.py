#!/usr/bin/env python3
"""Build a causal ETH short-tip Label Studio pack for 3m/5m/10m charts.

The pack is for target discovery and owner labeling, not model evaluation.  Every
image ends at its candidate bar and contains exactly 200 completed candles, so
the reviewer cannot see the future.  The global project holdout
(``signal_time >= 2026-05-04``) is excluded before any candidate calculation or
rendering.

Candidate sources are intentionally mixed so the existing 15m v10 detector does
not define the new target:

* v10 exact-tip proposals, re-inferred on a causal micro-timeframe window;
* causal numeric short-density candidates;
* future-downside discovery anchors (selection only; future is never rendered);
* regime-stratified random backgrounds.

The 10m series is derived only from two complete, UTC-aligned native 5m bars.
It is never reconstructed from 15m data.  v10 is out-of-distribution on all
three micro timeframes and its rectangles are Label Studio predictions, never
ground-truth annotations.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_eth_short_tip_label_pack.py

Smoke:
  PYTHONPATH=. .venv/bin/python scripts/build_eth_short_tip_label_pack.py \
    --total 30 --v10-probe-limit 120 --out /tmp/eth_short_tip_label_smoke
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import random
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import IMG_HEIGHT, IMG_WIDTH, render_chart  # noqa: E402
from src.judgment.candidates import add_indicators, scan_short_candidates  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

SYMBOL = "ETH_USDT_SWAP"
TIMEFRAMES = ("3m", "5m", "10m")
BAR_MINUTES = {"3m": 3, "5m": 5, "10m": 10}
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
DEV_START = pd.Timestamp("2024-07-29", tz="UTC")
VAL_START = pd.Timestamp("2025-11-01", tz="UTC")
INTERNAL_ACCEPT_START = pd.Timestamp("2026-02-01", tz="UTC")
DEFAULT_OUT = PROJECT / "datasets" / "eth_short_tip_label2000"
DEFAULT_WEIGHTS = (
    PROJECT / "runs/detect/runs/detect/owner_short_star_v10/weights/best.pt"
)
SEED = 20260729
SOURCE_WEIGHTS = {
    "v10": 0.30,
    "numeric": 0.25,
    "downside": 0.20,
    "random": 0.25,
}


@dataclass(frozen=True)
class Candidate:
    idx: int
    source: str
    score: float | None = None
    v10_conf: float | None = None
    v10_box: tuple[float, float, float, float] | None = None
    group: str = ""


def _load_native(bar: str) -> pd.DataFrame:
    paths = list_series(bar=bar).get(("okx", SYMBOL), [])
    frame = load_series(paths)
    if frame.empty:
        raise SystemExit(f"missing OKX {SYMBOL} {bar} data")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame[
        (frame["open_time"] >= DEV_START) & (frame["open_time"] < HOLDOUT_START)
    ].reset_index(drop=True)
    if len(frame) < WINDOW + 300:
        raise SystemExit(f"{bar}: only {len(frame)} pre-holdout bars")
    return frame


def _derive_10m(frame_5m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate exactly two aligned 5m rows into one 10m row."""
    src = frame_5m.copy()
    src["bucket"] = src["open_time"].dt.floor("10min")
    rows: list[dict] = []
    for bucket, group in src.groupby("bucket", sort=True):
        group = group.sort_values("open_time")
        expected = [bucket, bucket + pd.Timedelta(minutes=5)]
        actual = group["open_time"].tolist()
        if len(group) != 2 or actual != expected:
            continue
        rows.append(
            {
                "open_time": bucket,
                "open": float(group["open"].iloc[0]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group["close"].iloc[-1]),
                "volume": float(group["volume"].sum()),
            }
        )
    out = pd.DataFrame(rows)
    if len(out) < WINDOW + 300:
        raise SystemExit(f"10m: only {len(out)} complete derived bars")
    return out.reset_index(drop=True)


def load_frames() -> dict[str, pd.DataFrame]:
    frame_3m = _load_native("3m")
    frame_5m = _load_native("5m")
    return {"3m": frame_3m, "5m": frame_5m, "10m": _derive_10m(frame_5m)}


def _time_group(frame: pd.DataFrame, idx: int) -> str:
    return pd.Timestamp(frame["open_time"].iloc[idx]).strftime("%Y-%m")


def _split_hint(ts: pd.Timestamp) -> str:
    if ts < VAL_START:
        return "train"
    if ts < INTERNAL_ACCEPT_START:
        return "val"
    return "internal_accept"


def _source_quotas(n: int) -> dict[str, int]:
    quotas = {
        "v10": int(round(n * SOURCE_WEIGHTS["v10"])),
        "numeric": int(round(n * SOURCE_WEIGHTS["numeric"])),
        "downside": int(round(n * SOURCE_WEIGHTS["downside"])),
    }
    quotas["random"] = n - sum(quotas.values())
    return quotas


def _tf_quotas(total: int) -> dict[str, int]:
    base, rem = divmod(total, len(TIMEFRAMES))
    return {tf: base + (1 if i < rem else 0) for i, tf in enumerate(TIMEFRAMES)}


def numeric_pool(frame: pd.DataFrame, enriched: pd.DataFrame) -> list[Candidate]:
    idxs = scan_short_candidates(enriched, horizon_bars=0, mode="expanded")
    out = []
    for idx in idxs:
        if idx < WINDOW - 1:
            continue
        score = float(enriched["short_shape_score"].iloc[idx])
        out.append(
            Candidate(
                idx=int(idx),
                source="numeric",
                score=score,
                group=_time_group(frame, int(idx)),
            )
        )
    return out


def downside_pool(
    frame: pd.DataFrame,
    enriched: pd.DataFrame,
    *,
    bar_minutes: int,
    limit: int,
) -> list[Candidate]:
    """Rank anchors by next-3h short favorable excursion minus adverse excursion.

    Future values are used only to enrich the discovery queue.  They are never
    rendered, exported to Label Studio task data, or used as detector labels.
    """
    horizon = max(3, int(round(180 / bar_minutes)))
    lows = frame["low"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    if len(frame) <= horizon + WINDOW:
        return []
    low_windows = np.lib.stride_tricks.sliding_window_view(lows[1:], horizon)
    high_windows = np.lib.stride_tricks.sliding_window_view(highs[1:], horizon)
    n_scores = len(low_windows)
    base_close = close[:n_scores]
    favorable = (base_close - low_windows.min(axis=1)) / base_close
    adverse = (high_windows.max(axis=1) - base_close) / base_close
    atr_pct = enriched["atr_pct"].to_numpy(dtype=float)[:n_scores]
    score = favorable - 0.5 * adverse
    # Prefer economically visible moves, but keep ranking deterministic when
    # the absolute threshold is not met in a quiet regime.
    score = score + 0.10 * np.nan_to_num(favorable / np.maximum(atr_pct, 1e-9))
    valid = np.flatnonzero(np.isfinite(score) & (np.arange(n_scores) >= WINDOW - 1))
    order = valid[np.argsort(score[valid])[::-1]]
    min_gap = max(1, int(math.ceil(60 / bar_minutes)))
    chosen: list[int] = []
    out: list[Candidate] = []
    for raw_idx in order:
        idx = int(raw_idx)
        pos = bisect.bisect_left(chosen, idx)
        near_left = pos > 0 and idx - chosen[pos - 1] < min_gap
        near_right = pos < len(chosen) and chosen[pos] - idx < min_gap
        if near_left or near_right:
            continue
        bisect.insort(chosen, idx)
        out.append(
            Candidate(
                idx=idx,
                source="downside",
                score=float(score[idx]),
                group=_time_group(frame, idx),
            )
        )
        if len(out) >= limit:
            break
    return out


def random_pool(
    frame: pd.DataFrame,
    enriched: pd.DataFrame,
    *,
    rng: random.Random,
) -> list[Candidate]:
    eligible = np.arange(WINDOW - 1, len(frame), dtype=int)
    atr = enriched["atr_pct"].iloc[eligible]
    try:
        atr_bin = pd.qcut(atr.rank(method="first"), 4, labels=False).astype(int).to_numpy()
    except ValueError:
        atr_bin = np.zeros(len(eligible), dtype=int)
    close = frame["close"].to_numpy(dtype=float)
    ema200 = enriched["ema200"].to_numpy(dtype=float)
    buckets: dict[str, list[int]] = defaultdict(list)
    for pos, idx in enumerate(eligible):
        trend = "below" if close[idx] < ema200[idx] else "above"
        month = _time_group(frame, int(idx))
        buckets[f"{month}|q{int(atr_bin[pos])}|{trend}"].append(int(idx))
    for values in buckets.values():
        rng.shuffle(values)
    out: list[Candidate] = []
    keys = sorted(buckets)
    while keys:
        next_keys = []
        for key in keys:
            values = buckets[key]
            if values:
                idx = values.pop()
                out.append(Candidate(idx=idx, source="random", group=key))
            if values:
                next_keys.append(key)
        keys = next_keys
    return out


def _conf_bin(conf: float) -> str:
    if conf < 0.20:
        return "low"
    if conf < 0.50:
        return "mid"
    return "high"


def v10_tip_pool(
    frame: pd.DataFrame,
    ma_frame: pd.DataFrame,
    model,
    anchors: list[int],
    *,
    target: int,
    conf: float,
    batch_size: int,
    device: str,
    probe_limit: int,
    tf: str,
) -> list[Candidate]:
    """Re-infer v10 on causal windows; retain boxes ending on the exact tip bar."""
    anchors = [int(i) for i in anchors if WINDOW - 1 <= int(i) < len(frame)]
    anchors = list(dict.fromkeys(anchors))[:probe_limit]
    found: list[Candidate] = []
    wanted_inventory = max(target + 40, target * 2)
    with tempfile.TemporaryDirectory(prefix=f"eth_{tf}_v10_tip_") as tmp:
        tmp_dir = Path(tmp)
        for chunk_start in range(0, len(anchors), batch_size):
            chunk = anchors[chunk_start : chunk_start + batch_size]
            rendered: list[tuple[int, object, Path]] = []
            for k, idx in enumerate(chunk):
                sub = ma_frame.iloc[idx - WINDOW + 1 : idx + 1]
                if len(sub) != WINDOW:
                    continue
                path = tmp_dir / f"{chunk_start + k:06d}.png"
                _, transform = render_chart(sub, out_path=path)
                rendered.append((idx, transform, path))
            if not rendered:
                continue
            results = model.predict(
                [str(path) for _, _, path in rendered],
                conf=conf,
                verbose=False,
                device=device,
            )
            for (idx, transform, _), result in zip(rendered, results):
                boxes = result.boxes
                if boxes is None or len(boxes) == 0:
                    continue
                xywhn = boxes.xywhn.cpu().numpy()
                confs = boxes.conf.cpu().numpy()
                best: tuple[float, tuple[float, float, float, float]] | None = None
                for row, raw_conf in zip(xywhn, confs):
                    cx, cy, width, height = map(float, row[:4])
                    if right_edge_to_bar(cx, width, transform, n_bars=WINDOW) != WINDOW - 1:
                        continue
                    item = (float(raw_conf), (cx, cy, width, height))
                    if best is None or item[0] > best[0]:
                        best = item
                if best is None:
                    continue
                found.append(
                    Candidate(
                        idx=idx,
                        source="v10",
                        score=best[0],
                        v10_conf=best[0],
                        v10_box=best[1],
                        group=f"{_time_group(frame, idx)}|{_conf_bin(best[0])}",
                    )
                )
            done = min(chunk_start + len(chunk), len(anchors))
            print(
                f"  {tf} v10 causal probes {done}/{len(anchors)} exact-tip={len(found)}",
                flush=True,
            )
            if len(found) >= wanted_inventory:
                break
    return found


def _far_enough(idx: int, selected: list[int], gap: int) -> bool:
    pos = bisect.bisect_left(selected, idx)
    if pos > 0 and idx - selected[pos - 1] < gap:
        return False
    if pos < len(selected) and selected[pos] - idx < gap:
        return False
    return True


def pick_diverse(
    pool: list[Candidate],
    n: int,
    *,
    selected_indices: list[int],
    gap: int,
    rng: random.Random,
) -> list[Candidate]:
    buckets: dict[str, list[Candidate]] = defaultdict(list)
    for cand in pool:
        buckets[cand.group or "all"].append(cand)
    for values in buckets.values():
        rng.shuffle(values)
        values.sort(key=lambda c: float(c.score or 0.0))
    keys = sorted(buckets)
    rng.shuffle(keys)
    out: list[Candidate] = []
    while keys and len(out) < n:
        next_keys: list[str] = []
        for key in keys:
            values = buckets[key]
            while values:
                cand = values.pop()
                if _far_enough(cand.idx, selected_indices, gap):
                    bisect.insort(selected_indices, cand.idx)
                    out.append(cand)
                    break
            if values:
                next_keys.append(key)
            if len(out) >= n:
                break
        keys = next_keys
    return out


def yolo_to_ls_box(box: tuple[float, float, float, float]) -> dict:
    cx, cy, width, height = box
    x = max(0.0, (cx - width / 2) * 100)
    y = max(0.0, (cy - height / 2) * 100)
    return {
        "x": x,
        "y": y,
        "width": min(width * 100, 100 - x),
        "height": min(height * 100, 100 - y),
        "rotation": 0,
    }


def label_config() -> str:
    return """<View>
  <Header value="ETH 做空 causal-tip：只按当前图判断；不要猜后续走势。"/>
  <Image name="image" value="$image" zoom="true" zoomControl="true"/>
  <Choices name="decision" toName="image" choice="single" required="true">
    <Choice value="short_start" hotkey="1"/>
    <Choice value="neutral" hotkey="2"/>
    <Choice value="uncertain" hotkey="3"/>
    <Choice value="bad_data" hotkey="4"/>
  </Choices>
  <RectangleLabels name="label" toName="image" strokeWidth="2">
    <Label value="short_start" background="#d32f2f" hotkey="s"/>
  </RectangleLabels>
  <TextArea name="note" toName="image" placeholder="可选备注" rows="2"/>
  <Header value="short_start 才保留/新增红框；框右缘必须到最后一根已收盘 K。neutral 删除预框。"/>
</View>
"""


def build_pack(args: argparse.Namespace) -> dict:
    if args.total < len(TIMEFRAMES):
        raise SystemExit(f"--total must be >= {len(TIMEFRAMES)}")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output: {out}")
    out.mkdir(parents=True, exist_ok=True)
    image_dir = out / "images"
    label_dir = out / "labels"
    ls_dir = out / "label_studio"
    for directory in (image_dir, label_dir, ls_dir):
        directory.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    frames = load_frames()
    tf_targets = _tf_quotas(args.total)
    if not args.weights.exists():
        raise SystemExit(f"missing v10 weights: {args.weights}")
    model = load_yolo_model(args.weights)

    selected_by_tf: dict[str, list[Candidate]] = {}
    prepared: dict[str, pd.DataFrame] = {}
    coverage: dict[str, dict] = {}

    for tf in TIMEFRAMES:
        frame = frames[tf]
        enriched = add_indicators(frame)
        ma_frame = add_mas(frame)
        prepared[tf] = ma_frame
        target = tf_targets[tf]
        quotas = _source_quotas(target)
        numeric = numeric_pool(frame, enriched)
        downside = downside_pool(
            frame,
            enriched,
            bar_minutes=BAR_MINUTES[tf],
            limit=max(quotas["downside"] * 8, 800),
        )
        random_candidates = random_pool(frame, enriched, rng=rng)

        # Candidate discovery is mixed before the v10 probe.  Shuffle once so
        # early stop cannot bias the probe toward one source or time range.
        probe_ids = list(
            dict.fromkeys(
                [c.idx for c in numeric]
                + [c.idx for c in downside[: max(800, quotas["downside"] * 6)]]
                + [c.idx for c in random_candidates[: max(1600, quotas["random"] * 8)]]
            )
        )
        rng.shuffle(probe_ids)
        v10 = v10_tip_pool(
            frame,
            ma_frame,
            model,
            probe_ids,
            target=quotas["v10"],
            conf=args.v10_conf,
            batch_size=args.batch_size,
            device=args.device,
            probe_limit=args.v10_probe_limit,
            tf=tf,
        )

        gap = max(1, int(math.ceil(args.min_gap_minutes / BAR_MINUTES[tf])))
        selected_indices: list[int] = []
        chosen: list[Candidate] = []
        for source, pool in (
            ("v10", v10),
            ("numeric", numeric),
            ("downside", downside),
            ("random", random_candidates),
        ):
            picked = pick_diverse(
                pool,
                quotas[source],
                selected_indices=selected_indices,
                gap=gap,
                rng=rng,
            )
            chosen.extend(picked)
            if len(picked) < quotas[source]:
                print(
                    f"  {tf} {source}: requested={quotas[source]} got={len(picked)}; "
                    "remainder will be random-fill",
                    flush=True,
                )
        if len(chosen) < target:
            fill = pick_diverse(
                random_candidates,
                target - len(chosen),
                selected_indices=selected_indices,
                gap=gap,
                rng=rng,
            )
            chosen.extend(
                Candidate(
                    idx=c.idx,
                    source="random_fill",
                    score=c.score,
                    group=c.group,
                )
                for c in fill
            )
        if len(chosen) != target:
            raise RuntimeError(f"{tf}: selected {len(chosen)} != target {target}")
        chosen.sort(key=lambda c: int(c.idx))
        selected_by_tf[tf] = chosen
        coverage[tf] = {
            "bars": int(len(frame)),
            "range": [str(frame["open_time"].min()), str(frame["open_time"].max())],
            "target": target,
            "source_quota": quotas,
            "candidate_inventory": {
                "v10_exact_tip": len(v10),
                "numeric": len(numeric),
                "downside": len(downside),
                "random": len(random_candidates),
            },
        }

    all_candidates = [(tf, cand) for tf in TIMEFRAMES for cand in selected_by_tf[tf]]
    # Hide one quarter of v10 preboxes to measure anchoring bias.  All non-v10
    # sources are already blind, so the overall pack is predominantly unboxed.
    v10_keys = [(tf, cand.idx) for tf, cand in all_candidates if cand.source == "v10"]
    rng.shuffle(v10_keys)
    hidden_v10 = set(v10_keys[: int(round(len(v10_keys) * args.hide_v10_frac))])

    tasks: list[dict] = []
    manifest: list[dict] = []
    counters = Counter()
    serial = 0
    for tf, cand in all_candidates:
        serial += 1
        frame = frames[tf]
        ma_frame = prepared[tf]
        ts = pd.Timestamp(frame["open_time"].iloc[cand.idx])
        if ts >= HOLDOUT_START:
            raise RuntimeError(f"holdout leak: {tf} {ts}")
        window = ma_frame.iloc[cand.idx - WINDOW + 1 : cand.idx + 1]
        if len(window) != WINDOW or pd.Timestamp(window["open_time"].iloc[-1]) != ts:
            raise RuntimeError(f"non-causal window: {tf} idx={cand.idx}")
        stem = (
            f"ETH_USDT_SWAP_{tf}_{ts:%Y%m%dT%H%MZ}_{cand.source}_{serial:04d}"
        )
        image_path = image_dir / f"{stem}.png"
        render_chart(window, out_path=image_path)
        # Empty YOLO label placeholder: owner export is the only gold source.
        (label_dir / f"{stem}.txt").write_text("", encoding="utf-8")

        prebox_visible = (
            cand.source == "v10"
            and cand.v10_box is not None
            and (tf, cand.idx) not in hidden_v10
        )
        predictions = []
        if prebox_visible:
            result = {
                "id": f"v10_{stem}",
                "type": "rectanglelabels",
                "from_name": "label",
                "to_name": "image",
                "original_width": IMG_WIDTH,
                "original_height": IMG_HEIGHT,
                "image_rotation": 0,
                "value": {
                    **yolo_to_ls_box(cand.v10_box),
                    "rectanglelabels": ["short_start"],
                },
            }
            predictions = [
                {
                    "model_version": "owner_short_star_v10_15m_ood_causal_tip",
                    "score": float(cand.v10_conf or 0.0),
                    "result": [result],
                }
            ]
        tasks.append(
            {
                "data": {
                    "image": (
                        "/data/local-files/?d="
                        f"{out.name}/images/{image_path.name}"
                    ),
                    "stem": stem,
                    "timeframe": tf,
                    "candidate_time": ts.isoformat(),
                },
                "predictions": predictions,
            }
        )
        row = {
            "task_id": serial,
            "stem": stem,
            "symbol": SYMBOL,
            "timeframe": tf,
            "candidate_time": ts.isoformat(),
            "candidate_index": cand.idx,
            "window_start": pd.Timestamp(window["open_time"].iloc[0]).isoformat(),
            "window_end": pd.Timestamp(window["open_time"].iloc[-1]).isoformat(),
            "window_bars": len(window),
            "source": cand.source,
            "source_score": cand.score,
            "v10_conf": cand.v10_conf,
            "v10_prebox_visible": prebox_visible,
            "split_hint": _split_hint(ts),
            "holdout_safe": ts < HOLDOUT_START,
            "image_rel": f"images/{image_path.name}",
            "label_rel": f"labels/{stem}.txt",
        }
        manifest.append(row)
        counters[(tf, cand.source)] += 1
        if serial % 100 == 0 or serial == len(all_candidates):
            print(f"  rendered {serial}/{len(all_candidates)}", flush=True)

    tasks_path = ls_dir / "tasks_eth_short_tip_2000.json"
    tasks_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    (ls_dir / "label_config.xml").write_text(label_config(), encoding="utf-8")
    manifest_path = out / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)

    source_counts = {
        tf: dict(Counter(c.source for c in selected_by_tf[tf])) for tf in TIMEFRAMES
    }
    summary = {
        "symbol": SYMBOL,
        "total": len(tasks),
        "timeframes": tf_targets,
        "sources": source_counts,
        "coverage": coverage,
        "v10_weights": str(args.weights.resolve()),
        "v10_conf_floor": args.v10_conf,
        "v10_visible_preboxes": int(
            sum(bool(row["v10_prebox_visible"]) for row in manifest)
        ),
        "v10_hidden_preboxes": int(len(hidden_v10)),
        "holdout_start_exclusive": HOLDOUT_START.isoformat(),
        "max_candidate_time": max(row["candidate_time"] for row in manifest),
        "causal_contract": "each image has 200 bars and window_end == candidate_time",
        "task_json": str(tasks_path),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    source_lines = []
    for tf in TIMEFRAMES:
        counts = source_counts[tf]
        source_lines.append(
            f"| {tf} | {tf_targets[tf]} | {counts.get('v10', 0)} | "
            f"{counts.get('numeric', 0)} | {counts.get('downside', 0)} | "
            f"{counts.get('random', 0) + counts.get('random_fill', 0)} |"
        )
    readme = f"""# ETH short causal-tip Label Studio pack

- Tasks: **{len(tasks)}**
- Symbol: `{SYMBOL}`
- Timeframes: 3m / 5m / derived 10m
- Holdout excluded: every candidate is `< {HOLDOUT_START.isoformat()}`
- Every image: exactly 200 completed bars, right edge = candidate time
- v10 is a 15m OOD proposal source; its rectangles are predictions, not labels

| TF | total | v10 | numeric | downside discovery | random/background |
|---|---:|---:|---:|---:|---:|
{chr(10).join(source_lines)}

## Start Label Studio

```bash
docker compose -f scripts/label_studio_compose.yml up -d
```

Open `http://127.0.0.1:8081`, create/select a project, then:

1. Settings → Labeling Interface → paste `label_studio/label_config.xml`.
2. Import → upload `label_studio/tasks_eth_short_tip_2000.json`.
3. Label only from pixels visible in the chart. Do not open `manifest.csv` while labeling;
   it contains the hidden candidate-source audit.

## Label rule

- `short_start`: the current completed bar is actionable from visible history alone.
  Keep/add one red rectangle over the causal setup, with its right edge on the last bar.
- `neutral`: no short setup now; delete any v10 prebox.
- `uncertain`: would need more bars or information to decide. This is not a negative.
- `bad_data`: broken/missing-looking candles or rendering anomaly.

## Important limitation

The currently available local 3m history begins in 2026-03, and 5m begins in
2025-12. This pack is a first causal target-discovery batch, not the final
two-year training universe. Older native micro history must be added before a
production training split is frozen.
"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--total", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--v10-conf", type=float, default=0.05)
    ap.add_argument("--v10-probe-limit", type=int, default=3000)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--min-gap-minutes", type=float, default=30.0)
    ap.add_argument("--hide-v10-frac", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=SEED)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    if not (0 < args.v10_conf < 1):
        raise SystemExit("--v10-conf must be in (0,1)")
    if not (0 <= args.hide_v10_frac <= 1):
        raise SystemExit("--hide-v10-frac must be in [0,1]")
    summary = build_pack(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
