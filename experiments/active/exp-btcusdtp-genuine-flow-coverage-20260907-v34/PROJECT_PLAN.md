# Genuine Flow Coverage · V34

## Scope frozen before source reads

Owner: “那开始下一步啊，开始目标模式”. This continues V33's proposed real-input
coverage audit, not an economic experiment or deployment. Inspect BTCUSDT USD-M
5-minute monthly archives for bar opens in [2023-01-01, 2025-01-01) UTC only.
Do not open other cached months, outcomes, holdout, or OKX execution data.
Python 3.9.6, NumPy 2.0.2 and pandas 2.3.3 remain unchanged.

## Single change and source contract

V33 had synthetic parser tests only. V34 applies that unchanged parser to the
24 named real monthly archives. First freeze official `.CHECKSUM` receipts and
all source URLs, then read only the exact cached ZIPs. Hash mismatch is a failure,
not permission to overwrite the old cache. Missing local ZIP may be downloaded
from the same official URL into this experiment's immutable data directory.
No alternate venue or proxy substitutes are allowed.

Official source: https://github.com/binance/binance-public-data#futures
USD-M columns explicitly include taker buy base/quote volumes. Sell = total-buy;
delta = buy-sell in each matching unit. This is neither open interest nor capital
inflow. A candle's price direction is not a taker-flow label.

## Checks and interpretation

1. Source-first guard: committed runner, unchanged V33 parser, legacy parser and
   frozen config, then official source receipts. Original ZIPs stay read-only.
2. Every month: SHA/member/header/epoch/OHLCV/flow constraints via V33; independently
   compare the exact expected 5-minute calendar including month edges. A successful
   parse is NOT a coverage pass. Missing archives remain unknown, not zero-volume.
3. Persist missing timestamps and per-month expected/observed counts, zero-volume
   rows, null/duplicate counts, complete 12-bar hourly buckets and clock bounds.
4. Describe same-bar candle/flow sign disagreement and flow balance distribution
   only; do not fit a threshold, compare future returns or select favorable dates.
5. Round-trip float64 CSV with round-trip reader and exact frame/dtype equality;
   hash both original ZIP and new output. End-of-window bar open is exclusive,
   but its earliest complete boundary may equal 2025-01-01 exactly.
6. Synthetic counterexamples: missing first/middle/last bars, duplicate or shifted
   clock, changed flow with same OHLCV, missing checksum, changed source hash.
   They replace economic random controls for this non-directional input audit.

Quality statuses: `complete` only with all validated bars; `gapped` for validated
data with missing grid points; `unknown`/`invalid` for unverified sources. Separate
source integrity, calendar completeness and live delivery suitability. Historical
archive availability is retrospective; theoretical close boundaries are not
historical observed network delivery. No live-ready claim.

## Deliverable and report contract

Question: can genuine trade-direction data support the next separately frozen
trend-confluence experiment? Audience: product stakeholders; portable HTML only.
Required structure: title, Executive Summary, definitions and findings, next steps,
open questions, risks. Exact 24-month detail table plus one native missing-bar
monthly bar chart (zero baseline, single blue-root palette, chronological month,
full width). Adjacent narrative explains counts and implications. No return chart:
there are no economic labels. Saved SQL/summary/source receipts and companion
notebook provide reproducibility. AUC, win rate, top-decile return, permutation
profit p and matched trading controls are not applicable to this source audit.

## Reproduction

Commit builder and config first, then:

```bash
.venv/bin/python -m pytest -q tests/test_binance_flow_coverage.py tests/test_binance_um_flow_archives.py tests/test_binance_um_archives.py
.venv/bin/python -m yoyo.evaluation.binance_flow_coverage sources
.venv/bin/python -m yoyo.evaluation.binance_flow_coverage audit
```

Source stage is one-shot; audit is repeatable against frozen receipts without
network when all ZIPs exist. Derived artifacts may only be recreated byte-identically.
No goals completion claim: overall profitability remains unproven; app goal is
currently usageLimited and this request does not authorize redeeming a usage reset.
