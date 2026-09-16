# Owner manual trading protocol v2

Owner clarification on 2026-09-16: the intended deliverable is a human-operated trading system, retaining moving-average compression and YOLO support. It is not an automated execution implementation.

Scope: translate the verified personal-ledger findings and existing indicator rules into pre-trade, order, position-management, and review instructions. Preserve original audit and strategy reports. No market-data access, new backtest, training, model promotion, or account action.

Deliverables: `analysis/p1_owner_manual_trading_system_20260916.md`, its local HTML, and a concise `analysis/p1_owner_manual_order_card_20260916.md` / HTML. Notion captures a new unverified manual version linked to the previous proposal. A delivery manifest binds sources and final text.

The new discretionary screening, default timeframe, admission expiry, adverse-entry-distance cap, and account limits are proposals. They must not inherit the original V9 or BB backtest returns. Original source logic and proposed human choices are separately identified. Source files are committed before final manifest generation; the existing committed Markdown converter renders each report immediately.

Validation: reconcile displayed indicator semantics, validate arithmetic examples, check links and Markdown/HTML structure, and review the order lifecycle for missing or unobservable steps. No directional performance metrics are created for this document-only round. Model and production eligibility remain false.

User inputs still useful: capital, acceptable drawdown, and time available to supervise positions. Defaults are explicit rather than fabricated user facts.
