# An omitted zero-balance currency must not kill an idle manual worker

- **问题**：Reloading the manual worker failed with “expected one exchange record”; read-only requests confirmed valid account/balance receipts, no positions, and no USDT detail, including with `ccy=USDT`.
- **死胡同**：Treating every missing currency row as an unavailable account breaks startup after funds have been removed. Inventing zero from a missing or malformed balance response would hide real failures.
- **有效路径**：OKX documents balance responses as non-zero assets only. The manual account reader accepts zero USDT only with an explicit valid details list and no open positions; identity/mode checks remain. Defaults reject unavailable USDT funds. Other execution readers retain their previous strict behavior.
- **通用规则**：Distinguish a documented empty collection from missing data; service liveness must not imply funding or trading eligibility.
- **牵连**：`engine.py`, `manual_engine.py`, `free_manual.py`; [OKX balance contract](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-balance); synthetic empty/malformed/exposed account tests.
