# Chart timeframe is part of a multitimeframe signal contract

- **Problem**: V2.34 put the requested15m/5m confirmation window on the15m chart, adding an unintended H1 entry gate. The owner meant the5m chart route.
- **Dead end**: Correct pairing arithmetic and causal closes did not establish the intended product behavior; tests exercised the helper without asserting its owning chart route.
- **Effective path**: V2.35 routes the window only on5m charts, starts candidates directly at confirmed15m arrows, and preserves the original H1-to15m route. Native integration fixtures cover both routes, simultaneous closes, the disabled option, and H1 independence for5m entry.
- **General rule**: Name the chart, observation clock, confirmation clock and execution clock separately; test routing through the same admission function used by production, not only a correct helper.
- **Scope**: `yoyo/evaluation/pine/monday_range_v2_35.pine` and its native fixtures. Exit management and key-level signals remain unchanged.
