# Fixed chart orders need a server-bound range and execution policy

- **问题**：A read-only 1.618 field does not prevent a preview from being reused with a different price, side, market action, or expired weekly range.
- **死胡同**：Frontend-only restrictions leave confirmation and delayed workers with the old free-order behavior. A display fallback alone is also not an executable range contract.
- **有效路径**：Resolve completed chart ranges server-side, round mirrored entry/target prices to exchange ticks, bind paper quotes and live intents to direction/price/range lifetime, and require the updated worker revision. Home live orders are post-only; paper confirmation rejects a crossed book. Monday's displayed previous range has an explicit manual lifetime ending when the new Monday closes. Automatic scanners keep their original causal reader.
- **通用规则**：Carry the reviewed policy through preview, confirmation and worker revalidation; never infer execution authority from disabled inputs or a display cache.
- **牵连**：`yoyo/contracts/monday_order.py`, `monday_chart_levels.py`, `free_manual.py`, `monday_ticket.py`, `paper_desk_limits.py`; weekend, rollover, forged-price and quote-reuse tests.
