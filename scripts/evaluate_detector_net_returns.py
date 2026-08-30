"""Net return and matched random control for a detector's own proposals.

The question every model in this project has avoided: if you traded what it
proposes, would you make money? mAP cannot answer it. The 15m detector scored
0.91 while its two classes differed only by whether price had already moved,
and the 5m outcome-labelled rerun of the same recipe scored 0.40 once that
shortcut was removed.

Each detector is run over its own frozen validation split, every box above the
frozen confidence is entered at the close of the window's right edge, and the
position is resolved with yoyo.contracts.outcomes under TP 5 ATR / SL 2 ATR
and a 0.2% round trip.

The decisive line is not the pool return. CLAUDE.md records a pool that made
+16.9bp of which +7.2bp was short beta, so every result here is paired against
a matched random control: same symbol, same calendar month, same ATR tercile,
same direction, same barriers, same cost. Returns are in ATR units because the
barriers are ATR multiples and a percent return would mostly measure the coin.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.contracts.outcomes import resolve_barrier_outcome  # noqa: E402
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix  # noqa: E402
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402

TP_ATR, SL_ATR, COST = 5.0, 2.0, 0.002
CONTROL_DRAWS = 20


def atr_series(frame: pd.DataFrame) -> pd.Series:
    prev = frame["close"].shift(1)
    tr = pd.concat([(frame["high"] - frame["low"]),
                    (frame["high"] - prev).abs(),
                    (frame["low"] - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = np.nan
    atr = tr.ewm(alpha=1.0 / 14, adjust=False, ignore_na=True).mean()
    atr.iloc[:14] = np.nan
    return atr


def resolve(frame, entry_i, side, horizon):
    atr = float(frame["atr14"].iloc[entry_i])
    price = float(frame["close"].iloc[entry_i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(price) or price <= 0:
        return None
    # Returns are divided by ATR/price, so a near-frozen bar produces an
    # astronomical number rather than a large trade. The first control run
    # reported an edge of 2.9e30 ATR from exactly this. One basis point of ATR
    # is the floor below which a bar is not tradeable in any case.
    if atr / price < 1e-4:
        return None
    if entry_i + horizon >= len(frame):
        return None
    res = resolve_barrier_outcome(
        frame.iloc[entry_i:].reset_index(drop=True), side=side.lower(), entry_i=0,
        entry_price=price, atr=atr, tp_atr_mult=TP_ATR, sl_atr_mult=SL_ATR,
        horizon_bars=horizon, same_bar_policy="conservative_sl", gap_policy="barrier_price",
        return_convention="linear_long" if side == "LONG" else "linear_short",
        allow_partial=False)
    if res.gross_ret is None:
        return None
    unit = atr / price
    return {"outcome": res.outcome, "net_atr": res.gross_ret / unit - COST / unit,
            "atr_pct": unit}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--dataset", required=True, help="dataset dir whose val split to score")
    ap.add_argument("--bar-minutes", type=int, required=True)
    ap.add_argument("--horizon", type=int, required=True)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", default="analysis/output/detector_net_returns_20260830")
    args = ap.parse_args()

    from ultralytics import YOLO

    dataset = ROOT / args.dataset
    rows = [json.loads(l) for l in (dataset / "manifest.jsonl").read_text().splitlines() if l.strip()]
    # The 15m and 5m manifests were written by different builders: one carries
    # image_path/window_end_i, the other a bare name. Normalise instead of
    # assuming, so a schema difference cannot silently score the wrong images.
    def normalise(row: dict) -> dict | None:
        image = row.get("image_path")
        if image:
            stem = Path(image).stem
        elif row.get("name"):
            stem = str(row["name"])
        else:
            return None
        end = row.get("window_end_i")
        if end is None:
            return None
        return {"stem": stem, "window_end_i": int(end),
                "symbol": str(row.get("symbol", "")), "source_path": str(row["source_path"])}

    val = [n for n in (normalise(r) for r in rows if r.get("split") == "val") if n]
    print(f"{args.label}: scoring {len(val)} val images", flush=True)

    model = YOLO(str(ROOT / args.weights))
    fired: list[dict] = []
    stats: Counter[str] = Counter()
    cache: dict[str, pd.DataFrame] = {}

    for i, row in enumerate(val, 1):
        image = dataset / "images" / "val" / f"{row['stem']}.png"
        result = model.predict(str(image), imgsz=args.imgsz, conf=args.conf, verbose=False)[0]
        if not len(result.boxes):
            stats["no detection"] += 1
            continue
        best = int(np.argmax(result.boxes.conf.cpu().numpy()))
        cls = int(result.boxes.cls.cpu().numpy()[best])
        conf = float(result.boxes.conf.cpu().numpy()[best])
        side = "LONG" if cls == 0 else "SHORT"

        path = row["source_path"]
        if path not in cache:
            frame, _ = read_preholdout_prefix(ROOT / path, end_exclusive=HOLDOUT_START,
                                              bar_minutes=args.bar_minutes)
            frame = add_mas(frame)
            frame["atr14"] = atr_series(frame)
            cache = {path: frame}
        frame = cache[path]

        # Entry at the close of the window's right edge: the first bar at which
        # this proposal could have been acted on without seeing more than the
        # model saw.
        out = resolve(frame, int(row["window_end_i"]), side, args.horizon)
        if out is None:
            stats["unresolvable"] += 1
            continue
        stats["traded"] += 1
        fired.append({"name": row["stem"], "symbol": row["symbol"], "side": side, "conf": conf,
                      "entry_i": int(row["window_end_i"]), "source_path": path,
                      "entry_time": str(frame["open_time"].iloc[int(row["window_end_i"])]), **out})
        if i % 200 == 0:
            print(f"  {i}/{len(val)}  fired {len(fired)}", flush=True)

    if not fired:
        print("no proposals; nothing to evaluate")
        return 0
    table = pd.DataFrame(fired)

    # Matched control: same symbol, month, ATR tercile and direction.
    rng = np.random.default_rng(20260830)
    control: list[dict] = []
    cache = {}
    for row in fired:
        path = row["source_path"]
        if path not in cache:
            frame, _ = read_preholdout_prefix(ROOT / path, end_exclusive=HOLDOUT_START,
                                              bar_minutes=args.bar_minutes)
            frame = add_mas(frame)
            frame["atr14"] = atr_series(frame)
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            cache = {path: frame}
        frame = cache[path]
        entry = pd.Timestamp(row["entry_time"])
        same_month = ((frame["open_time"].dt.year == entry.year) &
                      (frame["open_time"].dt.month == entry.month)).to_numpy()
        pool = np.flatnonzero(same_month)
        pool = pool[(pool > 200) & (pool + args.horizon < len(frame) - 1)]
        if len(pool) < 5:
            continue
        atr_pct = (frame["atr14"].to_numpy()[pool] / frame["close"].to_numpy()[pool])
        low, high = np.nanquantile(atr_pct, [0.33, 0.67])
        target = row["atr_pct"]
        mask = (atr_pct <= low) if target <= low else ((atr_pct >= high) if target >= high
                                                       else ((atr_pct > low) & (atr_pct < high)))
        candidates = pool[mask & np.isfinite(atr_pct)]
        if len(candidates) < 3:
            candidates = pool[np.isfinite(atr_pct)]
        for j in rng.choice(candidates, size=min(CONTROL_DRAWS, len(candidates)), replace=False):
            out = resolve(frame, int(j), row["side"], args.horizon)
            if out:
                control.append({"name": row["name"], **out})

    ctrl = pd.DataFrame(control)
    per_event = ctrl.groupby("name").net_atr.mean()
    paired = table.set_index("name").join(per_event.rename("ctrl_atr")).dropna(subset=["ctrl_atr"])
    diff = paired.net_atr - paired.ctrl_atr
    boot = rng.choice(diff.to_numpy(), (4000, len(diff)), replace=True).mean(1)

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / f"{args.label}_proposals.csv", index=False)

    summary = {
        "label": args.label, "weights": args.weights, "dataset": args.dataset,
        "conf": args.conf, "imgsz": args.imgsz, "horizon_bars": args.horizon,
        "bar_minutes": args.bar_minutes,
        "val_images": len(val), "proposals": len(table), "paired": len(paired),
        "fire_rate": len(table) / max(len(val), 1),
        "tp_rate": float((table.outcome == "tp").mean()),
        "pool_net_atr": float(paired.net_atr.mean()),
        "control_net_atr": float(paired.ctrl_atr.mean()),
        "edge_atr": float(diff.mean()),
        "edge_ci95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
        "outcomes": table.outcome.value_counts().to_dict(),
        "skipped": dict(stats),
    }
    (out_dir / f"{args.label}_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    print(f"\n=== {args.label} ===")
    print(f"  val 图 {len(val)}   开火 {len(table)} ({100*summary['fire_rate']:.1f}%)   TP率 {summary['tp_rate']*100:.1f}%")
    print(f"  池子净收益   {summary['pool_net_atr']:+.3f} ATR")
    print(f"  对照组净收益 {summary['control_net_atr']:+.3f} ATR")
    print(f"  edge         {summary['edge_atr']:+.3f} ATR   95%CI [{summary['edge_ci95'][0]:+.3f}, {summary['edge_ci95'][1]:+.3f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
