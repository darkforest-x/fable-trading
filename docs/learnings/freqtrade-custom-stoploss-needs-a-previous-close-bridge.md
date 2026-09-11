# Freqtrade custom stoploss must bridge the prior close

## Context

The ETH SPIKE V1 stop-sensitivity study must preserve the frozen rule: a
ratchet formed at a bar close becomes protective only on the following bar.

## Finding

Freqtrade 2026.8 backtesting calls `custom_stoploss` with intrabar extrema
available (`high` for a long, `low` for a short) and can then test the stop
against the same candle's opposing extreme. Computing the new close-based
ratchet inside that callback can therefore make it active before the source
strategy permits it.

## Rule

Generate a per-trade stop plan from the causal replay. For each Freqtrade
candle return only the stop that was known at that candle's open. Convert that
absolute price through `stoploss_from_absolute`; never calculate a fresh
close-based ratchet in the callback.

## Verification

The V1 ETH bridge matched all 15 frozen ledger events' entry price, exit price
and net R. Freqtrade's stop-close timestamp labels the candle open while the
research ledger labels the candle end; their stop prices matched.

## Scope

This prevents a simulator clock mismatch. It does not validate a strategy,
model, fee assumption, or real order fill.
