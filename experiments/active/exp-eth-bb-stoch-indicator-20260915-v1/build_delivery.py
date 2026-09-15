"""Build copy pages and a chart-data-free native probe from the canonical Pine.

Only runSelfTest's input default differs in the probe; no price data is read.
"""
from pathlib import Path
import hashlib
import html
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / 'yoyo/evaluation/pine/eth_bb_stoch_reversal_v1.pine'

def copy_page(code: str, title: str) -> str:
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:16px system-ui;margin:40px auto;max-width:1000px;padding:0 24px;color:#203036;background:#f7f9fa}button{background:#008f82;color:white;border:0;border-radius:8px;padding:12px 22px;font-size:16px;cursor:pointer}textarea{display:block;width:100%;height:65vh;box-sizing:border-box;margin-top:20px;padding:16px;border:1px solid #ccd6da;border-radius:8px;font:13px monospace;background:white;white-space:pre}</style><title>''' + html.escape(title) + '''</title><h1>''' + html.escape(title) + '''</h1><p>TradingView → Pine 编辑器 → 新建指标 → 粘贴完整源码 → 添加到图表。</p><button onclick="navigator.clipboard.writeText(document.querySelector('textarea').value).then(()=>this.textContent='已复制').catch(()=>{document.querySelector('textarea').select();this.textContent='请按 ⌘C / Ctrl+C'})">复制完整源码</button><textarea readonly aria-label="Pine 源码">''' + html.escape(code) + '</textarea></html>'

if __name__ == '__main__':
    source = SOURCE.read_text()
    needle = 'bool runSelfTest = input.bool(false,'
    assert source.count(needle) == 1
    probe = source.replace(needle, 'bool runSelfTest = input.bool(true,', 1)
    (HERE / 'native_selftest.pine').write_text(probe)
    (HERE / 'native_selftest.html').write_text(copy_page(probe, 'BB Stoch 合成检查源码'))
    (ROOT / 'analysis/html/eth_bb_stoch_reversal_v1_code.html').write_text(copy_page(source, 'ETH 5m · BB × Stoch 指标源码'))
    receipt = {'source_path': str(SOURCE.relative_to(ROOT)), 'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'probe_sha256': hashlib.sha256(probe.encode()).hexdigest(), 'probe_difference': 'runSelfTest input default true only'}
    (HERE / 'source_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))
