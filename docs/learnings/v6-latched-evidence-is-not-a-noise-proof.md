# V6 latched evidence is not a noise-proof filter

- **问题**：V6 can still show many signals despite adding volume-price launch evidence.
- **死胡同**：Treating the PEPE 24-bar retained event as evidence that V6 reduces market noise confuses a functional parity check with a false-positive-rate experiment.
- **有效路径**：Source review established that V6 inherits V5/V4 provenance and its rolling three-bar advance/volume envelope, but deliberately does not restore V1's same-bar RV >= 4 and TR/ATR >= 3 gates. A directional body/range and close-location candle is latched during pending structure without an age expiry. A new V4 provenance can arm another event in a continuous six-MA-above run after the prior provenance is consumed.
- **通用规则**：Describe V6 as a causal structural/evidence gate, not as a proven noise filter, until matched event-level false-positive and recall evidence exists. A retained known-positive functional window cannot estimate either quantity.
- **牵连**：`yoyo/evaluation/pine/spike_burst_v6.pine` lines 166-177 and 301-355; `analysis/p1_spike_v6_volume_price_20260911.md`. Future single-variable candidates are an evidence-freshness limit or same-structure deduplication; either can discard legitimate delayed continuation signals and needs separate evaluation.
