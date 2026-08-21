# Close-only exits are not reversals

## Problem

The Pine replay supported `opposite_signal_action="close_only"`, but both a
close-only exit and a true close-and-reopen reversal were recorded with
`exit_reason="reverse"`. Cash accounting was unchanged, yet the ledger erased
which policy action actually occurred.

## Dead end

Infer the action later from the next trade. A skipped reopen can change
cooldown, position and subsequent candidate execution, so there may be no
adjacent trade from which to reconstruct the original decision reliably.

## Effective path

Emit `reverse` only when the accepted opposite signal closes the current
position and is allowed to reopen the new side from the same next-open event.
Emit `opposite_signal_close_only` when that signal closes without reopening.
Pin both meanings in the state-aware LR contract and a simulator regression
test.

## General rule

Execution reason codes must identify policy actions, not merely share an exit
price or trigger. Distinct actions that happen at the same timestamp still need
distinct ledger vocabulary when downstream labels, state replay or accounting
will interpret them.

## Implications

- Close-only and reversal rows must not be merged into one LR outcome class.
- Existing historical reports remain records of their former vocabulary; new
  replays use the corrected reason code.
- The change does not alter fills, commission, P&L, cooldown or order timing.
- Governing code: `yoyo/layers/l3_backtest/pine_allin_v7.py` and
  `scripts/design_pine_eth_15m_state_aware_lr_contract.py`.
