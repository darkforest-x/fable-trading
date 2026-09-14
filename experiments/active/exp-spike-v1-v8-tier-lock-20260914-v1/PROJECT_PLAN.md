# V1 / V8: two-stage observed-MFE protection

Owner's 2026-09-14 request: compare `0.5R -> entry` plus `1.5R -> +0.5R` with both original exits and the preceding BE05 study, by timeframe.

## Fixed contract

- Scope is the preceding authenticated Binance/OKX/Gate 30m/1H/4H pool, 2024-09-10 inclusive to 2026-09-10 exclusive UTC; split at 2025-09-10. No new fetch or symbol selection.
- Preserve original signal, actual next-open entry, frozen initial risk, cost0.002 round trip and original strategy-specific trailing/reverse contract.
- Test an already active stop and opening gaps before observing each completed candle's favorable high/low. Stage1 at observed MFE>=0.5R protects at entry; stage2 at observed MFE>=1.5R protects at entry+side*0.5R. Both updates become effective next candle; one candle may reach both stages. Never loosen an already tighter stop, never lower stage, never use final-trade MFE as an entry/earlier-exit input.
- Stage2 price is conservatively quantized to the instrument tick: long floor, short ceil. These are gross price levels, not fee-adjusted net profit guarantees. Gaps can fill beyond the stop.
- Only one new intervention versus BE05: add the second protection stage. No search over other levels, entry changes or account sizing.

## Strategy and control identities

1. Native original V1 long-only uses frozen6185 events and its native signal-reference protection path. Do not invent native shorts or common opposite exits.
2. V8 retains frozen both-direction admission and common-execution contract. Fixed original-entry and independent serial tier replays are separate.
3. Common-execution V1 is supplemental and separately labelled, never substituted for native V1.

Reuse completed baseline/BE05 outcomes from `exp-spike-v1-v8-be05-20260914-v1` after receipt/hash checks. Old study files remain unchanged. Small compatibility replays must match old outcomes; new full-tier results cover every expected stream/event. Do not rerun the previous expensive archival engine merely to duplicate already authenticated evidence.

## Evaluation and chronology

- Primary three-arm comparison: same original entry and all three exits observed. Pairwise baseline-BE, baseline-tier and BE-tier tables disclose their own censoring footprints; independent closed ledgers and serial reentries are supplementary.
- Report net win rate, mean/sum R, PF(R)/nominal PF, event drawdown (not account drawdown), original realized>=10R retention, reduced losses/harmed winners, stage counts, timeframes, years and V8 sides.
- Earlier-year results exclude exits across the cut. Whole-week bootstrap intervals describe reused history, not new blind OOS. No matched random-entry alpha or AUC claim: the controlled variable is exit on the same entry.
- Owner already permits research across all historical dates. This new fixed tier configuration reuses previously consumed history. Register every sample/full attempt; configuration numbering must not be presented as a count of physical reads.
- Commit builders before market evaluation, retain failures and source identities, run one CPU worker per delegate. No global monkeypatch of old engines, no old-file edits, no new dependency, branch, worktree or nested delegates.

## Ownership and delivery

- Parent: plan, registries, saved-outcome aggregation, report/HTML, Notion.
- Terra High native_v1_be05: new native tier replay/test module and `native_v1/` only.
- Terra High v1_v8_be05: new common tier replay/test module and `common/` only.

Deliver `analysis/p1_spike_v1_v8_tier_lock_20260914.md` and matching HTML with hash-bound detailed CSVs and a version-linked Notion record. No Pine, live-monitor, notification, account, ACTIVE or paused-automation changes.
