# ADR 0004: Serve the signal console through a read-only model adapter

## Status

Accepted

## Date

2026-09-03

## Owner authorization

“接入我们的模型，模型后期可以替换，先接上跑通。”

## Context

The local Fable Signal Console began as a visually faithful frontend with mock
records. It now needs to prove that the repository's real model stack can be
served while keeping the frontend replaceable. The owner clarified that both
layers must use the latest research artifacts, not the older runtime pointers.
As of 2026-09-02, that means the Grade-A full40 native-1280 L1 checkpoint and
the tune-selected, side-specific L2 pair: LONG `full_110` and SHORT
`plus_ma_family`.

This is not permission to promote either artifact or to consume the frozen
holdout. `models/active_bundle.json` remains absent, the executor therefore
remains fail-closed. The latest L2 experiment's recorded decision is `reject`,
and all three artifacts remain `production_eligible=false`. Importing
Ultralytics and LightGBM into the long-lived
web process is also unsafe on this host because the project has observed
dual-libomp crashes and an import-order-sensitive prediction failure.

## Decision

1. The React console consumes one stable `/api/model/*` JSON contract. It does
   not import, name, or understand a concrete model implementation.
2. A local-only FastAPI bridge selects a whitelisted adapter with
   `FABLE_SIGNAL_CONSOLE_ADAPTER`. The current adapter is
   `latest-research-preholdout-replay`; a future model adds another adapter without
   changing the UI.
3. Compatible artifact replacements may override the explicit L1 contract and
   weights or the L2 preregistration, feature, selection, and training receipts
   through dedicated environment variables. Request payloads cannot choose
   paths or arbitrary commands.
4. ML inference runs in a short-lived subprocess with Ultralytics before
   LightGBM and with OMP/MKL limited to one thread. The API process never imports
   either runtime.
5. The first end-to-end proof uses one event from the hash-verified latest L2
   feature ledger. The worker reconstructs that event's exact L1 input from the
   byte-pinned pre-holdout OHLCV snapshot and requires pixel SHA parity before
   inference. L1 and L2 therefore run on the same event, whose availability and
   dataset maximum are both before `2026-05-04T00:00:00Z`.
6. The UI shows the exact YOLO input, returned boxes, selected L2 side/arm,
   score, threshold verdict, model identity, recorded `REJECT` decision, and
   safety mode. It labels the result as a serving smoke, not performance
   evidence.
7. The adapter never reads live bars, writes `forward_log`, touches
   `models/active_bundle.json`, promotes a model, or reaches the executor.
   Enabling a live/holdout-backed mode requires a separate explicit owner
   authorization and permanent holdout-consumption record.

## Consequences

- The latest research L1 and side-specific L2 stack is genuinely exercised from
  browser to Python to both model runtimes while production state stays unchanged.
- Model replacement has an explicit seam and a fail-closed registry instead of
  model paths embedded in React components.
- The local prototype needs both the Vite frontend and the local model bridge;
  `npm run dev:full` owns their lifecycle.
- A successful replay proves serving compatibility only. It must not be quoted
  as unseen accuracy, economic performance, tip freshness, or production
  eligibility.

## Alternatives considered

- Point the browser directly at `/api/check-symbol`: rejected for this first
  pass because that probe reads current market data, which would score the
  unapproved post-boundary window, and because its legacy private import had
  broken after the four-layer restructure.
- Read `models/ACTIVE` or create `active_bundle.json`: rejected because model
  selection for a local UI is not authorization to change production state,
  and those pointers are not the latest research-model source of truth.
- Use the older v10 L1/L2 pair because it is easier to discover from runtime
  pointers: rejected after owner correction; "current runtime" and "latest
  research artifact" are different axes in this repository.
- Import both ML libraries in FastAPI: rejected because it repeats a known
  process-crash failure mode and couples web uptime to heavyweight inference.
- Keep mock data and display only model filenames: rejected because it does not
  prove that either artifact can actually load and predict.
