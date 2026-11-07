"""Early market exit at the first tradable open after the original hourly reclaim close."""
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_limit_backstop import CODEX, stops
from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
from yoyo.evaluation.range_framework import ROOT, dump

EXP = ROOT / 'experiments/active/exp-live-wick-reclaim-progress-20261107-v1'
STEP = pd.Timedelta(minutes=5)
OHLC = ['open', 'high', 'low', 'close']


def event_fills(args):
    """Use entry-through-confirmation OHLC, followed only by the next candle's observed open.

    Progress uses the completed hourly close and already known Monday boundaries. The
    following bar's extremes and close are never supplied. The 4ns offset separates
    the two ordered market events sharing a real timestamp in the native OHLC adapter.
    """
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

    symbol, source, group, original, threshold = args
    if source == 'extension':
        jobs = json.loads((ROOT/original['extension']/'jobs.json').read_text())
        job = jobs[symbol]
    else:
        job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        raise ValueError(f'{symbol} requires its original five-minute data')
    raw, _ = load_job(job)
    # Use exactly the same tick convention as monday_limit_reclaim.prepare.
    decimals = max(2, min(9, int(np.ceil(-np.log10(raw[OHLC].to_numpy().min()))) + 7))
    tick = 10.**-decimals
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
                        target_from_entry=True, intrabar_path='OHLC', reclaim_next_bar=True,
                        breakeven_includes_cost=False, fill_maker_fee=.0002, fill_taker_fee=.0005,
                        monotone_segments=True, reclaim_backstop=True)
    output = []
    for r in group.itertuples():
        candles = raw.loc[r.entry_t-STEP:r.due-STEP, OHLC].round(decimals).copy()
        expected = pd.date_range(r.entry_t-STEP, r.due-STEP, freq=STEP)
        if not candles.index.equals(expected) or r.due not in raw.index:
            raise ValueError(f'{r.event}: missing market node')
        close = candles.close.iloc[-1]
        opening = round(float(raw.at[r.due, 'open']), decimals)
        if not np.isclose(close, r.confirm_close, rtol=0, atol=tick*.51) or abs(candles.close.iloc[0]-r.entry) > tick*.51:
            raise ValueError(f'{r.event}: checkpoint or entry mismatch')
        progress = (r.confirm_close-r.level)/(r.target-r.level)
        if r.side*(r.target-r.level) <= 0 or not 0 < progress < 1:
            raise ValueError(f'{r.event}: inconsistent live target geometry')
        adverse = candles.low.iloc[1:].min() if r.side == 1 else candles.high.iloc[1:].max()
        mae = 100 * max(0., r.side*(r.entry-adverse), r.side*(r.entry-opening))/r.entry
        candles.index = candles.index + STEP
        candles.loc[r.due+pd.Timedelta(4, unit='ns')] = [opening]*4
        # Use the same outward quantization as the original limit-reclaim setup.
        level_ticks = np.floor(r.level/tick+1e-7) if r.side == 1 else np.ceil(r.level/tick-1e-7)
        orders = pd.DataFrame([dict(trade_id=r.event, entry_i=0, end_i=len(candles)-1,
                                    side=r.side, entry=candles.close.iloc[0], risk=candles.close.iloc[0],
                                    reclaim_ref=level_ticks*tick, target_price=r.target, reclaim_minutes=60)])
        orders['stop'] = stops(orders, 'P', 20, tick)
        rows, _, audit = run_native(candles, np.full(len(candles), np.nan), np.zeros(len(candles)),
                                    orders, policy, tick=tick, symbol=symbol.removesuffix('USDT'))
        native = rows.iloc[0]
        if (native.reclaim_status != 'reclaimed' or native.reclaim_due_ns != r.due.value
                or native.exit_i != len(candles)-1 or native.reason not in (0, 4, 5)
                or audit['positions'] != 1 or audit['fills'] != 2):
            raise ValueError(f'{r.event}: original barriers or checkpoint disagree')
        reason = int(native.reason)
        if reason == 0 and abs(native.exit_price-opening) > tick*.51:
            raise ValueError(f'{r.event}: native market exit differs from open')
        net = native.net_notional_bp - original['slippage_bp']['market_entry']
        if reason != 4:
            net -= original['slippage_bp']['non_target_exit']
        output.append(dict(event=r.event, progress=progress, slow=progress < threshold, due=r.due,
                           next_open=opening, early_exit_price=native.exit_price, early_reason=reason,
                           early_mae_pct=mae, early_fee_bp=native.fee_r*1e4,
                           early_net_bp=net, native_fills=audit['fills'], native_version=audit['version']))
    return output


def fills(cfg):
    from yoyo.layers.l3_backtest.nautilus_independent import ENGINE_VERSION
    cohort = pd.read_csv(ROOT/cfg['cohort'], parse_dates=['entry_t', 'due'])
    eligible = cohort[cohort.eligible]
    accounts = json.loads((ROOT/cfg['accounts']).read_text())['accounts']
    original = json.loads((ROOT/accounts['live-wick6x3']['paths']/'config.json').read_text())
    if len(cohort) != 416 or len(eligible) != 66 or not (cohort.alive_ohlc == cohort.alive_olhc).all():
        raise ValueError('Frozen landmark cohort differs')
    args = [(s, src, g, original, cfg['threshold']) for (s, src), g in eligible.groupby(['symbol', 'source'])]
    with ProcessPoolExecutor(5) as ex:
        data = pd.DataFrame([row for block in ex.map(event_fills, args) for row in block])
    if set(data.event) != set(eligible.event) or data.event.duplicated().any() or not data.native_version.eq(ENGINE_VERSION).all():
        raise ValueError('Incomplete native exit coverage')
    data.sort_values(['due','event']).to_csv(EXP/'fills.csv.gz', index=False)


def stats(x):
    if x.empty:
        return dict(n=0)
    return dict(n=len(x), weeks=int(x.week.nunique()), targets=int(x.reason.eq(4).sum()),
                protected=int(x.reason.eq(5).sum()), protected_profitable=int((x.reason.eq(5)&x.net_bp.gt(0)).sum()),
                original_wins=int(x.net_bp.gt(0).sum()), early_wins=int(x.early_net_bp.gt(0).sum()),
                hold_bp=float(x.net_bp.mean()), early_bp=float(x.early_net_bp.mean()), delta_bp=float(x.delta.mean()),
                median_delta_bp=float(x.delta.median()), improved=int((x.delta>0).sum()),
                fixed_1000u_delta=float(x.delta.sum()*.1))


def inference(x, cfg):
    """Paired hold vs early on the same trade; cluster bootstrap and count-preserving label permutations."""
    rng = np.random.default_rng(cfg['seed'])
    slow, delta = x.slow.to_numpy(bool), x.delta.to_numpy()
    weeks = list(x.groupby('week').indices.values())
    strata = list(x.groupby([x.entry_t.dt.year, x.side, x.vol_bucket]).indices.values())
    mobile = [m for m in strata if slow[m].any() and not slow[m].all()]
    observed = float(delta[slow].mean())
    boot, null = [], []
    for _ in range(cfg['draws']):
        idx = np.concatenate([weeks[i] for i in rng.integers(0, len(weeks), len(weeks))])
        if slow[idx].any():
            boot.append(delta[idx[slow[idx]]].mean())
        mask = slow.copy()
        for m in mobile:
            mask[m] = rng.permutation(slow[m])
        null.append(delta[mask].mean())
    return dict(week_cluster_ci95_bp=np.percentile(boot, [2.5,97.5]).tolist(),
                shuffle_movable_cases=sum(map(len,mobile)), shuffle_strata=len(strata), shuffle_mixed_strata=len(mobile),
                shuffle_null_mean_bp=float(np.mean(null)), shuffle_null_95_bp=np.percentile(null,[2.5,97.5]).tolist(),
                shuffle_one_sided_p=(1+int((np.array(null)>=observed-1e-9).sum()))/(len(null)+1))


def summarize(cfg):
    from yoyo.evaluation.live_wick_progress_accounts import run_accounts
    source = pd.read_csv(ROOT/cfg['cohort'], parse_dates=['entry_t','due'])
    alternative = pd.read_csv(EXP/'fills.csv.gz', parse_dates=['due'])
    x = source[source.eligible].merge(alternative, on=['event','due'], validate='one_to_one')
    tags = pd.read_csv(ROOT/cfg['volatility'], usecols=['event','decision','vol_bucket'], parse_dates=['decision'])
    x = x.merge(tags, left_on=['event','entry_t'], right_on=['event','decision'], validate='one_to_one', how='left')
    if len(x)!=66 or x.vol_bucket.isna().any() or not np.array_equal(x.slow, x.progress<cfg['threshold']):
        raise ValueError('Missing eligible events, volatility tags or progress classification')
    x['delta'] = x.early_net_bp-x.net_bp
    slow = x[x.slow]
    periods = {'all':np.ones(len(x), bool), 'through_2024':x.entry_t.dt.year<=2024, '2025':x.entry_t.dt.year==2025,
               '2026':x.entry_t.dt.year==2026, 'long':x.side==1, 'short':x.side==-1, 'far':x.far, 'funded_6x3':x.funded_6x3}
    groups = {key:{'slow':stats(x[mask & x.slow]), 'fast':stats(x[mask & ~x.slow])} for key,mask in periods.items()}
    whole = dict(n=len(source), changed=len(slow), hold_net_mean_bp=float(source.net_bp.mean()),
                 rule_net_mean_bp=float((source.net_bp.sum()+slow.delta.sum())/len(source)),
                 targets_before=int(source.reason.eq(4).sum()), targets_after=int(source.reason.eq(4).sum()
                        - slow.reason.eq(4).sum()+slow.early_reason.eq(4).sum()),
                 early_open_conflicts=int(x.early_reason.ne(0).sum()))
    out = dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
               universe=whole, groups=groups, comparison=inference(x, cfg), accounts=run_accounts(alternative, cfg['accounts']))
    dump(EXP/'summary.json', out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['fills','summarize'])
    args = parser.parse_args()
    paths = ['yoyo/evaluation/live_wick_reclaim_progress.py', str((EXP/'config.json').relative_to(ROOT))]
    if args.stage == 'summarize':
        paths.append('yoyo/evaluation/live_wick_progress_accounts.py')
    committed(*paths)
    cfg = json.loads((EXP/'config.json').read_text())
    (fills if args.stage == 'fills' else summarize)(cfg)


if __name__ == '__main__':
    main()
