"""Explain the saved allin3 1.75m endpoint with source-backed static figures.

Owner request 2026-10-28: strategy logic, capital timeline and sample candles.
Read saved capital events and native fills only; do not run a trade engine,
change fees, optimize sampling, or touch paper state. The historical score is
known to contain lookahead. Candle context is post-trade review, not an input
to the strategy. Monday anchors use only the completed NY Monday OHLC.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / 'experiments/active'
OUT = A / 'exp-allin3-review-20261028-v1'
PATHS = A / 'exp-strategy-booklet-paths-20261027-v1'
NATIVE = A / 'exp-monday-limit-v3-levels-20261025-v1'
STEP = pd.Timedelta(minutes=5)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2,
        default=lambda v: v.item() if hasattr(v, 'item') else str(v)))


def setup():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Heiti SC', 'Arial Unicode MS'],
        'axes.unicode_minus': False, 'figure.facecolor': 'white', 'axes.facecolor': 'white',
        'font.size': 12, 'text.color': '#263747', 'axes.labelcolor': '#263747'})
    return plt


def load():
    ledger = pd.read_csv(PATHS / 'allin3-ledger.csv.gz')
    ledger['date'] = pd.to_datetime(ledger.time_ms, unit='ms', utc=True)
    ledger['before'] = ledger.equity.shift(1)
    assert ledger.time_ms.is_monotonic_increasing
    np.testing.assert_allclose(ledger.equity, 1000+ledger.pnl.cumsum()+ledger.cashflow.cumsum(), atol=1e-7)
    native = pd.read_csv(PATHS / 'native-fills.csv.gz')
    native = native[native.variant == 'opp1618_P30']
    funded = ledger[ledger.notional > 0].merge(native, left_on='trade_id', right_on='event',
        suffixes=('', '_native'), validate='one_to_one')
    assert len(funded) == 336 and len(ledger) == 342
    np.testing.assert_allclose(funded.net_bp, funded.net, atol=1e-7)
    # Fixed descriptive roles, not a random sample or a performance estimate.
    winners = funded[(funded.reason == 4) & (funded.side == 1)].sort_values(['net_bp', 'event'])
    failures = funded[(funded.reason == 11) & (funded.side == -1)].sort_values(['net_bp', 'event'])
    selected = [funded.iloc[-1], funded.loc[funded.pnl.idxmin()],
                winners.iloc[len(winners)//2], failures.iloc[len(failures)//2]]
    roles = ['期末结算 / 最大金额盈利', '最大金额亏损', '做多止盈 / 收益率中位样本', '做空确认失败 / 收益率中位样本']
    rows = []
    for i, (r, role) in enumerate(zip(selected, roles), 1):
        versions = []
        for path in ('ohlc', 'olhc'):
            p = NATIVE / path / 'symbols' / r.symbol / 'trades.csv.gz'
            d = pd.read_csv(p)
            d = d[(d.variant == 'opp1618_P30') &
                  (d.trade_id.str.split(':').str[:3].str.join(':') == r.event)]
            assert len(d) == 1
            q = d.iloc[0].to_dict()
            q['net'] = q['net_notional_bp'] - (0 if q['reason'] == 4 else 10)
            q['source'] = str(p.relative_to(ROOT)); q['path'] = path
            versions.append(q)
        q = versions[1] if versions[1]['net'] < versions[0]['net'] else versions[0]
        assert abs(q['net'] - r.net_bp) < 1e-7
        assert pd.Timestamp(q['entry_time']) == pd.Timestamp(r.entry_t)
        assert pd.Timestamp(q['exit_time']) == pd.Timestamp(r.exit_t)
        row = r.to_dict()
        row.update({k: q[k] for k in ['entry', 'stop', 'target_price', 'exit_price', 'entry_i', 'exit_i', 'reclaim_status', 'source', 'path']})
        row.update(number=i, role=role, file=f'{i:02d}_{r.symbol}.png', track='allin3')
        rows.append(row)
    return ledger, funded, rows


def timeline(ledger, rows):
    plt = setup()
    from matplotlib import dates as mdates
    from matplotlib.ticker import FuncFormatter
    dates = ledger.date.dt.tz_convert('Asia/Shanghai')
    dd = -100*(1-ledger.equity/ledger.equity.cummax())
    fig, axes = plt.subplots(3, 1, figsize=(16, 11), dpi=140,
        gridspec_kw={'height_ratios': [3.6, 1.4, 1.25]}, sharex=True)
    fig.subplots_adjust(left=.085, right=.97, top=.80, bottom=.15, hspace=.18)
    ax = axes[0]
    ax.step(dates, ledger.equity, where='post', color='#236c9c', lw=1.6)
    ax.set_yscale('log'); ax.set_ylim(50, 3_000_000)
    ax.set_yticks([100, 1000, 10000, 100000, 1000000], ['100', '1,000', '1万', '10万', '100万'])
    ax.set_ylabel('已实现余额 U（对数刻度）')
    refills = ledger[ledger.cashflow > 0]
    ax.scatter(refills.date, refills.equity, marker='D', color='#b88224', s=48, zorder=6,
               label='补足至 1,000U（5次，属于入金）')
    ax.legend(loc='upper left', frameon=False, fontsize=11)
    for date, label, offset in [
        ('2023-12-31', '2023末 9,658U', (0, 20)),
        ('2024-12-31', '2024末 10,408U', (-45, 25)),
        ('2025-12-31', '2025末 296,962U', (-130, 22)),
        ('2026-03-28 11:00Z', '跌至 94.47U，再次补款', (-50, -27)),
        ('2026-09-24', '期末 1,754,717U', (-160, 10))]:
        ts = pd.Timestamp(date)
        ts = ts.tz_localize('UTC') if ts.tzinfo is None else ts
        r = ledger[ledger.date <= ts].iloc[-1]
        if '94.47' in label:
            r = ledger.loc[dd.idxmin()]
        ax.annotate(label, (r.date, r.equity), xytext=offset, textcoords='offset points', fontsize=11,
            arrowprops={'arrowstyle': '-', 'color': '#778796'}, bbox={'fc': 'white', 'ec': 'none', 'alpha': .88})
    axes[1].step(dates, dd, where='post', color='#a44655', lw=1.1)
    axes[1].fill_between(dates, dd, 0, step='post', color='#a44655', alpha=.12)
    axes[1].set_ylim(-105, 5); axes[1].set_yticks([-100, -50, 0]); axes[1].set_ylabel('余额回撤 %')
    axes[1].text(.015, .12, f'最大已实现余额回撤 {abs(dd.min()):.4f}%（没有日内浮亏路径）', transform=axes[1].transAxes, fontsize=11)
    axes[2].step(dates, 1000+ledger.cashflow.cumsum(), where='post', color='#b88224', lw=1.6)
    axes[2].set_ylim(0, 6500); axes[2].set_yticks([1000, 3000, 5527.237], ['1,000', '3,000', '5,527'])
    axes[2].set_ylabel('累计净入金 U')
    ticks = pd.to_datetime(['2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01', '2026-09-23'], utc=True)
    axes[2].set_xticks(ticks, ['2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01', '2026-09-23'])
    for a in axes:
        a.set_xlim(dates.iloc[0], dates.iloc[-1]+pd.Timedelta(days=20))
        a.grid(axis='y', color='#e2e7ec', lw=.6)
        for s in ['top', 'right']: a.spines[s].set_visible(False)
        for s in ['bottom', 'left']: a.spines[s].set_color('#cbd4dc')
        a.tick_params(labelsize=11)
    fig.text(.085, .945, 'allin3：1000U → 175万U 的原始历史时间线', fontsize=23, weight='bold')
    fig.text(.085, .902, '旧评分存在前视，该175万结果不能作为有效收益证据；本图仅解释历史数字，不代表当前模拟盘。', fontsize=13, color='#a44655')
    fig.text(.085, .855, '336笔成交 · 胜率 33.3% · 3倍全仓复利 · 5次补款 · 实际净入金合计 5,527U', fontsize=13)
    fig.text(.085, .090, '最后两笔贡献 1,013,958U（期末余额的57.8%）；其中 ON 的522,745U来自样本末强制结算，并未触及目标。', fontsize=12)
    fig.text(.085, .052, '北京时间；台阶仅在平仓/入金时变化。原账本BE0，当前模拟盘BE +0.17%；原手续费与滑点全部保留。', fontsize=10.5, color='#647382')
    fig.savefig(OUT/'timeline.png'); plt.close(fig)
    return float(abs(dd.min()))


def draw_sample(row):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.evaluation.paper_2026_trade_charts import panel, display_bars, CAUSE
    from matplotlib.ticker import FuncFormatter
    plt = setup()
    r = SimpleNamespace(**row)
    job = json.loads((A/'exp-monday-limit-reclaim-20261024-v1/symbols'/r.symbol/'identity.json').read_text())['job']
    stamps = []
    for name in job['paths']:
        p = Path(name)
        if not p.exists(): raise FileNotFoundError(f'Registered source unavailable: {p}')
        stamps.append(dict(path=name, size=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns))
    f, _ = load_job(job)
    et, xt = f.index[int(r.entry_i)], f.index[int(r.exit_i)]
    assert et+STEP == pd.Timestamp(r.entry_t)
    assert pd.Timestamp(r.exit_t) in (xt, xt+STEP)
    for time, price in [(et, r.entry), (xt, r.exit_price)]:
        bar = f.loc[time]
        assert bar.low-price*1e-7 <= price <= bar.high+price*1e-7, (r.event, time, price)
    r.entry_bar, r.exit_bar = str(et), str(xt)
    monday = pd.Timestamp(r.event.split(':')[1], tz='America/New_York')
    mon = f.loc[(f.index >= monday) & (f.index < monday+pd.Timedelta(days=1))]
    assert len(mon) in (276, 288, 300)
    high, low = float(mon.high.max()), float(mon.low.min())
    level = low-.618*(high-low) if r.side == 1 else high+.618*(high-low)
    target = high+.618*(high-low) if r.side == 1 else low-.618*(high-low)
    assert np.isclose(level, r.entry, rtol=1e-6) and np.isclose(target, r.target_price, rtol=1e-6)
    raw = f.loc[et-STEP*30:xt+STEP*12]
    minutes = next(m for m in [5, 15, 60, 240, 1440] if len(raw)/(m/5) <= 210)
    main = display_bars(raw, minutes)
    fig = plt.figure(figsize=(16, 10), dpi=140)
    if minutes > 5:
        gs = fig.add_gridspec(1, 2, width_ratios=[1, 1.55], left=.065, right=.95, bottom=.31, top=.73, wspace=.21)
        frames = [(fig.add_subplot(gs[0]), f.loc[et-STEP*30:et+STEP*30], 5, '入场细节 · 原始5分钟K线'),
                  (fig.add_subplot(gs[1]), main, minutes, f'完整持仓 · {minutes}分钟展示聚合')]
    else:
        frames = [(fig.add_axes([.065, .31, .885, .42]), main, 5, '完整持仓 · 原始5分钟K线')]
    validations = []
    for ax, frame, step, title in frames:
        validations.append(panel(ax, frame, r, step, title))
        for label, value in [('周一高', high), ('周一低', low)]:
            if ax.get_ylim()[0] <= value <= ax.get_ylim()[1]:
                ax.axhline(value, color='#8795a3', ls=':', lw=.8)
                ax.text(len(frame), value, label, fontsize=9, color='#647382')
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.6g}'))
    assert any(v['entry_visible'] and v['exit_visible'] for v in validations)
    direction = '做多' if r.side == 1 else '做空'
    fig.text(.065, .950, f'{r.number:02d}  {r.symbol} · {direction}   |   {r.role}', fontsize=20, weight='bold')
    fig.text(.065, .908, '旧版回测复盘：成交K线评分含前视；历史BE0。此图不是可复现收益能力的证明。', fontsize=12, color='#a44655')
    fig.text(.065, .862, f'{CAUSE[int(r.reason)]}    名义本金净收益 {r.net_bp/100:+.2f}%   × 3倍 = 账户 {100*r.pnl/r.before:+.2f}%', fontsize=15)
    fig.text(.065, .820, f'本笔盈亏 {r.pnl:+,.2f}U    余额 {r.before:,.2f} → {r.equity:,.2f}U    旧评分 {r.score:.4f}', fontsize=13)
    fmt = lambda x: f'{x:.7g}'
    fig.text(.065, .248, f'周一范围 {fmt(low)} — {fmt(high)}    1.618入场 {fmt(r.entry)}    对侧1.618目标 {fmt(r.target_price)}', fontsize=12)
    protection_state = '旧BE0，已激活' if r.reclaim_status == 'reclaimed' else '计划价，未激活'
    fig.text(.065, .208, f'初始止损 {fmt(r.stop)}    确认后保护 {fmt(r.entry)}（{protection_state}）    实际退出 {fmt(r.exit_price)}', fontsize=12)
    when = lambda t: t.tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')
    fig.text(.065, .165, f'入场所在5m：{when(et)} — {(et+STEP).tz_convert("Asia/Shanghai").strftime("%H:%M")}    退出所在5m：{when(xt)}', fontsize=11)
    if r.reason in (0, 11):
        note = f'账本退出时刻：{when(pd.Timestamp(r.exit_t))}；'+('样本结束强制结算，目标尚未到达。' if r.reason == 0 else '确认K线未收回入场线，按收盘退出。')
    else:
        note = 'E/X标在原始成交K线；具体成交秒数未知，同K顺序沿用原框架较差路径。'
    fig.text(.065, .123, note, fontsize=11, color='#647382')
    fig.text(.065, .080, '蓝虚线=入场，金点线=目标，红虚线=兜底止损，紫点划线=确认后保护，灰点线=周一高低；远离画面价位只列数值。', fontsize=10, color='#647382')
    fig.text(.065, .045, 'Binance历史行情；北京时间。maker 0.02% / taker 0.05%，非目标退出另扣0.10%滑点；未计资金费。', fontsize=10, color='#647382')
    fig.savefig(OUT/r.file); plt.close(fig)
    raw.to_csv(OUT/(Path(r.file).stem+'-ohlcv.csv.gz'))
    return dict(event=r.event, file=r.file, source_native=r.source, inputs=stamps, panels=validations,
                monday_low=low, monday_high=high, entry_bar=str(et), exit_bar=str(xt),
                prices_in_native_bars=True, monday_levels_match=True)


def main():
    for p in ['yoyo/evaluation/allin3_review_charts.py', str((OUT/'config.json').relative_to(ROOT))]:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or subprocess.run(
            ['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit renderer/config before generating')
    ledger, funded, rows = load()
    dd = timeline(ledger, rows)
    checks = [draw_sample(row) for row in rows]
    pd.DataFrame(rows).to_csv(OUT/'selected-trades.csv', index=False)
    summary = dict(start=1000, end=ledger.equity.iloc[-1], trading_pnl=ledger.pnl.sum(),
        cash_added=ledger.cashflow.sum(), total_cash_in=1000+ledger.cashflow.sum(),
        refill_count=int((ledger.cashflow > 0).sum()), stake_budget=ledger.stake_budget.sum(),
        funded=len(funded), win_pct=100*(funded.pnl > 0).mean(), realized_balance_max_dd_pct=dd,
        terminal_settlement_pnl=funded[funded.reason == 0].pnl.sum(),
        last_two_pnl=funded.tail(2).pnl.sum(),
        source_ledger=str((PATHS/'allin3-ledger.csv.gz').relative_to(ROOT)),
        causal_summary='experiments/active/exp-monday-limit-paper-accounts-20261028-v1/summary.json',
        sampling='Purposive: final settlement, largest monetary loss, median net-rate long target, median net-rate short confirmation failure.',
        known_invalidity='Touch-bar score lookahead; historical BE0 differs from current BE17bp. In sample; no matched-random funded account comparison.',
        charts=checks, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        training_eligible=False, production_eligible=False)
    save('manifest.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'charts'}, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
