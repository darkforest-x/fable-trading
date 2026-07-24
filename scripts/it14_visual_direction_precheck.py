"""IT-14: does the tip-only CHART image carry direction signal beyond tabular?

Cheap Mac-side gate for the owner's two-detector idea. Prior lines say direction
isn't causally recoverable (IT-02 tabular AUC 0.5; owner-side appearance AUC 0.97
but causal PF 1.23; IT-12/13 breakout both ways fail). Before spending 3060 on
two YOLO detectors, test the ONE new element -- visual gestalt -- directly:

  render each v16 candidate as a box-at-tip chart (window ENDING at the tip, so
  the cluster sits at the right edge, no future bars) -> embed with the frozen
  COCO yolo11n backbone -> LightGBM predicts forward DIRECTION -> walk-forward
  held-out AUC + top-decile directional PF. Compare head-to-head vs the same
  candidates' 130 tabular features.

Green light for the full 3060 build only if the VISUAL AUC clears ~0.55 held-out
or directional PF clears 1.3 -- i.e. pixels see what the features miss. Caveat:
frozen COCO backbone isn't chart-tuned, so a clear POSITIVE is decisive; a
negative is strong-but-not-final (a trained detector could still differ), which
is why a pass escalates to real training rather than this being the verdict.

Causal, <2026-05-04, TP5/SL2 maker cost. Embeddings cached to scratchpad.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from src.costs import FORWARD_COST  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402
from scripts.it09_both_sides import net_dir  # noqa: E402

# Prefer SCRATCH env; else a local scratch dir (Mac YOLO embed is flaky on huge batches).
_CACHE_DIR = Path(os.environ["SCRATCH"]) if os.environ.get("SCRATCH") else (PROJECT / "analysis" / "output" / "_it14_scratch")
CACHE = _CACHE_DIR / "it14_embed.npz"
HORIZON, WIN = 72, 160
DIR_K = 3.0  # symmetric barrier ATR multiple for the direction label


def fwd_direction(ind, i):
    """1 if price races +K*atr before -K*atr from next-bar entry, else 0. Causal."""
    ei = i + 1
    atr = float(ind["atr14"].iloc[i]); ap = float(ind["atr_pct"].iloc[i])
    if ei >= len(ind) or not np.isfinite(atr) or atr <= 0 or not np.isfinite(ap) or ap < ATR_PCT_MIN:
        return None
    e = float(ind["open"].iloc[ei])
    last = min(ei + HORIZON - 1, len(ind) - 1)
    H = ind["high"].to_numpy()[ei:last + 1]; L = ind["low"].to_numpy()[ei:last + 1]; C = ind["close"].to_numpy()[ei:last + 1]
    if len(C) < 8:
        return None
    up, dn = e + DIR_K * atr, e - DIR_K * atr
    ut = np.argmax(H >= up) if (H >= up).any() else 10**9
    dt = np.argmax(L <= dn) if (L <= dn).any() else 10**9
    if ut == 10**9 and dt == 10**9:
        return 1 if C[-1] >= e else 0
    return 1 if ut < dt else 0


def build_embeddings():
    # Mac: batch embed + MPS has segfaulted (exit 139). Force CPU, one image at a time.
    import torch
    torch.set_num_threads(1)
    from ultralytics import YOLO

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(PROJECT / "models" / "yolo11n.pt"))
    model.to("cpu")
    cand = pd.read_csv(PROJECT / "data" / "v16_candidates_100.csv")
    cand["t"] = pd.to_datetime(cand["signal_time"], utc=True)
    embs, labs, times, longnet, shortnet, tabidx = [], [], [], [], [], []
    n_sym = cand["symbol"].nunique()

    for si, (sym, grp) in enumerate(cand.groupby("symbol"), 1):
        print(f"  [{si}/{n_sym}] {sym} tips_so_far={len(embs)}", flush=True)
        try:
            frame = add_mas(load_series(list_series(bar="15m")[("okx", sym)]))
        except Exception:
            continue
        ind = add_indicators(frame)
        tmap = {str(v): k for k, v in enumerate(pd.to_datetime(frame["open_time"], utc=True))}
        for ridx, r in grp.iterrows():
            i = tmap.get(str(r["t"]))
            if i is None or i < WIN + 60 or i >= len(frame) - HORIZON - 2:
                continue
            d = fwd_direction(ind, i)
            ln = net_dir(ind, i, +1); sn = net_dir(ind, i, -1)
            if d is None or ln is None or sn is None:
                continue
            win = frame.iloc[i - WIN + 1:i + 1]
            try:
                img, _ = render_chart(win)
            except Exception:
                continue
            bgr = img[:, :, ::-1].copy()  # RGB->BGR for cv2/yolo
            # No device= kwarg: on Mac it has segfaulted intermittently with device="cpu".
            out = model.embed([bgr], verbose=False)
            embs.append(out[0].detach().cpu().numpy().astype(np.float32))
            labs.append(d); times.append(str(r["t"])); longnet.append(ln)
            shortnet.append(sn); tabidx.append(int(ridx))
        # checkpoint per symbol so a mid-run crash doesn't lose everything
        if embs:
            np.savez_compressed(
                CACHE, E=np.vstack(embs), y=np.array(labs), t=np.array(times),
                longnet=np.array(longnet), shortnet=np.array(shortnet),
                tabidx=np.array(tabidx), done_sym=np.array([sym]),
            )
    E = np.vstack(embs); y = np.array(labs); t = np.array(times)
    np.savez_compressed(CACHE, E=E, y=y, t=t,
                        longnet=np.array(longnet), shortnet=np.array(shortnet), tabidx=np.array(tabidx))
    print(f"embedded {len(y)} tips, emb_dim={E.shape[1]}, up_rate={y.mean():.3f}", flush=True)
    return CACHE


def pf(x):
    x = np.asarray(x); w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def wf_eval(X, y, t, longnet, shortnet, tag):
    order = np.argsort(t); X, y, t = X[order], y[order], t[order]
    ln, sn = longnet[order], shortnet[order]
    n = len(y); rows = []
    P = {"objective": "binary", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 30,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tri, tei = slice(int(n*a), int(n*b)), slice(int(n*b), int(n*c))
        Xtr, ytr = X[tri], y[tri]; Xte, yte = X[tei], y[tei]
        if len(ytr) < 100 or len(yte) < 30 or len(np.unique(ytr)) < 2:
            rows.append({"n": len(yte)}); continue
        bo = lgb.train(P, lgb.Dataset(Xtr, label=ytr), num_boost_round=250)
        p = bo.predict(Xte)
        from sklearn.metrics import roc_auc_score
        auc = roc_auc_score(yte, p) if len(np.unique(yte)) == 2 else None
        # directional trade: p>0.5 -> long net, else short net; top-decile by confidence
        chosen_net = np.where(p > 0.5, ln[tei], sn[tei])
        conf = np.abs(p - 0.5); k = max(int(len(yte) * 0.1), 1)
        top = chosen_net[np.argsort(-conf)[:k]]
        rows.append({"n": int(len(yte)), "AUC": round(float(auc), 3) if auc else None,
                     "dir_acc": round(float(((p > 0.5) == (yte == 1)).mean()), 3),
                     "top_dir_PF": pf(top), "top_n": k})
    print(f"[{tag}] " + " | ".join(
        f"AUC{r.get('AUC')}/acc{r.get('dir_acc')}/PF{r.get('top_dir_PF')}" for r in rows))
    return rows


def main() -> int:
    if not CACHE.exists():
        build_embeddings()
    d = np.load(CACHE, allow_pickle=True)
    E, y, t = d["E"], d["y"].astype(int), d["t"]
    ln, sn, tabidx = d["longnet"], d["shortnet"], d["tabidx"].astype(int)
    print(f"loaded {len(y)} tips, emb_dim={E.shape[1]}, up_rate={y.mean():.3f}")
    vis = wf_eval(E, y, t, ln, sn, "VISUAL emb")
    # head-to-head tabular baseline on the SAME tips/labels
    cand = pd.read_csv(PROJECT / "data" / "v16_candidates_100.csv")
    TAB = [c for c in cand.columns if c not in {"symbol", "signal_time", "net", "t"}]
    Xtab = cand.loc[tabidx, TAB].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy()
    tab = wf_eval(Xtab, y, t, ln, sn, "TABULAR 130")
    out = {"n": int(len(y)), "up_rate": round(float(y.mean()), 3),
           "visual_emb": vis, "tabular_130": tab,
           "verdict_gate": "visual AUC>0.55 held-out OR top_dir_PF>1.3 => greenlight 3060"}
    (PROJECT / "analysis" / "output" / "it14_visual_direction_precheck.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
