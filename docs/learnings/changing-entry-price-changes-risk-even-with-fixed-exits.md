# A different entry price changes risk even when exit prices stay fixed

- **Problem**: Entering after rebound was described as preserving spike's P18 and BE17 rules, inviting the assumption that loss remained18percent and the protection was still breakeven for the new trade.
- **Dead end**: Reusing the original rule names or a simplified5x margin overlay obscures that both prices are anchored to1.618, not the delayed fill.
- **Effective path**: Carry the frozen level explicitly as `reclaim_ref`; separately calculate distance from actual entry to that level's stop. In106 close entries,48 have stop distance at least19percent and24 at least20percent. HEMI's close entry0.011607 has stop0.009147624, a21.19percent distance. Its old BE17 price is also below the new entry.
- **General rule**: When changing entry timing, recalculate target room, stop distance, protective-price PnL and margin feasibility from the actual fill. Keeping absolute exits fixed does not keep trade risk fixed. Do not automatically move exits or approve leverage; those are distinct decisions.
- **Dependencies**: Frozen `monday_limit_paper_accounts.py` rules, native `reclaim_ref`, and OKX mark-price/maintenance-margin semantics. This study reports independent returns; it does not validate exchange-account liquidation.
