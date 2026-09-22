"""Robustness audit of the morphology v6 arm-A top-decile economic result.

Source: owner request 2026-09-22 ("先测试yolo模型").  Plan and frozen config:
``experiments/active/exp-ma-morph-v6-econ-audit-20260922-v1/``.

Read-only over the frozen v6 arm-A economic outputs (per-event score, rule
direction, timeframe, gross/net bp, outcome; per-event matched random control
means) joined to the v4 bank manifest for asset and decision time.  It re-
computes the reported top-decile numbers, then checks score monotonicity,
concentration, UTC-day block bootstrap intervals and ISO-week sign-flip p for
the excess over matched random.  Excess uses bp (scale invariant); R excess
is inflated when control stops are narrower.  No inference, redraw or edit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-ma-morph-v6-econ-audit-20260922-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
TEST = Path("tests/evaluation/test_ma_morph_v6_econ_audit.py")


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(cfg: dict) -> pd.DataFrame:
    """One row per event: split, score, economics, control mean, asset, time."""
    root = Path(cfg["economics_dir"])
    rows = []
    for split in cfg["splits"]:
        events = pd.read_json(root / f"events_{split}.jsonl", lines=True)
        metrics = json.loads((root / f"metrics_{split}.json").read_text())
        pairs = {p["event_id"]: p for p in metrics["matched_random_control"]["pairs"]}
        events["control_net_bp"] = [pairs[e]["control_mean"]["net_bp"] if e in pairs and pairs[e]["resolved_controls"] > 0
                                    else np.nan for e in events.event_id]
        top = set(metrics["model"]["top10_event_ids"])
        events["top10_reported"] = events.event_id.isin(top)
        events["split"] = split
        rows.append(events)
    frame = pd.concat(rows, ignore_index=True)
    meta = {}
    with open(cfg["manifest"]) as handle:
        for line in handle:
            r = json.loads(line)
            meta.setdefault(r["event_id"], (r["canonical_asset"], r["decision_at_utc"]))
    missing = set(frame.event_id) - set(meta)
    if missing:
        raise ValueError(f"{len(missing)} events absent from manifest")
    frame["asset"] = [meta[e][0] for e in frame.event_id]
    frame["decision_at"] = pd.to_datetime([meta[e][1] for e in frame.event_id], utc=True)
    return frame


def top_fraction(frame: pd.DataFrame, fraction: float) -> pd.DataFrame:
    """ceil(fraction*N) by score with event_id tiebreak, as in the source study."""
    k = int(np.ceil(fraction * len(frame)))
    return frame.sort_values(["score", "event_id"], ascending=[False, True]).head(k)


def block_stats(x: pd.Series, blocks: pd.Series, rng, reps: int, flips: int):
    """Mean, block-bootstrap 95% interval, and one-sided sign-flip p on block sums."""
    f = pd.DataFrame({"x": x.to_numpy(float), "b": blocks.to_numpy()}).dropna()
    g = f.groupby("b").x
    sums, counts = g.sum().to_numpy(), g.size().to_numpy()
    if len(sums) < 2:
        return f.x.mean(), np.nan, np.nan, np.nan, len(sums)
    draws = rng.integers(0, len(sums), size=(reps, len(sums)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    signs = rng.choice([-1.0, 1.0], size=(flips, len(sums)))
    p = float(((signs * sums).sum(axis=1) >= sums.sum()).mean())
    return f.x.mean(), float(np.quantile(means, .025)), float(np.quantile(means, .975)), p, len(sums)


def audit(frame: pd.DataFrame, cfg: dict) -> dict:
    rng = np.random.default_rng(cfg["seed"])
    out: dict = {"reconcile": {}, "deciles": {}, "scopes": {}}
    tops = []
    for split in cfg["splits"]:
        s = frame[frame.split == split]
        t = top_fraction(s, cfg["top_fraction"])
        if set(t.event_id) != set(s[s.top10_reported].event_id):
            raise ValueError(f"top10 membership differs from source for {split}")
        out["reconcile"][split] = {"n": len(t), "net_bp": t.net_bp.mean(), "gross_bp": t.gross_bp.mean(),
                                   "win_rate": float((t.net_bp > 0).mean()),
                                   "reported": cfg["reported"][split]}
        ranks = s.score.rank(method="first", ascending=False)
        decile = np.ceil(ranks / len(s) * 10).clip(1, 10).astype(int)
        out["deciles"][split] = {"net_bp_by_decile": s.groupby(decile).net_bp.mean().round(3).to_dict(),
                                 "spearman_score_net_bp": float(s.score.corr(s.net_bp, method="spearman"))}
        tops.append(t)
    pooled = pd.concat(tops, ignore_index=True)
    scopes = {"val": pooled[pooled.split == "val"], "test": pooled[pooled.split == "test"], "pooled": pooled}
    for name, t in scopes.items():
        t = t.assign(day=t.decision_at.dt.strftime("%Y-%m-%d"), week=t.decision_at.dt.strftime("%G-W%V"),
                     excess_bp=t.net_bp - t.control_net_bp)
        best = t.sort_values("net_bp", ascending=False)
        mean, lo, hi, _, days = block_stats(t.net_bp, t.day, rng, cfg["bootstrap"], cfg["flips"])
        ex_mean, ex_lo, ex_hi, _, _ = block_stats(t.excess_bp, t.day, rng, cfg["bootstrap"], cfg["flips"])
        _, _, _, ex_p_week, weeks = block_stats(t.excess_bp, t.week, rng, cfg["bootstrap"], cfg["flips"])
        share = t.asset.value_counts(normalize=True)
        scope = {"n": len(t), "days": days, "weeks": weeks, "assets": int(t.asset.nunique()),
                 "max_asset_share": float(share.iloc[0]), "max_asset": str(share.index[0]),
                 "net_bp": mean, "net_bp_day_ci": [lo, hi], "median_net_bp": float(t.net_bp.median()),
                 "gross_bp": float(t.gross_bp.mean()), "win_rate": float((t.net_bp > 0).mean()),
                 "tp_rate": float((t.outcome == "TP").mean()),
                 "excess_bp": ex_mean, "excess_bp_day_ci": [ex_lo, ex_hi], "excess_bp_week_p": ex_p_week,
                 "control_net_bp": float(t.control_net_bp.mean()),
                 "drop_best": {str(k): float(best.net_bp.iloc[k:].mean()) for k in cfg["drop_best"]},
                 "by_timeframe": t.groupby(np.where(t.bar_minutes.isin([1, 3]), t.bar_minutes.astype(str) + "m", "other"))
                                  .net_bp.agg(["size", "mean"]).round(3).to_dict(orient="index"),
                 "by_direction": t.groupby("direction").net_bp.agg(["size", "mean"]).round(3).to_dict(orient="index")}
        out["scopes"][name] = scope
    p = out["scopes"]["pooled"]
    out["verdict"] = ("robust_positive" if p["net_bp"] > 0 and p["net_bp_day_ci"][0] > 0
                      and p["excess_bp_week_p"] < cfg["p_threshold"] and p["drop_best"]["3"] > 0
                      else "not_robust")
    return out


def run(output: Path) -> None:
    cfg = json.loads(CONFIG.read_text())
    code = (Path(__file__), CONFIG, PLAN, TEST)
    if not _committed(code):
        raise ValueError("commit audit code/plan/config/tests before generating results")
    root = Path(cfg["economics_dir"])
    inputs = {str(p): digest(p) for p in sorted(root.glob("*_*.json*"))} | {cfg["manifest"]: digest(Path(cfg["manifest"]))}
    receipt_sha = json.loads((root / "receipt.json").read_text())["artifacts"]
    for name, sha in receipt_sha.items():
        if digest(root / name) != sha:
            raise ValueError(f"source artifact changed: {name}")
    result = audit(load(cfg), cfg)
    output.mkdir(parents=True, exist_ok=False)
    (output / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float) + "\n")
    receipt = {"inputs": inputs, "code": {str(p): digest(p) for p in code},
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "audit_sha256": digest(output / "audit.json"), "verdict": result["verdict"]}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"verdict": result["verdict"], "pooled": result["scopes"]["pooled"]}, ensure_ascii=False, default=float, indent=1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
