# Account switching must select an immutable ledger identity

- **问题**：Adding mobile account switching must not send a delayed response or order to the wrong account, reset risk history, or stop protection on an account no longer visible.
- **死胡同**：A single mutable global active-account pointer would make concurrent phones and outstanding requests race. Creating a new ledger for every API key would let the same OKX UID obtain a new risk budget. Holding the registry lock during enrollment network IO would also delay shared quotes.
- **有效路径**：Route each request with an explicit account ID, bind each ledger to the verified UID, reject duplicate identities, retain the original primary ledger, and keep all account workers running. The client invalidates old polling generations when switching. Enrollment has its own lock; GET verification never blocks existing quote fanout. Validation errors omit raw request input, so credentials cannot be reflected.
- **通用规则**：Account selection changes routing and view state, never accounting identity. Verify the no-reset, no-cross-account, and no-protection-stop properties before exposing the switcher.
- **牵连**：`yoyo/discipline_app/accounts.py`, `api.py`, `mobile/src/App.tsx`; credentials remain outside the repository with owner-only permissions. Tests use fictional exchanges.
