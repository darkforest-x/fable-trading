# Body-only entry with2percent adverse invalidation,2026-10-22

Final rules: first weekly-side touch of outer1.618, then first completed15m open and close both inside. No arrow, no elapsed observation/holding timer. Before entry, short high>outer*1.02 or long low<outer*.98 invalidates that weekly side, including a wick before a same-minute recovery close. Exact2percent is not greater. Original nextMonday reset remains. No rearm after invalidation or fee-geometry skip.

Retain0.7percent actual-entry SL; four25percent targets; immediate remaining-leg costBE after anyTP; explicit20bp roundtrip fee and owner-approved skip when TP1 room is insufficient.

This is only a causal admission filter versus v4. Reuse retained v4 native positions/fills and their ten matched controls, after asserting every entry,stop,target,risk,side,weight and bound unchanged. Do not rerun the engine or report account returns. Original native full-superset evidence is preserved.

```sh
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-body-20261022-v5 --initialize
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-body-20261022-v5 --project
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-body-20261022-v5 --collect
```
