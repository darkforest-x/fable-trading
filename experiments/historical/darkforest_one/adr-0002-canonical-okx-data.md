# ADR 0002: Canonical OKX closed-bar snapshots

## Status

Accepted.

## Context

Darkforest One needs one reproducible source of ETH-USDT-SWAP 15-minute bars before candidate,
label, replay, or paper work can be trusted. The old research repository used multiple CSV caches
and updater scripts with broader multi-symbol compatibility. Carrying those paths into the small
core would preserve ambiguity around duplicates, incomplete candles, and artifact lineage.

OKX exposes public historical candlesticks at `GET /api/v5/market/history-candles`. Rows are
newest-first arrays and include a `confirm` field that distinguishes incomplete from completed
candles. The endpoint supports timestamp pagination and a maximum page size of 300.

## Decision

- Freeze ingestion to `ETH-USDT-SWAP`, `15m`, starting at `2024-01-01T00:00:00Z`.
- Use the unauthenticated OKX history-candles endpoint.
- Parse only `confirm=1`; never persist or expose an unclosed row.
- Convert exchange payloads immediately into validated domain `Bar` objects.
- Request an overlap from the latest stored candle for every incremental update.
- Deduplicate byte-equivalent domain bars and reject conflicting bars at the same open time.
- Require a contiguous UTC 15-minute sequence with a bounded tail lag.
- Persist a paired Parquet snapshot and JSON manifest containing protocol, configuration, source
  commit, row range, content hash, and Parquet hash. Each file is replaced atomically; a process
  interruption between replacements leaves a detectable, fail-closed mismatch rather than a
  silently accepted partial snapshot.
- Treat a missing pair, hash mismatch, malformed manifest, gap, stale tail, or future bar as a hard
  failure.

## Consequences

The data path remains small and auditable, and downstream modules are independent of OKX response
shapes. Re-running an update with the same exchange rows does not add records or rewrite the
snapshot. Generated market data and manifests are not committed to Git. Network integration tests
remain outside CI; CI uses deterministic transport fixtures and exercises the real parser,
pagination, merge, and store contracts.

## Lineage scope

The manifest stores a data-only configuration fingerprint derived from the protocol identifier,
market scope, and data settings. Candidate thresholds, strategy exits, and cost assumptions do not
change the canonical market-data identity. The raw `volume` column maps to OKX `vol`; for
`ETH-USDT-SWAP`, it represents contract volume.
