# Censored fields must remain censored in the displayed CSV as well

- **问题**：Frozen trade records may retain data-boundary exit prices, PnL and holding durations even when closed=false. A detailed report must preserve the raw record while preventing those values from looking realized.
- **死胡同**：Adding null display aliases fixes the table but spreading the original row into a display CSV silently exports the original non-null outcome fields alongside them.
- **有效路径**：Keep two explicit exports: an unchanged raw ledger with a nearby boundary-field warning, and a display export that projects only displayed fields. Mask realized PnL, fees, exit and holding duration for censored rows. Verify an unresolved example and the downloaded CSV.
- **通用规则**：Treat the raw ledger, visible table and downloadable display projection as separate contracts. Check closed=false through all three, rather than assuming a blank cell implies a blank export.
- **牵连**：AltN5Chapter.jsx; ResearchEvidence.jsx; original 9,065-row N5 ledger remains unchanged. This does not alter execution or infer missing outcomes.
