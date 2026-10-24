# Unclassified session gaps cannot prove barrier survival

- **Problem**: Spot-gold minutes contain both expected market closures and missing observations. Conflict cleaning alone does not identify which timestamp gaps are safe for a live stop or target.
- **Dead end**: Reusing conflict-only segmentation preserves long holding windows but implicitly assumes that every other gap contains no barrier event. Censoring only before confirmation still leaves the same problem after an entry-price stop is armed.
- **Effective path**: Without a verified historical session calendar, conservatively end the observable first-touch prefix and any open position before the first missing execution interval. Retain these positions as unresolved, never scored at an invented stop or target. Keep normal-closure ambiguity visible instead of inferring a calendar from the outcome.
- **General rule**: A missing bar is not evidence that an order survived. Audit observation continuity independently for signal admission and the entire holding path.
- **Scope**: Monday limit/reclaim gold input and `segment_ends`; identified by read-only Luna review before any gold replay. Initial BTC and1000SHIB outputs use the previous builder unchanged because crypto already applied timestamp segmentation. Preserve both source identities and successful results rather than rerunning unaffected assets.
