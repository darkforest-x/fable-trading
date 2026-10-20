# Fee-covering stop rounding can turn scratches into statistical wins

- **Problem**: The ETH fifth-5m-bar breakeven experiment raised the strict net-positive win rate from 9.56% to 34.47%, while total net return worsened by 578.47R.
- **Dead end**: Treating every positive floating-point return as an economically meaningful winner would suggest that the exit improved. This was not floating-point noise: executable tick rounding deliberately placed the fee-covering stop slightly beyond exact breakeven.
- **Effective path**: Check native fills, fee-adjusted results, exit reasons, and the distribution near zero. The time-only arm had 1,215 breakeven-stop exits, whose maximum net result was below 0.009R. It had 1,196 positive results below 0.01R overall. Actual cumulative net R and retained large winners deteriorated.
- **Reusable rule**: Keep the exact mathematical win rate, but accompany breakeven studies with a near-zero distribution and profit magnitude. Do not silently redefine wins, or select an exit policy by the higher win rate alone.
- **References**: `experiments/active/exp-eth-timebe-rsi-20261020-v1/`, `yoyo/layers/l3_backtest/nautilus_independent.py`, `yoyo/evaluation/range_rsi_scaleout.py`. Diagnostic 0.01R/0.1R bins are descriptive only; they do not change entry, exit, or selection.
