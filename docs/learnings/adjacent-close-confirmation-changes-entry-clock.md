# Adjacent-close confirmation changes the execution clock

- **Problem**: Adding a second15m close after the original full-body confirmation looks like a signal filter, but the entry happens later at another price.
- **Dead end**: Filtering old PnL by the second close would retain the earlier entry and stop. Latching the first body indefinitely would also accept a non-adjacent confirmation that the owner did not request.
- **Effective path**: Search for the earliest valid adjacent pair, enter only at the second completed close, keep the adverse-excursion gate live through actual entry and calculate the stop from that entry. Replay native exits. Compare old and new trades by weekly-side opportunity while explicitly recording changed entry times.
- **General rule**: A new confirmation timestamp requires checking every dependent clock: entry, pre-entry invalidation, stop anchoring, random-control matching and paired comparisons. Never backdate a confirmed signal.
- **Scope**: `monday_fib_confirmation.py`, `monday_fib_tp_breakeven.py`, direct minute-tape review and `exp-btc-monday-fib-body-20261022-v8`. Earlier results remain historical evidence.
