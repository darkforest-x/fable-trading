"""Walk-forward comparison of entry-score models and feature groups for the Monday1618 limit-reclaim rule (owner2026-10-25).

Owner: build feature table v2, then compare logistic regression with LightGBM
under a walk-forward time split, with ablations by feature group. Input:
exp-monday-limit-score-features-v2-20261025-v1/features_v2.csv.gz (causal
features, labels from framework fills). Label: win = worst-path near-target net
> 0. Test years 2022..2026, each trained only on fills before 1 January of that
year (expanding window). LightGBM early-stops on the last 15 percent of the
training rows by time. Feature sets: A (current 10), A plus one group, all
groups, and all minus one group. Thresholds for the top 10 / 20 / 40 percent
come from each fold's training scores, never from the test year. Pooled
2025-2026 predictions (each from its own fold) are also replayed from 1000
USDT: equal 10 percent on the top 40 percent, and linear in the training
percentile above 0.6 (0 to 25 percent per trade), gross <= 1x, medians over
TIES random orders of same-time fills. Infinite ratios (zero denominators) count as missing. Only fills with all ten base
features are used, so every model and the frozen score rank the same rows. The frozen current score is evaluated on
the same 2025-2026 rows as the reference. Read-only arithmetic.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import INK, INK2, MUTED, GRID, SURF
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-score-v2-train-20261025-v1'
FEAT = ROOT/'experiments/active/exp-monday-limit-score-features-v2-20261025-v1'
FROZEN = ROOT/'experiments/active/exp-monday-limit-reclaim-recent-20261025-v1/score_model.json'
YEARS = [2022, 2023, 2024, 2025, 2026]
TIES = 10
BASE = ['bucket', 'log_vol_ratio', 'log_approach', 'opp_first', 'monday_close_side', 'log_liquidity', 'toward24', 'cluster24', 'side', 'range_pct']  # rows the current score can score (16 percent of fills lack some base feature)


def feature_sets(groups):
    allc = [c for g in 'ABCDEF' for c in groups[g]]
    sets = {'A': groups['A']}
    for g in 'BCDEF':
        sets[f'A+{g}'] = groups['A']+groups[g]
    sets['ALL'] = allc
    for g in 'BCDEF':
        sets[f'ALL-{g}'] = [c for c in allc if c not in groups[g]]
    return sets


T = None


def _init(path):
    global T
    T = pd.read_csv(path, parse_dates=['touch', 'exit_time']).dropna(subset=['win', 'net']).sort_values('touch').reset_index(drop=True)
    T = T.replace([np.inf, -np.inf], np.nan).dropna(subset=BASE).reset_index(drop=True)
    T['year'] = T.touch.dt.year


def fit_predict(args):
    model, name, cols, year = args
    train, test = T[T.year < year], T[T.year == year]
    X, y = train[cols].to_numpy(float), train.win.to_numpy()
    if model == 'logit':
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        m = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), LogisticRegression(max_iter=3000, C=1.))
        m.fit(X, y)
        ptr, pte = m.predict_proba(X)[:, 1], m.predict_proba(test[cols].to_numpy(float))[:, 1]
        imp = None
    else:
        import lightgbm as lgb
        cut = int(len(train)*.85)
        m = lgb.LGBMClassifier(n_estimators=2000, learning_rate=.02, num_leaves=15, min_child_samples=200, subsample=.8, subsample_freq=1,
                               colsample_bytree=.8, reg_lambda=1., random_state=0, verbose=-1)
        m.fit(X[:cut], y[:cut], eval_set=[(X[cut:], y[cut:])], eval_metric='auc', callbacks=[lgb.early_stopping(100, verbose=False)])
        ptr, pte = m.predict_proba(X)[:, 1], m.predict_proba(test[cols].to_numpy(float))[:, 1]
        imp = dict(zip(cols, m.booster_.feature_importance('gain').tolist()))
    return model, name, year, ptr, pte, imp


def topk(score_tr, score_te, net):
    from sklearn.metrics import roc_auc_score
    out = {}
    for q in (.9, .8, .6):
        thr = np.quantile(score_tr, q)
        sel = score_te >= thr
        out[f'top{int(round(100*(1-q)))}'] = dict(n=int(sel.sum()), mean_bp=float(net[sel].mean()) if sel.any() else None,
                                                   win_pct=float(100*(net[sel] > 0).mean()) if sel.any() else None)
    return out


def replay(df, pct, seed):
    rng = np.random.default_rng(seed)
    d = df.assign(pct=pct).sort_values(['entry', 'event'], kind='mergesort').reset_index(drop=True)
    out = {}
    for sizing in ('equal_top40', 'linear_pct'):
        k = np.where(d.pct >= .6, .10, 0.) if sizing == 'equal_top40' else np.clip((d.pct.to_numpy()-.6)/.4, 0, 1)*.25
        rep = []
        for _ in range(TIES):
            order = np.lexsort((rng.random(len(d)), d.entry.to_numpy()))
            z = d.iloc[order].reset_index(drop=True)
            rep.append(opt.replay_w(z, k[order], 1.)[0])
        out[sizing] = dict(final_median=float(np.median([r['final_multiple'] for r in rep])), dd_median=float(np.median([r['max_drawdown_pct'] for r in rep])))
    return out


def run():
    p = 'yoyo/evaluation/monday_limit_score_v2_train.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    from sklearn.metrics import roc_auc_score
    cfg = json.loads((EXP/'config.json').read_text())
    groups = json.loads((FEAT/'summary.json').read_text())['groups']
    groups['A'] = [c for c in groups['A'] if c != 'side']+['side']
    t = pd.read_csv(FEAT/'features_v2.csv.gz', parse_dates=['touch', 'exit_time'])
    t = t.dropna(subset=['win', 'net']).sort_values('touch').replace([np.inf, -np.inf], np.nan).dropna(subset=BASE).reset_index(drop=True)
    t['year'] = t.touch.dt.year
    t['entry'] = t.touch.astype('int64'); t['exit'] = t.exit_time.astype('int64')
    sets = feature_sets(groups)
    years = [y for y in cfg.get('years', YEARS) if (t.year < y).sum() >= 200 and (t.year == y).sum() >= 50]
    jobs = [(model, name, cols, y) for y in years for model in ('logit', 'lgbm') for name, cols in sets.items()]
    with ProcessPoolExecutor(cfg.get('workers', 4), initializer=_init, initargs=(str(FEAT/'features_v2.csv.gz'),)) as ex:
        out = list(ex.map(fit_predict, jobs, chunksize=2))
    res = dict(rows=len(t), win_rate=float(t.win.mean()), folds={}, pooled_2025_2026={}, importance_last_fold={})
    pooled = {}
    for (model, name, cols, y), (_, _, _, ptr, pte, imp) in zip(jobs, out):
        tr, te = t[t.year < y], t[t.year == y]
        net = te.net.to_numpy()
        res['folds'][f'{model}|{name}|{y}'] = dict(train_n=len(tr), test_n=len(te), auc=float(roc_auc_score(te.win, pte)), **topk(ptr, pte, net))
        if y >= 2025:
            pct = np.searchsorted(np.sort(ptr), pte, side='right')/len(ptr)
            pooled.setdefault(f'{model}|{name}', []).append(te.assign(score=pte, pct=pct))
        if y == years[-1] and imp:
            res['importance_last_fold'][f'{model}|{name}'] = dict(sorted(imp.items(), key=lambda kv: -kv[1])[:25])
    spec = json.loads(FROZEN.read_text())
    late = t[t.year >= 2025].copy()
    z = (late[spec['features']].to_numpy(float)-np.array(spec['mean']))/np.array(spec['scale'])
    late['score'] = 1/(1+np.exp(-((z*np.array(spec['coef'])).sum(1)+spec['intercept'])))
    early = t[t.year < 2025]
    ze = (early[spec['features']].to_numpy(float)-np.array(spec['mean']))/np.array(spec['scale'])
    early_score = 1/(1+np.exp(-((ze*np.array(spec['coef'])).sum(1)+spec['intercept'])))
    late['pct'] = np.searchsorted(np.sort(early_score[np.isfinite(early_score)]), late.score, side='right')/np.isfinite(early_score).sum()
    pooled['frozen|A'] = [late]
    for i, (k, parts) in enumerate(pooled.items()):
        d = pd.concat(parts, ignore_index=True).dropna(subset=['score'])
        r = dict(n=len(d), auc=float(roc_auc_score(d.win, d.score)))
        for q in (.9, .8, .6):
            sel = d.pct >= q
            r[f'top{int(round(100*(1-q)))}'] = dict(n=int(sel.sum()), mean_bp=float(d.net[sel].mean()), win_pct=float(100*(d.net[sel] > 0).mean()))
        r |= replay(d, d.pct.to_numpy(), 20261025+i)
        res['pooled_2025_2026'][k] = r
    EXP.mkdir(exist_ok=True)
    charts(res, list(sets))
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(f"rows {res['rows']} win {res['win_rate']:.3f}")
    for k, r in sorted(res['pooled_2025_2026'].items(), key=lambda kv: -kv[1]['top20']['mean_bp']):
        aucs = '/'.join(f"{res['folds'].get(f'{k}|{y}', {}).get('auc', float('nan')):.3f}" for y in years) if not k.startswith('frozen') else '-'
        print(f"{k:16s} AUC25-26 {r['auc']:.3f} | top10 {r['top10']['mean_bp']:.1f} top20 {r['top20']['mean_bp']:.1f} top40 {r['top40']['mean_bp']:.1f} | "
              f"eq40 {r['equal_top40']['final_median']:.2f}/{r['equal_top40']['dd_median']:.0f} lin {r['linear_pct']['final_median']:.2f}/{r['linear_pct']['dd_median']:.0f} | fold AUC {aucs}")


def charts(res, names):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    fig, axes = plt.subplots(1, 2, figsize=(17, 6))
    y = np.arange(len(names)); h = .38
    for off, model, col in ((-h/2, 'logit', '#898781'), (h/2, 'lgbm', '#2a78d6')):
        axes[0].barh(y+off, [res['pooled_2025_2026'][f'{model}|{n}']['auc'] for n in names], h*.92, color=col, label='逻辑回归' if model == 'logit' else 'LightGBM')
        axes[1].barh(y+off, [res['pooled_2025_2026'][f'{model}|{n}']['top20']['mean_bp'] for n in names], h*.92, color=col)
    fr = res['pooled_2025_2026']['frozen|A']
    axes[0].axvline(fr['auc'], color='#eb6834', ls='--', lw=1.5, label='现行冻结打分'); axes[1].axvline(fr['top20']['mean_bp'], color='#eb6834', ls='--', lw=1.5)
    for ax_, title in zip(axes, ('2025–2026 合并 AUC（滚动训练，每年只用之前的数据）', '分数前20%（训练期分界）每笔平均净收益（bp）')):
        ax_.set_yticks(y, names); ax_.invert_yaxis(); ax_.set_title(title, color=INK, loc='left'); ax_.grid(axis='x', color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].set_xlim(.5, max(.75, axes[0].get_xlim()[1])); axes[0].legend(frameon=False, loc='lower right')
    fig.suptitle('打分模型对比 · A=现有10因素 B=大盘 C=波动扩张 D=均线结构 E=币种记忆 F=区间结构', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_models_groups.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()
