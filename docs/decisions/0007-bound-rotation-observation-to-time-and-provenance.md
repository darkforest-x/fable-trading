# ADR 0007: Bind rotation observation to time and provenance

## Status

Accepted for engineering implementation. Current-market scoring awaits its own
owner approval. Training and production eligibility remain false.

## Date

2026-09-09

## Context

The owner requested an entire selective-altcoin observation system after a
market discussion. Existing IMACD research provides pure numerical components,
but its experiment dates, permissions, caches and outcome accounting belong to
other configurations. The repository also contains active uncommitted work.

## Decision

1. Add only uniquely named rotation modules. L1 and L2 communicate through
   plain contracts/data; orchestration sits in yoyo/rotation, outside execution.
2. Share the frozen holdout boundary. Before HTTP, enforce historical cutoff
   or require an owner receipt bound to experiment, config hash, source hash,
   observation scope, expiry and consumption number. No command mints approval.
3. Keep market candles in memory. Persist derived observations in this one
   experiment only. No canonical K-line writer, feature pickle reuse or forward
   log access. A source/config/input identity accompanies each result.
4. Use closed UTC 1d/4h/15m bars. Universe selection precedes scoring; explicit
   historical samples do not claim historical market-wide completeness. Unknown
   asset/event classification remains unknown and blocks risk readiness.
5. Deterministic heuristic ranks and structure states are explanatory research
   outputs, not calibrated probabilities or executable trade signals. Financial
   examples use existing cost constants and never modify presets.
6. A local SQLite journal is idempotent, transactional and namespace-bound.
   FastAPI serves a local browser interface without external scripts. GETs do
   not scan; explicit scan calls use server configuration and concurrency gates.

## Consequences

The engineering system is usable and testable without consuming new holdout.
Current-market operation requires a concrete recorded approval. No historical
performance claim is inferred from engineering tests or synthetic examples.
Supply/event coverage and derivatives gaps are visible, never filled with
invented current data or retroactively joined announcements.

## Sources

- Binance spot klines/endTime/quote-volume:
  https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market
- FastAPI static files:
  https://fastapi.tiangolo.com/tutorial/static-files/
- Python3.9 SQLite transactions:
  https://docs.python.org/3.9/library/sqlite3.html
