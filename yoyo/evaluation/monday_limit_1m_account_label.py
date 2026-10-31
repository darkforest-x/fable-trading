"""Fair retest of the 1m entry score on the paper accounts: rank-mapped cuts and account-exit labels (owner 2026-10-31).

Owner: "同时去做试试看吧". The 10-28 1m model ranked 2026 better than the 5m base (AUC 0.702 vs 0.661, top decile +45.8
vs +21.4bp) yet every paper account did worse in 2026. Two mismatches explain part of it: the accounts' fixed cuts
(0.5 admission, 0.6 target band and adds, linear size in the score) were set on the frozen score's scale while the 1m
score spreads wider (2026: 984 vs 661 events >= 0.5, 577 vs 255 >= 0.6); and the model learned v2 near-edge wins while
v3 / allin3 / spike exit elsewhere. The owner approved both fixes together; the arms separate them:
  frozen        frozen model on pre-touch features, fixed cuts (the executable baseline of the 20261028 accounts)
  m1_fixed      the 10-28 1m score at the fixed cuts (the 10-28 1m account run)
  m1_ranked     the 10-28 1m score rank-mapped onto the frozen scale (cuts only)
  m1_acct       1m model refit on the account's own exit label, rank-mapped (cuts and label)
  base_acct     5m base model refit on the same account label, rank-mapped (control: is the gain from the 1m features?)
  m1_shuf_k     m1_acct with the thirteen 1m features permuted jointly across rows, train and test (null, k seeds)
Labels come from step 1: NautilusTrader fills of every signal event under the accounts' exact rules
(monday_limit_paper_accounts, rest family, types v3 and spike, admit-all scores written by --prepare). The v3 family
label is the v3 opposite 1.618 parent row (-30 percent, breakeven +0.17 percent, 60m; the allin3 exit, the v3 band
from 0.6 and the add parents); the spike label is the spike row (near Monday edge, -18 percent, breakeven +0.17
percent). win = net > 0 on the worse path with slippage, estimator and features as the 10-28 fit. Train on fills
before 2026, test 2026. Rank mapping: score -> the frozen causal score at the same training-period rank
(np.interp over both sorted training columns), so every account rule applies unchanged and each band keeps its
training share. Accounts: v3, v3add, v3add20, allin3 on the v3-label score, spike on the spike-label score with EMA
order >= 3, fills, costs and sizing as the 20261028 accounts; v2 and v3lock are not rerun.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump

A = ROOT/'experiments/active'


def committed(*paths):
    for p in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def prepare(config):
    """Admit-all scores for the label run: pre 0.55 (v3 range band), touch 0.65 (opposite band, spike) for every event."""
    cfg = json.loads(config.read_text())
    ev = pd.read_csv(ROOT/cfg['frozen_scores']).event
    out = ROOT/cfg['admit_all_scores']
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(dict(event=ev, causal=.55, stored=.65)).to_csv(out, index=False)
    print(len(ev), out)


def event_features(cfg, workers):
    """Ten causal 5m features plus the thirteen 1m features per signal event, as monday_limit_score_1m_fit scores them."""
    from yoyo.evaluation.monday_limit_reclaim_patterns import SNAP
    from yoyo.evaluation import monday_limit_reclaim_allexits as ax
    from yoyo.evaluation import monday_limit_reclaim_optimize as opt
    from yoyo.evaluation.monday_limit_score_leak_audit import bar_features
    from yoyo.evaluation.monday_limit_score_causal_refit import with_bar
    from yoyo.evaluation.monday_limit_1m_features import FEATS as M1
    m1 = pd.read_csv(ROOT/cfg['features_1m']).set_index('event')
    ok = m1[m1.status == 'ok'][M1]
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(workers) as ex:
        x = pd.concat([d for d in ex.map(bar_features, syms, chunksize=4) if len(d)], ignore_index=True).set_index('event')
    ae = pd.read_csv(ROOT/cfg['events'])
    ae['event'] = ae.symbol+':'+ae.week+':'+np.where(ae.side_n == 1, '+1', '-1')
    v2 = sorted(d.name for d in ax.V2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(workers) as ex:
        ex2 = pd.concat(list(ex.map(opt.ledger_extra, v2, chunksize=8)), ignore_index=True)
    ex2['event'] = ex2.trade_id.str.split(':').str[:3].str.join(':')
    e = ae.set_index('event').join(ex2.drop_duplicates('event').set_index('event')[['bucket', 'range_pct']], how='inner').join(x, how='inner')
    e['side'] = e.side_n.astype(float); e['log_approach'] = np.log1p(e.approach_min.clip(lower=0))
    e['opp_first'] = (e.opp_first == '是').astype(int)
    e = with_bar(e, 'causal').join(ok, how='inner')
    return e[list(opt.FEATS)+M1].dropna(), list(opt.FEATS), M1


def labels(cfg, workers):
    """Per event: the v3 opposite 1.618 parent row and the spike row of the label run (worse path, slippage)."""
    from yoyo.evaluation.monday_limit_paper_accounts import load_symbol, symbols
    acfg = json.loads((ROOT/cfg['accounts_config']).read_text())
    run = ROOT/cfg['accounts_run']
    slip = acfg['slippage_bp']
    syms = symbols(acfg)
    missing = [(p, s) for p in ('ohlc', 'olhc') for s in syms if not (run/p/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete label run: {len(missing)}')
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load_symbol, [(str(run), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t['kind'] = t.variant.str.split('_', n=1).str[1]
    return t, [f'{g[0]}: {i}' for g in got for i in g[2]]


def rank_map(score, train_score, train_frozen):
    """Monotone map onto the frozen causal scale: same training-period rank, same frozen score."""
    a, b = np.sort(np.asarray(train_score, float)), np.sort(np.asarray(train_frozen, float))
    return pd.Series(np.interp(np.asarray(score, float), a, b), index=score.index).where(score.notna())


def fit(config, workers):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from yoyo.evaluation.monday_limit_score_causal_refit import auc
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_1m_account_label.py', str(config.resolve().relative_to(ROOT)))
    X, base, M1 = event_features(cfg, workers)
    t, issues = labels(cfg, workers)
    t = t[~t.event.isin(BAD_EVENTS)]
    split = pd.Timestamp(cfg['split'], tz='UTC')
    frozen = pd.read_csv(ROOT/cfg['frozen_scores']).set_index('event').causal
    old1m = pd.read_csv(ROOT/cfg['scores_1m']).set_index('event').causal
    rng = np.random.default_rng(cfg['seed'])
    out = pd.DataFrame(index=X.index)
    out['frozen'] = frozen.reindex(X.index)
    out['m1_old'] = old1m.reindex(X.index)
    res = dict(checks=dict(events_with_features=int(len(X)), label_issues=issues[:20]), labels={})
    for lab, sel in (('v3', (t.kind == 'v3') & (t['mode'] == 'opposite_1618')), ('spike', t.kind == 'spike')):
        r = t[sel].drop_duplicates('event').set_index('event')
        g = X.join(r[['net', 'entry_t']], how='inner')
        g['y'] = (g.net > 0).astype(int)
        tr, va = g[g.entry_t < split], g[g.entry_t >= split]
        info = dict(train_n=int(len(tr)), test_n=int(len(va)), train_win_pct=float(100*tr.y.mean()), test_win_pct=float(100*va.y.mean()),
                    test_mean_bp=float(va.net.mean()))
        sets = {'base_acct': base, 'm1_acct': base+M1}
        models = {}
        for name, cols in sets.items():
            m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(tr[cols], tr.y)
            models[name] = (m, cols)
        for k in range(cfg['shuffles']):
            sh = tr.copy(); sh[M1] = sh[M1].to_numpy()[rng.permutation(len(sh))]
            models[f'm1_shuf{k}'] = (make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(sh[base+M1], sh.y), base+M1)
        # Scores for every event with features; the null permutes the 1m features of all events (train and test alike).
        trn = X.index.isin(tr.index)
        for name, (m, cols) in models.items():
            Z = X[cols].copy()
            if name.startswith('m1_shuf'):
                Z[M1] = Z[M1].to_numpy()[rng.permutation(len(Z))]
            s = pd.Series(m.predict_proba(Z)[:, 1], index=X.index)
            ok = trn & out.frozen.notna().to_numpy()
            out[f'{lab}_{name}'] = rank_map(s, s[ok], out.frozen[ok])
            p = s.reindex(va.index)
            info[name] = dict(auc=auc(va.y, p), top10_mean_bp=float(va.net[p >= p.quantile(.9)].mean()),
                              coef=dict(zip(cols, m[-1].coef_[0].round(4).tolist())) if not name.startswith('m1_shuf') else None)
        for name, col in (('frozen', 'frozen'), ('m1_old', 'm1_old')):
            p = out[col].reindex(va.index)
            ok = p.notna()
            info[name] = dict(auc=auc(va.y[ok], p[ok]), top10_mean_bp=float(va.net[ok][p[ok] >= p[ok].quantile(.9)].mean()))
        ok = trn & out.frozen.notna().to_numpy() & out.m1_old.notna().to_numpy()
        out[f'{lab}_m1_ranked'] = rank_map(out.m1_old, out.m1_old[ok], out.frozen[ok])
        res['labels'][lab] = info
    out.reset_index(names='event').to_csv(exp/'scores_arms.csv.gz', index=False)
    res = clean(res)
    dump(exp/'fit_summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                     training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False)[:3000])


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_paper_accounts import V3MODE, replay
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_1m_account_label.py')
    acfg = json.loads((ROOT/cfg['accounts_config']).read_text())
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    t, issues = labels(cfg, workers)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    sc = pd.read_csv(exp/'scores_arms.csv.gz').set_index('event')
    # The two fixed-cut arms keep their full original score files, for exact parity with the 20261028 runs.
    sc = sc.join(pd.read_csv(ROOT/cfg['frozen_scores']).set_index('event').causal.rename('frozen_full'), how='outer')
    sc = sc.join(pd.read_csv(ROOT/cfg['scores_1m']).set_index('event').causal.rename('m1_full'), how='left')
    order = pd.read_csv(ROOT/acfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score
    t['order'] = t.event.map(order)
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    sz, cut, hi = acfg['v3_sizing'], acfg['cut'], acfg['hi']
    arms = {'frozen': ('frozen_full', 'frozen_full'), 'm1_fixed': ('m1_full', 'm1_full'), 'm1_ranked': ('v3_m1_ranked', 'spike_m1_ranked'),
            'm1_acct': ('v3_m1_acct', 'spike_m1_acct'), 'base_acct': ('v3_base_acct', 'spike_base_acct')}
    arms.update({f'm1_shuf{k}': (f'v3_m1_shuf{k}', f'spike_m1_shuf{k}') for k in range(cfg['shuffles'])})
    plans = {}
    for arm, (c3, cs) in arms.items():
        s3, ss = t.event.map(sc[c3]), t.event.map(sc[cs])
        v3 = t[(t.kind == 'v3') & (s3 >= cut) & (t['mode'] == V3MODE(s3, hi))].copy()
        v3['k'] = np.minimum(sz['fraction']*(s3[v3.index]-sz['cut'])/sz['mean_excess'], sz['cap'])
        legs = t[(t.kind == 'v3_add') & t.base.isin(v3.trade_id) & (s3 >= hi)]
        legs = legs.assign(k=legs.base.map(v3.set_index('trade_id').k))
        plans[f'v3|{arm}'] = ('linear', v3)
        plans[f'v3add|{arm}'] = ('linear', pd.concat([v3, legs]))
        plans[f'v3add20|{arm}'] = ('frac', pd.concat([v3, legs]))
        plans[f'spike|{arm}'] = ('slots', t[(t.kind == 'spike') & (ss >= hi) & (t.order >= acfg['min_order'])])
        if not arm.startswith('m1_shuf'):
            plans[f'allin3|{arm}'] = ('allin', t[(t.kind == 'v3') & (s3 >= hi) & (t['mode'] == 'opposite_1618')])
    res = dict(checks=dict(label_issues=issues[:20], plan_rows={k: int(len(v)) for k, (_, v) in plans.items()}), plans={})
    with ProcessPoolExecutor(workers) as ex:
        for name, o in ex.map(replay, [(k, s, v.reset_index(drop=True), window, acfg, ro, sh, py) for k, (s, v) in plans.items()]):
            res['plans'][name] = o
    # Parity: the frozen and m1_fixed arms on this run's fills against the 20261028 account runs.
    par = {}
    for arm, src in (('frozen', cfg['parity_frozen']), ('m1_fixed', cfg['parity_m1'])):
        old = json.loads((ROOT/src).read_text())['results']['plans']
        for acct in ('v3', 'v3add', 'v3add20', 'spike'):
            a, b = res['plans'].get(f'{acct}|{arm}', {}), old.get(f'{acct}|A_rest_causal', {})
            par[f'{acct}|{arm}'] = {per: [a.get(per, {}).get('n'), b.get(per, {}).get('n'),
                                          (a.get(per, {}).get('replay') or {}).get('final_median'),
                                          (b.get(per, {}).get('replay') or {}).get('final_median')] for per in ('all', 'y2026')}
    res['parity_vs_20261028'] = par
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False)[:2000])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--prepare', action='store_true'); g.add_argument('--fit', action='store_true'); g.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    prepare(a.config) if a.prepare else fit(a.config, a.workers) if a.fit else summarize(a.config, a.workers)
