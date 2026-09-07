# V38 — confirmation before exposure: a clock feasibility audit

Frozen before new source materialization. Origin: rejected V37, not blind data.
This is a non-economic audit, not a scored delayed-entry trading policy.

## Scope and question

Same approved Binance USD-M BTCUSDT2023-2024, original63case/108control requests,
36originalthree-control groups and27unmatched. No outcomes, realised arming labels,
return ranks or exit fields enter request inputs. No new matching or signal search.
Question: for each original K2 request, can we identify the earliest confirmed,
positive-risk next-open eligibility before its fixed reference stop is breached?

## Explicit new research protocol, not old source behavior

E=K2.close, native5 SMA40(HL2) as before. At E inspect only the new OPEN for risk,
and the valid complete seed[E-5m,E) for colour. An aligned seed can qualify at E.
The seed's low/high formed K2 and MUST NOT retrospectively invalidate waiting.
An opposite seed waits for the first valid same-direction STATE; no extra edge,
two-bar confirmation, slope, body, volume or numerical gate is introduced.

At each later boundary C: validate completed waiting bar[C-5m,C); any low<=stop
for long or high>=stop for short invalidates the request before close-colour
confirmation. Then validate actual C OPEN and risk direction, before confirming
same-direction native colour. Never use the future HLC of the bar opening at C.
A missing/invalid earlier raw or management observation censors the FIRST-entry
clock as unknown; do not resume across missing history pretending first is known.
No resetting a breached setup, moving its stop, or substituting the next OPEN.

All original decisions are hourly. Administrative audit cutoff T=E+1hour,
including the last completed native5 available at T and at most T's OPEN.
This observes one decision-hour; it is NOT an entry expiry, a72h waiting window,
or permission to score unconfirmed requests as cash0. Pending at T is right-censored.

Events: eligible, invalidated_open, invalidated_wait_bar, unknown_raw,
unknown_management, pending_at_cutoff. Preserve all requests, reasons and
visited-boundary traces. Only eligible events have reference-open risk/ATR;
this is feasibility, not an executed fill or realised PnL. Every control uses
its original fixed synthetic stop, never reanchored to its delayed OPEN.

## Checks and negative controls

Freeze exact builder/config/plan AND both test files before the one-shot audit.
Verify original receipt hashes, timestamp-only2023-2024 projection, unique IDs,
unchanged identity/stop/ATR, all36originalparentlinks and cutoff before fold end.
Same24 source hashes and native SMA only; no outcomes importer or backtest call.

Synthetic boundary, same-bar conflict, seed-touch, gap-open, missing raw/colour,
malformed identity/time, mirror/scale and future mutation checks. No Hypothesis
installation: finite parameterized tests, not a claim of random domain coverage.
Negative control: an intentionally wrong current-bar-extreme check must react
to injected future HLC while the correct audit remains invariant. All real171
events additionally replay from40bar local SMA warmup to their terminal boundary,
using only that boundary OPEN and masking its future HLC; compare terminal
events and traces with the full-source run. Opaque segment names are normalized
within each source domain; unknowns and segment-change patterns must match.
Mismatches fail closed, not relaxed. Final builder/input bytes are reverified;
an unrelated HEAD advancement is recorded, not itself input drift.

Counts/percentages by cohort, seed and four halfyears; preserve unknown/risk
null reasons. Report matched support as support, not an economic contrast.
No valAUC, PF, winrate, top-decile return or p: there is no outcome target or new
economic arm. Their required null control is causal prefix/mutation detection,
not invented profitability. Audit success clears code semantics only, not strategy.

## Delivery and next economic decision

MD immediately to HTML, canonical source-backed report with actual SQLite count
query, plus small review notebook of saved outputs. Outcome-independent code
review before source run, independent results review before packaging. Notebook
may use explicit stdlib sequential execution if Jupyter is absent; disclose it.

After audit, entry lifetime and post-delay holding deadline must be separately
frozen before an economic comparison. Existing72h starts at actual entry in the
engine: passing a delayed decision silently shifts the original request horizon.
Do not transplant V37 exit/PnL labels into new fills. Do not widen data scope or
change costs/stop/TP/presets without the corresponding owner authority.
No TV, ACTIVE, training, order, dependency or holdout changes. V37 stays rejected;
the goal remains robust net-positive returns, not successful audit software.
