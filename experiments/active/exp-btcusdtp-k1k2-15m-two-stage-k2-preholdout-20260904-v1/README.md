# BTCUSDT.P 15m two-stage K2 pre-holdout experiment

This experiment changes one signal representation: K2 may be a causal
touch-to-confirmation interval instead of requiring touch and strong rejection
on the same candle. All K1 thresholds, SMA40(HL2), gap, execution barriers,
cost, cooldown, horizon and protection remain frozen from the 15m research
baseline.

The candidate definition is fixed before outcomes are calculated. Development
uses 2023--2024 only. The 2025--2026-02-28 audit can open only if every
development gate passes. Repository holdout at or after 2026-05-04 is excluded.

Pre-outcome clarification: a delay-zero confirmation exactly preserves the
baseline K2 morphology. Only delayed confirmations at +1 or +2 require an
explicit direction-aligned MA colour. This resolves a preregistration wording
conflict before any arm result is calculated.

Freqtrade 2026.8 runs as a full, Docker-isolated second implementation using
the exact same locally materialized OHLCV source. It does not place live or
dry-run orders.

## Final result

Rejected on the registered 2023--2024 development window. The same-bar arm
produced 89 trades at -16.30 bp net per trade; the two-stage arm produced 100
trades at -16.09 bp, a -18.93 bp robust score and -26.33 bp worst half-year.
All four half-years were negative and the matched-control sign-flip test was
not significant (`p=0.2313`). The audit window therefore remained unopened.

Freqtrade 2026.8 reproduced all 100 entry time/direction keys with zero entry
or initial-stop price error. Its mean was -15.77 bp and its lookahead analysis
reported no biased entry or exit signals. Post-hoc diagnostics found no rule
among 26 one-coordinate replays that passed the original gate. A minimum
0.5-ATR touch depth was the strongest new hypothesis, but it had only 23
events, a negative worst half-year and `p=0.0717`; it was not selected.

The complete report is
`analysis/p1_btcusdtp_k1k2_15m_two_stage_k2_freqtrade_preholdout_20260904.md`.
