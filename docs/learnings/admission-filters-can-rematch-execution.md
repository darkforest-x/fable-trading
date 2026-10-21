# Candidate filters can change surviving trades, not just remove rows

- **Problem**: V2.31 adds a midpoint-side eligibility rule before wick/key matching. Most annual signals disappear or remain identical, but two BTC arrows match a later touch and change both entry and stop.
- **Dead end to avoid**: Filtering the old execution ledger by surviving arrow IDs would silently retain those two obsolete entries and risks. Replaying everything would waste already verified independent paths.
- **Effective path**: Match signal identities before reading outcomes; compare touch, entry, stop and key fields. Reuse 142 unchanged signal/control pairs, replay two rematched pairs through the same native engine, and assert every execution input after merging. Keep each old control time only after rechecking its week, chronological half and volatility bucket, then rescale its stop to the new signal risk.
- **General rule**: An upstream eligibility change can alter the selected candidate within an otherwise unchanged signal. Cache execution by execution inputs, not signal ID alone.
- **Scope**: `yoyo/evaluation/range_v231_returns.py`, `tests/evaluation/test_range_v231_returns.py`; no exit, fee, position-sizing or live-trading change.
