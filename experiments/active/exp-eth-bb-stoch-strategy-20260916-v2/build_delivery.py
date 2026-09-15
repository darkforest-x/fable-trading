"""Build v2 strategy copy pages and a no-order native preview probe."""
from pathlib import Path
import hashlib
import json
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
copy_page = runpy.run_path(str(ROOT / 'experiments/active/exp-eth-bb-stoch-indicator-20260915-v1/build_delivery.py'), run_name='copy_template')['copy_page']
source = (ROOT / 'yoyo/evaluation/pine/eth_bb_stoch_strategy_v2.pine').read_text()
needle = 'bool selfTest = input.bool(false,'
assert source.count(needle) == 1
probe = source.replace(needle, 'bool selfTest = input.bool(true,', 1)
(HERE / 'native_selftest.pine').write_text(probe)
(HERE / 'native_selftest.html').write_text(copy_page(probe, 'BB Stoch v2 · 合成检查'))
(ROOT / 'analysis/html/eth_bb_stoch_strategy_v2_code.html').write_text(copy_page(source, 'ETH 5m · BB × Stoch 策略 v2'))
receipt = {'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'native_probe_sha256': hashlib.sha256(probe.encode()).hexdigest(), 'probe_delta': 'selfTest input default true only', 'native_probe_orders': False}
(HERE / 'source_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
