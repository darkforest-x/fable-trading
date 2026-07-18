"""Execution layer: OKX order placement from forward signals.

Environment comes from the owner-created keys file (data/okx_demo_keys.json,
gitignored): {"environment": "demo"} sends x-simulated-trading:1 (paper),
{"environment": "live"} trades the real account. The owner flipped production
to live on 2026-07-17; the agent never creates, edits, or reads the keys.

Safety rails (all verified in incident post-mortems, see git log):
  - entry refused unless 0 < SL < mark < TP with finite, tick-rounded prices
    (the 2026-07-16 naked-DOGE incident);
  - signals older than max_signal_age_min (45) never open positions;
  - kill switch: `touch data/executor_KILL` pauses new entries, positions
    untouched; delete to resume;
  - circuit breaker after max_consecutive_losses; TG alert on every order
    event; systemd single instance on the VPS.

Usage:
  PYTHONPATH=. python3 -m src.execution --ping
  PYTHONPATH=. python3 -m src.execution --dry-run --once
  PYTHONPATH=. python3 -m src.execution            # the VPS unit runs this
"""
