"""V34 exact-text audit of VWAP flags, using only hash-frozen source bars.

Uses each bar's raw low/high, base/quote volume and taker buy base/quote.
Derives sell by Decimal subtraction with 80-digit precision. No future bars,
rolling window, threshold fitting or economic outcome. Float VWAP flags are
compared to exact quote-vs-base*price bounds; no bars are changed or excluded.
Source: https://github.com/binance/binance-public-data#futures
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import zipfile
from decimal import Decimal, localcontext
from pathlib import Path

from yoyo.evaluation.binance_flow_coverage import ROOT, HERE, REL, digest, save_json


def classify(raw: list[str]) -> list[dict]:
    """Same-bar unit diagnostics; test flags, never adjust source quantities."""
    with localcontext() as context:
        context.prec = 80
        low, high = Decimal(raw[3]), Decimal(raw[2])
        base, quote, buy_base, buy_quote = (Decimal(raw[i]) for i in (5,7,9,10))
        flags = []
        for side, b, q in [('total',base,quote),('buy',buy_base,buy_quote),('sell',base-buy_base,quote-buy_quote)]:
            if b == 0:
                continue
            vwap = float(q)/float(b)
            float_outside = vwap < float(low) or vwap > float(high)
            excess_quote = max(b*low-q, q-b*high, Decimal(0))
            exact_outside = excess_quote > 0
            if float_outside or exact_outside:
                flags.append(dict(open_time_ms=int(raw[0]), side=side,
                    float_outside=float_outside, exact_outside=exact_outside,
                    classification='exact_source_discrepancy' if exact_outside else 'float_boundary_only',
                    raw_low=str(low),raw_high=str(high),raw_base=str(b),raw_quote=str(q),
                    float_vwap=vwap,excess_quote=str(excess_quote)))
        return flags


def run() -> None:
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    rel = str(Path(__file__).relative_to(ROOT))
    if subprocess.check_output(['git','show',commit+':'+rel],cwd=ROOT)!=Path(__file__).read_bytes():
        raise ValueError('Commit unit audit before reading raw source')
    summary_raw=(HERE/'summary.json').read_bytes()
    manifest_raw=(HERE/'source_manifest.json').read_bytes()
    summary=json.loads(summary_raw)
    manifest=json.loads(manifest_raw)
    config=json.loads((HERE/'config.json').read_text())
    if summary['source_manifest_sha256']!=digest(manifest_raw):
        raise ValueError('Frozen source manifest drift')
    ledger=[]
    zeros=[]
    rows=0
    for receipt,saved in zip(manifest['months'],summary['monthly']):
        month=receipt['month']
        name=f'BTCUSDT-5m-{month}.zip'
        source=ROOT/config['raw_cache']/name
        payload=source.read_bytes()
        if digest(payload)!=receipt['expected_sha256'] or receipt['expected_sha256']!=saved['zip_sha256']:
            raise ValueError('Official source SHA changed')
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            raw=list(csv.reader(io.StringIO(archive.read(f'BTCUSDT-5m-{month}.csv').decode())))
        if raw[0][0].lower() in ('open_time','open time'):
            raw=raw[1:]
        if len(raw)!=saved['valid_bars']:
            raise ValueError('Original row count changed')
        monthly=[]
        for row in raw:
            flags=classify(row)
            monthly.extend(dict(flag,month=month) for flag in flags)
            if Decimal(row[5])==0:
                zeros.append(dict(open_time_ms=int(row[0]),month=month,volume=row[5],quote_volume=row[7],trade_count=row[8]))
        for side in ('total','buy','sell'):
            if sum(flag['side']==side and flag['float_outside'] for flag in monthly)!=saved[side+'_vwap_outside_ohlc_bars']:
                raise ValueError('Float flags do not reproduce original summary')
        ledger.extend(monthly)
        rows+=len(raw)
        if digest(source.read_bytes())!=saved['zip_sha256']:
            raise ValueError('Original source changed during read')
    if rows!=summary['valid_bars'] or len(manifest['months'])!=24 or len(zeros)!=summary['zero_volume_bars']:
        raise ValueError('Population changed')
    result=dict(status='complete',source_commit=commit,source_manifest_sha256=digest(manifest_raw),
        summary_sha256=digest(summary_raw),rows=rows,months=24,
        float_flags=sum(r['float_outside'] for r in ledger),
        unique_float_flagged_bars=len({r['open_time_ms'] for r in ledger if r['float_outside']}),
        exact_source_flags=sum(r['exact_outside'] for r in ledger),
        unique_exact_flagged_bars=len({r['open_time_ms'] for r in ledger if r['exact_outside']}),
        float_only_flags=sum(r['classification']=='float_boundary_only' for r in ledger),
        ledger=ledger,zero_bars=zeros,economic_labels_read=False,bars_adjusted=0,
        interpretation='Exact discrepancies remain unresolved; float-only flags are representation effects, not edited away.')
    if (HERE/'summary.json').read_bytes()!=summary_raw or (HERE/'source_manifest.json').read_bytes()!=manifest_raw:
        raise ValueError('Saved audit inputs changed')
    save_json(HERE/'unit_audit.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('ledger','zero_bars')},ensure_ascii=False))


if __name__=='__main__':
    run()
