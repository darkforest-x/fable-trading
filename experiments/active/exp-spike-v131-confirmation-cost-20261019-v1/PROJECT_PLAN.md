# Confirmation-cost attribution

Owner authorized this study on 2026-10-19 after the multi-dimensional research
discussion. This is a diagnostic of the existing confirmation rule, not a new
threshold search or production change. Costs, stops and trailing parameters stay
frozen. No report requested.

- Reuse registered 638-symbol original inputs and candidate ledgers. Read original
  paths, including mounted migration links. Record size, mtime and actual reads;
  do not compute content hashes or download data.
- For 15m/1H, join immediate and retest candidates by original trade_key. Evaluate
  every mature anchor independently, retaining overlapping opportunities; this is
  not an account simulation. Also mark the two archived serial admission sets.
- Restore 1H actual 2ATR exits for both entry choices; 15m remains 4ATR. Serial
  admission stays archived, as in the incumbent V13.1 analysis. No new entries
  after earlier 1H exits are claimed.
- Exclude unavailable/censored outcomes explicitly. Cancellation/expiry is a known
  zero-fill retest outcome. Do not select on eventual winners or MFE.
- Exact per-anchor decomposition: avoided immediate outcome + entry price change
  + exit price change + fee-notional change + equal-initial-risk sizing change.
  Same-unit components use anchor risk; final equal-risk return uses delayed risk.
  The hindsight selected-early-entry subtotal is an attribution bridge, never an
  executable strategy. An earlier parent's exit can precede confirmation.
- Apply both immediate and confirmation policies to each existing matched random
  anchor. Retain failures as zero fills; no replacement draws. Require mature
  baseline and known retest outcomes on both sides. Match is original symbol,
  direction, anchor UTC week, temporal fold and causal ATR/price bucket. Opportunity
  comparisons differ from the old both-filled-only comparison; label denominators.
- Time split uses anchor and all required actual exits/decisions before 2025 for
  early results, and anchors in/after 2025 for later results. Pair periods require
  both sides in that period. Reused history is exploratory, not blind validation.
- On the original 21,547 closed V13.1 trades, independently describe cost R,
  confirmation delay and chase distance across all four timeframes. Features at
  confirmation use only closed OHLC then available; actual next-open fill is a
  separate execution diagnostic. Preserve 19 December-2022 1H entries here.
- Fixed bins are descriptive, not optimized gates. Show matching coverage and
  baseline/random comparisons, block uncertainty by anchor UTC week. No random
  train/test split. No AUC/top-decile ranking metric: no trained model or score.
- Focused synthetic identity/time-boundary tests, full archived parity, independent
  scalar accounting and aggregation checks, and a bounded causal-prefix audit
  precede conclusions. Store raw ledgers/config/receipts and update registries,
  Notion and the required learning note.
