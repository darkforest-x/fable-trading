# Final owner rule: body-only admission,2026-10-22

First weekly-side1.618 touch starts observation. Enter at first completed15m candle with both open and close inside the level; no arrow/check-cross prerequisite, no285minute timeout. Original nextMonday interval reset remains; filled trades may crossweeks with no holding expiry. Initial SL0.7percent; four original25percent target legs; anyTP immediately protects remaining legs at cost-inclusive entry. Owner explicitly authorized skipping entry when firstTP space cannot cover20bp fee. No second opportunity for the same weekly side.

Reuse cached1mOKX and105weekly anchors. Recompute only body-only admission and matched controls. One native policy, no n search. Previous arrow arms are not the final strategy.

Reproduce in isolated runtime:

```sh
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-body-20261022-v4 --initialize
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-body-20261022-v4 --delays 0
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-body-20261022-v4 --collect
```

Collect uses existing native evidence without replay. Calendar/data-end censoring is separate from a time-expiration strategy. Compare net returns and strict wins against10matched controls per actual entry.
