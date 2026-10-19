# Exchange timeouts need a committed outbox before send

- **问题**：A trading request can reach the exchange even when the phone or server times out. Retrying a new request can double the position.
- **死胡同**：A SQLite transaction around an HTTP request cannot roll back an external order. An exchange client ID alone is not a permanent deduplication guarantee after the order closes.
- **有效路径**：Persist the reviewed intent, daily reservation and stable exchange ID atomically; commit an attempted marker before sending. Unknown results remain blocked until a read of the original ID resolves them. Never automatically resend an attempted command.
- **通用规则**：Separate accepted intent, submitted command, exchange acknowledgment, actual fill and verified protection. Test a timeout after the fake exchange fills, not only an error before sending.
- **牵连**：`yoyo/discipline_app/execution_journal.py`, `live_book.py`, and `tests/discipline_app/test_execution_journal.py`. Evidence here is offline fault injection, not a real-money execution certification.
