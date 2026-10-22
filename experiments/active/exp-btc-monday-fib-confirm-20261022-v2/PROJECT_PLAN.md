# Owner-authorized exit correction, 2026-10-22

Owner: any TP immediately pushes remaining tranches to breakeven. Preserve the four original25percent tranches, intrabar arrow/body independent latches,285minute expiry,0.7percent initial SL, and explicitly restored0.2percent roundtrip cost. Timer acts only if still unprotected. This version changes only the missing TP trigger versus completed v1 arms; frozen223signals and2230controls are reused. No production/model changes.

Execute with Nautilus1.231.0 after builder commit. Initialize once, run disjoint delay workers, then collect and cash/causality review. The0delay arm disables only the timer, never TP protection. Choose n on first entry-year and apply unchanged to second; known historical data, not forward evidence.

Reproduction from the preserved input cache (first initialize once, then run disjoint workers, collect after both finish):

```sh
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --initialize
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --delays 0 15 30 60 120 180
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --delays 240 360 480 720 1440 2880
/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --collect
```

Existing completed runs are preserved. The 240/360 minute arms completed under builder `dbb6123ea1`; the native adapter is unchanged in `96e0e99b7d`, which refines only same-endpoint TP metadata auditing and preserves unchecked native output for debugging. Do not replay completed arms just to change their audit stamp; use `--collect` for saved artifacts. A full new replay requires a separate output directory and an explicit updated input identity.
