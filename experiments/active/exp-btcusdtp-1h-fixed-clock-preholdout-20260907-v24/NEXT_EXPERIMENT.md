# Next bounded step: current K1 structure event, support first

This is a proposal, not an executed experiment or observed improvement. Register
and commit a separate V25 contract/builder/tests before reading its gate support
or joining any V24 markout. V24 remains not_supported; no retrospective edits.

## Why this one change

The existing K1 cohort does not show sufficient fixed-clock persistence. V20's
persistent same-direction confirmed structure gate was rejected. Test a narrower
mechanism: K1 itself establishes/reverses the confirmed structure, rather than
merely occurring while a previous state remains aligned. This is a reused
structure-family hypothesis, not an independent discovery. No event gate has
been scored here and no cutoffs were fitted to the V24 winner groups.

## Frozen semantics for the support proposal

Use the V20 frozen10-left/10-right tie-inclusive confirmed-pivot approximation
in yoyo/data/hourly_impulse_structure.py. Same old known/unknown mapping:
missing own complete hour or structure_known false is unknown. Among known
requests, accept iff structure_break_on_k1 is true AND
structure_break_direction equals own direction; otherwise observed abstain.
Do not relabel a not-yet-established state as known merely to enlarge coverage.
No changes to pivot length, MA40, candles, timeframe, cost or exit parameters.

Attach the exact own hour (signal_time == decision_time−1h), requiring its
structure_available_at == decision_time; never nearest/asof/forward fill.
Own control contexts use their own clocks/directions, not their case's event.
Past pivot origins alone are not availability: confirmed_at may not exceed E.
Gap reset, stable level price guard and alternating break-state semantics stay.

## Candidate saved-only inputs

- V24 results/case_requests.csv.gz SHA0444142d5d99f6013bcbf9ff160d307818316130177e3e2d48fad7abf38cbe27; all251 own intentions.
- V24 results/control_requests.csv.gz SHA327c655fb1f989467d4ccc5ea4c9f6293a7a976e594ad853e7ebb2cbc8ef2a71; original744 random controls retained, no redraw.
- V24 random_assignments/allocation and sampling_frozen must be pinned from the existing manifests;3 unmatched retained.
- V20 results/hourly_trace.csv.gz SHAbe9b9e73108047ada00ffac0ed0c4d5b2a3c137000435872c2d33c4ec1cbf7dd. A timestamp/schema-only feasibility check found18222 rows from2022-11-30 16:00 to2024-12-28 22:00UTC; V24 latest control E2024-12-28 23:00UTC is within that outer range. This is NOT proof every required hour/field is present or of any gate count.

The exact V20 source/checkpoint/resume lineage must be checked, including its
historical recoverable failure, before reuse. Do not mistake an old failure.json
for success or silently disregard it. Prefer independently recomputing the
frozen10/10 state from saved complete-hour OHLC to verify clocks, not new raw.
If the saved trace cannot support a request, preserve unknown; do not refill
from another source during this support-only phase.

## Support audit and stop

Keep all251+744 requests and original triple membership. Save accepted/abstain/
unknown identities and per-fold/per-month counts before any outcome read.
Inherit the existing practical support screen: at least80 accepted cases,
12 in each halfyear,12 active months overall and3 active months per halfyear.
Also report all-context known coverage and complete-triple coverage relative
to original251 and accepted cases separately; never silently shift denominators.
These are practical screens, not a prospective power guarantee.

Do not read V24 case/control/paired labels or old execution outcome files in
this phase, including hashes only needed for future outcome joins. Support
failure means support-insufficient, not profitable/unprofitable. Do not loosen
the event definition, drop months, optimize pivot lengths or change minimums
after seeing the count. Report failure honestly if sparse.

If support passes, preregister a separate economic comparison before outcome
join: keep4h primary, original random controls,20bp, all opportunities including
abstentions, own-control gating, unknown accounting, monthly dependence, missed
winners/avoided losers and four chronological cuts. Positive accepted quality
alone is insufficient; require absolute and matched-background evidence.
Fixed-clock economics remain label-only until an independently tested execution
policy exists. Do not promote based on the earlier V24 exploratory period.

No raw/archive/2025+/holdout price reading, training, dependency changes,
TradingView replacement, ACTIVE/frozen change or real-money action is required
for this saved-only support step. The overall profitability goal stays active.
