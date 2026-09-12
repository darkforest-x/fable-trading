# V1+ frozen replay run notes

## Effective run

- Engine: `results/replay_full_v3/`
- Engine manifest: `results/replay_full_v3/manifest.json`
- Engine completion: 3,531/3,531 receipt-verified frozen streams.
- Postprocessor: `post_full_v2/`; `post_manifest.json` records `complete: true`, 3,531 streams and 20,184 simulated trade records across the two arms. These records are simulated strategy/exit records, not independent market opportunities or live orders.
- Frozen inputs: `config.json`, pinned V1+ Pine SHA, raw manifest SHA and raw input-manifest SHA are recorded in the engine manifest identity.
- Economic scope: default `v1_plus_default_both` versus display-derived bidirectional master-off `v1_display_both`, 0.2% round-trip cost. This is a descriptive, all-date authorized and non-blind historical replay; it is not a parameter search or a live-performance claim.

## Recovery history

The original standard runner started `results/replay_full_v3` and completed 3,191 streams before the host's approximately 20-minute child-process lifetime ended. Its ordinary resume path reconstructs every completed raw source before it can inspect the receipt key, so it cannot reach the remaining tail under that limit.

`resume_v3_driver.py` (commits `e82cb45`, `667b08b`) was added as a one-off receipt-preserving recovery wrapper. It first verified every existing completion's identity and output SHA, skipped exactly 3,191 verified keys, and reconstructed only the 340 pending keys with the original reader, aggregation, continuous segmentation, reference replay and writer functions. `receipts/replay_full_v3_resume_receipt.json` records the driver SHA, original identity, raw input-manifest SHA, the exact skipped/pending/completed key lists, and final engine/summary SHA values. The final all-3,531 verification passed.

The following directories are technical attempts and are excluded from every conclusion: `results/smoke_builder_v2/`, `results/smoke_execution_order_v3/`, `results/smoke_ghost_reverse_v4/`, `results/smoke_array_execution_v5/`, `results/replay_full_v1/` (80 streams before execution-clock fixes), and `results/replay_full_v2/` (366 streams before the array-access performance implementation). `post_full_v1/` stopped during strict metadata verification because CSV parsing inferred the literal asset identifier `"4"` as an integer. It produced no economic conclusion. `post_full_v2/` uses the strict literal-identifier reader and is the only valid postprocess output.

## Validation and reproduction

Builder/test commits: `54951ef`, `46bae02`, `3cacd92`, `6ddea30`, `2972ce0`. The array execution path replaces only per-bar pandas scalar access. It matched `_simulate_next_open_series` with `assert_frame_equal` for trades and fills on five full frozen streams times two arms; the measured execution segment was 4.495 s versus 0.029 s. This does not change signal, protection, queueing, cost, or fill semantics.

```bash
python3 -m py_compile yoyo/evaluation/spike_v1_plus_replay.py yoyo/evaluation/spike_v1_plus_study.py
python3 -m pytest -q tests/evaluation/test_spike_v1_plus_replay.py
python3 -m yoyo.evaluation.spike_v1_plus_study \
  experiments/active/exp-spike-v1-plus-backtest-20260912-v1/results/replay_full_v3
python3 experiments/active/exp-spike-v1-plus-backtest-20260912-v1/resume_v3_driver.py
.venv/bin/python -m yoyo.evaluation.spike_v1_plus_report \
  experiments/active/exp-spike-v1-plus-backtest-20260912-v1/results/replay_full_v3 \
  experiments/active/exp-spike-v1-plus-backtest-20260912-v1/post_full_v2
```

The focused replay suite passed 11 tests after the array-access change. Parent-owned postprocessor tests passed 8 tests at commit `5ca0a2b` before the valid postprocess run.
