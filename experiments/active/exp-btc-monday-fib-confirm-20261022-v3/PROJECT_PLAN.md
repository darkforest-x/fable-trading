# Remove elapsed-time limits, owner2026-10-22

Owner withdrew both timed holding protection and the285minute observation deadline. Preserve weekly first-touch-per-side admission and original nextMonday interval reset. Arrow and closed15m body remain independent post-touch latches. Four25percent TP legs, actual-entry0.7percent stop, immediate cost-inclusive BE after anyTP, explicit20bp roundtrip fee. Filled trades have no holding-time expiration.

Single change versus v2 delay0 is removal of285minute admission timeout. Reuse original one-minute OKX candle cache; rebuild only affected confirmation entries/controls, run one native policy, then reconcile shared and newly admitted trades separately. Preserve all old outputs; no timed optimization or holding timeout.

Reproduction (isolated runtime, no dependency changes):

```sh
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-confirm-20261022-v3 --initialize
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-confirm-20261022-v3 --delays 0
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-confirm-20261022-v3 --collect
```

For completed evidence use collect only. Signal reconstruction uses minute-close samples of forming higher bars, not true tick flashes. Data-end marks are censored evidence, not a time-based strategy exit.
