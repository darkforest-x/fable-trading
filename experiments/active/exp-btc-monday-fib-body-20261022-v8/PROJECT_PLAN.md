# Adjacent second-close confirmation

Only add the owner-requested next15m close condition. The first bar retains inner open and close, the second only needs an inner close. Enter at the second close, recalculate0.7percent SL at actual entry; retain four25percent Monday targets, immediate any-TP breakeven, zero fees, no arrows or elapsed timers. The pre-entry2percent wick gate remains active through actual entry. Failed pairs keep observing; original weekly reset remains.

Native entries and exits change, so reuse market data and the Nautilus adapter but replay the new entries. Match random controls on the updated entry context. Pair comparison to v7 by weekly-side opportunity, not by identical price or time. Keep prior results and charts intact.

```sh
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-body-20261022-v8 --initialize --delays 0 --collect
```
