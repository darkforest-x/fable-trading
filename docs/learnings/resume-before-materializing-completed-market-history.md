# Check completed work before materializing market history

- **Problem**: A receipt-bound replay resumed without producing new completions before the host time limit.
- **Dead end**: Treating the delay as file hashing missed that the stream generator decoded and aggregated every already-complete source before the consumer could skip it. A nominally resumable job could therefore never reach its unfinished tail.
- **Effective path**: Use the frozen input manifest to verify completed outputs first, select the missing keys, then run the original reader, aggregation, segmentation and replay for those keys. Preserve a separate committed-driver receipt with skipped/pending sets and the original engine identity, and validate the complete final set.
- **General rule**: A checkpoint must be checked before expensive input materialization. Resumption is not proven by the existence of checkpoints; it must reach unfinished work within the host execution limit.
- **Related files**: `experiments/active/exp-spike-v1-plus-backtest-20260912-v1/resume_v3_driver.py`, `yoyo/evaluation/spike_v1_plus_study.py`.
