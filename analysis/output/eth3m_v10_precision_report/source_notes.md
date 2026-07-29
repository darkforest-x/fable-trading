# ETH 3m v10 precision report — source notes

- Audience: product stakeholder / project owner.
- Delivery mode: portable HTML fallback because no Data Analytics MCP artifact renderer is callable in this runtime.
- Required structure mapping: title → `title`; Executive Summary → `executive_summary`; findings/evidence → metric strip plus four finding blocks; next steps → `recommendation` and `acceptance`; further questions → `questions`; caveats → `caveats`.
- Visual contract: use a four-card metric strip plus one full-width bar chart. The chart asks whether higher v10 confidence implies a higher future-3h down rate; it uses six ordinal confidence bins on one common denominator, a single-series palette, no legend, and percent formatting. Its intended takeaway is that outcome rate is not monotonic in confidence, while the adjacent text warns that outcome is not shape precision. Cross-unit comparisons (native 15m fires/symbol-month, 3m fires/anchor, and owner-estimated validity) remain narrative because plotting them on one axis would be misleading.
- Validation stance: Share with caveats. Root-cause direction is strongly supported; the user's roughly 60% invalid estimate is not yet task-level data, so precision-by-confidence and error-type shares remain unresolved.
- Source precedence: the corrected fire-density learning controls over stale density fields in `after_train.log` and `diag_precision_vs_recall_v10.json`.
- Holdout: all local recomputation filters rows to `<2026-05-04 00:00 UTC`; holdout consumed = false.
