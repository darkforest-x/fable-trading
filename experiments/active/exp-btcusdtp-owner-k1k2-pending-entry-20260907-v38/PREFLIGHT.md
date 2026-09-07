# V38 pre-source review

Independent read-only review of the pure state machine, runner, frozen protocol
and synthetic tests found no blocking clock lookahead or stop-precedence defect.
Two pre-source robustness findings were resolved and tested: normalize opaque
segment names in local-prefix comparisons while retaining NA/change patterns;
reverify exact source bytes at completion without mistaking unrelated HEAD
advancement for data drift. True drift leaves existing outputs and a failure receipt.

Focused command before source audit:

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_k1k2_pending_entry.py tests/test_owner_k1k2_pending_entry_audit.py tests/test_hourly_impulse_data.py tests/test_k1k2_genuine_flow_alignment.py tests/test_owner_k1k2_genuine_flow.py tests/test_owner_k1k2_transition_exit.py tests/test_owner_k1k2_exit_transition_contract.py tests/test_owner_k1k2_exit_clock_audit.py tests/boundaries/test_layer_imports.py
```

Result: **494 passed in 8.27s**. No dependency installation, market reads,
economic labels or holdout evaluation were part of these tests. This is focused
coverage, not a claim that the entire repository is green. The prefix proof
will reuse the same frozen SMA implementation on independent truncated inputs;
it is not an independent formula implementation or live-delivery verification.
