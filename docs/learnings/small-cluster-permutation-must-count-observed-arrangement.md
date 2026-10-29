# A small-cluster permutation must count the observed arrangement

- **Problem**: A six-week sign-flip result initially approached the Monte Carlo floor although its exact one-sided floor is1/64.
- **Dead end**: Comparing a matrix sum divided by count with a separately computed row mean can exclude the observed all-positive arrangement by one floating-point unit.
- **Effective path**: Form the observed statistic through the same cluster sums, enumerate signs for at most16weeks and include equality within a scale-aware numerical tolerance. Bootstrap sampled week sums together with sampled week counts. Regression checks require six positive weeks to return1/64 and all-zero weeks to return1.
- **General rule**: Before trusting a small-sample randomization p-value, test its attainable floor and inclusion of the observed arrangement. Preserve dependence clusters and their sizes.
- **Dependencies**: `week_sign_flip` and `paired` in `spike_close_execution.py`. Correction changes uncertainty calculations only; no native trade was replayed.
