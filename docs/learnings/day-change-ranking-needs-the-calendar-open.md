# Today's contract ranking must use the calendar open

- **问题**：A request to sort today's gainers is not the same as a rolling 24-hour ticker ranking.
- **死胡同**：Using `open24h` under a “today” label silently changes the time interval. Treating missing day-open data as zero fabricates a rank.
- **有效路径**：Use OKX `sodUtc8` for the Beijing calendar day, reject invalid opening prices, and show missing/stale values as unknown. Snapshot sorting when the user chooses a ranking so polling cannot move a row under a finger; prices may still update.
- **通用规则**：Name and validate the clock before ranking; keep ordering interaction stable while updating quote values.
- **牵连**：`market.today_change`, `ContractOptions.tsx`; official source https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers .
