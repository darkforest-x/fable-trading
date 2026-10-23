# Equity exposure, collateral leverage and stop distance need separate contracts

- **Problem**: The owner wanted BTC/ETH 10x and shared-alt 5x total equity exposure, exchange-maximum leverage, and wick stops without 1R sizing.
- **Dead end**: Reusing the discipline app's whole estimator would silently keep its risk-distance cap and fixed leverage caps. Multiplying available funds by leverage would produce a different position whenever collateral or leverage changed.
- **Effective path**: Reuse only the app's independent BTC/ETH and shared-alt allowance semantics. Subtract gross positions, pending opening orders and unresolved confirmed intents. Size contracts from remaining equity allowance, then select the matching exchange leverage tier. Recheck the cap in the execution worker and in the paper ledger's transaction, including delayed limit fills. Stops remain fixed prices and do not enter the sizing formula.
- **General rule**: State which account quantity controls exposure before reusing an order estimator. Keep exposure, collateral and loss estimates independent, and reserve asynchronous orders in the same budget as positions.
- **Touches**: `yoyo/contracts/manual_exposure.py`, `yoyo/okx_execution/free_exposure.py`, `free_manual.py`, `yoyo/research_workspace/monday_ticket.py`; owner confirmation remains the only route to exchange writes.
