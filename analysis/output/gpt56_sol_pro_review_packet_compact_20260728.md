CONTEXT_PACKET_V2 — FABLE_TRADING_STRICT_PROJECT_REVIEW

```json
{
  "task_id": "gpt56-sol-pro-consult-20260728-145417",
  "sentinel": "GPT56_SOL_PRO_RESULT_20260728_145417",
  "task_type": "architecture_and_runtime_safety_review",
  "context_strategy": "compact_prompt_plus_pasted_real_code_bundle",
  "credential_status": "scanner_passed_no_executable_credentials",
  "context_hash": "e1b2acbf55b1f5f8a00014fac6ce165b9b39f2d0d3e6f9d6327f8bdbb45f24cb",
  "evidence_card": "FABLE_TRADING_REAL_CODE_BUNDLE_V1",
  "required_output": ["reasoning_brief", "direct_judgment", "biggest_flaw", "ranked_changes", "tests", "adoption_recommendation"]
}
```

## TASK

Strictly review the real repository evidence in the attached pasted-text card `FABLE_TRADING_REAL_CODE_BUNDLE_V1`, then decide what Codex should implement and test now. This is a real-money-capable crypto trading system. Prioritize fail-closed runtime safety, causal validity, and contract integrity. The requested loop is: GPT 5.6 Sol Pro reviews; Codex verifies and implements; then you re-review the actual diff and test evidence.

## SYSTEM AND CURRENT TRUTH

`fable-trading` is a 15-minute two-layer strategy: YOLO proposes moving-average-density launch candidates, LightGBM ranks them, a forward log feeds an OKX executor with TP/SL/timeout. Historical detectors that only produced hindsight signals were removed. The current honest production state is `detector=none`: update data and resolve tracked rows, but discover no new candidate. Model promotion, ACTIVE/frozen changes, forward-log clearing, position sizing, real orders, thresholds, barriers, costs, and freshness gates require explicit owner approval.

The newest development-window audit found:

- 25,602 short candidates: +26.91 bp gross/trade.
- Matched random short control (coin × month × ATR bucket, same barriers) explains most directional drift.
- Detector incremental selection value: +8.97 bp versus 10 bp fixed round-trip cost, before slippage.
- Owner gold labels show +107 bp matched excess, but only 2/499 were drawn with ≤2 future bars visible. Causal learnability is unknown, not disproven.
- Decisive research step is owner labeling of live-tip-only images; Codex cannot autonomously manufacture that evidence.

No holdout was read. Current full test baseline: `209 passed, 2 skipped`.

## NON-NEGOTIABLES

1. Never read or recommend reading holdout (`signal_time >= 2026-05-04`) without explicit owner approval.
2. No random time splits or feature look-ahead.
3. Do not change thresholds, TP/SL, ATR floor, 10 bp cost, freshness gates, sizing, models, ACTIVE/frozen config, forward-log data, or live orders.
4. Live detection only accepts tip/tip-1/tip-2. A path that only produces after-the-fact signals must not exist.
5. No validated detector means no candidate discovery.
6. Preserve the user's dirty worktree; audit artifacts are uncommitted and user-owned.
7. Each non-trivial fix needs regression tests and a concise learning note.

## CODEX JUDGMENT BEFORE CONSULTATION

The highest-priority autonomous fix appears to be eliminating a production fail-open candidate-source fallback.

Actual attached evidence:

- `scripts/forward_pulse.sh` says missing ultralytics/torch falls back to rule candidates “so the clock still moves.” It exports `FABLE_CANDIDATE_SOURCE=rules`, runs forward tracking, then invokes the executor.
- `src/judgment/forward_types.py` explicitly documents/accepts `rules` as the VPS fallback.
- `src/judgment/forward_scan.py::_rule_candidate_indices` really generates candidates; it does not idle.
- The Python YOLO path already handles absent weights correctly: catches `FileNotFoundError`, logs `detector=none`, emits no new candidates, while tracked rows may still resolve.

This shell fallback contradicts the current iron rule and can bypass the intended missing-detector idle path. Codex's tentative implementation boundary:

1. Production pulse must force/validate source `yolo`, never silently select rules.
2. Candidate-source parsing should reject unsupported values loudly. Preserve explicit rules capability only for offline research entrypoints if needed.
3. If the forward scan fails, do not immediately invoke the executor against a stale forward log in the same pulse. Whether stale but still <30-minute rows may be traded after a failed discovery cycle needs a fail-closed decision.
4. Add tests proving production never selects rules, missing detector yields zero discovery, invalid source fails, and executor dispatch is gated on successful forward tracking.
5. Update stale comments such as “live 6-window”; code now uses tip/tip-1/tip-2.

Second contract gap: research is short-only, but `ForwardRecord` has no side and `executor.open_one` is long-only (`buy` entry, `sell` bracket, hedge `posSide=long`). No short artifact is promoted, so this is not an active mis-trade today. Codex prefers a fail-closed compatibility guard before any future short cutover, not implementing live short order support without owner authorization.

Methodology gap: matched controls are now required in directional result tables. It is unclear whether to integrate them into `src/judgment/train.py` now or keep the matching implementation as a diagnostic until overlap, missing-stratum, and estimator decisions stabilize.

## REAL ATTACHED EVIDENCE

The `FABLE_TRADING_REAL_CODE_BUNDLE_V1` pasted-text attachment contains the actual current contents of 22 files (not paths or summaries):

- project rules and architecture;
- latest matched-control/gold-label audit and learning notes;
- exact audit scripts;
- production pulse, candidate source, YOLO schedule, frozen artifact, forward scan, training, executor, and config code;
- targeted tip, forward, and execution tests.

The bundle has 163,150 characters. Its scanner found zero high-severity credential findings; five warnings are only local absolute filesystem paths in offline diagnostic scripts. No executable credentials are present.

## OPTIONS AND TRADEOFFS

- A — safety fix first: remove production rule fallback, centralize fail-closed validation, gate executor after scan failure, test, update comments. No strategy or live-state change. Codex preference.
- B — matched-control framework first: improves research honesty but leaves a known runtime contradiction and needs estimator design.
- C — implement short execution: solves a future contract, but expands real-money behavior before promotion and exceeds current authority.
- D — wait only for labels: research-wise decisive, but leaves known safety debt.

Strongest counterargument to A: with no detector installed, the executor freshness gate and duplicate ledger may already limit harm, while fallback rules keep the clock alive. Please assess whether that is sufficient or whether a real-money-capable pipeline must make discovery provenance an explicit invariant.

## ASK

Answer explicitly and concretely:

1. Is automatic rule fallback a P0/P1 production defect under this doctrine?
2. What is the smallest correct boundary: shell-only, central Python validation, dedicated production mode, or another design?
3. Should executor dispatch be skipped when forward tracking fails, even if older log rows remain fresh?
4. Give exact regression tests and an acceptance checklist.
5. Should Codex add only a short-direction compatibility guard now, or defer all side work?
6. Should matched controls be integrated now, and at what layer?
7. Rank at most three changes for this iteration and state what must not change.

Do not provide generic encouragement. Do not reveal hidden chain-of-thought; provide a concise reasoning brief with assumptions, decision frame, evidence weighting, strongest counterargument, and tradeoffs.

## RETURN FORMAT

First line exactly: `GPT56_SOL_PRO_RESULT_20260728_145417`

Then:

1. Reasoning brief
2. Direct judgment
3. Biggest flaw
4. Ranked required changes (maximum three)
5. Exact implementation boundary
6. Exact tests and acceptance checklist
7. What to defer
8. Final adoption recommendation
