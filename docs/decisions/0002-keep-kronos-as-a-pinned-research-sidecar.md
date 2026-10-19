# ADR 0002: Keep Kronos as a pinned research sidecar

## Status

Accepted

## Date

2026-09-01

## Owner authorization

“详细分析一下 github 上 kronos 项目，将能给我们项目用的帮助的弄进来，或者详细分析他的架构和思路参考一下。”

## Context

Kronos is a probabilistic K-line foundation model released under MIT. It first
encodes six-dimensional OHLCVA bars as hierarchical binary-spherical tokens,
then predicts coarse and fine tokens autoregressively with a causal Transformer.
The official repository is useful source material, and the released small model
is light enough for offline research on this Mac.

This repository already ran a 2026-07-30 pre-holdout experiment that turned one
sampled 12-bar forecast into six LightGBM features. The exact tested arm failed
its preregistered acceptance bar. A source audit now also shows that the run used
the README sampling profile (`T=1.0`, one path), did not pin model revisions or
an inference seed, did not explicitly call `eval()`, used only 30 permutations,
and called an ATR-only draw a matched control. Those facts do not reverse the
negative decision, but they limit it to the tested forecast-feature
configuration; they do not rule out Kronos's deterministic token representation.

The project is still at P0/P1. New training and promotion are forbidden. Kronos
also has a different objective and evidence standard: generic path forecasting
is not the same task as recognizing an owner-defined MA-launch shape or earning
positive top-decile net return after the project's costs.

## Decision

1. Keep Kronos outside the four production layers as an **offline research-only
   sidecar**. No `yoyo/layers/` module may import it. It remains ineligible for
   training, production, `ACTIVE`, the active bundle, forward scanning, or
   execution until a later owner decision and the P0/P1 gates have passed.

2. Pin the complete foreign identity before any future experiment:

   - upstream commit `67b630e67f6a18c9e9be918d9b4337c960db1e9a`;
   - exact Kronos-small and Tokenizer-base Hugging Face revisions;
   - config and weight SHA-256 values plus byte sizes;
   - MIT license hash.

3. Require explicit `model.eval()` and `tokenizer.eval()`, an explicit Torch
   seed, UTC timestamps, causal context ending at the decision bar, walk-forward
   evaluation, 10,000 plus-one permutations, and matched controls on symbol,
   time bucket, volatility bucket, horizon, and cost. The research contract may
   not authorize a holdout read.

4. Do not copy Kronos's public CSV fine-tuning path. It normalizes a combined
   lookback-and-future window and therefore lets future values affect the model
   input scale. The Qlib dataset path correctly derives its scale from the past
   lookback only, but neither path authorizes training during P0/P1.

5. If Kronos is revisited after P0/P1, test a deterministic, one-pass
   representation arm before another autoregressive forecast arm. Candidate
   features are coarse/fine token transitions, train-fold-only current-token
   surprise, reconstruction residuals, and the final causal hidden state with
   any reducer fitted only on each training fold. Compare `BASE`,
   `BASE + Kronos representation`, and `Kronos representation only` under the
   same event set, economics, walk-forward splits, and matched controls.

6. Preserve the 2026-07-30 result unchanged as historical evidence. Its precise
   conclusion is “the README-style six-forecast-feature configuration did not
   pass.” It must not be relabeled as proof that all Kronos representations are
   useless, nor may its flawed p-value machinery be reused.

## Consequences

- The useful transfer is architectural and reproducibility-related today, not a
  new model or production signal.
- Exact upstream bytes and a deterministic inference profile can now be audited
  offline without reading market data or holdout.
- The main environment is not changed to match Kronos's dependency pins. A
  future market experiment needs an isolated evaluation runtime/subprocess so
  that the repository's Torch/Ultralytics/NumPy/Pandas contract is untouched.
- Ten-path forecasts reduce sampling spread but add material latency. They are
  unsuitable for the sub-15-minute live pulse unless a later latency budget and
  owner approval demonstrate otherwise.

## Alternatives considered

- Importing Kronos directly into L2 or the forward scanner: rejected because it
  violates layer isolation, current stage gates, latency discipline, and the
  requirement for economic validation.
- Fine-tuning now: rejected by P0/P1 and because the convenient CSV pipeline has
  future-dependent normalization.
- Treating the old negative run as a blanket architecture rejection: rejected
  because a stochastic sampled forecast and a deterministic latent
  representation are different interventions.
- Replacing the current detector with Kronos: rejected because path forecasting
  does not supply owner-confirmed boxes or fresh tip/tip-1/tip-2 detections.

## Primary sources

- [Pinned upstream source](https://github.com/shiyu-coder/Kronos/tree/67b630e67f6a18c9e9be918d9b4337c960db1e9a)
- [Official AAAI 2026 paper](https://ojs.aaai.org/index.php/AAAI/article/view/39730)
- [Extended paper](https://arxiv.org/abs/2508.02739)
- [Pinned Kronos-small files](https://huggingface.co/NeoQuasar/Kronos-small/tree/901c26c1332695a2a8f243eb2f37243a37bea320)
- [Pinned tokenizer files](https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base/tree/0e0117387f39004a9016484a186a908917e22426)
