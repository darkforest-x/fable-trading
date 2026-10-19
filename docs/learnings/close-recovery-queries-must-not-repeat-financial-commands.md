# Recover close outcomes by identity, never by another close

- **Problem**: A phone restart loses an in-memory close retry, even when the exchange already accepted it.
- **Dead end**: Treating a missing HTTP response as failure would allow a fresh close request; treating a missing query row as proof of non-delivery races a delayed POST.
- **Effective path**: Persist the account-scoped request identity before transmission, query that exact outbox with GET-only exchange reads, and fence absent requests under the same execution lock. A prepared command is retried only by an explicit user action using the original identity and fraction.
- **General rule**: Transport acknowledgements and execution outcomes are separate. Recovery must establish an outcome without producing a new side effect.
- **Connections**: `yoyo/discipline_app/live_book.py`, `/v1/close-requests`, mobile close recovery storage, `tests/discipline_app/test_close_recovery.py`. Local fake exchange verifies no extra writes after a timeout/restart and rejects delayed requests after fencing.
