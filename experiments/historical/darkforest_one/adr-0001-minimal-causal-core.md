# ADR 0001: Build a minimal causal core

- Status: Accepted
- Date: 2026-07-31

## Context

The predecessor project mixed YOLO experiments, multiple candidate pools, model training,
backtests, forward logs, and execution behavior. Its size made it difficult to prove which layer
created or destroyed economic value.

## Decision

Create Darkforest One as a separate repository with one frozen protocol:
ETH-USDT-SWAP, 15m, short-only, numerical MA-density candidates, LightGBM judgment,
deterministic replay, and paper execution. Live orders and scope expansion are excluded.

## Consequences

- Every layer has explicit, testable contracts.
- New candidate generators compete under the same downstream protocol.
- Research can fail without destabilizing the core.
- The project deliberately postpones multi-symbol, multi-timeframe, YOLO, RL, and live execution.
