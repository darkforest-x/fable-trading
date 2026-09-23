# Bounded time-semantics review

Reviewer: `/root/delayed_confirmation_prior`, explicitly configured and runtime-verified `gpt-6-luna`, reasoning `max`. Runtime turn metadata timestamp: 2026-09-23T08:34:10.836Z. Read-only scope; no nested agents or edits. Numerical ledger analysis and final inference remained with the parent.

- V9 excludes UTC Sunday on the signal-close / scheduled next-open clock, not the signal bar open. Evidence: `yoyo/evaluation/pine/spike_burst_v12_8.pine:571`, `yoyo/evaluation/spike_v128_recent.py:111`, `analysis/p1_spike_v9_implementation_20260915.md:34`.
- The excluded clock is Beijing Sunday 08:00 through Monday 08:00, right-open. The replay separately checks the next bar exists and cancels gaps.
- The older ICT research tested a combined New York [02:00,11:00) window with DST, not a best individual hour; explicit actual next-open admission and serial replay were required. Evidence: `analysis/p1_spike_v8_ict_multitf_20260915.md:37`.
- Only three of six timeframes improved net R and only two had positive net R. Its recent 15m window showed 52.9% win rate but 22.74R versus all-day 27.75R; older 15m history was 123 trades and -9.84R. Evidence: same report lines 5, 108.
- The top three older ICT winners contributed about 93.2% of net R, and the matched random control included New York hour as well as symbol, side, month, weekend and volatility. Evidence: same report lines 102, 108.
- V9 implementation did not establish profitability of its UTC Sunday exclusion. Evidence: `analysis/p1_spike_v9_implementation_20260915.md:7`.

Scope is protocol/history evidence, not independent numerical audit of the current output. The parent independently recomputed all 240 full-period hour/4h buckets from the frozen source ledger using stdlib csv/datetime; see `verification.json`.
