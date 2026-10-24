# Check passive-limit geometry after converting prices to the execution grid

- **Problem**: Five MA6 candidate limits were marginally favorable as floating values but equal to the submission close after native tick rounding. The native validator correctly rejected them, aborting four asset jobs before execution completed.
- **Dead end**: The signal's strict float inequality did not establish a valid passive order on the execution grid. Weakening the native guard would silently turn a limit strategy into marketable entry behavior.
- **Effective path**: Keep the native guard. After quantization, mark only those candidates `invalid_tick_geometry`, retain their opportunity/order rows, and send the other valid orders to the native framework. The focused regression retains a valid sibling while rejecting the collapsed order. Complete existing assets are not replayed.
- **General rule**: Validate order geometry in the same price representation used by execution. An invalid candidate should not erase the rest of an asset's evidence.
- **Scope**: `monday_ma6_retest.normalized_native`; audit in `exp-monday-ma6-retest-20261024-v1/initial-limit-rejection-audit.json`. No new threshold, target, fee, signal timing or fill engine.
