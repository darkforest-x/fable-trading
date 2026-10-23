# Mobile order drawers must follow the visual viewport

- **Problem**: A movable desktop ticket became unusable when the iOS keyboard covered fields and the order button.
- **Dead end**: A bottom position and `dvh` max-height alone did not solve it. Browser testing reproduced a footer below the visible sheet because its fixed header and summary consumed the keyboard-shrunken viewport; a CSS window-height query did not notice that change.
- **Effective path**: Use `visualViewport.height` and `offsetTop` to anchor the drawer, ignore saved desktop-style positions on mobile, and independently scroll the form. Compact secondary summaries when the visual viewport is short while retaining the action. Reuse focus trapping, background inertness, focus restoration and handle-only swipe dismissal. A delayed automatic stop cannot overwrite a manual edit or a reviewed quote.
- **General rule**: Verify sheet bounds and footer bounds against the visible viewport, including an offset viewport and the smallest supported width. A screenshot with the keyboard closed cannot validate a trading form's mobile interaction.
- **Touches**: `yoyo/monitor/static/monday-quick.js`, `monday-fib.css`, `monday-live.js`, `monday-live.css`; Stitch screen `0e1cb04622f444cea23f0fa2a9f46919`. Browser tests used synthetic accounts and intercepted confirmations, never real orders.
