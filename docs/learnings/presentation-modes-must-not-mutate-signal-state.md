# Presentation modes must not mutate signal state

## Problem
SPIKE V12.6 needs theme contrast, short breakout candle highlights, Pin Bar coloring and a minimal chart mode while retaining V12.5 admission, pairing and stops.

## Dead end
Reusing the trading break state for coloring can extend or consume pairing evidence. Hiding break events to hide their labels also removes the historical trendlines created with those labels. Back-coloring the first above-line bars after multi-bar confirmation creates retrospective visual evidence.

## Effective path
Keep the complete signal engine unchanged. Copy confirmed frozen anchors into independent presentation state; begin coloring only at the known confirmation bar, expire after a bounded chart-bar window, and cancel on a close back through the line or a data gap. Coalesce repeated B-anchor events without restarting the window. In minimal mode preserve line archives but omit labels and other render layers. Route all candle styling through one priority decision. Compare the full source to its parent after explicitly reversing display edits, then compile and inspect the actual chart.

## General rule
A presentation state machine should only mutate its own state, and a display toggle should change rendering without changing the event ledger. Theme contrast and historical opacity are independent requirements. A wick shape alone is not evidence of a reversal or liquidity sweep.

## Related
- `yoyo/evaluation/pine/spike_burst_v12_6.pine`
- `tests/evaluation/test_spike_v12_6_pine_contract.py`
- https://www.tradingview.com/pine-script-docs/concepts/chart-information/
- https://www.tradingview.com/pine-script-docs/visuals/bar-plotting/
