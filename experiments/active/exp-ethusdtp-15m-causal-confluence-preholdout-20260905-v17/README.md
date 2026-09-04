# ETHUSDT.P 15m causal confluence V17

This experiment asks one narrow question: can a causal entry gate remove false
V16 starts while keeping the right tail of the frozen gradual-profit runner?

Everything after entry is frozen to V16.  The only selected variable is one
pre-registered confluence gate.  The gates use information available at the
close of K2: complete ETH 1h/4h context, complete BTC 15m/1h context, trailing
ETH volume, trailing volatility expansion, and trailing structure quality.

Selection uses only the committed V16 2023--2024 ledger.  The already-seen
2025-through-February-2026 ledger may be opened only if one gate passes every
selection gate and the selection receipt is committed.  Repository holdout
rows beginning 2026-05-04 are never parsed.  This is research only: it cannot
change TradingView, ACTIVE/frozen, forward execution, or live orders.

Run after the preregistration and script are committed:

```bash
python3 -m scripts.research_ethusdtp_15m_causal_confluence_v17 --phase selection
```

If and only if `results/selection_receipt.json` says `frozen_for_audit`, commit
that receipt and then run:

```bash
python3 -m scripts.research_ethusdtp_15m_causal_confluence_v17 --phase audit
```
