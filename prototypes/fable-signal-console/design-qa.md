# Fable Signal Console — Design QA

## Scope

- Reference: `https://fable-signal-console.zc994739211.chatgpt.site/`
- Local implementation: `http://localhost:4173/`
- Reference evidence: `../../output/product-design/fable-signal-console-source/`
- Implementation evidence: `qa/`
- Baseline state compared: ETH selected, scan running, original demo view.
- Dynamic exceptions: the wall clock, scan-cycle number, and animated scan progress intentionally continue running, so their instantaneous values differ between captures.

## Normalization

| Surface | Reference | Implementation | Result |
| --- | --- | --- | --- |
| Desktop viewport | 1440 × 1000 | 1440 × 1000 | matched |
| Desktop full page | 1440 × 1367 | 1440 × 1367 | matched |
| Mobile viewport | 390 × 844 | 390 × 844 | matched |
| Mobile content canvas | 720 × 2673 | 720 × 2673 | matched |
| Base stylesheet | 159,307 bytes | 159,307 bytes | byte-identical, SHA-256 `8e55aa17…` |
| Local override stylesheet | n/a | 2,013 bytes | interaction wiring, measured mobile canvas, and actual-model image/box states |

The reference itself keeps a 720px content canvas below 760px and exposes it through a 390px viewport. The implementation deliberately preserves that behavior instead of replacing it with a different responsive layout.

## Evidence reviewed

- Desktop full comparison: `qa/compare-desktop-full-v1.jpg`
- Desktop viewport comparison: `qa/compare-desktop-source-left-implementation-right-v1.jpg`
- Mobile full comparison: `qa/compare-mobile-full-v2.jpg`
- Mobile viewport comparison: `qa/compare-mobile-source-left-implementation-right-v2.jpg`
- Focused chart comparison: `qa/compare-focus-chart-v1.jpg`
- Focused decision-trace comparison: `qa/compare-focus-decision-v1.jpg`
- Focused table/log comparison: `qa/compare-focus-lower-v1.jpg`

All comparison composites place the reference on the left and the local implementation on the right.

## Mandatory surface review

- Fonts and typography: the captured Inter / SF Pro Display / PingFang SC / system fallback stack, mono stack, weights, letter spacing, line height, wrapping, and numeric hierarchy match in desktop and mobile comparisons.
- Spacing and layout: rail width, page margins, grids, panel gaps, borders, caption cells, table density, and footer rhythm align. Desktop and full-page dimensions are identical.
- Viewport resilience: desktop and mobile were rendered in the browser. No overlap, collapsed section, or unusable control was found. The reference's intentional 720px mobile canvas is faithfully retained.
- Colors and tokens: background `#06080a`, lime `#caff4f`, state colors, borders, muted text, fills, and focus token come from the byte-identical reference stylesheet.
- Image and asset fidelity: favicon, trend icon, activity icon, and all five chart SVGs are local copies of the source assets with matching SHA-256 hashes. No hotlinks, placeholders, CSS art, or substitute illustrations are used.
- Copy and content: the baseline comparison captures matched the source. The
  model-integration state intentionally replaces demo claims with latest-model,
  aligned replay, `REJECT`, and execution-off disclosures while preserving the
  source hierarchy and density.
- Icons: source favicon, trend, activity, chart marks, and status glyphs are all present and aligned in focused comparisons.
- States and interactions: scan pause/resume was exercised and exposed `aria-pressed`; ETH, SOL, BTC, LINK, and AVAX candidate buttons each updated chart, price, conclusion, action, score, flow states, and selected row; ETH was restored after the test.
- Accessibility: controls are semantic buttons, selected/running state is exposed, keyboard focus receives a 2px lime outline, reduced-motion rules are present, and text/icon contrast follows the source tokens. Visible controls remain operable at the tested mobile viewport.
- AI shortcut artifacts: none found. The implementation uses the real captured stylesheet and local source assets rather than approximated cards, icons, or charts.

## Comparison history

1. Mobile v1 exposed a 706px implementation canvas while the reference measured 720px. Severity P2 because the 14px drift changed the horizontal crop and panel proportions.
2. Fixed by pinning `.workspace` to the measured 720px minimum inside the source's mobile breakpoint.
3. Mobile v2 measured 720 × 2673 on both sides. Full-page and focused composites were re-inspected; no remaining P0, P1, or P2 fidelity issue was found.

## Runtime verification

- Production build: passed.
- Sites worker test: passed.
- Browser console in a clean page after model integration: 0 warnings, 0 errors.
- Local preview remains available at `http://localhost:4173/`.

## Latest-model integration QA — 2026-09-03

- The default ETH replay rendered the exact 1280 × 742 YOLO input and one
  overlaid LONG box. The pixel identity badge showed `1ed05166…`.
- The decision trace displayed Grade-A full40 native-1280, same-event
  pre-holdout input, LONG `full_110` with 110 features, the frozen q90
  threshold, and the real held result.
- Candidate switching was exercised from ETH to SOL. The chart, event time,
  L1 confidence, L2 score, percentile ring, table row, and event stream all
  updated from the returned model payload.
- BTC exercised the separate SHORT route and visibly resolved to
  `SHORT plus_ma_family · 54 特征 · q90 0.001088`.
- The UI kept `L2 实验结论保持 REJECT`, `production_eligible=false`,
  pre-holdout, and executor-off disclosures visible. No live or production
  wording was introduced.
- Fresh-page runtime verification found 0 browser warnings and 0 browser errors.

final result: passed
