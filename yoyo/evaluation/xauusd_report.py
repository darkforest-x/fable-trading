"""Build the Chinese XAUUSD research report from completed frozen artifacts.

This builder never downloads quotes, executes a strategy, changes a nomination,
or reads raw minute prices. Inputs are audited study summaries and trade ledgers.
The builder itself must be committed before use. Markdown is immediately
converted by scripts/md_to_html.py; charts use only saved daily equity and do
not replace the engine's common one-minute-close drawdown measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/active/exp-xauusd-system-search-20260908-v1'
RESULTS = EXPERIMENT / 'results'
MD = ROOT / 'analysis/p0_xauusd_system_search_20260908.md'
HTML = ROOT / 'analysis/html/p0_xauusd_system_search_20260908.html'
EXPECTED_STUDY_COMMIT = 'b1204cb'
MAIN_TEST_COUNT = 58
CARRY_TEST_COUNT = 1
REQUIRED = ('selection_all.csv', 'final_summary.csv', 'nomination.json',
            'source_metadata.json', 'coverage.json', 'completion.json',
            'selection_daily_equity.csv.gz', 'final_daily_equity.csv.gz')


def clean(value: Any) -> Any:
    """Return JSON-safe scalars without inventing values for missing evidence."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def fmt(value: Any, digits: int = 2, suffix: str = '', signed: bool = False) -> str:
    parsed = number(value)
    if parsed is None:
        return 'NA'
    return format(parsed, f'{"+" if signed else ""},.{digits}f') + suffix


def pformat(value: Any) -> str:
    parsed = number(value)
    return 'NA' if parsed is None else f'{parsed:.4g}'


def text(value: Any) -> str:
    if value is None or value is pd.NA or value is pd.NaT:
        return 'NA'
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return 'NA'
    return str(value).replace('|', '／').replace('\n', ' ').strip()


def table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return 'NA：没有对应记录。\n'
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |',
                      *['| ' + ' | '.join(text(v) for v in row) + ' |' for row in rows]]) + '\n'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def tf(value: Any) -> str:
    parsed = number(value)
    if parsed is None:
        return 'NA'
    minutes = int(parsed)
    if minutes == 0:
        return '不适用'
    return '1D' if minutes == 1440 else f'{minutes//60}h' if minutes % 60 == 0 else f'{minutes}m'


def key(candidate: str, duration: int) -> str:
    return f'{candidate}_{duration}m'


def name(candidate: str, duration: int, cfg: dict) -> str:
    passive = {'BUY_HOLD': '买入持有', 'CASH': '现金'}
    if candidate in passive:
        return passive[candidate]
    label = cfg.get('candidates', {}).get(candidate, {}).get('name', candidate)
    return f'{candidate} · {label} · {tf(duration)}'


def select_row(frame: pd.DataFrame, candidate: str, duration: int,
               stage: str | None = None) -> dict:
    mask = frame.candidate.eq(candidate) & frame.timeframe.eq(duration)
    if stage is not None:
        mask &= frame.stage.eq(stage)
    found = frame.loc[mask]
    if len(found) != 1:
        raise ValueError(f'Expected exactly one {stage or "selection"} {candidate}/{duration}; got {len(found)}')
    return found.iloc[0].to_dict()


def performance_rows(frame: pd.DataFrame, cfg: dict, roles: dict | None = None) -> list:
    rows = []
    for row in frame.to_dict('records'):
        identifier = (str(row['candidate']), int(row['timeframe']))
        role = (roles or {}).get(identifier, '')
        rows.append([role or name(*identifier, cfg), fmt(row.get('return_pct'), suffix='%'),
                     fmt(row.get('max_drawdown_pct'), suffix='%'), fmt(row.get('trades'), 0),
                     fmt(row.get('winrate_pct'), suffix='%'), pformat(row.get('paired_p')),
                     pformat(row.get('holm_p'))])
    return rows


def read_curve(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0)
    frame.index = pd.to_datetime(frame.index, utc=True, errors='raise')
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f'Invalid equity time index: {path}')
    return frame


def equity_chart(stage: str, chosen: list[tuple[str, int]], selection: pd.DataFrame,
                 final: pd.DataFrame, cfg: dict) -> Path | None:
    """Plot saved daily closes; never derive or relabel these as minute MDD."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    begin, end = (pd.Timestamp(v, tz='UTC') for v in cfg[stage])
    colors = ['#138C80', '#6876BC', '#D29340', '#34445C']
    fig, axis = plt.subplots(figsize=(11.8, 5.0), dpi=150)
    fig.patch.set_facecolor('#FAFBFC')
    axis.set_facecolor('#FAFBFC')
    for i, (candidate, duration) in enumerate(chosen):
        if candidate == 'CASH':
            continue
        column = key(candidate, duration)
        source = selection if stage == 'selection' and candidate != 'BUY_HOLD' else final
        column = column if source is selection else f'{stage}_{"BUY_HOLD" if candidate == "BUY_HOLD" else column}'
        if column not in source:
            raise ValueError(f'Missing saved curve {column}')
        series = pd.to_numeric(source[column], errors='raise').dropna()
        series = series.loc[(series.index >= begin) & (series.index < end)]
        if series.empty:
            continue
        axis.plot(series.index, series.to_numpy(), label=f'{candidate} {tf(duration) if duration else ""}',
                  color=colors[i % len(colors)], linewidth=1.9 if i == 0 else 1.4,
                  linestyle='--' if candidate == 'BUY_HOLD' else '-')
    axis.axhline(1, color='#8A9099', linewidth=1, linestyle=':', label='CASH')
    axis.set(title=f'{stage.upper()} | {cfg[stage][0]} to {cfg[stage][1]} (end exclusive)',
             ylabel='Net account equity (initial capital = 1)')
    axis.grid(alpha=.18)
    axis.spines[['top', 'right']].set_visible(False)
    axis.legend(frameon=False, loc='best', ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = RESULTS / f'report_{stage}_equity.png'
    fig.savefig(path, bbox_inches='tight')
    plt.close(fig)
    return path


def ledger_evidence(path: Path, label: str) -> tuple[str, list]:
    """Describe realized tail examples and a static financing break-even proxy."""
    if not path.is_file():
        return f'### {label}\n\nNA：交易账本缺失。\n', [label, 'NA', 'NA', 'NA', 'NA']
    ledger = pd.read_csv(path)
    needed = {'net_pnl', 'net_bp', 'notional', 'holding_seconds'}
    if len(ledger) and not needed.issubset(ledger):
        raise ValueError(f'Incomplete trade ledger: {path}')
    days = float((ledger.notional * ledger.holding_seconds / 86400).sum()) if len(ledger) else 0.
    pnl = float(ledger.net_pnl.sum()) if len(ledger) else 0.
    breakeven = pnl / days * 10000 if days > 0 else None
    finance = [label, fmt(len(ledger), 0), fmt(days, 4), fmt(pnl*100, suffix='pp'), fmt(breakeven, 4)]
    blocks = [f'### {label}\n\n完整账本：[CSV]({path})。以下仅为事后解释收益来源，不参与信号、score或选优。\n']
    for positive in (True, False):
        subset = ledger.loc[ledger.net_pnl > 0] if positive else ledger.loc[ledger.net_pnl < 0]
        subset = subset.sort_values('net_pnl', ascending=not positive, kind='stable').head(5)
        blocks.append(('最大5笔盈利' if positive else '最大5笔亏损') + '（按对初始账户资金的净贡献排序）：\n')
        rows = []
        for row in subset.to_dict('records'):
            rows.append([row.get('trade_id'), '多' if row.get('side') == 1 else '空',
                         row.get('entry_time'), fmt(row.get('entry_price'), 5),
                         row.get('exit_time'), fmt(row.get('exit_price'), 5),
                         fmt(row.get('net_bp'), 2), fmt(number(row.get('net_pnl'))*100 if number(row.get('net_pnl')) is not None else None, 3),
                         fmt(number(row.get('holding_seconds'))/86400 if number(row.get('holding_seconds')) is not None else None, 2),
                         row.get('exit_reason')])
        blocks.append(table(['编号', '方向', '入场UTC', '模型入场价', '退出UTC', '模型退出价',
                             '单笔净bp', '账户净贡献pp', '持有天数', '退出类型'], rows))
    return '\n'.join(blocks), finance


def build(no_plots: bool = False) -> dict:
    """Require complete receipts before writing any report or chart artifact."""
    missing = [str(RESULTS / item) for item in REQUIRED if not (RESULTS / item).is_file()]
    if missing:
        raise FileNotFoundError('Study is not complete: ' + ', '.join(missing))
    self_path = Path(__file__).resolve()
    relative = str(self_path.relative_to(ROOT))
    subprocess.run(['git', 'ls-files', '--error-unmatch', relative], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', relative], cwd=ROOT, check=True)
    builder_commit = subprocess.check_output(['git', 'log', '-1', '--format=%H', '--', relative], cwd=ROOT, text=True).strip()
    builder_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    cfg = load_json(EXPERIMENT / 'preregistration.json')
    nomination = load_json(RESULTS / 'nomination.json')
    metadata = load_json(RESULTS / 'source_metadata.json')
    coverage = load_json(RESULTS / 'coverage.json')
    completion = load_json(RESULTS / 'completion.json')
    selection = pd.read_csv(RESULTS / 'selection_all.csv')
    final = pd.read_csv(RESULTS / 'final_summary.csv')
    required_cols = {'candidate', 'timeframe', 'return_pct', 'max_drawdown_pct', 'trades'}
    if not required_cols.issubset(selection) or not (required_cols | {'stage'}).issubset(final):
        raise ValueError('Required result columns are missing')
    if len(selection) != 189 or completion.get('selection_count') != len(selection):
        raise ValueError('The full 189-candidate selection family is required')
    if selection.duplicated(['candidate', 'timeframe']).any():
        raise ValueError('Duplicate selection configuration')
    if nomination.get('selection_table_sha256') != sha(RESULTS / 'selection_all.csv'):
        raise ValueError('Selection table no longer matches the frozen nomination')
    if completion.get('nomination_sha256') != sha(RESULTS / 'nomination.json'):
        raise ValueError('Nomination no longer matches completion receipt')
    source_commit = completion.get('source', {}).get('commit', '')
    if not source_commit.startswith(EXPECTED_STUDY_COMMIT):
        raise ValueError(f'Unexpected study source receipt: {source_commit}')
    if nomination.get('source_freeze', {}).get('commit') != source_commit:
        raise ValueError('Nomination and completion use different compute sources')
    best = (str(nomination['candidate']), int(nomination['timeframe']))
    imacd = (str(nomination['best_imacd']['candidate']), int(nomination['best_imacd']['timeframe']))
    picked = list(dict.fromkeys([best, imacd, ('C02', 240), ('BUY_HOLD', 0), ('CASH', 0)]))
    winner = select_row(final, *best, 'confirmation')
    selected = select_row(selection, *best)
    buy = select_row(final, 'BUY_HOLD', 0, 'confirmation')
    for identifier in picked:
        select_row(final, *identifier, 'confirmation')
    roles: dict[tuple[str, int], str] = {}
    for identifier, role in [(best, '冻结主动冠军'), (imacd, '冻结IMACD族冠军'),
                             (('C02', 240), '固定原密集系统'), (('BUY_HOLD', 0), '买入持有'), (('CASH', 0), '现金')]:
        roles[identifier] = roles.get(identifier, '') + ('／' if identifier in roles else '') + role
    roles = {identifier: role + '：' + name(*identifier, cfg) for identifier, role in roles.items()}
    r, bh = number(winner.get('return_pct')), number(buy.get('return_pct'))
    verdict = ('确认期没有交易，无法据此确认主动系统有效。' if number(winner.get('trades')) == 0 else
               '冻结冠军在确认期仍为正收益。' if r is not None and r > 0 else
               '冻结冠军在确认期未取得正收益。')
    relative_result = ('同期高于买入持有' if r is not None and bh is not None and r > bh else
                       '同期低于买入持有' if r is not None and bh is not None and r < bh else
                       '同期与买入持有相同' if r is not None and bh is not None else '与买入持有的差异为NA')
    confirmation = final.loc[final.stage.eq('confirmation')].copy()
    order = {identifier: i for i, identifier in enumerate(picked)}
    confirmation['_order'] = [order.get((str(a), int(b)), 999) for a, b in zip(confirmation.candidate, confirmation.timeframe)]
    confirmation = confirmation.sort_values(['return_pct','max_drawdown_pct','_order'], ascending=[False,True,True])
    curve_selection, curve_final = read_curve(RESULTS / REQUIRED[6]), read_curve(RESULTS / REQUIRED[7])
    charts = [] if no_plots else [equity_chart(stage, picked, curve_selection, curve_final, cfg)
                                for stage in ('confirmation', 'selection')]
    plots = {stage: path for stage, path in zip(('confirmation', 'selection'), charts) if path is not None}
    parts = ['# XAUUSD：有限交易系统搜索与冻结后确认\n',
             f'**在本轮实际比较的规则和账户中，买入持有的选优期收益及2026确认期收益最高。**确认期买入持有净收益为 **{fmt(bh, suffix="%")}**，一分钟最大回撤为 **{fmt(buy.get("max_drawdown_pct"), suffix="%")}**。主动规则的具体表现、持仓延续与空仓起步区别如下。\n',
             f'本次先在2024–2025年对21套规则×9周期、共189个主动候选选优，再固定配置进行2026年前五个月的空仓账户收益确认。**{verdict}**\n',
             f'冻结主动冠军为 **{name(*best, cfg)}**：确认期净账户收益 **{fmt(r, suffix="%")}**，一分钟收盘盯市最大回撤 **{fmt(winner.get("max_drawdown_pct"), suffix="%")}**，{relative_result}；同期买入持有为 **{fmt(bh, suffix="%")}**。这是本次预先固定候选库的比较，不是所有可能交易系统的全球最高收益证明。\n',
             f'确认范围为 **[{cfg["confirmation"][0]}, {cfg["confirmation"][1]}) UTC**；实际最后一分钟收盘为 **{text(completion.get("latest_minute_close"))}**。未使用2026确认结果重新挑选冠军。\n',
             '## 1. 优先看冻结后确认\n',
             table(['角色与固定配置', '净账户收益', '分钟收盘最大回撤', '交易笔数', '胜率', '月份配对p', '189项Holm'],
                   performance_rows(confirmation, cfg, roles)),
             '本表只包括事前冻结的主动冠军、IMACD族冠军、C02/4h和固定被动对照；同配置兼任多个角色时只计算一次。确认期并未运行全部189个候选，Holm列NA不能解释成p=0。\n']
    sparse = [(name(str(z['candidate']), int(z['timeframe']), cfg), number(z.get('trades')))
              for z in confirmation.to_dict('records') if z['candidate'] not in ('BUY_HOLD', 'CASH')
              and number(z.get('trades')) is not None and number(z.get('trades')) <= 2]
    if sparse:
        parts.append('**交易稀少：**' + '；'.join(f'{label} 为 {fmt(count, 0)} 笔' for label, count in sparse)
                     + '。0笔表示本次从空仓启动后没有信号，资金留在现金，不能验证大趋势新入场能力；1–2笔也不足以确认可重复盈利。\n')
    if number(selected.get('trades')) is not None and number(selected.get('trades')) <= 2:
        parts.append(f'选优冠军的两年收益也只来自 **{fmt(selected.get("trades"), 0)} 笔**，不可把收益百分比当作大量独立机会的证据。PF缺失不能硬转成0；无亏损造成的无穷PF也不代表稳定性。\n')
    if 'confirmation' in plots:
        parts.append(f'![确认期已保存净值曲线]({plots["confirmation"]})\n\n曲线为已保存的日末净值；上表最大回撤取同一真实1分钟收盘网格，不从这张日线图重新计算。\n')
    carry_path, carry_curve_path = RESULTS / 'carry_diagnostic.json', RESULTS / 'carry_daily_equity.csv'
    if carry_path.is_file():
        carry = load_json(carry_path)
        if carry.get('nomination_sha256') != sha(RESULTS / 'nomination.json'):
            raise ValueError('Carry diagnostic uses a different nomination')
        parts += ['### 事后解释：已经拿住的旧仓与新账户不同\n',
                  '**这是看到空仓确认结果后追加的持仓延续诊断，不属于首次独立样本外验收，不重新排名。**它保留选优期历史持仓，在2026开始时按当时权益归一化，检查继续持有的分段结果。\n',
                  table(['配置', '2026分段净收益%', '分钟收盘最大回撤%', '跨年带入仓位', '2026新入场'],
                        [[name(str(carry.get('candidate')), int(carry.get('timeframe', 0)), cfg), fmt(carry.get('return_pct')),
                          fmt(carry.get('max_drawdown_pct')), fmt(carry.get('carried_positions'), 0), fmt(carry.get('fresh_entries_in_2026'), 0)]]),
                  f'同一2026区间买入持有为 {fmt(bh, suffix="%")}，最大回撤 {fmt(buy.get("max_drawdown_pct"), suffix="%")}。该诊断回答“原来已经持有，能否继续拿住”，不能替代“2026才部署，能否及时发现新趋势”。[诊断记录]({carry_path})；[保存的分段日末净值]({carry_curve_path})。\n']
    overall = nomination.get('best_including_passive', {})
    parts += ['## 2. 选优结果：只能作为选优样本\n',
              f'2024–2025主动冠军净账户收益为 {fmt(selected.get("return_pct"), suffix="%")}，最大回撤为 {fmt(selected.get("max_drawdown_pct"), suffix="%")}。纳入现金、买入持有后，该阶段登记的最优选项为 **{name(str(overall.get("candidate", "NA")), int(overall.get("timeframe", 0)), cfg)}**。选优区间已经参与排名，不能称为冠军完全独立的最终样本外证据。\n',
              '各周期各取既定排序下第一名；没有根据确认期重新选周期：\n']
    ranked = selection.sort_values(['return_pct', 'max_drawdown_pct', 'trades', 'candidate', 'timeframe'],
                                   ascending=[False, True, True, True, True], kind='stable')
    tied=selection.loc[np.isclose(selection.return_pct,selected['return_pct'],rtol=0,atol=1e-10)&
                       np.isclose(selection.max_drawdown_pct,selected['max_drawdown_pct'],rtol=0,atol=1e-10)&
                       selection.trades.eq(selected['trades'])]
    parts.append(f'共有 **{len(tied)}** 个同收益、同回撤、同笔数的主动候选并列：'+
                 '、'.join(f'{z.candidate}/{tf(z.timeframe)}' for z in tied.itertuples())+
                 '。F01由预登记的ID次序破同胜出；这些日线版本实际为同一笔多单，不能将利润归因于新增均线密集或多周期条件。\n')
    leaders = ranked.groupby('timeframe', sort=True).head(1).sort_values('timeframe')
    parts.append(table(['周期', '候选', '规则', '净账户收益%', '最大回撤%', '笔数', '配对p', 'Holm'],
                       [[tf(z['timeframe']), z['candidate'], z.get('name'), fmt(z.get('return_pct')),
                         fmt(z.get('max_drawdown_pct')), fmt(z.get('trades'), 0), pformat(z.get('paired_p')), pformat(z.get('holm_p'))]
                        for z in leaders.to_dict('records')]))
    comparison = []
    for identifier in picked:
        if identifier[0] == 'BUY_HOLD':
            comparison.append(select_row(final, *identifier, 'selection'))
        elif identifier[0] == 'CASH':
            comparison.append(select_row(final, *identifier, 'selection'))
        else:
            comparison.append(select_row(selection, *identifier))
    parts.append(table(['同选优期对照', '净账户收益', '最大回撤', '笔数', '胜率', '配对p', 'Holm'],
                       performance_rows(pd.DataFrame(comparison), cfg, roles)))
    if 'selection' in plots:
        parts.append(f'![选优期固定配置净值曲线]({plots["selection"]})\n')
    parts += [f'完整189项全部保留：[selection_all.csv]({RESULTS / "selection_all.csv"})。完整确认与开发回放：[final_summary.csv]({RESULTS / "final_summary.csv"})。排名依据和冻结时间见[提名记录]({RESULTS / "nomination.json"})。\n',
              '## 3. 固定冠军周期的IMACD机制对照\n',
              f'以下只比较同一个 **{tf(best[1])}** 周期的2024–2025数据，避免把周期切换冒充条件改善。Δ为相对登记父规则的账户收益百分点变化；只是归因线索，不是确认期新选优。\n']
    mechanism = selection.loc[selection.timeframe.eq(best[1]) & selection.candidate.str.startswith(('C', 'F', 'N'))].sort_values('candidate')
    lookup = {z['candidate']: z for z in mechanism.to_dict('records')}
    mechanism_rows = []
    for row in mechanism.to_dict('records'):
        candidate_name = row['candidate']
        parent = cfg['candidates'][candidate_name].get('parent')
        pr = lookup.get(parent, {})
        delta = number(row.get('return_pct')) - number(pr.get('return_pct')) if number(row.get('return_pct')) is not None and number(pr.get('return_pct')) is not None else None
        mode = '组合参考，非单变量' if candidate_name in ('C01', 'C03') else '独立起点' if parent is None else '相对登记父规则的单项变更'
        mechanism_rows.append([candidate_name, row.get('name'), parent or '—', mode,
                               fmt(row.get('return_pct')), fmt(delta, signed=True), fmt(row.get('max_drawdown_pct')),
                               fmt(row.get('trades'), 0), fmt(row.get('excess_bp')), pformat(row.get('holm_p'))])
    parts.append(table(['规则', '定义', '父规则', '比较性质', '净收益%', 'Δ收益pp', '最大回撤%', '笔数', '配对超额bp', 'Holm'], mechanism_rows))
    parts += ['C01同时改变入场与退出，C03是多条件组合参考；不把其差异解释为一个因素的贡献。F/N系列按预登记父子关系展示，未把最好的一笔交易倒推成规则。\n',
              '### 宽松高周期许可与严格同向\n',
              'F03与F04只改变高周期许可门。下表覆盖全部预定周期，保留变好与变差的情况；这是选优样本中的机制解释，未做独立确认，不能据此追加一个后验冠军。\n']
    gate_rows = []
    for duration in cfg['timeframes']:
        permissive, strict = select_row(selection, 'F03', duration), select_row(selection, 'F04', duration)
        delta = number(strict.get('return_pct')) - number(permissive.get('return_pct'))
        gate_rows.append([tf(duration), fmt(permissive.get('return_pct')), fmt(strict.get('return_pct')),
                          fmt(delta, signed=True), fmt(permissive.get('trades'), 0), fmt(strict.get('trades'), 0),
                          fmt(permissive.get('max_drawdown_pct')), fmt(strict.get('max_drawdown_pct'))])
    parts += [table(['周期', 'F03净收益%', 'F04净收益%', '严格门Δpp', 'F03笔数', 'F04笔数', 'F03最大回撤%', 'F04最大回撤%'], gate_rows),
              '宽松许可允许高周期md同向、动量同向或md处在零轴，严格许可只接受md同向；严格门不保证每个周期改善。若多个日线候选的交易记录完全相同，就没有证据把同一笔利润分别归因于密集和共振。\n',
              '## 4. 随机对照、月份推断与固定score\n',
              '每笔实际接受的交易匹配3个同XAUUSD、同决策UTC月、同因果波动桶、同方向的非信号入场；bar＋方向不复用。先冻结配对身份，再计算候选同侧退出和20bp成本。配不齐3笔就不进入配对均值，并明确显示覆盖率。这是事件层对照，不是可直接投资的随机组合。\n']
    diagnostics = [('选优', select_row(selection, *identifier)) for identifier in picked if identifier[0] not in ('BUY_HOLD', 'CASH')]
    diagnostics += [('确认', z) for z in confirmation.to_dict('records') if z['candidate'] not in ('BUY_HOLD', 'CASH')]
    parts.append(table(['期间/配置', '匹配/实际', '匹配实际均净bp', '随机均净bp', '交易加权超额bp', '等权月均超额bp', '月均95%区间bp', '配对p', 'Holm'],
                       [[f'{stage} {z["candidate"]}/{tf(z["timeframe"])}', f'{fmt(z.get("matched_n"), 0)}/{fmt(z.get("actual_n"), 0)}',
                         fmt(z.get('matched_actual_mean_net_bp')), fmt(z.get('control_mean_net_bp')), fmt(z.get('excess_bp')),
                         fmt(z.get('monthly_mean_excess_bp')), f'{fmt(z.get("ci_low"))} 至 {fmt(z.get("ci_high"))}',
                         pformat(z.get('paired_p')), pformat(z.get('holm_p'))] for stage, z in diagnostics]))
    parts += ['**单位不能混用：**账户收益是百分比，单笔与配对收益是bp（1bp＝0.01%）。`excess_bp`先对每笔的3个控制求均值、再对交易加权；p和区间对应**等权月份配对均值**。月份数≤12用精确符号翻转，否则1999次；区间按月份重采样。189项选优p统一Holm，NA项仍计入计划family。月份可交换／符号对称是假设，不能把该p称为随机试验因果证明。\n',
              '固定score为决策时的 abs(md)/ATR，没有拟合；AUC标签为该笔净收益是否为正。前10%按score排序，同分按决策顺序和交易ID稳定破同，**不是按已实现收益挑前10%**。单类、没有有效score或没有匹配时显示NA。\n',
              table(['期间/配置', '固定score AUC', 'score前10%笔数', '前10%均毛bp', '前10%均净bp', '前10%胜率%', '推断状态'],
                    [[f'{stage} {z["candidate"]}/{tf(z["timeframe"])}', fmt(z.get('score_auc'), 4), fmt(z.get('score_top_decile_n'), 0),
                      fmt(z.get('score_top_decile_gross_bp')), fmt(z.get('score_top_decile_net_bp')),
                      fmt(z.get('score_top_decile_win_pct')), z.get('inference_status', 'NA')] for stage, z in diagnostics]),
              '上述指标仅用于解释；冠军排序优先账户净收益及最大回撤。若实际交易触发破产／权益地板，线性控制口径不再可比，配对p和区间置NA而保留原始明细。\n']
    failed_path = RESULTS / 'failed_attempt1.json'
    failed = load_json(failed_path) if failed_path.exists() else {}
    failed2_path = RESULTS / 'failed_attempt2.json'
    failed2 = load_json(failed2_path) if failed2_path.exists() else {}
    archives = metadata.get('archives', [])
    duplicate_count = sum(number(a.get('duplicate_exact_removed')) or 0 for a in archives) if archives else None
    parts += ['## 5. 数据统计与质量截断\n',
              table(['项目', '实际记录'], [[k, v] for k, v in [
                  ('源', text(metadata.get('provider')) + ' / ' + text(metadata.get('symbol')) + ' / ' + text(metadata.get('price_side'))),
                  ('执行券商', metadata.get('execution_venue')), ('真实分钟行数', fmt(metadata.get('rows'), 0)),
                  ('档案数量', fmt(metadata.get('archive_count'), 0)), ('首个分钟开盘UTC', metadata.get('time_min')),
                  ('末个分钟收盘UTC', metadata.get('time_close_max')), ('保留的零volume行', fmt(metadata.get('zero_volume_rows'), 0)),
                  ('完全相同重复行删除量', fmt(duplicate_count, 0)), ('非连续分钟间隔数', fmt(metadata.get('nonconsecutive_minute_gaps'), 0)),
                  ('原始分钟文件落盘数', fmt(completion.get('raw_minute_files_written'), 0)),
                  ('数据源时区', metadata.get('source_timezone')), ('协议修订', cfg.get('protocol_revision'))]]),
              '2012年起的历史只提供固定指标预热，保证2020年之后含周线高周期的340根预热。无计划休市补价、不合成ASK；数据源是BID档案，并非已核实执行券商的同源逐笔记录。质量分类只作排查提示，档案存在不代表全部预期市场分钟齐备。\n',
              f'首轮原计划覆盖至2026年8月底，在202606档案发现26个冲突分钟而中止；失败记录为 `{text(failed.get("error"))}`，该轮经济收益计算次数为 **{fmt(failed.get("economic_runs"), 0)}**。随后v1b只将确认截止改为2026-06-01，保留候选、选优窗口、成本及排名。未择优保留冲突报价，也未拿之后收益决定截断。[失败记录]({failed_path})\n',
              f'第二轮在合并统计字段时因重复cost_bp中止：已完成 {fmt(failed2.get("selection_simulations_completed"), 0)} 个选择期模拟，保存结果行数 {fmt(failed2.get("selection_rows_saved"), 0)}，确认期模拟 {fmt(failed2.get("confirmation_simulations"), 0)}，没有提名。修复仅限字段合并并新增合成端到端测试；正式完成结果的源码为 `{source_commit}`。[第二轮记录]({failed2_path})\n',
              table(['周期', '全部bar数（含预热）', '选优bar数', '首bar', '末bar关闭', 'bar内真实分钟中位数', '低于半桶观察量bar数'],
                    [[tf(z.get('timeframe')), fmt(z.get('total_bars'), 0), fmt(z.get('selection_bars'), 0), z.get('first'), z.get('last_close'),
                      fmt(z.get('median_observed_minutes'), 1), fmt(z.get('low_coverage_bars'), 0)] for z in coverage])]
    gap_path = RESULTS / 'quote_gaps.csv.gz'
    if gap_path.exists():
        gaps = pd.read_csv(gap_path)
        hint_count = lambda col: int(gaps[col].astype(str).str.lower().isin(['true', '1']).sum()) if col in gaps else None
        parts.append(table(['间隔审计提示', '数量'], [['全部不连续间隔', fmt(len(gaps), 0)],
                          ['可能周末', fmt(hint_count('possible_weekend'), 0)], ['可能日内休市', fmt(hint_count('possible_daily_closure'), 0)],
                          ['未分类', fmt(hint_count('unclassified'), 0)],
                          ['无观察的时钟分钟总数（包含休市）', fmt(gaps.missing_minutes.sum() if 'missing_minutes' in gaps else None, 0)]]))
        parts.append(f'以上“可能”标签不是交易所日历认证，不能把全部间隔自动判为数据丢失或正常休市。[逐段间隔记录]({gap_path})\n')
    quality_path = RESULTS / 'data_quality_audit.json'
    if quality_path.is_file():
        quality = load_json(quality_path)
        periods = quality.get('periods', {})
        parts += ['### 时间覆盖仍有实质限制\n',
                  '**reader结构检查通过，只说明可解析、OHLC和重复冲突检查通过，不是完整市场覆盖保证。**以下无报价时钟分钟含休市及假日，不能直接称为丢失的可交易分钟。\n',
                  table(['区间', '真实行数', '不连续间隔数', '无报价时钟分钟（含闭市）', '常见周末外≥120分间隔', '同日白天≥60分诊断间隔'],
                        [[label, fmt(z.get('observed_rows'), 0), fmt(z.get('all_gap_count'), 0), fmt(z.get('all_absent_calendar_minutes'), 0),
                          fmt(z.get('other_large_count'), 0), fmt(z.get('same_day_01_to_21_utc_weekday_gaps_at_least_60_min_count'), 0)]
                         for period, label in [('selection_2024_2025', '2024–2025选优'), ('confirmation_2026_jan_may', '2026年1–5月确认'), ('development_2023', '2023开发回放')]
                         if (z := periods.get(period))]),
                  f'2023同日白天诊断子集有 {fmt(periods.get("development_2023", {}).get("same_day_01_to_21_utc_weekday_gaps_at_least_60_min_count"), 0)} 段，累计 {fmt(periods.get("development_2023", {}).get("same_day_01_to_21_utc_weekday_gaps_at_least_60_min_absent_minutes"), 0)} 分钟，不能把该年回放视为完整行情上的业绩。2024–2025仍有周五提前结束和少量日内断档，可能改变信号与成交；尚未量化对收益的影响。2026年1–5月没有该诊断定义下的同类反复白天断档，仍不证明完整。\n',
                  f'常见周末之外的长间隔也包括未经券商日历核实的假日候选，不能一律判故障。逐段时间、分类理由与限制见[独立质量审计]({quality_path})。没有填补、删除这些间隔，也没有因审计结果重选策略。\n']
    else:
        parts.append('独立时间覆盖审计尚无产物；这里只能引用quote_gaps的观察提示，不能宣称已保证数据完整。\n')
    parts.append(f'源档案哈希、行数与逐包记录见[source_metadata.json]({RESULTS / "source_metadata.json"})；SHA256为本地计算值，不冒充发布方签名。\n')
    ledger_blocks, financing, ledger_paths = [], [], []
    for stage, label in [('selection', '选优期主动冠军'), ('confirmation', '确认期冻结主动冠军')]:
        suffix = '.csv.gz' if stage == 'selection' else '.csv'
        ledger_path = RESULTS / f'{stage}_{key(*best)}_trades{suffix}'
        ledger_paths.append(ledger_path)
        block, row = ledger_evidence(ledger_path, label)
        ledger_blocks.append(block)
        financing.append(row)
    parts += ['## 6. 盈亏来自哪些具体交易\n', *ledger_blocks,
              '### 持仓时间与融资压力近似\n',
              table(['账本', '笔数', '累计名义本金×日', '账户净贡献', '盈亏平衡融资bp/日'], financing),
              '累计名义本金×日＝Σ(入场名义本金×实际持有秒数/86400)，名义本金以初始账户资金1归一化。盈亏平衡融资＝Σ净盈亏/Σ名义本金日×10000；负值表示需要净返息才可能打平。这里只是固定交易路径的粗压力近似：没有真实券商结息时点、方向差异、三倍计息日，也未重算收费后仓位反馈，不能作为实际历史swap成本。\n',
              '## 7. 执行、复现与验证\n',
              f'研究源码冻结：`{source_commit}`（登记版本 `{EXPECTED_STUDY_COMMIT}`；较早的1c38855在第二轮字段合并失败后由此版替代）。报告builder提交：`{builder_commit}`；生成时HEAD：`{builder_head}`。提名时间：`{text(nomination.get("created_at"))}`；运行完成：`{text(completion.get("completed_at"))}`。主研究合成验证记录为 **{MAIN_TEST_COUNT}项测试通过**，加持仓延续边界测试 **{CARRY_TEST_COUNT}项**，共 **{MAIN_TEST_COUNT + CARRY_TEST_COUNT}项**；本生成器不重新运行策略或替代验证记录。\n',
              '每次入场名义仓位为当前权益/(1+0.001)，入场和退出各按该初始名义本金扣10bp；持仓不再平衡。信号收盘后第一个真实分钟开盘成交，退出先于同刻新入场。fold末按最后完整分钟收盘行政结算，不删除未退出交易；实际账户因破产归零时有独立标记。全部周期共用一分钟收盘盯市口径。\n',
              '完整新运行命令（仅适用于尚无该次nomination的结果状态，已有结果不覆盖）：\n',
              '```bash\ncd /Users/zhangzc/fable-trading\n'
              'git show b1204cb:yoyo/evaluation/xauusd_search.py > /dev/null\n'
              '.venv/bin/python -m pytest -q tests/data/test_xauusd_histdata.py tests/evaluation/test_xauusd_systems.py tests/evaluation/test_xauusd_execution.py tests/evaluation/test_xauusd_controls.py tests/evaluation/test_xauusd_search.py tests/evaluation/test_xauusd_carry_audit.py\n'
              '.venv/bin/python -i -u -m yoyo.evaluation.xauusd_search\n```\n',
              '主研究结束后，在同一个Python交互会话中复用仍在内存的已验证报价，完成诊断与分钟复核；不重新下载或保存原始分钟：\n\n'
              '```python\nfrom yoyo.evaluation.xauusd_carry_audit import run as carry_run\ncarry_run(LAST_DATA)\n'
              'from yoyo.evaluation.xauusd_mdd_audit import run as mdd_run\nmdd_run(LAST_DATA)\nexit()\n```\n'
              '返回终端后生成其余审计与报告：\n\n```bash\n'
              '.venv/bin/python -m yoyo.evaluation.xauusd_gap_quality --results-dir experiments/active/exp-xauusd-system-search-20260908-v1/results --source-commit 53f83cc\n'
              '.venv/bin/python -m yoyo.evaluation.xauusd_result_review\n'
              '.venv/bin/python -m yoyo.evaluation.xauusd_report\n```\n',
              '已有完成产物时只需重建报告；不会重新消耗经济确认：\n\n```bash\n.venv/bin/python -m yoyo.evaluation.xauusd_report\n```\n',
              '研究脚本会验证源码已入库并保存逐文件哈希；发现既有nomination即拒绝覆盖。重新下载的档案若哈希或质量改变，必须作为数据版本变化报告，不得声称旧结果已逐字复现。Markdown写入后立即调用项目md_to_html生成自包含HTML。\n',
              '## 8. 风险与诚实声明\n',
              '- 本次最高仅限21套固定规则×9周期；没有证明全球最优，也没有根据确认结果追加参数搜索。\n'
              '- 2024–2025参与排名，存在选优偏差；2020–2023仅对提名主动冠军做事后描述，不能冒充一轮独立walk-forward选优。\n'
              '- 2026行情此前用于图表和样式QA；首轮数据质量检查也读取过2026。这里是冻结后的首次经济确认，不是从未见过行情的严格盲测。\n'
              f'- 当前冻结确认配置的经济验收次数：每项 **{fmt(completion.get("confirmation_holdout_economic_use_each"), 0)}**；质量读取与经济评分分开记录。[完成receipt]({RESULTS / "completion.json"})\n'
              '- 只有BID历史档案＋固定入场名义本金往返20bp，不含实际ASK、测得的历史价差、滑点、隔夜融资或保证金制度；这不是券商真实全成本业绩。\n'
              '- 最大回撤只观察真实一分钟收盘，不包含分钟内极值、无报价时段的连续风险；日末净值图不能替代一分钟回撤。\n'
              '- 固定EST UTC−5分桶及桶末决策时钟不承诺与OANDA／TradingView交易日逐bar一致。\n'
              '- 随机对照属于事件层，月度推断有依赖结构假设；交易稀少、单类或配对不足时NA是证据不足，不是零风险。\n'
              '- 未训练、未promote、未修改生产或TradingView交易逻辑，未下单；原始分钟及下载ZIP仅在内存处理，磁盘保存的是研究产物和数据审计元信息。\n',
              '## 9. 下一步\n',
              '先结合确认期净收益、同期间买入持有差异、最大回撤和样本数判断是否值得继续。下一轮若修改规则、成本或新增候选，应另立冻结协议；真实券商ASK／融资费用接入、未覆盖月份的数据修复和任何生产接入均应作为明确的新决策，不回写本轮结果。\n']
    review_path,mdd_path=RESULTS/'result_review.json',RESULTS/'minute_mdd_audit.json'
    if review_path.is_file() and mdd_path.is_file():
        review,mdd=load_json(review_path),load_json(mdd_path)
        if review.get('status')!='passed' or mdd.get('status')!='passed':
            raise ValueError('Independent audit failed; refuse completion report')
        parts += ['## 10. 独立复核记录\n',
                  f'独立核对 {review["ledger_files"]} 份实际账本、{review["trade_rows"]:,} 笔交易与 {review["control_pair_rows"]:,} 条随机对照。原运行的 {review["reported_runner_ledger_checks"]:,} 项账本检查可重算；权益算术最大绝对浮差 {review["max_equity_math_abs_error"]:.3g}。[排名、账本与对照复核]({review_path})\n',
                  f'另使用留在RAM中的原始分钟、按持仓账本独立重建 {mdd["portfolios"]} 条分钟权益路径；最大回撤的最大差值 {mdd["max_mdd_abs_error"]:.3g} 个百分点。此次复核不重新选择规则，不把日末回撤当分钟回撤。[分钟回撤复核]({mdd_path})\n',
                  f'选优阶段有 {review["holm_finite_tests"]} 个有限p值，覆盖全部189项的Holm校正后，p<0.01的项数为 {review["holm_p_below_001"]}。第一名无完整3对照配对，不能从这个结果声明已验证策略超额。\n']
    content = '\n'.join(parts).strip() + '\n'
    MD.parent.mkdir(parents=True, exist_ok=True)
    MD.write_text(content, encoding='utf-8')
    # Required repository ordering: render immediately after the markdown write.
    subprocess.run([sys.executable, 'scripts/md_to_html.py', str(MD.relative_to(ROOT)),
                    '--out-dir', 'analysis/html'], cwd=ROOT, check=True)
    used = [RESULTS / item for item in REQUIRED] + [EXPERIMENT / 'preregistration.json']
    used += [path for path in (failed_path, failed2_path, gap_path, quality_path, carry_path, carry_curve_path,review_path,mdd_path,*ledger_paths) if path.is_file()]
    receipt = dict(generated_at=datetime.now(timezone.utc).isoformat(), builder_commit=builder_commit,
                   builder_sha256=sha(self_path), study_commit=source_commit,
                   inputs={str(path.relative_to(ROOT)): sha(path) for path in used},
                   outputs={str(path.relative_to(ROOT)): sha(path) for path in [MD, HTML, *plots.values()]},
                   no_strategy_execution=True, no_raw_minute_read=True)
    (RESULTS / 'report_build_receipt.json').write_text(json.dumps(clean(receipt), ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    manifest_path = RESULTS / 'artifact_manifest.json'
    artifacts = sorted(path for path in RESULTS.rglob('*') if path.is_file() and path != manifest_path)
    artifacts += [MD, HTML]
    manifest = dict(generated_at=datetime.now(timezone.utc).isoformat(),
                    experiment_id=cfg.get('experiment_id'), source_compute_commit=nomination['source_freeze']['commit'],
                    report_builder_commit=builder_head, report_builder_source_commit=builder_commit,
                    production_eligible=False, training_eligible=False,
                    artifacts=[dict(source_path=str(path.relative_to(ROOT)), sha256=sha(path), size_bytes=path.stat().st_size)
                               for path in artifacts])
    manifest_path.write_text(json.dumps(clean(manifest), ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-plots', action='store_true', help='Use saved equity CSV links without rendering PNGs')
    args = parser.parse_args()
    result = build(no_plots=args.no_plots)
    print(json.dumps(clean(result), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
