# ETH 3m V8 +1R price-breakeven study

One authorized, fixed exit-policy change: if an admitted frozen V8 ETH 3m trade's favorable **high/low** touches +1 initial R, observe that at the bar close and make entry-price protection effective on the next bar. The original protection is checked first; no tick ordering is claimed for a bar that both touches +1R and revisits entry. Price breakeven still pays the unchanged 0.2% round-trip cost.

The baseline is hash-pinned to low-timeframe `run_20260913_v3` and must reproduce its 111 post-cutoff ETH 3m closed trades and -26.282877R before results are accepted. The output reports (1) each original frozen entry paired with its new exit, and (2) a serial replay where early exits may enable later original signals to enter. It does not scan other markets or tune a threshold.

Holdout-era use is owner-authorized configuration-specific exposure #1 on nonblind historical data. It is research only, with no registry, admission, cost, stop, trail, or production change from this builder.
