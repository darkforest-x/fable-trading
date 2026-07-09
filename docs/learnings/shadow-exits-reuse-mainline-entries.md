# Shadow exit logs should share mainline entries, not a half-frozen booster

- **问题**：H1 scaled exit needs forward confirmation without contaminating the TP5/SL2 100-trade gate; a `frozen_scaled_25_t3_*` stub exists but is not loadable by `load_artifact`.
- **死胡同**：Wiring the stub as if it were a real freeze would silently change scores/threshold and invent identity (`features` vs `feature_columns`, short `pool_sha256`). Training a full scaled freeze overnight also over-scopes MVP and risks another selection pass before any OOS rows exist.
- **有效路径**：Keep **one** entry model (mainline freeze + q90) and swap only the **exit resolver** into a side CSV (`forward_log_h1_scaled.csv`). Partial-horizon open support must port labeling math carefully (stop-before-target, trail from prior run_max).
- **通用规则**：When discovery exit ≠ mainline freeze, first ship a **single-variable shadow log** (same signals, different outcome function). Promote a dedicated freeze only after owner wants different scores, not just different exits.
- **牵连**：`scripts/forward_track_h1_shadow.py`, `resolve_forward_exit_scaled`, `run_forward_tracking_h1_shadow`, `docs/H1_SCALED_FORWARD_SHADOW_PLAN.md`, mainline `data/forward_log.csv` must stay untouched.
