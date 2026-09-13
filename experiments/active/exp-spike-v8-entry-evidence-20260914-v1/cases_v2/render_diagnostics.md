# V2 render diagnostics

- **Historical contacts for this render revision**: one authenticated RAVE 30m cache was loaded during the pre-commit causal-facts/window unit check. The committed `02f52a1b9f` formal render then loaded the six fixed winner caches and read eight corresponding V8 trade files (six winners plus two explicitly exploratory relaxed comparators). Total cache-load calls for V2: 7; V8 trade-file reads: 8.
- **Scope**: V2 repeats the fixed selection from V1. It changed only plot layout and exported local audit slices: 60 bars before entry plus 12 bars after entry, six-MA/BB context, and confirmation-time facts. It did not test a new condition, threshold, strategy, or full-pool cache scan.
- **Interpretation**: at-entry facts are causal descriptions of the already selected cases, never a newly proposed gate. Reused validation history remains nonblind and owner-authorized under the same explanatory gallery configuration's holdout-era use #1.
