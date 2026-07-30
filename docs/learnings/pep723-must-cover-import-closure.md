# PEP 723 must cover the runtime import closure

## Problem

The direction evaluator declared its obvious model dependencies, but importing
the holdout boundary from `src.judgment.train` also imported scikit-learn. A
clean `uv run` environment therefore failed before evaluation even though the
developer's global environment had previously run the script.

## Approach

Treat inline script dependencies as a reproducibility contract. Test the script
through `uv run`, and declare packages required by imported project modules as
well as packages imported directly by the script.

## Verification

The evaluator now starts from its inline dependency environment and reaches the
full 5,900-image validation inference instead of failing during imports.
