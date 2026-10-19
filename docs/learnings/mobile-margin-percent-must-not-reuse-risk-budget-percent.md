# Margin allocation percent and risk budget percent need different contracts

- **Problem**: A slider labelled position size represented a fraction of the stop-loss budget, while the owner meant margin divided by account equity.
- **Dead end**: Renaming the slider would preserve the wrong amount. Multiplying equity by leverage without applying the initial stop would also ignore the cash risk ceiling.
- **Effective path**: Add explicit margin sizing, cap BTC/ETH collectively at 10% and alts collectively at 5%, reserve existing positions and pending orders, then take the minimum quantity allowed by collateral, exposure, and the 5% cash-risk budget. Render immediate estimates but recompute authoritative values on the server. Tightening limits affects new entries without rewriting active exit plans or counters.
- **Rule**: Confirm the denominator and unit before implementing a percentage. Display the margin, notional, and planned stop loss separately.
- **Scope**: `policy.py`, `book.py`, `mobile/src/ticketEstimate.ts`; legacy documents retain absent caps until explicit activation. Synthetic tests verify cash risk, category reservations, legacy requests, and preserved active plans.
