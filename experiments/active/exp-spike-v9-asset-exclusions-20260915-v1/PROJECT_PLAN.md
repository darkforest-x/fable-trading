# Fixed historical asset-exclusion readout

Owner asks on 2026-09-15: “假如我们排除掉胜率低于等于10%的币种呢 / 平均净r小于-0.36的币种呢 / pf.r小于0.5的币种呢”. Evaluate these exact three thresholds independently using already saved V9 full-pool outcomes, with V8 as a separately scored reference. No union, intersection, parameter search, new OHLC reads, new replay, Pine edit or deployment.

## Metrics and temporal scope

Group exact frozen base asset across venues/timeframes, pool individual closed trades (not means of stream metrics). Win is netR>0, mean is netR/closed count, PF(R) is positiveR sum / absolute negativeR sum. Positive-only PF is infinity; all-zero PF and no closed observations are unknown. Unknown asset/unknown scores stay retained and are counted. No new minimum-trade cutoff: report removed assets with under10 and under30 closed trades as uncertainty diagnostics.

Primary display uses full2024-09-10 to2026-09-10 scores to select and summarize the same history: explicitly posthoc and circular. Temporal check selects using only entry AND exit before2025-09-10, then freezes the entire asset blacklist for entry>=2025-09-10. No reselection from the later period; new/unscored assets retained. Report each arm's baseline,3 individual masks, full metrics, later metrics, gross/cost decomposition, original10R identities and matched controls already associated with retained entries. Whole-asset removals preserve all retained independent stream paths; no shared-account claim.

## Evidence, authorization and delivery

This is first saved-outcome analytical consumption for each exact threshold/mode, including already-exposed holdout-era outcomes authorized by the current specific what-if request. Inherited V9 original replay exposure1 and lowtf extension2 remain recorded; zero new market-data replay. These selection hypotheses have no blind/OOS claim. Commit builder/config/tests before outcome aggregation; authenticate source receipt and ledgers, reproduce both source full/period baselines, reconcile retained+removed totals and winner identities.

Use one Luna Max helper for source semantics and one bounded follow-up on saved results. Deliver Chinese answer-first MD and immediately render with owner-required scripts/md_to_html.py to analysis/html/p1_spike_v9_asset_exclusions_20260915.html. Owner repository report format overrides the analytics plugin's alternative HTML packager. Structure: outcome summary, definitions, full hindsight comparison, frozen earlier-to-later check, interpretation/limitations, reproducibility and evidence. Tables show exact threshold lookups; no decorative chart. Capture reusable conclusion in Notion and learning note. All training/production flags false.
