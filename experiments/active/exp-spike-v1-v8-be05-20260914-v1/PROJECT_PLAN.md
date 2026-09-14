# SPIKE V1 common-execution and V8 0.5R price-BE sensitivity

One predeclared change: after a completed bar first reaches a favourable 0.5
of frozen actual next-open initial R, protect at actual entry from the next
bar. Prior protection is tested first; a gap through BE fills at the next open.
The original initial stop, 2R/4ATR trail, raw opposite-signal priority, entry
rounding, and 0.2% fixed round-trip cost remain unchanged.

The inputs are the authenticated 3,531 Binance/OKX/Gate 30m/1H/4H caches. V1
means `v1_common_execution_long`, explicitly not archived native V1: the
native ledger is outcome-only for this purpose and cannot support a causal BE
replacement replay. Both serial replay (including exit-created re-entry) and
fixed-original-entry paired exits will be retained. Historical history is
owner-authorized reused and nonblind; configuration exposure is recorded as 1.
