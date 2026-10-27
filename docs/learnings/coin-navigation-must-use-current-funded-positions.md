# Coin navigation must use current funded exposure before trade history

- **Problem**: Selecting a touched coin retained an unrelated simulated account, and the latest-fill lookup could choose a newer closed trade over a remaining open position.
- **Dead end**: A symbol's signal, shadow record, pending add or latest historical fill cannot establish which account currently holds exposure. Recomputing the account on every poll would also overwrite a subsequent manual selection.
- **Effective path**: Select among funded, filled open positions with an active stage and positive remaining notional. Retain the current holder; otherwise follow the persisted picker order; choose spike when no account holds the symbol. Apply this only during coin navigation, atomically with the symbol update. Prefer an open position for chart selection, retaining historical fallback when that account has none.
- **General rule**: Navigation defaults should derive from current domain state at the user's action boundary. Historical chronology and automatic polling must not override explicit selection.
- **Scope**: reclaim-watchlist.js, monday-reclaim-paper.js, reclaim-strategy-book.js and frontend_reclaim_watchlist.test.cjs. No account or order mutations.
