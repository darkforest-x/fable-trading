# Architecture

## Design objective

`darkforest-one` is a deliberately narrow vertical slice extracted from `fable-trading`. It exists to answer whether one causal ETH 15-minute short signal can survive selection, costs, replay, and forward paper validation.

## Main flow

```text
closed canonical bar
  -> causal feature snapshot
  -> candidate generator
  -> judgment prediction
  -> fixed decision policy
  -> replay or paper execution
  -> immutable ledger
  -> evaluation report
```

## Module contracts

- `data`: acquire, canonicalize, persist, and validate closed bars. Exchange payloads terminate
  at `OkxHistoryClient`; downstream code receives only validated `Bar` objects. Canonical snapshots
  are a verified Parquet plus JSON manifest pair; each file is replaced atomically, and any
  interrupted pair update fails closed on the next read.
- `features`: produce point-in-time features from available history only.
- `candidate`: propose opportunities; it does not decide whether to trade.
  P1 consumes only a verified canonical snapshot and emits a fail-closed Parquet/diagnostics/
  manifest triple containing point-in-time candidates and matched offline controls.
- `judgment`: estimate net outcome under the frozen execution protocol.
- `strategy`: turn a prediction into an accepted or rejected decision.
- `execution`: replay or simulate positions using shared semantics.
- `evaluation`: compare candidates, accepted trades, controls, and costs.
- `governance`: validate protocol scope and artifact lineage.

## Dependency direction

Domain objects have no infrastructure dependencies. Candidate, judgment, strategy, and execution depend on domain contracts. Infrastructure modules must not leak exchange-specific payloads into the domain layer.

## Modes

- `replay`: historical bars are yielded one at a time.
- `paper`: only newly closed bars are processed; no exchange order is submitted.
- `live`: intentionally absent in v0.1.

## Canonical data update

```text
OKX public history-candles
  -> parse confirmed rows only
  -> validate UTC 15m OHLCV
  -> overlap one existing bar
  -> reject conflicting duplicates
  -> validate full continuity and tail freshness
  -> replace verified Parquet + manifest files with fail-closed recovery
```

Initial history begins at `2024-01-01T00:00:00Z`. Incremental runs request an overlap from the
last stored candle so a changed exchange row cannot be silently accepted. An identical overlap is
deduplicated; a conflicting overlap aborts the update.

## Candidate artifact build

```text
verified canonical snapshot
  -> fully warmed SMA/EMA + ATR features
  -> causal rolling normalized-volatility percentile
  -> deterministic MA-density candidates
  -> exact-stratum matched random controls (offline only)
  -> density diagnostics
  -> atomic Parquet + JSON diagnostics + JSON manifest
```

The build is offline and never constructs an exchange client. Candidate membership is determined
before matching and cannot observe a future bar. Control matching is retrospective over the frozen
snapshot, never reads outcomes, and is kept outside the decision path. Semantic hashes use fixed
column order, UTC ISO timestamps, and hexadecimal float encodings so lineage does not depend only
on Parquet bytes. Re-running identical canonical/config/source inputs is a no-op.
