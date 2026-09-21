# SPIKE V12.6 presentation release

Owner requested theme-aware trendlines, early confirmed-break candle coloring, a six-MA/main-trendline-only mode, and independent Pin Bar colors. This is a versioned private indicator delivery, not a profitability experiment.

- Parent: `yoyo/evaluation/pine/spike_burst_v12_5.pine`, retained byte-for-byte.
- Source: `yoyo/evaluation/pine/spike_burst_v12_6.pine`.
- Initial implementation commit: `18722beb0f` (before TradingView save/compile).
- Parent equivalence receipt: `tests/evaluation/fixtures/spike_v126_visual_replacements.json`.
- Verification: `tests/evaluation/test_spike_v12_6_pine_contract.py`; native QA `output/qa/spike_v12_6_visual_20260922/`.
- Final report: `analysis/p1_spike_v12_6_visual_20260922.md`.
- training_eligible: false
- production_eligible: false

Stop placement was inspected, not changed. The cropped PEPE screenshot lacks an entry timestamp; current 15m layout does not uniquely identify the pictured historical event. SMA120/confirmed higher-MA stop anchoring remains a hypothesis, not a validated replacement.
