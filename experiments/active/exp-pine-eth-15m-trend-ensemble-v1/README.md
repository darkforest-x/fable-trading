# ETH 15m V15E soft trend ensemble

This experiment tests one composite entry-quality change on top of the frozen
V12F full-state candidate stream:

- three ATR-normalized EWMAC forecasts: `8/32`, `16/64`, `32/128` bars;
- three prior-channel Donchian forecasts: `24`, `48`, `96` bars;
- the existing causal six-MA dense-start score as a 20% soft feature;
- one development-selected quality threshold applied only to guarded V12F
  candidates.

Stops, break-even, sizing, cooldown, reversal semantics and the 20bp round-trip
cost are unchanged.  The repository holdout beginning 2026-05-04 is excluded.
This is a paper research arm only and cannot be promoted or deployed.

The exact pre-outcome contract is in `preregistration.json`.
