# Design QA — ClauseOS detail polish

## Comparison target

- Source visual truth: `/var/folders/fr/fq9wwmwx3xn63_cdk_v8nvt00000gn/T/codex-clipboard-9593916c-703c-4622-8a70-6b80755af69a.jpg`
- Rendered implementation: `http://127.0.0.1:8643/#overview`
- Final desktop screenshot: `/Users/zhangzc/.codex/visualizations/2026/07/29/019fae88-a0db-76c3-854a-b1f21dfd4bc4/fable-polish-desktop-final.png`
- Final mobile screenshot: `/Users/zhangzc/.codex/visualizations/2026/07/29/019fae88-a0db-76c3-854a-b1f21dfd4bc4/fable-polish-mobile-final.png`
- Signals screenshot: `/Users/zhangzc/.codex/visualizations/2026/07/29/019fae88-a0db-76c3-854a-b1f21dfd4bc4/fable-polish-signals-final.png`
- Full-view comparison evidence: `/Users/zhangzc/.codex/visualizations/2026/07/29/019fae88-a0db-76c3-854a-b1f21dfd4bc4/fable-polish-compare-final.png`
- State: white theme, overview route, real local data loaded; dark theme and signals route were checked separately.

## Viewport and normalization

- Source pixels: 1260 × 1325. The app-owned region was cropped to 1260 × 720 from y=160.
- Implementation pixels and CSS viewport: 1280 × 720 at device scale 1.
- Full comparison: both source crop and implementation were normalized to 720 × 405, then placed side by side.
- Mobile implementation: 390 × 844 at device scale 1.
- Focused evidence: the separate desktop screenshot preserves readable evidence-card typography and controls; the mobile screenshot preserves status-strip and card-stack measurements, so an additional crop was unnecessary.

## Findings

- No actionable P0, P1, or P2 issues remain.
- Typography: the system/SF-compatible display stack and PingFang fallback produce the intended compact, neutral hierarchy. Evidence values retain a monospace treatment, small labels remain readable, and real long values truncate without breaking the card frame.
- Spacing and layout: the centered status capsule, circular mainline hub, four-card 3D fan, and overlapping judgment panel retain the reference composition. The mobile status strip is now one compact three-column row; the last evidence card ends 36 px before the judgment panel begins.
- Colors and tokens: the requested white palette preserves the reference's green emphasis using stronger paper/glass separation, restrained green bloom, and consistent Harmony Green accents. Dark mode now explicitly inherits the same green tokens rather than the legacy blue accent.
- Image quality and assets: the physical tablet/hands are reference framing, not app-owned UI, and were intentionally not fabricated. No replacement raster asset, placeholder illustration, custom SVG, or fake icon asset was introduced.
- Copy and content: all trading metrics and status text remain API-backed. Loading, empty, and live states keep their truthful existing meanings.
- Interaction states: evidence cards lift in place instead of jumping to the center; focus treatment remains visible without creating a heavy double outline; theme changes reload charts with the correct palette.

## Comparison history

1. Earlier build — P2: first paint had no evidence cards while data loaded. Fixed with four truthful loading cards, then verified with live replacement.
2. Earlier build — P2: the original mobile card deck extended into the judgment panel. Fixed with responsive grid/card sizing and a bounded stage.
3. Detail pass 1 — P2: white surfaces were too close in luminance, collapsing the spatial depth visible in the reference. Fixed by strengthening white-theme borders, paper/glass opacity, elevation, and green ambient contrast. Post-fix evidence: `fable-polish-desktop-final.png`.
4. Detail pass 1 — P2: card hover moved every card to the same center coordinate. Fixed with per-card transform variables so each card lifts from its own position. Post-fix behavior uses the same fan geometry at rest and hover.
5. Detail pass 1 — P2: the mobile status stack consumed excessive vertical space, while 132 px cards exceeded the compact stage. Fixed with a 68 px three-column status capsule and four 116 px cards. Final measurements: stage bottom 996.84 px, last card bottom 952.97 px, judgment panel y 988.84 px, horizontal overflow 0.
6. Detail pass 1 — P2: a hidden focus section still caused an empty separator above the Signals side panel. Fixed with a hidden-sibling exception; post-fix evidence: `fable-polish-signals-final.png`.
7. Detail pass 1 — P2: legacy dark-theme variable specificity made the primary action blue. Fixed with explicit ClauseOS dark tokens; computed dark accent is `#68e78e` and primary background is `rgb(104, 231, 142)`.

## Open Questions

- The source contains many more background cards and no persistent text navigation. The implementation intentionally keeps four truthful metric cards and the product's navigation rail; adding fake cards would reduce information integrity.

## Implementation Checklist

- [x] Refine white theme contrast, radii, elevation, and hierarchy.
- [x] Preserve card position during hover.
- [x] Compact the mobile status and evidence deck without overlap.
- [x] Unify Signals controls, panels, and empty-state spacing.
- [x] Keep dark and light palettes semantically consistent.
- [x] Verify overview-to-signals navigation, theme switching, mobile drawer, overflow, and browser console.

## Follow-up Polish

- P3: if the overview API later exposes more distinct metrics, additional real cards could extend the fan closer to the source without duplicating or inventing data.

final result: passed
