# Entry-price protection and cost breakeven are different actions

- **问题**：Users need a direct entry-price stop button alongside the automatic fee-aware 1R breakeven rule.
- **死胡同**：Reusing the label without checking the exchange stop can loosen an already better stop. Resubmitting on timeout can also repeat a write.
- **有效路径**：Derive the target from immutable actual entry and contract tick, verify a fresh price on the profitable side, and keep a stronger stop unchanged. Use a stable position action ID with the existing durable amendment/read-back path. Preserve TP quantities and triggers. Fake exchange tests cover both directions and repeated taps.
- **通用规则**：A protection shortcut must define its price basis, preserve monotonic protection and reconcile exchange acceptance separately from application.
- **牵连**：`LiveBook.entry_stop`, `Book.auto_stop`, `PositionCard.tsx`. One-R entry calculation uses equity risk including costs and conservative tick rounding; entry-price stop does not cover fees.
