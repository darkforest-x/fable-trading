# A cooldown duration does not define its trigger

- **问题**：The account displayed a three-loss stop rule but a separate legacy rule started a 24-hour cooldown after every losing close. One loss therefore blocked entry.
- **死胡同**：Removing the warning or clearing the timestamp alone would leave the old trigger active at the next settlement. Resetting all counters would erase valid limits.
- **有效路径**：Represent the cooldown trigger explicitly, preserve legacy policies on parse, and migrate only the owner's requested trigger. Reconstruct eligible cooldowns from complete net settlements so a later winner cannot erase a valid three-loss cooldown. Existing position R/TP plans stay fixed; account-level settlement uses the current discipline rule.
- **通用规则**：Model trigger, duration, counter and reset clock independently. Display remaining entries, losses to threshold and equity distance to the daily floor from the same server snapshot.
- **牵连**：`yoyo/discipline_app/policy.py`, `book.py`, `live_book.py`; default three consecutive net losses, 24 hours, Beijing daily clock.
