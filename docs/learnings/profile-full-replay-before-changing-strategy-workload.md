# Profile the complete replay before reducing its workload

- **Problem**: A 3,531-stream fixed-configuration study initially projected around 100 minutes on one CPU worker. Reducing history or adding workers would change coverage or increase heat.
- **Dead end**: Profiling only feature/reference generation suggested a small saving from sharing features. It missed the execution phase, which constructed two pandas Series with `.iloc` for every bar.
- **Effective path**: Profile the full stream, preserve the Series executor as an oracle, and change only value access to typed arrays. Five complete streams in both directions/arms matched with `assert_frame_equal`; the measured execution portion fell from 4.495 seconds to 0.029 seconds. This is not a claim about whole-study speed. Commit the builder before starting a new receipt-bound output directory.
- **General rule**: A fast subroutine does not prove a fast pipeline. Measure phase costs, preserve a correctness oracle, and require identical ledgers before accepting a performance change. Never mix results from changed code identities during resume.
- **Related files**: `yoyo/evaluation/spike_v1_plus_replay.py`, `tests/evaluation/test_spike_v1_plus_replay.py`, `experiments/active/exp-spike-v1-plus-backtest-20260912-v1/`.
