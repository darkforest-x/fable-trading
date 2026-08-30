"""Event-level economic evaluation for a causally aligned detector dataset.

Every manifest row must declare the shared close-entry/next-bar contract.  A
detector proposal enters at ``decision_i`` close and resolves from the next bar.
Repeated views, if a future dataset introduces any, collapse to the earliest
causal proposal per ``event_id``; confidence never selects a later view.  The
matched control and bootstrap both operate on events, not image rows.

Market columns used are open_time, open, high, low, close and causal ATR14.
Only the pre-holdout source prefix is readable.
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

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix  # noqa: E402
from yoyo.datasets.ma_launch_5m_causal import (  # noqa: E402
    ATR_PCT_FLOOR,
    BAR_MINUTES,
    HORIZON_BARS,
    ROUND_TRIP_COST,
    assert_manifest_timing,
    atr_series,
    net_atr_from_resolution,
    resolve_causal_trade,
)
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402

CONTROL_DRAWS = 20
BOOTSTRAPS = 4000
SEED = 20260831


def collapse_first_causal_proposals(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep the earliest visible proposal per event, never the maximum score."""
    selected: dict[str, dict[str, object]] = {}
    for row in rows:
        event_id = str(row["event_id"])
        current = selected.get(event_id)
        rank = (int(row["decision_i"]), str(row["name"]))
        if current is None or rank < (int(current["decision_i"]), str(current["name"])):
            selected[event_id] = row
    return sorted(selected.values(), key=lambda row: (str(row["entry_time"]), str(row["event_id"])))


def resolve_event(
    frame: pd.DataFrame,
    *,
    decision_i: int,
    side: str,
    horizon_bars: int,
) -> dict[str, object] | None:
    """Resolve one event using the same contract that generated its label."""
    index = int(decision_i)
    if index < 14 or index + 1 + int(horizon_bars) > len(frame):
        return None
    atr = float(frame["atr14"].iloc[index])
    price = float(frame["close"].iloc[index])
    unit = atr / price
    if not np.isfinite(unit) or unit < ATR_PCT_FLOOR:
        return None
    try:
        resolution = resolve_causal_trade(
            frame,
            decision_i=index,
            side=side,
            horizon_bars=horizon_bars,
        )
        net_atr = net_atr_from_resolution(resolution, entry_atr=atr)
    except (IndexError, ValueError):
        return None
    return {
        "outcome": resolution.outcome,
        "net_atr": net_atr,
        "atr_pct": unit,
        "entry_price": price,
        "round_trip_cost": ROUND_TRIP_COST,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--dataset", default="datasets/ma_launch_5m_outcome_causal_v2")
    parser.add_argument("--bar-minutes", type=int, default=5)
    parser.add_argument("--horizon", type=int, default=HORIZON_BARS)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", default="analysis/output/detector_net_returns_causal")
    args = parser.parse_args()

    if args.bar_minutes != BAR_MINUTES:
        raise SystemExit(
            f"bar-minutes drift: contract requires {BAR_MINUTES}, got {args.bar_minutes}"
        )
    if args.horizon != HORIZON_BARS:
        raise SystemExit(
            f"horizon drift: contract requires {HORIZON_BARS}, got {args.horizon}"
        )

    from ultralytics import YOLO

    dataset = (ROOT / args.dataset).resolve()
    manifest = [
        json.loads(line)
        for line in (dataset / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    val: list[dict[str, object]] = []
    for row in manifest:
        if row.get("split") != "val":
            continue
        assert_manifest_timing(row)
        val.append(row)

    model = YOLO(str((ROOT / args.weights).resolve()))
    raw_fired: list[dict[str, object]] = []
    stats: Counter[str] = Counter()
    cache: dict[str, pd.DataFrame] = {}

    for number, row in enumerate(val, 1):
        image = dataset / str(row["image_path"])
        prediction = model.predict(
            str(image), imgsz=args.imgsz, conf=args.conf, verbose=False
        )[0]
        if not len(prediction.boxes):
            stats["no detection"] += 1
            continue
        best = int(np.argmax(prediction.boxes.conf.cpu().numpy()))
        class_id = int(prediction.boxes.cls.cpu().numpy()[best])
        if class_id not in (0, 1):
            raise SystemExit(f"unexpected detector class id {class_id} for {image}")
        confidence = float(prediction.boxes.conf.cpu().numpy()[best])
        side = "LONG" if class_id == 0 else "SHORT"

        source = str(row["source_path"])
        if source not in cache:
            frame, _ = read_preholdout_prefix(
                ROOT / source,
                end_exclusive=HOLDOUT_START,
                bar_minutes=args.bar_minutes,
            )
            frame = add_mas(frame)
            frame["atr14"] = atr_series(frame)
            cache = {source: frame}
        frame = cache[source]

        result = resolve_event(
            frame,
            decision_i=int(row["decision_i"]),
            side=side,
            horizon_bars=args.horizon,
        )
        if result is None:
            stats["unresolvable"] += 1
            continue
        stats["resolved image proposal"] += 1
        raw_fired.append(
            {
                "event_id": str(row["event_id"]),
                "name": str(row["dataset_sample_id"]),
                "symbol": str(row["symbol"]),
                "side": side,
                "conf": confidence,
                "decision_i": int(row["decision_i"]),
                "source_path": source,
                "entry_time": str(row["decision_at"]),
                **result,
            }
        )
        if number % 200 == 0:
            print(f"  {number}/{len(val)} images, {len(raw_fired)} raw proposals")

    fired = collapse_first_causal_proposals(raw_fired)
    stats["event proposals"] = len(fired)
    stats["collapsed repeated views"] = len(raw_fired) - len(fired)
    if not fired:
        raise SystemExit("no event-level proposals; nothing to evaluate")
    table = pd.DataFrame(fired)

    rng = np.random.default_rng(SEED)
    controls: list[dict[str, object]] = []
    cache = {}
    for row in fired:
        source = str(row["source_path"])
        if source not in cache:
            frame, _ = read_preholdout_prefix(
                ROOT / source,
                end_exclusive=HOLDOUT_START,
                bar_minutes=args.bar_minutes,
            )
            frame = add_mas(frame)
            frame["atr14"] = atr_series(frame)
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            cache = {source: frame}
        frame = cache[source]
        entry = pd.Timestamp(row["entry_time"])
        same_month = (
            (frame["open_time"].dt.year == entry.year)
            & (frame["open_time"].dt.month == entry.month)
        ).to_numpy()
        pool = np.flatnonzero(same_month)
        pool = pool[(pool > 200) & (pool + 1 + args.horizon <= len(frame))]
        if len(pool) < 5:
            continue
        atr_pct = frame["atr14"].to_numpy()[pool] / frame["close"].to_numpy()[pool]
        finite = np.isfinite(atr_pct) & (atr_pct >= ATR_PCT_FLOOR)
        if int(finite.sum()) < 3:
            stats["insufficient matched-control ATR pool"] += 1
            continue
        low, high = np.nanquantile(atr_pct[finite], [0.33, 0.67])
        target = float(row["atr_pct"])
        bucket = (
            atr_pct <= low
            if target <= low
            else (atr_pct >= high if target >= high else ((atr_pct > low) & (atr_pct < high)))
        )
        candidates = pool[bucket & finite]
        if len(candidates) < 3:
            candidates = pool[finite]
        for decision_i in rng.choice(
            candidates,
            size=min(CONTROL_DRAWS, len(candidates)),
            replace=False,
        ):
            result = resolve_event(
                frame,
                decision_i=int(decision_i),
                side=str(row["side"]),
                horizon_bars=args.horizon,
            )
            if result is not None:
                controls.append({"event_id": row["event_id"], **result})

    if not controls:
        raise SystemExit("no matched controls could be resolved; refusing an unpaired result")
    control_table = pd.DataFrame(controls)
    per_event_control = control_table.groupby("event_id").net_atr.mean()
    paired = table.set_index("event_id").join(
        per_event_control.rename("ctrl_atr")
    ).dropna(subset=["ctrl_atr"])
    differences = paired.net_atr - paired.ctrl_atr
    if differences.empty:
        raise SystemExit("no paired events; refusing to bootstrap an empty comparison")
    bootstrap = rng.choice(
        differences.to_numpy(),
        (BOOTSTRAPS, len(differences)),
        replace=True,
    ).mean(1)

    out_dir = (ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / f"{args.label}_event_proposals.csv", index=False)
    summary = {
        "label": args.label,
        "weights": args.weights,
        "dataset": args.dataset,
        "entry_contract": "decision_close_then_next_bar",
        "unit_of_analysis": "event_id",
        "conf": args.conf,
        "imgsz": args.imgsz,
        "horizon_bars": args.horizon,
        "bar_minutes": args.bar_minutes,
        "val_images": len(val),
        "raw_image_proposals": len(raw_fired),
        "event_proposals": len(table),
        "paired_events": len(paired),
        "event_fire_rate": len(table) / max(len({str(row["event_id"]) for row in val}), 1),
        "tp_rate": float((table.outcome == "tp").mean()),
        "pool_net_atr": float(paired.net_atr.mean()),
        "control_net_atr": float(paired.ctrl_atr.mean()),
        "edge_atr": float(differences.mean()),
        "edge_ci95": [
            float(np.percentile(bootstrap, 2.5)),
            float(np.percentile(bootstrap, 97.5)),
        ],
        "outcomes": table.outcome.value_counts().to_dict(),
        "stats": dict(stats),
        "holdout_rows_read": 0,
        "training_eligible": False,
        "production_eligible": False,
    }
    (out_dir / f"{args.label}_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
