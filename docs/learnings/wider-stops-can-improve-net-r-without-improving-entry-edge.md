# Wider stops can improve net R without improving entry edge

- **Problem**: ETH15m-watch5m stop/exit optimization improved total net R dramatically as the structural buffer widened.
- **Dead end**: Reading that improvement as better directional forecasting or preserved original20R trades. Changing the stop changes both the R denominator and fixed-risk position size.
- **Effective approach**: Separate grossR and costR under the same entries. With all baseline exits unchanged, buffer0.05→0.4 percent changed net−3747.09→−874.48R, but gross543.31→480.47R. Lower risk-normalized costs explain more than the net improvement. Baseline median stop distance0.19965percent makes the frozen0.2percent round-trip cost about1.00174R per trade. Matched controls also improve with wider stops.
- **General rule**: For fixed-risk parameter searches, report stop-distance distribution, costR, grossR and same-policy matched controls. Count original tail trade IDs separately; a new20R count is not retention when the denominator changed.
- **Scope**: `exp-eth-exit-search-20261020-v1`, `single_factor.csv`, `comparisons.json`. This does not authorize changing the frozen cost assumption or live position sizes.
