"""Paper exchange for the Discord copier ("模拟实盘", owner request 2026-10-07).

The copier's ``dry_run`` was never a paper account: it sized orders from the real
account balance (OKX key revoked, Gate balances ~0, so plans were errors or zero
size) and never followed a trade to its exit. This package is a fourth
"exchange", ``paper``, implementing the same TradingClient / executor seams the
router already uses, so a paper-routed channel goes through the identical parse ->
risk -> entry -> management path as live, and only the final order lands in a
local ledger.

Fixed assumptions (shown in the UI, not tuned):
* one isolated account per Discord channel, starting equity 1000 USDT;
* sizing, leverage, entry price and deviation guard reuse the OKX executor's
  functions with the channel's live settings;
* fills come from public last prices (OKX, Gate fallback) polled every few
  seconds -- limit entries fill when crossed, stops/targets/liquidation trigger on
  the polled price, so gaps between polls are not seen;
* cost 0.1% of notional per side (0.2% round trip, the repository default);
  funding and order-book impact are not modelled.

Nothing here talks to an authenticated exchange endpoint.
"""
