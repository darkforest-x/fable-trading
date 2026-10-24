# Correct split-position returns do not make inherited fill metadata correct

- **Problem**: A half/half exit correctly averaged two independent native leg returns, but inherited the last leg's target and exit price. The report therefore looked like one fill at one target.
- **Dead end**: Verifying only the additive PnL identity missed the misleading single-price fields.
- **Effective path**: Keep native fills frozen. In the analysis export, preserve last-leg fields, leave the composite single target empty, show both targets, and use the equal-allocation exit mean. Label its time as the final leg's exit, not a simultaneous fill.
- **General rule**: Audit prices, quantities, event times, and policy labels separately when combining trades. Correct aggregate returns do not validate copied metadata.
- **Scope**: `monday_ma6_analysis.annotate_split_prices`, its focused test, and the report table. No signal, barrier or native execution changes.
