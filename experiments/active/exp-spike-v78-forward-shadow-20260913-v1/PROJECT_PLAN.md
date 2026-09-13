# SPIKE V7/V8 forward shadow — immutable activation contract

This is the forward-only answer to repeated historical tuning. V7 and the
single-variable V8 (`rope_distance_atr <= 3`) observe the same newly closed OKX
30m, 1H and 4H checkpoints after one immutable activation time.

The first engineering activation used protocol v1. Independent review found
that its signal and path tables were causal but its market-state table admitted
the latest pre-activation checkpoint. It contained zero signal events and was
retired without reuse. Protocol v2 uses a new database and applies the strict
activation boundary to all three evidence types.

The sidecar reads only the primary monitor's completed checkpoint cache. It has
its own SQLite database and has no outbox, order, candidate, YOLO, exchange or
execution table. It does not notify. Market breadth is stored as a descriptive
snapshot and cannot affect admission. Future path bars are evaluation evidence
and are physically separate from immutable signal rows.

Acceptance requirements:

- signal events and context bars must both satisfy `bar_close_ms > activated_ms`;
- rerunning an unchanged checkpoint is idempotent;
- every event binds the causal input-prefix SHA and frozen source/config SHA;
- the service runs at low priority, without `caffeinate` or network fetching;
- API and UI label the book as research-only and non-executable;
- V7/V8 remain unchanged until enough genuinely new events mature.

Activation does not authorize a production promotion, Bark change, Pine change,
model training, order, position or risk change.
