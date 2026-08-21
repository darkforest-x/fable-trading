# ETH 15m dense-start V13 research

This experiment replaces V12F's loose `W8 net crosses >= 0` gate with one
owner-approved composite variable: a causal `dense -> compression -> direction
-> release` gate over the same six close-derived SMA/EMA 20/60/120 lines.

The experiment is fixed to ETH-base 15-minute bars. It changes no barrier,
cost, sizing, cooldown or reversal parameter. The repository holdout beginning
at `2026-05-04T00:00:00Z` is already consumed by the predecessor experiment and
is unreachable here. The data loader stops before `2026-03-01T00:00:00Z` and
must report zero holdout rows.

Threshold profiles and their one-time 2023 selection order are frozen in
`preregistration.json` before any outcome replay. 2024 is validation only; the
2025-to-February-2026 segment is already inspected final-preholdout evidence and
cannot become a new holdout. No LR or LightGBM is fitted while project P0/P1
blocks training; only causal feature rows are exported for a future judgment
layer.

All generated Pine and economic outputs are paper-only and default to
`training_eligible=false`, `forward_eligible=false`, and
`production_eligible=false`.
