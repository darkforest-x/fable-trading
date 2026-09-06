# Confirmation state is not another transition edge

## Context

V18 hourly impulse research delays only V17's unprofitable fast full exit
until the next native5 opposite bar. The first observation is an aligned to
opposite transition; the confirming observation is opposite to opposite.
Treating both as a flip changes signal counts and falsely invents another
profitable partial opportunity. This is an implementation insight, not a
claim that waiting improves actual returns; no V18 raw replay existed yet.

## Approach

Keep the original real-edge log and counters. Record pending creation,
confirmation, cancellation and higher-priority termination in a separate
lifecycle log. Preserve first-edge trigger time/source/quote; put the later
confirmation source and actual execution price in distinct fill fields.
Each pending gets exactly one adjacent confirmation chance. Cancellation
consumes the edge; a new action requires actual alignment then another flip.
Profit recovered at the confirming open cancels full exit but does not create
a half without a new real edge. Intrabar hard stop during waiting remains
prior to the next opening decision, with the engine's existing bar-end clock.

## Evidence and reuse

tests/test_hourly_impulse_failed_confirm.py covers117 synthetic boundaries,
including both directions, exact fee equality, nextafter quotes, resets,
current unseen HLC, hard-stop priority and explicit1/default old-column parity.
tests/test_hourly_impulse_failed_confirm_research.py covers full-population
mechanics and unknowns. The source-clock review corrected an auditor's
bar-open stop assumption by reading finish(now+FIVE_MINUTES) directly.
For any debounce/confirmation policy, separate triggering events from
persistent states, and distinguish observed-at from executed-at and censored
last-known time. Additional logging must not grant new execution authority.
