"""Render every funded allin3 historical trade with the existing review renderer.

Owner 2026-10-28 requests all images in one folder. Scope is exactly the 336
funded trades of the saved 1.75m path, excluding cash refills and unfunded
candidates. Reuse native prices, costs, timing, sources and chart semantics;
do not replay transactions or alter the historical lookahead warning.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pandas as pd
from PIL import Image

from yoyo.evaluation import allin3_review_charts as review

ROOT = review.ROOT
OUT = review.A / 'exp-allin3-all-trade-charts-20261028-v1'
DELIVERY = OUT / 'allin3_336笔交易图'
CAUSE = {0: '样本末结算', 1: '兜底止损', 4: '目标止盈', 5: '保护止损', 11: '确认失败'}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
        default=lambda v: v.item() if hasattr(v, 'item') else str(v)))


def render_symbol(item):
    symbol, records = item
    source = review.load_source(symbol)
    result = [review.draw_sample(row, source, DELIVERY, OUT/'source-bars') for row in records]
    save(OUT/'progress'/f'{symbol}.json', result)
    return result


def run(workers):
    for p in ['yoyo/evaluation/allin3_all_trade_charts.py', 'yoyo/evaluation/allin3_review_charts.py',
              str((OUT/'config.json').relative_to(ROOT))]:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or subprocess.run(
            ['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit renderer and config before rendering')
    for p in [DELIVERY, OUT/'progress', OUT/'source-bars']:
        p.mkdir(parents=True, exist_ok=True)
    ledger, funded, records = review.load(all_trades=True)
    rows = pd.DataFrame(records)
    assert len(rows) == rows.event.nunique() == rows.file.nunique() == 336
    assert set(rows.event) == set(funded.event)
    np.testing.assert_allclose(rows.pnl.sum(), ledger.pnl.sum(), atol=1e-7)
    rows.to_csv(OUT/'trades.csv', index=False)
    checks = []
    jobs = []
    for symbol, g in rows.groupby('symbol', sort=True):
        receipt = OUT/'progress'/f'{symbol}.json'
        if receipt.exists():
            old = json.loads(receipt.read_text())
            if set(c['event'] for c in old) == set(g.event) and all((DELIVERY/c['file']).exists()
                and (OUT/'source-bars'/(Path(c['file']).stem+'-ohlcv.csv.gz')).exists() for c in old):
                checks.extend(old)
                continue
        jobs.append((symbol, g.to_dict('records')))
    print(f'Funded trades 336; symbols {rows.symbol.nunique()}; cached {len(checks)}; pending symbols {len(jobs)}', flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(render_symbol, item): item[0] for item in jobs}
        errors = []
        for fut in as_completed(futures):
            symbol = futures[fut]
            try:
                result = fut.result()
                checks.extend(result)
                print(f'{len(checks)}/336 complete: {symbol}', flush=True)
            except Exception as exc:
                error = dict(symbol=symbol, error=repr(exc))
                errors.append(error)
                save(OUT/'progress'/f'{symbol}-error.json', error)
                print(f'ERROR {symbol}: {exc!r}', flush=True)
        if errors:
            save(OUT/'errors.json', errors)
            raise RuntimeError(f'{len(errors)} symbol render errors; see errors.json')
    assert len(checks) == 336 and {c['event'] for c in checks} == set(rows.event)
    actual = {p.name for p in DELIVERY.glob('[0-9][0-9][0-9][0-9]_*.png')}
    assert actual == set(rows.file)
    for file in rows.file:
        with Image.open(DELIVERY/file) as im:
            assert im.size == (2240, 1400)
            im.verify()
    shutil.copy2(review.OUT/'timeline.png', DELIVERY/'0000_资金时间线.png')
    with Image.open(DELIVERY/'0000_资金时间线.png') as im:
        assert im.size == (2240, 1540)
        im.verify()
    index = pd.DataFrame({
        '序号': rows.number, '图片': rows.file, '币种': rows.symbol,
        '方向': rows.side.map({1: '多', -1: '空'}),
        '入场账本时间_北京时间': pd.to_datetime(rows.entry_t, utc=True).dt.tz_convert('Asia/Shanghai'),
        '退出账本时间_北京时间': pd.to_datetime(rows.exit_t, utc=True).dt.tz_convert('Asia/Shanghai'),
        '入场价': rows.entry, '退出价': rows.exit_price, '目标价': rows.target_price,
        '退出原因': rows.reason.map(CAUSE), '旧评分': rows.score,
        '名义本金净收益百分比': rows.net_bp/100, '账户盈亏U': rows.pnl,
        '账户入场前余额U': rows.before, '账户退出后余额U': rows.equity,
        '事件ID': rows.event, '前视说明': '旧评分含成交K线未来信息；历史结果不构成策略盈利证据',
    })
    index.to_csv(DELIVERY/'交易索引.csv', index=False, encoding='utf-8-sig')
    result = dict(trades=len(rows), symbols=int(rows.symbol.nunique()), images=337,
        wins=int((rows.pnl > 0).sum()), losses=int((rows.pnl < 0).sum()),
        first_entry=rows.entry_t.iloc[0], last_exit=rows.exit_t.iloc[-1],
        pnl_sum=rows.pnl.sum(), ending_equity=ledger.equity.iloc[-1],
        refills=int((ledger.cashflow > 0).sum()), cash_added=ledger.cashflow.sum(),
        source_ledger=str((review.PATHS/'allin3-ledger.csv.gz').relative_to(ROOT)),
        delivery=str(DELIVERY), checks=checks,
        total_image_bytes=sum(p.stat().st_size for p in DELIVERY.glob('*.png')),
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        training_eligible=False, production_eligible=False)
    save(OUT/'delivery.json', result)
    print(json.dumps({k:v for k,v in result.items() if k!='checks'}, ensure_ascii=False, default=str), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=2)
    run(ap.parse_args().workers)
