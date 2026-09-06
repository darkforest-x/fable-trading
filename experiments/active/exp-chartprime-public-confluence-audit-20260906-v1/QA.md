# Final audit receipt

As of 2026-09-06; review-only, no prices or profit claims.

- 148 exact catalogue IDs; three per-item partitions 45/50/53.
- 134 source_read, 14 description_only; zero missing/duplicate/collection errors.
- Independent frozen catalogue access/source ID/version/URL joins and raw-byte
  source SHA checks pass for all 134. References within actual line bounds.
- 30 synthetic collector/validator tests plus16 registry tests pass (46 total).
  Closed-source swaps, changed
  source identity/version, missing entries, changed CRLF bytes and bad lines
  cannot obtain an accepted review. Semantic truth still requires code reading.
- Cross-review fixed two misleading category placements: Price Ratio is a
  single-asset oscillator; Swing Profile counts price frequency, not volume.
- Root inspected high-impact original paths: MA Shift, HTF Matrix, Flow Surge,
  VTR Pro, Breakout Boxes, VWAP, Historical Volatility, Market Break, ML Momentum,
  Multi Asset Histogram, RSI Probability, MACD HTF request and profile indices.
  Other 134 source bodies read by assigned reviewers; external-library limits
  are not upgraded to full runtime verification.
- Canonical artifact: 165 blocks, 148 individually numbered reviews, one native
  category-count chart and one 148-row index. All source references resolve.
  Categories sum to148 (40/33/17/16/14/13/8/7). Chart counts are navigation, not
  strategy ratings. Payload514087bytes; no charts of unobserved returns.
- Official portable delivery returned validation=passed, package=passed,
  verification=structural_only. Headless executable unavailable; no dependency
  installed. Source dialog/interaction/mobile/theme tests not verified.
- CUA file preview was explicitly blocked by browser URL policy. No localhost,
  alternate browser, raw CDP or other workaround attempted. Visual QA remains
  a handoff limitation; structural checks are not claimed as visual success.
- Catalogue frozen before evidence generation; collector commit6ebad6b;
  final report/query builder fee0dec committed before final generation.
- No TradingView mutations, orders, model promotion, holdout, market downloads
  or new backtests. V19 prepared changes remain separate and unexecuted.

## Reproduce exact report packaging

After commands in the MD report, use the installed official portable builder:

```bash
node /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs --input experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/artifact.json --output analysis/html/p1_chartprime_public_confluence_audit_20260906.html
```

Fresh generation timestamps change artifact/HTML hashes; the catalogue and
source identities are frozen. Human review JSON records must be preserved.
