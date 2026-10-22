# Pine historical state transitions must reach the returned series

- **Problem**: V2.36's helper fixtures passed, but the native chart ledger was empty. Adding diagnostic signal counters and logging produced 77 records, including the owner's real example; removing instrumentation returned to zero.
- **Dead end**: Successful final-bar fixtures and a compile success did not verify historical execution. Leaving diagnostic logging in production would hide the dependency issue and add unnecessary work.
- **Effective path**: Accumulate the matching helper's returned count in a persistent scalar and return it in the snapshot. This makes the historical transition an explicit series dependency, in addition to its nested-array mutations. The uninstrumented production engine then retained all 77 records, with the same 05:40 example and 69 retrospective confirmations.
- **General rule**: Test the final uninstrumented engine against a real historical event. For stateful Pine helpers, ensure historical transitions contribute explicitly to the returned series rather than relying only on nested collection side effects read at the final bar. A fixture suite must also reject a vacuous empty real-history result.
- **Scope**: `txReviewEngine`, `txMatchReview`, V2.36 native fixture appendix and local source guards. No changes to stop/exit parameters or the original fifteen-minute engine.
