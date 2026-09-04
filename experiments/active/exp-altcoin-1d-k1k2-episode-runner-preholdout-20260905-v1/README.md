# Altcoin 1D K1→K2 episode runner v1

This experiment is the preregistered daily altcoin branch of the Owner's
K1→K2 trend idea. It is deliberately separate from BTC/ETH 15m parameters.

The signal is anchored to a completed neutral/compression episode. A K1 attempt
consumes that episode, so repeated MA crossings inside the same range cannot
create repeated entries. The first causal K2 retest is entered at the next UTC
daily open. Profit is banked gradually, while the remaining position follows a
causally updated fast or slow moving average.

The 52-symbol universe and every tunable factor are frozen in `config.json` and
`preregistration.json` before any outcome is inspected. Data at or after the
repository holdout boundary (`2026-05-04`) are forbidden. Selection, audit, and
confirmation are opened in order, with a committed receipt between phases.

This is research only. It cannot alter TradingView, ACTIVE/frozen, forward,
deployment, position sizing, API keys, or live orders.
