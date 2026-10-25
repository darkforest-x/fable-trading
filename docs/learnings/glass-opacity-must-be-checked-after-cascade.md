# Glass transparency is a computed material, not a single alpha value

- **Problem**: Paper-chart floating controls still looked opaque after their base background alpha had been reduced.
- **Dead end**: Editing only `.rp-glass` left stacked white gradients, per-panel solid fills and later dark-theme overrides in place. The visible material was much denser than the first declaration suggested.
- **Effective path**: Inspect computed backgrounds on the live page, then use one shared material token with separate pill/panel opacity and remove competing fills. Stitch screen `7908551d2e9a4ccc8a684a8da56dd952` supplies the 22% light pill / 52% panel and 45% dark pill / 70% panel values. Keep text opaque, blur bounded to 20–24px and edge highlights separate. Verify actual candles and colored rays through the material in both themes and on mobile.
- **General rule**: Audit the full cascade and compositing stack before lowering alpha again. Increasing blur does not make an opaque tint transparent. Keep a denser no-backdrop fallback and animate transforms/opacity rather than blur.
- **Connections**: `monday-reclaim-paper.css`, `night-mode.css`; fixed shortcuts sit outside the native horizontal scroll lane, so stronger transparency does not change hit targets or chart selection.
