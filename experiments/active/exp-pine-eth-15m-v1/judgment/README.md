# Pine V9 judgment gate contract

This directory is a fail-closed interface for a future owner-authorized
judgment experiment. It is not a model, score file or execution approval.

`judgment_scores.template.csv` defines the only accepted external score shape.
Every raw guarded V9 candidate in a scored period must have exactly one finite
score, the score must be available no later than the next-bar entry open, and
both model and feature-contract hashes must be fixed. A missing, duplicate or
late score rejects the candidate.

The threshold must be preregistered using an earlier calibration period. Scores
must be joined to `v9_long` / `v9_short` before the stateful replay, because a
rejected entry changes later position and cooldown state. Filtering the
baseline executed-trade CSV after the fact is invalid.

`gate_manifest.template.json` locks the candidate surface, ordered feature
contract, frozen V9 configuration, 15-minute bar size, 20 bp round-trip cost
and 1% comparison risk. Its null model/period/threshold/approval fields are
intentional: the replay refuses the template until an authorized experiment
fills and preregisters them. The score timestamp must be both no earlier than
feature availability and no later than the next-open decision (under this bar
contract those timestamps are equal).

One subtle state rule is preserved explicitly: V9 consumes cooldown on every
raw signal before checking calendar/volatility eligibility. Such ineligible
raw signals are not model candidates, but the replay passes them through only
for their cooldown transition. Eligible raw signals are fail-closed and exist
only when their validated score passes. The synthetic allow-all contract audit
reproduces both 2023 and 2024 V9 ledgers exactly and rejects missing,
duplicate, early, late, non-finite, hash-mismatched and unregistered inputs.

```bash
PYTHONPATH=. .venv/bin/python \
  scripts/replay_pine_eth_15m_judgment_gate.py --self-audit

# Only after owner/P0-P1 authorization and preregistration:
PYTHONPATH=. .venv/bin/python \
  scripts/replay_pine_eth_15m_judgment_gate.py \
  --scores path/to/judgment_scores.csv \
  --gate-manifest path/to/locked_gate_manifest.json \
  --output-dir experiments/active/exp-pine-eth-15m-v1/judgment/runs/<run_id>
```

Current status: no labels, no scores, no threshold, no LR/LightGBM training,
no forward collection, no TradingView parity and no production eligibility.
