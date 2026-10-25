# Reset statistics through a display epoch, not by deleting the ledger

- **Problem**: The owner wanted the App's accumulated PnL and completed records cleared. The same closed-trade ledger also supplies loss streaks, daily limits and settlement reconciliation.
- **Dead ends**: Deleting `closed`, receipts or journal rows would silently change trading eligibility. Resetting equity or the confirmed capital would invent account data. These paths were excluded before execution.
- **Effective path**: Under the SQLite writer lock, archive the committed database and record an account-scoped display epoch. Only statistics and completed-record responses filter past trades. Preserve every execution field, fixed capital and journal. Refuse a reset while any managed position, pending order or unfinished intent exists. A request UUID makes retry idempotent.
- **General rule**: Before clearing a dashboard, trace whether its data also governs actions. Separate presentation history from the durable records used for authorization, risk and recovery.
- **Connections**: `yoyo/discipline_app/statistics_reset.py`, `live_book.py`, `tests/discipline_app/test_statistics_reset.py`. Regression checks compare the entire state before/after and verify zero exchange writes.
