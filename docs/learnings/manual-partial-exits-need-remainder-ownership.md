# Partial exits must preserve ownership of the remaining position

- **问题**：The manual ticket originally matched current position size to the entry fill. A partial close invalidates that equality and can strand both position management and oversized protection.
- **死胡同**：Changing only the close percentage in the form is insufficient. Treating every close as terminal would also cancel protection for the remaining position.
- **有效路径**：Bind a preview to the current position and protection, quantize the requested fraction, confirm the exact close receipt, persist cumulative closed size, and reconcile native protection at the remaining size. Persist every POST boundary before sending; an ambiguous response only permits GET reconciliation. Verify replacement protection before canceling the old protection when its type changes.
- **通用规则**：Model original fill, confirmed reductions, and current remainder separately; UI success requires exchange receipts for every consequential step.
- **牵连**：`yoyo/okx_execution/free_management.py`, `monday-ledger.js`; synthetic partial/restart/ambiguous-receipt tests; OKX V5 long/short closing orders and algo amendment semantics.
