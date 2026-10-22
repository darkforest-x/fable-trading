# Zero-fee Monday body confirmation study

Change only round-trip fee from20bp to0 versus v4. Regenerate fee-sensitive admission and replay native TP-triggered BE; preserve all historical artifacts.

Owner latest fee instruction supersedes the earlier20bp session setting. Fees affect admission geometry and protective-stop prices, so prior gross returns alone cannot represent this policy. Reuse the existing market data and native engine. Preserve fifteen-minute body entry, initial0.7percent SL, four25percent targets, any-TP protection, no arrow/timer and original weekly reset. No production changes.

Reproduction (run v6 before v7):

```sh
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --experiment experiments/active/exp-btc-monday-fib-body-20261022-v6 --initialize --delays 0 --collect
```
