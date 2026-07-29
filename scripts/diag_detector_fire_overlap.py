"""Do two detectors fire in the same places? Decide reuse before a 10-hour rebuild.

Swapping detectors currently means rebuilding the candidate pool blind -- 10 hours
on the 3060 for v10 -- because the judgment layer must be trained on the
distribution it will score. That rule is real: the frozen v11 layer scored on v6's
candidates inverts, top decile -32.91bp against the pool's +0.0312%, and the same
failure has now been watched three times.

But "must retrain" does not follow from "the weights changed". It follows from the
firing distribution having moved. That is cheap to measure and nobody has:

  OVERLAP   share of bars where both detectors fire, within a tolerance, so
            near-identical anchors are not counted as disagreement
  DENSITY   how often each fires, since a detector that fires 3x more can look
            like high overlap while its pool is mostly new material
  CONF      whether the shared fires get similar confidence, which decides
            whether a threshold carries over

Read as: above 80% the old pool is a defensible starting point, 50-80% mix and
say so in the report, below 50% rebuild. Those cut-offs are judgement, not
measurement, and are stated so they can be argued with.

Read-only. No promote, no threshold change.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_detector_fire_overlap.py \
      --a runs/detect/runs/detect/owner_short_star_v9/weights/best.pt \
      --b runs/detect/runs/detect/owner_short_star_v10/weights/best.pt
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF, TIP_EDGE_BARS, WINDOW, load_yolo_model, right_edge_to_bar,
)

HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
TOL_BARS = 3          # anchors this close are the same event, not a disagreement
SEED = 20260730


def fires_on(model, fr: pd.DataFrame, lo: int, hi: int, conf: float,
             tmp: Path, device: str) -> dict[int, float]:
    """Tip-aligned fire bars -> confidence. One render, both models score it."""
    out: dict[int, float] = {}
    last = -10 ** 9
    for t in range(lo, hi):
        try:
            _, tf = render_chart(fr.iloc[t - WINDOW + 1:t + 1], out_path=tmp)
            res = model.predict([str(tmp)], conf=conf, verbose=False, device=device)[0]
        except Exception:  # noqa: BLE001
            continue
        b = res.boxes
        if b is None or len(b) == 0:
            continue
        best = 0.0
        for row, cf in zip(b.xywhn.cpu().numpy(), b.conf.cpu().numpy()):
            if right_edge_to_bar(float(row[0]), float(row[2]), tf,
                                 n_bars=WINDOW) >= WINDOW - TIP_EDGE_BARS:
                best = max(best, float(cf))
        if best > 0 and t - last >= MIN_GAP_BARS:
            out[t] = best
            last = t
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True, help="old detector weights")
    ap.add_argument("--b", required=True, help="new detector weights")
    ap.add_argument("--n-symbols", type=int, default=12)
    ap.add_argument("--bars", type=int, default=1500)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--device", default="mps")
    args = ap.parse_args()

    for p in (args.a, args.b):
        if not (PROJECT / p).exists() and not Path(p).exists():
            print(f"权重不存在: {p}")
            return 2
    ma = load_yolo_model(str(Path(args.a) if Path(args.a).is_absolute() else PROJECT / args.a))
    mb = load_yolo_model(str(Path(args.b) if Path(args.b).is_absolute() else PROJECT / args.b))

    series = list_series(bar="15m")
    syms = sorted({s for (_x, s) in series
                   if s.endswith("_USDT_SWAP") and not is_stockish(s)})
    rng = np.random.default_rng(SEED)
    rng.shuffle(syms)
    syms = syms[: args.n_symbols]

    tmp = PROJECT / "data" / "_ovl.png"
    rows = []
    t0 = time.perf_counter()
    for k, sym in enumerate(syms, 1):
        try:
            fr = add_mas(load_series(series[("okx", sym)]))
        except Exception:  # noqa: BLE001
            continue
        t = pd.to_datetime(fr["open_time"], utc=True)
        fr = fr[t < HOLDOUT].reset_index(drop=True)      # iron rule 1
        if len(fr) < WINDOW + 50:
            continue
        lo = max(WINDOW, len(fr) - args.bars)
        fa = fires_on(ma, fr, lo, len(fr), args.conf, tmp, args.device)
        fb = fires_on(mb, fr, lo, len(fr), args.conf, tmp, args.device)
        ba, bb = np.array(sorted(fa)), np.array(sorted(fb))
        matched = 0
        pairs = []
        for x in ba:
            if len(bb) and np.min(np.abs(bb - x)) <= TOL_BARS:
                matched += 1
                y = int(bb[int(np.argmin(np.abs(bb - x)))])
                pairs.append((fa[int(x)], fb[y]))
        rows.append({"symbol": sym, "bars": len(fr) - lo,
                     "n_a": len(fa), "n_b": len(fb), "matched": matched,
                     "conf_pairs": pairs})
        print(f"  [{k}/{len(syms)}] {sym:<20} A {len(fa):>3}  B {len(bb):>3}  "
              f"共同 {matched:>3}", flush=True)
    tmp.unlink(missing_ok=True)
    wall = time.perf_counter() - t0

    if not rows:
        print("无数据")
        return 1
    n_a = sum(r["n_a"] for r in rows)
    n_b = sum(r["n_b"] for r in rows)
    m = sum(r["matched"] for r in rows)
    bars = sum(r["bars"] for r in rows)
    months = bars / 96 / 30.44
    # Jaccard, not "share of A", so a detector that fires far more cannot score
    # high overlap simply by covering everything the other one does.
    jac = m / max(n_a + n_b - m, 1)
    rec_a = m / max(n_a, 1)
    rec_b = m / max(n_b, 1)

    print(f"\n扫描 {len(rows)} 币 x {args.bars} bar = {months:.1f} 币·月"
          f"   用时 {wall/60:.1f} 分钟\n")
    print(f"{'':<22}{'开火数':>8}{'密度/币·月':>12}")
    print(f"{'A ' + Path(args.a).parent.parent.name:<22}{n_a:>8}{n_a/months:>12.1f}")
    print(f"{'B ' + Path(args.b).parent.parent.name:<22}{n_b:>8}{n_b/months:>12.1f}")
    print(f"\n共同开火(±{TOL_BARS} 根内算同一处): {m}")
    print(f"  Jaccard 重合度      {jac*100:>6.1f}%   ← 判据看这个")
    print(f"  A 的开火有多少被 B 覆盖 {rec_a*100:>6.1f}%")
    print(f"  B 的开火有多少被 A 覆盖 {rec_b*100:>6.1f}%")

    pairs = [p for r in rows for p in r["conf_pairs"]]
    if pairs:
        ca = np.array([p[0] for p in pairs]); cb = np.array([p[1] for p in pairs])
        print(f"\n共同开火的置信度:A 中位 {np.median(ca):.3f}  B 中位 {np.median(cb):.3f}"
              f"  相关 {np.corrcoef(ca, cb)[0,1]:+.3f}")

    if jac >= 0.8:
        verdict = (f"重合度 {jac*100:.1f}% ≥ 80% → 候选分布基本没变,"
                   f"老池可作为起点,风险低;仍需在报告里标注这是复用")
    elif jac >= 0.5:
        verdict = (f"重合度 {jac*100:.1f}%(50~80%)→ 可混用,但必须标注;"
                   f"B 有 {(1-rec_b)*100:.0f}% 的开火是老池里没有的新材料")
    else:
        verdict = (f"重合度 {jac*100:.1f}% < 50% → 必须重造候选池。"
                   f"B 的开火有 {(1-rec_b)*100:.0f}% 老池覆盖不到,"
                   f"在老池上训的判断层会在 B 的候选上反选")
    print(f"\n判读: {verdict}")
    print("注:阈值 80%/50% 是判断不是测量,可以争论;本脚本只测不改配置。")

    (PROJECT / "analysis" / "output" / "diag_detector_fire_overlap.json").write_text(
        json.dumps({"a": args.a, "b": args.b, "conf": args.conf,
                    "tol_bars": TOL_BARS, "symbol_months": round(months, 2),
                    "n_a": n_a, "n_b": n_b, "matched": m,
                    "jaccard": round(jac, 4),
                    "density_a": round(n_a / months, 2),
                    "density_b": round(n_b / months, 2),
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
