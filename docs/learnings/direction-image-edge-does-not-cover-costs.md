# Direction image edge must be tested after costs

## Problem

The causal direction classifier improved gross return relative to numeric and
candidate-side baselines, but classification accuracy remained near chance and
the model traded 82% of candidates. Looking only at relative improvement would
have promoted a model that still loses after fees.

## Approach

Evaluate classification and economics on the same time-ordered manifest. Report
the no-trade recall, trade coverage, gross mean, fixed-cost net mean and profit
factor together. Compare against simple baselines, but apply the absolute
economic gate to the challenger itself.

## Result

The image model produced +0.04764% gross return per trade, but -0.01236% after a
0.06% round trip and -0.15236% after the project's fixed 0.20% round trip. Its
no-trade recall was only 19.16%, so the correct decision is rejection despite a
better gross result than the baselines.

## Reuse

For signal-image models, treat no-trade recognition and cost-adjusted coverage
as first-class targets. Accuracy or gross uplift alone is not a promotion gate.
