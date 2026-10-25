# Zero balance is data, not a connection failure

- **Problem**: The App retained an old account value and reported a connection error after the USDT balance became zero. `_fetch` rejected nonpositive or omitted USDT rows before updating the display snapshot.
- **Dead ends**: Clearing the displayed number without reading the account would manufacture a value. Treating an empty or malformed HTTP response as a zero balance would conceal a real read failure.
- **Effective path**: Accept an authenticated, structurally valid balance response. OKX omits zero-balance currency details, so absence from a valid `details` list represents zero USDT. Store that observation and continue reconciliation; reject new entries separately when equity is nonpositive. Malformed responses still retain the last known observation and report failure.
- **General rule**: Separate read validity from action eligibility. A valid state with no available capacity should still update the UI.
- **Connections**: `yoyo/discipline_app/live_book.py`, `tests/discipline_app/test_zero_balance.py`; [OKX Get balance documentation](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-balance). No trading thresholds changed.
