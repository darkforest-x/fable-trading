# ETHUSDT.P 15m expansion confluence V18

V17 found that adding more trend-direction votes was redundant, while the
joint ETH ATR/Bollinger-width expansion axis was the only gate positive in all
four 2023--2024 half-years.  Its original floor of 1.00 was too sparse.

V18 freezes one follow-up hypothesis from the disclosed post-hoc sensitivity
table: `min(ATR14 / prior-96 median ATR14, BB20 width / prior-96 median width)
>= 0.85`.  This exact threshold was chosen because it was the only inspected
floor with at least 45 events, at least 45% top-decile positive-PnL retention,
and positive mean net return.  It is hypothesis generation, not an untouched
selection result.

Run the reused development freeze:

```bash
python3 -m scripts.research_ethusdtp_15m_expansion_confluence_v18 --phase selection
```

If the committed receipt says `frozen_for_diagnostic_audit`, run:

```bash
python3 -m scripts.research_ethusdtp_15m_expansion_confluence_v18 --phase audit
```

The audit is already-seen parent lineage and can test transport only.  Neither
phase reads repository holdout, changes V16 execution, writes TradingView, or
touches ACTIVE/frozen/forward/live state.
