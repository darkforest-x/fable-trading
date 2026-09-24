# Cross-platform signal parity includes metadata authority

- **问题**：ISRG-USDT-SWAP 30m produced backend short event `5300ec9405ab95353af46f15` at 2026-09-24 16:30 Beijing, confirmation close 395.56, while native TradingView V12.8 showed no arrow. Both Bark and TG receipts were sent once.
- **死胡同**：Matching names, Pine source hashes or backend replay do not establish native parity. The earlier GEV wording correction only clarified provenance. Hardcoding ISRG/stock-symbol exclusions or inventing a Pine base-asset fallback would change semantics without demonstrating equivalence.
- **有效路径**：Native desktop V12.8 on OKX:ISRGUSDT.P 30m displayed `crypto / 未知`. Minimal and only-joint were false; confirmation display and short-plot style were enabled. Pine reads `syminfo.basecurrency` and rejects unknown; Python accepts OKX `uly`/`baseCcy` resolving to ISRG. A single-variable replay kept the same 1,364 closed candles and tick 0.01: base `ISRG` reproduced the exact short, base `None` removed it with `base_asset_unknown`. MA/MD/SB remained unchanged. Thus metadata authority is a sufficient discriminator, independent of YOLO.
- **通用规则**：Treat the identity provider and unknown-value semantics as strategy inputs alongside OHLCV. To claim Pine parity, reconcile both metadata and causal historical state; do not turn “unknown here, known elsewhere” into automatic admission. This diagnosis is not a completed parity fix. The native panel's additional **latest raw confirmation** BB rejection was not tied to the target bar and must not be cited as that bar's BB verdict.
- **牵连**：`yoyo/monitor/v9_worker.py:17`, `v9_signals.py`, `v128_signals.py`, `yoyo/evaluation/spike_v9.py`, `yoyo/evaluation/pine/spike_burst_v12_8.pine:564`. Four monitor module startup hashes matched disk. No algorithm, notification gate, subscription or service state changed; native numeric parity remains unmeasured in this ISRG check. Desktop restored to prior BTC1H view. Raw evidence is `data/research/spike_v128_isrg_parity_20260924/{closed_prefix,event,counterfactual,receipt}.json`; receipt SHA-256 `eda1ca2643b746add6a55e3eb1a960233e597d4ed933003e2d7ba992fc74c482`.

Minimal reproduction against the saved prefix:

```python
import json
from pathlib import Path
from yoyo.monitor.v128_signals import analyze
root = Path('data/research/spike_v128_isrg_parity_20260924')
candles = json.loads((root / 'closed_prefix.json').read_text())
event = json.loads((root / 'event.json').read_text())
for base in ('ISRG', None):
    result = analyze(candles, None, '30m', tick=.01, base_asset=base)
    print(base, result['chart'][-1]['v9_reason'],
          [x['price'] for x in result['events']
           if x['bar_close_ms'] == event['bar_close_ms']])
# ISRG passed [395.56]
# None base_asset_unknown []
```
