# Owner-authorized exit correction, 2026-10-22

Owner: any TP immediately pushes remaining tranches to breakeven. Preserve the four original25percent tranches, intrabar arrow/body independent latches,285minute expiry,0.7percent initial SL, and explicitly restored0.2percent roundtrip cost. Timer acts only if still unprotected. This version changes only the missing TP trigger versus completed v1 arms; frozen223signals and2230controls are reused. No production/model changes.

Execute with Nautilus1.231.0 after builder commit. Initialize once, run disjoint delay workers, then collect and cash/causality review. The0delay arm disables only the timer, never TP protection. Choose n on first entry-year and apply unchanged to second; known historical data, not forward evidence.

Commands: isolated nautilus/bin/python -m yoyo.evaluation.monday_fib_tp_breakeven --initialize; --delays0 15 30 60 120 180; second worker --delays240 360 480 720 1440 2880; finally --collect. Use spaces between option and values.
