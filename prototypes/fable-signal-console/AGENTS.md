# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.
For a user-facing handoff, do not leave `dev:full` owned only by a temporary
command session: this environment may reap that process when the turn ends.
Host it in a detached `screen` session and verify both ports 4173 and 8643
return HTTP 200 before sharing the localhost link.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## Model integration contract

- The React UI consumes only the stable `/api/model/*` contract. Concrete model
  paths and Python ML imports belong behind the repository adapter in
  `yoyo/console/`.
- The default adapter is the latest-research pre-holdout replay: Grade-A full40
  native-1280 L1 plus the selection-receipt L2 pair (LONG `full_110`, SHORT
  `plus_ma_family`). Do not silently fall back to v10, `owner_best`, or
  `models/ACTIVE`.
- Every displayed replay must align L1 and L2 to the same immutable event and
  verify the reconstructed L1 pixel hash before inference.
- Keep the recorded L2 `REJECT` decision and `production_eligible=false` visible.
  Serving success is not performance evidence or production authorization.
- Live data, holdout data, ACTIVE/active_bundle mutation, forward writes, and
  execution remain out of scope unless the owner separately authorizes them.

## Market visualization contract

- The owner wants the primary chart to behave like TradingView: real-time 15m
  candles, crosshair, drag, zoom, and the six renderer-contract overlays:
  SMA/EMA 20, 60, and 120. Keep their names in the top legend; do not show
  per-series labels on the right price axis. `实时行情` and `模型回放` must use
  the same interactive dark chart treatment.
- Replay inference still uses the exact hash-verified L1 raster. The replay
  chart must consume server-provided candles and six precomputed MAs from that
  same frozen window; never recompute long MAs from the short display slice.
  Project model boxes through logical/price coordinates so they follow pan,
  zoom, and resize. Keep the raw exact raster available as collapsed evidence.
- Never label a bare clock such as `04:00:00` as detection time. Historical
  replay events must show the full date, UTC, Beijing time, and that they are
  not current.
- `/api/market/*` is display-only public OKX data. It may live in memory and the
  browser only; do not persist it, join it to replay verdicts, score it with a
  model, write it to forward logs, or send it to execution.
- Keep `历史裁决`, `REPLAY ONLY`, and `实时行情未送入模型` visible whenever live
  prices and replay results share a page. See ADR 0005 and ADR 0006.
