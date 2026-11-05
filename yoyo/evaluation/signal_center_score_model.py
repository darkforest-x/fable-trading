"""LightGBM scoring of signal-center cloud signals, per timeframe, out of time.

Reads the NautilusTrader outputs of yoyo.evaluation.signal_center_score (one run,
not repeated) for exp-signal-center-score-20261106-v1. For each timeframe and
fold the model trains only on signals whose trade EXITED before the fold start
(label purge) and scores signals entered inside the fold. Parameters are the
fixed small configuration reused from exp-clean-reversal-score-20261101-v1.

Evaluation (net of maker2/taker5 bp fees; slippage 5bp per market fill shown
separately): all signals; top 10 / top 20 / bottom 20 percent by score within the
fold; week-stratified permutation (same number of picks per NY week, random
within week) for the top-group mean; signal-minus-matched-control paired
difference with a week-cluster bootstrap; three single-feature baselines with
direction from the training-period Spearman sign. AUC is reported as reference
only. Run with the main venv:
  .venv/bin/python -m yoyo.evaluation.signal_center_score_model \
      --config experiments/active/exp-signal-center-score-20261106-v1/config.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.signal_center_score import FEATURES, ROOT, TFS

BASELINES = ["pen_atr", "rr_close", "ema_spread_atr"]
SLIP_BP = 10.0
N_PERM = 2000
N_BOOT = 2000


def load(out):
    sig, trd, raw = [], [], []
    for d in sorted((out / "symbols").iterdir()):
        if not (d / "done.json").exists():
            raise RuntimeError(f"unfinished symbol {d.name}")
        if (d / "signals.csv.gz").exists():
            sig.append(pd.read_csv(d / "signals.csv.gz"))
            trd.append(pd.read_csv(d / "trades.csv.gz"))
        raw.append(pd.read_csv(d / "raw.csv.gz"))
    sig, trd, raw = pd.concat(sig), pd.concat(trd), pd.concat(raw)
    same = raw.groupby(["tf", "close_ns", "side"]).size().rename("n_same_bar").reset_index()
    sig = sig.merge(same, on=["tf", "close_ns", "side"], how="left")
    st = trd[trd.kind == "signal"].set_index("trade_id")
    sig = sig.join(st[["net_bp", "gross_bp", "exit_ns", "exit_reason", "censored"]], on="signal_id")
    ctl = trd[trd.kind == "control"].groupby("parent").agg(ctrl_net=("net_bp", "mean"), n_ctrl=("net_bp", "size"))
    sig = sig.join(ctl, on="signal_id")
    if sig.net_bp.isna().any():
        raise RuntimeError("signal without a Nautilus trade")
    return sig, trd


def _pick(score, frac):
    n = max(1, int(round(len(score) * frac)))
    order = np.argsort(-score, kind="stable")
    mask = np.zeros(len(score), bool)
    mask[order[:n]] = True
    return mask


def week_perm_p(net, mask, weeks, rng):
    """P(mean of random within-week picks >= observed), same picks per week."""
    obs = net[mask].mean()
    total = np.zeros(N_PERM)
    for w in np.unique(weeks):
        idx = np.flatnonzero(weeks == w)
        k = int(mask[idx].sum())
        if not k:
            continue
        keys = rng.random((N_PERM, len(idx)))
        sel = np.argpartition(keys, k - 1, axis=1)[:, :k] if k < len(idx) else np.tile(np.arange(len(idx)), (N_PERM, 1))
        total += net[idx][sel].sum(axis=1)
    null = total / mask.sum()
    return float((1 + (null >= obs - 1e-12).sum()) / (1 + N_PERM)), float(null.mean())


def paired(diff, weeks, rng):
    ok = np.isfinite(diff)
    diff, weeks = diff[ok], weeks[ok]
    if not len(diff):
        return dict(n=0)
    uw, inv = np.unique(weeks, return_inverse=True)
    sums = np.bincount(inv, diff, len(uw))
    cnts = np.bincount(inv, None, len(uw))
    draws = rng.integers(0, len(uw), (N_BOOT, len(uw)))
    boot = sums[draws].sum(1) / cnts[draws].sum(1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(n=int(len(diff)), mean=round(float(diff.mean()), 2), ci=[round(float(lo), 2), round(float(hi), 2)])


def group_stats(net, mask, weeks, ctrl, rng):
    g = net[mask]
    p, null = week_perm_p(net, mask, weeks, rng)
    return dict(n=int(mask.sum()), mean_bp=round(float(g.mean()), 2), median_bp=round(float(np.median(g)), 2),
                win=round(float((g > 0).mean()), 4), mean_after_slip_bp=round(float(g.mean() - SLIP_BP), 2),
                perm_p=round(p, 4), perm_null_mean_bp=round(null, 2),
                ctrl_mean_bp=round(float(np.nanmean(ctrl[mask])), 2) if np.isfinite(ctrl[mask]).any() else None,
                vs_ctrl=paired(net[mask] - ctrl[mask], weeks[mask], rng))


def main(argv=None):
    import lightgbm as lgb
    from scipy.stats import spearmanr
    from sklearn.metrics import roc_auc_score
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    cfg = json.loads(Path(a.config).read_text())
    out = ROOT / cfg["output_dir"]
    sig, trd = load(out)
    feats = [c for c in FEATURES]
    sig["week_key"] = sig["week"].astype(str)
    sig["entry_ts"] = pd.to_datetime(sig.close_ns, utc=True)
    sig["year"] = sig.entry_ts.dt.year
    rng = np.random.default_rng(20261106)
    summary = dict(experiment_id=cfg["experiment_id"], features=feats, census={}, folds={}, by_year={})
    scored, gains = [], []
    models = out / "models"
    models.mkdir(exist_ok=True)
    for tf in TFS:
        s = sig[sig.tf == tf].copy()
        c = trd[(trd.schedule == tf)]
        x = np.sort(s.net_bp.to_numpy())[::-1]
        k = max(1, len(x) // 100)
        summary["census"][tf] = dict(
            top1pct_sum_bp=round(float(x[:k].sum()), 1), total_sum_bp=round(float(x.sum()), 1),
            mean_excluding_top1pct_bp=round(float(x[k:].mean()), 2), median_net_bp=round(float(np.median(x)), 2),
            signals=int(len(s)), controls=int((c.kind == "control").sum()),
            signals_with_controls=int((s.n_ctrl > 0).sum()), censored=int(s.censored.sum()),
            first=str(s.entry_ts.min()), last=str(s.entry_ts.max()),
            exit_reasons=s.exit_reason.value_counts().to_dict(),
            mean_net_bp=round(float(s.net_bp.mean()), 2), win=round(float((s.net_bp > 0).mean()), 4),
            vs_ctrl=paired((s.net_bp - s.ctrl_net).to_numpy(), s.week_key.to_numpy(), rng))
        yr = {}
        for y, g in s.groupby("year"):
            yr[int(y)] = dict(n=int(len(g)), mean_net_bp=round(float(g.net_bp.mean()), 1),
                              ctrl_mean_bp=round(float(g.ctrl_net.mean()), 1),
                              vs_ctrl=paired((g.net_bp - g.ctrl_net).to_numpy(), g.week_key.to_numpy(), rng))
        summary["by_year"][tf] = yr
        for fold in cfg["folds"]:
            start = pd.Timestamp(fold["test_from"], tz="UTC")
            end = pd.Timestamp(fold["test_to"], tz="UTC")
            cut = pd.Timestamp(fold["train_exit_before"], tz="UTC").value
            train = s[(s.exit_ns < cut)]
            test = s[(s.entry_ts >= start) & (s.entry_ts < end)].copy()
            y = (train.net_bp > 0).astype(int)
            model = lgb.LGBMClassifier(objective="binary", **cfg["lightgbm"])
            model.fit(train[feats], y)
            model.booster_.save_model(str(models / f"{tf}_{fold['name']}.txt"))
            score = model.predict_proba(test[feats])[:, 1]
            test["score"], test["fold"] = score, fold["name"]
            scored.append(test[["signal_id", "tf", "fold", "score", "net_bp", "ctrl_net"]])
            gains.append(pd.DataFrame(dict(tf=tf, fold=fold["name"], feature=feats,
                                           gain=model.booster_.feature_importance("gain"))))
            net, ctrl, weeks = test.net_bp.to_numpy(), test.ctrl_net.to_numpy(), test.week_key.to_numpy()
            res = dict(train_n=int(len(train)), train_win=round(float(y.mean()), 4), test_n=int(len(test)),
                       auc_reference=round(float(roc_auc_score(net > 0, score)), 4),
                       all=group_stats(net, np.ones(len(net), bool), weeks, ctrl, rng))
            for name, frac in (("top10", .1), ("top20", .2)):
                res[name] = group_stats(net, _pick(score, frac), weeks, ctrl, rng)
            res["bottom20"] = group_stats(net, _pick(-score, .2), weeks, ctrl, rng)
            base = {}
            for feat in BASELINES:
                rho = spearmanr(train[feat], train.net_bp, nan_policy="omit").statistic
                x = np.nan_to_num(np.sign(rho) * test[feat].to_numpy(), nan=-np.inf)
                m = _pick(x, .2)
                p, _ = week_perm_p(net, m, weeks, rng)
                base[feat] = dict(direction=int(np.sign(rho)), top20_mean_bp=round(float(net[m].mean()), 2), perm_p=round(p, 4))
            res["single_feature_top20"] = base
            # Diagnostics: rule-mechanical exits at the entry close, and 1/99% winsorized means.
            st = trd[(trd.kind == "signal") & (trd.schedule == tf)].set_index("trade_id")
            imm = (st.loc[test.signal_id, "exit_i"].to_numpy() == st.loc[test.signal_id, "entry_i"].to_numpy())
            top = _pick(score, .2)
            lo_q, hi_q = np.quantile(net, [.01, .99])
            wz = np.clip(net, lo_q, hi_q)
            res["diagnostics"] = dict(
                immediate_exit_all=round(float(imm.mean()), 4), immediate_exit_top20=round(float(imm[top].mean()), 4),
                win_all=round(float((net > 0).mean()), 4), win_top20=round(float((net[top] > 0).mean()), 4),
                winsorized_mean_all_bp=round(float(wz.mean()), 2), winsorized_mean_top20_bp=round(float(wz[top].mean()), 2),
                median_all_bp=round(float(np.median(net)), 2), median_top20_bp=round(float(np.median(net[top])), 2),
                auc_excluding_immediate=round(float(roc_auc_score(net[~imm] > 0, score[~imm])), 4))
            t20 = res["top20"]
            res["success"] = bool(t20["mean_bp"] > 0 and t20["perm_p"] < 0.01
                                  and t20["vs_ctrl"].get("ci", [0])[0] > 0)
            summary["folds"].setdefault(tf, {})[fold["name"]] = res
    summary["success_by_tf"] = {tf: all(v["success"] for v in f.values()) for tf, f in summary["folds"].items()}
    pd.concat(scored).to_csv(out / "scores_oos.csv.gz", index=False)
    pd.concat(gains).to_csv(out / "feature_gain.csv", index=False)
    (out.parent / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    for tf, f in summary["folds"].items():
        for name, r in f.items():
            print(tf, name, "n", r["test_n"], "auc", r["auc_reference"],
                  "all", r["all"]["mean_bp"], "top20", r["top20"]["mean_bp"], "p", r["top20"]["perm_p"],
                  "ctrl", r["top20"]["ctrl_mean_bp"], r["top20"]["vs_ctrl"], "top10", r["top10"]["mean_bp"],
                  "bot20", r["bottom20"]["mean_bp"], "ok", r["success"])


if __name__ == "__main__":
    main()
