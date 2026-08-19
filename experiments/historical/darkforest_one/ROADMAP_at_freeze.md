# Roadmap

The roadmap is sequential. A later phase does not start merely because code exists; the preceding
phase must satisfy its acceptance gate.

## P0 — Foundation

- [x] Freeze ETH-USDT-SWAP / 15m / short-only / paper-only scope
- [x] Add typed fail-closed configuration
- [x] Add immutable domain contracts and explicit UTC time semantics
- [x] Add deterministic numerical MA-density baseline
- [x] Add causal feature and replay primitives
- [x] Add data continuity validation and artifact hashing
- [x] Add unit tests, CI, development rules, ADRs, and protocol documentation

**Gate:** `make check` passes and the GitHub repository is the canonical source.

## P1 — Canonical data and candidates

- [x] Implement OKX historical 15m bar ingestion
- [x] Implement idempotent incremental updates
- [x] Persist canonical Parquet snapshots with manifests
- [x] Reject gaps, duplicates, malformed OHLCV, stale tails, and unclosed bars
- [x] Build the causal MA-density candidate dataset
- [x] Add density diagnostics and matched random controls

**Gate:** identical inputs produce identical bars, features, candidates, and hashes; no candidate
can observe a future bar.

## P2 — Judgment

- [ ] Implement fixed-protocol next-bar-open labels
- [ ] Record gross return, fees, slippage, and net return separately
- [ ] Train a LightGBM net-return regressor
- [ ] Add walk-forward train/calibration/test splits
- [ ] Freeze threshold, features, model, config, and source commit in a manifest
- [ ] Compare accepted candidates with all candidates and matched controls

**Gate:** improvement is stable across time folds and remains positive after conservative costs.

## P3 — Deterministic replay

- [ ] Implement next-bar-open short entry
- [ ] Implement 2 ATR stop, 4 ATR target, and 48-bar timeout
- [ ] Resolve same-bar TP/SL ambiguity pessimistically
- [ ] Implement 0.20% stop-distance risk sizing and one-position limit
- [ ] Write an immutable trade ledger
- [ ] Reconcile replay trades with labeling outputs

**Gate:** replay is deterministic, causal, cost-aware, and exactly reconcilable from the ledger.

## P4 — Paper execution

- [ ] Poll only newly closed 15m bars
- [ ] Add idempotent virtual order and position state
- [ ] Make restart behavior deterministic
- [ ] Share policy, cost, and exit semantics with replay
- [ ] Add replay/paper parity regression tests
- [ ] Accumulate a fresh protocol-specific forward sample from zero

**Gate:** no duplicate decisions after restart and no unexplained replay/paper semantic divergence.

## P5 — Candidate competition

- [ ] Define the candidate plugin interface formally
- [ ] Build a causal right-edge YOLO dataset outside the core
- [ ] Add YOLO as a candidate adapter, not a pipeline dependency
- [ ] Compare numerical, YOLO, and hybrid candidates under identical downstream controls

**Gate:** a candidate source enters the core only when it improves economic base rate without
uncontrolled firing density or distribution-shift failure.

## Explicitly deferred

- Multi-symbol or multi-timeframe trading
- Long signals
- Reinforcement learning
- Transformer price prediction
- Order-book or maker-queue simulation
- Portfolio optimization
- Real exchange order submission
