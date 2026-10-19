# Removing a confirmation dialog must preserve the server intent boundary

- **问题**：Owner requested immediate entry/close clicks with secondary confirmation disabled by default.
- **死胡同**：Skipping the dialog cannot mean skipping server sizing, policy revision, account selection, or deduplication. A new close request ID while an earlier market close remains pending could submit twice even if the UI disables the button during the first HTTP request.
- **有效路径**：A click performs a fresh server preview and sends the same validated intent through the existing outbox; unknown outcomes retain the original request ID. Optional dialogs use the same submission path. The backend checks preceding closes for the position before accepting a new close nonce. Immediate policy activation is an explicit local operator action on a flat ledger and preserves counters, cooldown and history.
- **通用规则**：Separate presentation confirmation from user intent, durable order identity, and risk enforcement; exercise both switch settings and delayed exchange outcomes.
- **牵连**：`mobile/src/App.tsx`, `live_book.py`, `book.py`, `tests/discipline_app/test_accounts_catalog.py`; UI execution tests use synthetic accounts, never real financial orders.
