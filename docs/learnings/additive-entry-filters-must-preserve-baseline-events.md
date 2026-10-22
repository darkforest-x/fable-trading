# Additive entry filters must preserve baseline events

- **Problem**: The owner requested an additional filter for existing5m multitimeframe trade frames. V2.34/V2.35 instead introduced new15m-close entries and consumed each15m observation after one entry.
- **Dead end**: Moving the replacement state machine from the15m chart to the5m chart corrected scope but still replaced the owner's original entry process.
- **Effective path**: Disable the rejected pairing option to restore original entries first. Base the next correction on V2.33 and resolve whether the window limits each entry or validates an observation; this distinction is pending owner clarification.
- **General rule**: An additive admission filter should produce a subset of baseline events, preserving retained timestamps, prices, stops and independent repeated entries. A correct new state machine is not evidence that the requested baseline was preserved.
- **Scope**: `monday_range_v2_33.pine` is the comparison baseline. V2.35's new option is disabled in the current5m chart; the requested replacement filter has not yet been delivered.
