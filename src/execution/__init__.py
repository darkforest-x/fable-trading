"""Execution layer: OKX DEMO trading only.

There is no live-trading code path in this package by construction. Every OKX
request carries the x-simulated-trading:1 header, hard-coded, not toggled by a
flag -- so the worst a bug or a leaked key can do is move paper balance. Going
live is a deliberate future rewrite the owner performs, not a config switch.

Usage:
  PYTHONPATH=. python3 -m src.execution --write-examples
  # owner creates data/okx_demo_keys.json (gitignored)
  PYTHONPATH=. python3 -m src.execution --ping
  PYTHONPATH=. python3 -m src.execution --dry-run --once
  PYTHONPATH=. python3 -m src.execution --once
  touch data/executor_KILL   # pause new entries
"""
