# Contract fees must follow the instrument fee group

- **问题**：Live USDT perpetual fee validation found an empty legacy `taker` field despite valid account authentication.
- **死胡同**：Treating an empty field as zero, or selecting another contract family's fee, would understate risk. Passing both `instFamily` and `groupId` is rejected by OKX.
- **有效路径**：Read the instrument's `groupId`, request rates for its `instFamily`, and select the matching `feeGroup` row. Missing or excessive rates stop entry before any order write.
- **通用规则**：Validate the market-unit contract and fee contract together. Successful authentication proves neither schema parity nor correct trading costs.
- **牵连**：`market.py`, `live_book.py`, and [OKX fee documentation](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-fee-rates). Read-only BTC, ETH and SOL checks passed on 2026-10-18; no trade was submitted for that check.
