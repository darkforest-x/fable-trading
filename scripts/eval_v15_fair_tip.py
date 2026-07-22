#!/usr/bin/env python3
"""Fair discovery-level tip revalidation (v15 vs v12[/v14]).

Two metrics, same live render protocol (full-MA → cut window → render):

1) true_tip tip_hit with --full-ma (fix tip_detectability.py; no slice-MA bias)
2) Real-tip preview sheet (Owner-accepted provisional classes):
   - should-fire = tip-hit ∪ tip-miss-dense
   - should-silence empty = tip-empty-ok
   - tip-noise reported separately
   - raw boxes vs A′ tip-edge KEEP reported separately

Does NOT train / promote / touch holdout / forward_log.

Usage:
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \\
    scripts/eval_v15_fair_tip.py \\
    --preview analysis/output/v13_real_tip_preview \\
    --out analysis/output/v15_revalidate_fair.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import importlib.util  # noqa: E402

from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
)


def _load_collect_mod():
    path = PROJECT / "scripts" / "collect_v13_tip_previews.py"
    spec = importlib.util.spec_from_file_location("collect_v13_tip_previews", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_COLLECT = _load_collect_mod()
TIP_DENSE_HIT_BARS = _COLLECT.TIP_DENSE_HIT_BARS
load_frame = _COLLECT.load_frame
predict_tip_window = _COLLECT.predict_tip_window

SHOULD_FIRE = {"tip-hit", "tip-miss-dense"}
SHOULD_SILENCE_EMPTY = {"tip-empty-ok"}
NOISE = {"tip-noise"}


def _cell_str(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"", "nan", "none"} else s


def gold_class(row: pd.Series) -> str:
    oc = _cell_str(row.get("owner_class"))
    if oc:
        return oc
    return _cell_str(row.get("provisional_class"))


def run_true_tip_full_ma(weights: Path, out: Path, *, limit: int, conf: float) -> dict:
    cmd = [
        sys.executable,
        str(PROJECT / "scripts" / "tip_detectability.py"),
        "--true-tip",
        "--full-ma",
        "--split",
        "val",
        "--limit",
        str(limit),
        "--dataset",
        str(PROJECT / "datasets" / "dense_owner_v11"),
        "--weights",
        str(weights),
        "--conf",
        str(conf),
        "--out",
        str(out),
    ]
    env = {**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(PROJECT)}
    env["OMP_NUM_THREADS"] = env.get("OMP_NUM_THREADS", "1")
    env["MKL_NUM_THREADS"] = env.get("MKL_NUM_THREADS", "1")
    subprocess.check_call(cmd, cwd=str(PROJECT), env=env)
    return json.loads(out.read_text())


def eval_real_tip_sheet(
    preview_dir: Path,
    weights: Path,
    *,
    conf: float,
    tip_edge_bars: int,
) -> dict:
    sheet_path = preview_dir / "review_sheet.csv"
    sheet = pd.read_csv(sheet_path)
    sheet = sheet.drop_duplicates(subset=["symbol", "signal_time", "source"], keep="first")
    sheet["signal_time"] = pd.to_datetime(sheet["signal_time"], utc=True)
    sheet["gold"] = sheet.apply(gold_class, axis=1)

    model = load_yolo_model(weights)
    tmp = PROJECT / "data" / f"_fair_tip_{weights.stem}.png"
    rows_out: list[dict] = []
    skipped: list[dict] = []

    for r in sheet.itertuples(index=False):
        gold = r.gold
        try:
            frame = load_frame(r.symbol)
        except FileNotFoundError as exc:
            skipped.append({"symbol": r.symbol, "reason": f"no_frame:{exc}"})
            continue
        ts = pd.to_datetime(frame["open_time"], utc=True)
        hits = (ts == r.signal_time).to_numpy().nonzero()[0]
        if len(hits) == 0:
            skipped.append(
                {
                    "symbol": r.symbol,
                    "signal_time": str(r.signal_time),
                    "reason": "bar_missing",
                    "last": str(ts.max()) if len(ts) else None,
                    "gold": gold,
                }
            )
            continue
        tip_i = int(hits[0])
        boxes, _rules, meta, _ = predict_tip_window(
            frame,
            tip_i,
            model,
            conf=conf,
            tip_edge_bars=tip_edge_bars,
            tmp_png=tmp,
        )
        n_raw = len(boxes)
        n_kept = sum(1 for b in boxes if b["kept"])
        # tipish: any kept box at tip or tip-1 (same spirit as tip-smoke tipish_hits)
        tipish_kept = any(b["kept"] and b["offset_from_tip"] <= 1 for b in boxes)
        tipish_raw = any(b["offset_from_tip"] <= 1 for b in boxes)
        right_edge_raw = any((b["xc"] + b["w"] / 2) >= 0.92 for b in boxes)
        right_edge_kept = any(b["kept"] and (b["xc"] + b["w"] / 2) >= 0.92 for b in boxes)
        rows_out.append(
            {
                "symbol": r.symbol,
                "signal_time": str(r.signal_time),
                "source": r.source,
                "gold": gold,
                "tip_dense_rule": bool(meta.get("tip_dense")),
                "n_boxes_raw": n_raw,
                "n_kept": n_kept,
                "fired_raw": n_raw > 0,
                "fired_edge": n_kept > 0,
                "tipish_raw": tipish_raw,
                "tipish_edge": tipish_kept,
                "right_edge_raw": right_edge_raw,
                "right_edge_kept": right_edge_kept,
                "max_conf": round(max((b["conf"] for b in boxes), default=0.0), 4),
            }
        )

    by_gold: dict[str, list[dict]] = defaultdict(list)
    for row in rows_out:
        by_gold[row["gold"]].append(row)

    def rate(rows: list[dict], key: str) -> dict:
        n = len(rows)
        k = sum(1 for x in rows if x[key])
        return {"n": n, "k": k, "rate": round(k / n, 4) if n else None}

    should = [x for g in SHOULD_FIRE for x in by_gold.get(g, [])]
    empty = [x for g in SHOULD_SILENCE_EMPTY for x in by_gold.get(g, [])]
    noise = [x for g in NOISE for x in by_gold.get(g, [])]

    # Confusion on evaluated rows (edge KEEP as positive prediction)
    tp = sum(1 for x in should if x["fired_edge"])
    fn = sum(1 for x in should if not x["fired_edge"])
    fp_empty = sum(1 for x in empty if x["fired_edge"])
    tn_empty = sum(1 for x in empty if not x["fired_edge"])
    fp_noise = sum(1 for x in noise if x["fired_edge"])

    return {
        "weights": str(weights),
        "conf": conf,
        "tip_edge_bars": tip_edge_bars,
        "window": WINDOW,
        "ma_protocol": "full-MA",
        "preview_dir": str(preview_dir),
        "gold_source": "owner_class if set else provisional_class (Owner-accepted prelabel)",
        "n_sheet": int(len(sheet)),
        "n_eval": len(rows_out),
        "n_skipped": len(skipped),
        "skipped_head": skipped[:20],
        "gold_counts_eval": dict(Counter(x["gold"] for x in rows_out)),
        "should_fire": {
            "classes": sorted(SHOULD_FIRE),
            "hit_raw": rate(should, "fired_raw"),
            "hit_edge": rate(should, "fired_edge"),
            "tipish_raw": rate(should, "tipish_raw"),
            "tipish_edge": rate(should, "tipish_edge"),
            "right_edge_raw": rate(should, "right_edge_raw"),
            "right_edge_kept": rate(should, "right_edge_kept"),
        },
        "empty_ok": {
            "classes": sorted(SHOULD_SILENCE_EMPTY),
            "false_fire_raw": rate(empty, "fired_raw"),
            "false_fire_edge": rate(empty, "fired_edge"),
        },
        "noise": {
            "classes": sorted(NOISE),
            "fire_raw": rate(noise, "fired_raw"),
            "fire_edge": rate(noise, "fired_edge"),
        },
        "confusion_edge": {
            "tp_should_fire": tp,
            "fn_should_fire": fn,
            "fp_empty": fp_empty,
            "tn_empty": tn_empty,
            "fp_noise_bucket": fp_noise,
            "note": "positive = A′ KEEP (bar_in_win >= window-tip_edge_bars)",
        },
        "by_gold_edge_hit": {
            g: rate(rows, "fired_edge") for g, rows in sorted(by_gold.items())
        },
        "by_gold_raw_hit": {
            g: rate(rows, "fired_raw") for g, rows in sorted(by_gold.items())
        },
        "rows": rows_out,
    }


def summarize_model(tag: str, true_tip: dict | None, real: dict | None) -> dict:
    out: dict = {"tag": tag}
    if true_tip:
        out["true_tip_full_ma"] = {
            "tip_hit_rate": true_tip.get("tip_hit_rate"),
            "tip_hits": true_tip.get("tip_hits"),
            "n": true_tip.get("n"),
            "ma_mode": true_tip.get("ma_mode"),
            "method": true_tip.get("method"),
        }
    if real:
        sf = real["should_fire"]
        em = real["empty_ok"]
        out["real_tip"] = {
            "n_eval": real["n_eval"],
            "n_skipped": real["n_skipped"],
            "should_fire_hit_raw": sf["hit_raw"],
            "should_fire_hit_edge": sf["hit_edge"],
            "empty_false_fire_raw": em["false_fire_raw"],
            "empty_false_fire_edge": em["false_fire_edge"],
            "noise_fire_edge": real["noise"]["fire_edge"],
            "confusion_edge": real["confusion_edge"],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--preview",
        type=Path,
        default=PROJECT / "analysis" / "output" / "v13_real_tip_preview",
    )
    ap.add_argument("--v12", type=Path, default=PROJECT / "models" / "owner_best.pt")
    ap.add_argument("--v14", type=Path, default=PROJECT / "models" / "owner_v14_pad200.pt")
    ap.add_argument("--v15", type=Path, default=PROJECT / "models" / "owner_v15_tipval.pt")
    ap.add_argument("--skip-v14", action="store_true")
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--tip-edge-bars", type=int, default=TIP_EDGE_BARS)
    ap.add_argument("--true-tip-limit", type=int, default=120)
    ap.add_argument("--skip-true-tip", action="store_true")
    ap.add_argument("--skip-real-tip", action="store_true")
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "analysis" / "output" / "v15_revalidate_fair.json",
    )
    args = ap.parse_args()

    models = [("v12", args.v12), ("v15", args.v15)]
    if not args.skip_v14 and args.v14.exists():
        models.insert(1, ("v14", args.v14))

    for tag, path in models:
        if not path.exists():
            print(f"MISSING weights {tag}: {path}", file=sys.stderr)
            return 2

    payload: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": {
            "true_tip_ma": "full-MA (add_mas on full series then cut)",
            "real_tip_ma": "full-MA (same as collect_v13 / live)",
            "conf": args.conf,
            "tip_edge_bars": args.tip_edge_bars,
            "tip_dense_hit_bars": TIP_DENSE_HIT_BARS,
            "preview": str(args.preview),
            "note": "Discovery-level only; not trade PF. No promote.",
        },
        "models": {},
        "summary": [],
    }

    for tag, weights in models:
        print(f"=== {tag} {weights} ===", flush=True)
        true_tip = None
        real = None
        if not args.skip_true_tip:
            out_tt = PROJECT / "analysis" / "output" / f"tip_rate_{tag}_fullma.json"
            print(f"--- true_tip full-MA → {out_tt}", flush=True)
            true_tip = run_true_tip_full_ma(
                weights, out_tt, limit=args.true_tip_limit, conf=args.conf
            )
        if not args.skip_real_tip:
            print(f"--- real_tip sheet {args.preview}", flush=True)
            real = eval_real_tip_sheet(
                args.preview,
                weights,
                conf=args.conf,
                tip_edge_bars=args.tip_edge_bars,
            )
            real_out = PROJECT / "analysis" / "output" / f"real_tip_fair_{tag}.json"
            real_out.write_text(json.dumps(real, indent=2, ensure_ascii=False) + "\n")
            print(
                f"  should_fire edge {real['should_fire']['hit_edge']} "
                f"empty_ff edge {real['empty_ok']['false_fire_edge']} "
                f"skipped={real['n_skipped']}/{real['n_sheet']} → {real_out}",
                flush=True,
            )
        payload["models"][tag] = {
            "weights": str(weights),
            "true_tip_full_ma": true_tip,
            "real_tip": {
                k: v
                for k, v in (real or {}).items()
                if k != "rows"  # rows live in per-model json
            }
            if real
            else None,
        }
        payload["summary"].append(summarize_model(tag, true_tip, real))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}", flush=True)
    for s in payload["summary"]:
        tt = s.get("true_tip_full_ma") or {}
        rt = s.get("real_tip") or {}
        print(
            f"{s['tag']}: true_tip_fullma={tt.get('tip_hit_rate')} "
            f"should_fire_edge={rt.get('should_fire_hit_edge')} "
            f"empty_ff_edge={rt.get('empty_false_fire_edge')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
