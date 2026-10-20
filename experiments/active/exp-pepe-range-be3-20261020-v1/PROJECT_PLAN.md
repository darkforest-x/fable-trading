# PEPE 3R cost-breakeven comparison

- Owner authorized “去做吧” after the proposed first experiment: add cost breakeven at 3R, preserve entry and other exit rules, measure saved losses and sacrificed 10R/20R winners.
- Primary single variable: enable 3R cost breakeven on confirmed 15m close. No grid or new selection. Freeze 5 completed 5m extrema plus 0.05% buffer, unrestricted risk cap, 5R activation of 15m 4ATR trailing, and armed H1 reversal exit.
- Preserve the previous raw ledger as legacy. Both scientific comparison arms share the known 5R price-domain numerical correction; report its separate legacy delta. The new 3R comparison uses the same tolerance. Do not silently change the original Pine.
- Breakeven price covers the frozen 20bp entry-notional cost, rounded toward profit. A pending trigger cannot create a stop above the current long price / below the current short price; require one tick of room at a 15m close. Gap execution can still lose after protection.
- Reuse original OKX data, causal features, original independent entries, deterministic matched random IDs and chronological segments. No downloads, content hashes, source replacement, or new production data.
- Before results, commit builder and frozen configuration. Check hand-built ordering, cost, long/short, tick and boundary cases; independently replay every selected path and match the full legacy ledger.
- Compare all original same-entry paths including losers and tail winners, with absolute signal/random performance and paired policy improvement. AUC and model-ranking top-decile metrics are inapplicable because this is an exit-policy experiment.
- Preserve necessary code, raw results and receipts; register and update the existing Notion research context. No standalone report, Pine changes, live orders, training, promotion, branch or worktree.
