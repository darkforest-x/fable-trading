# Glass fallbacks and foreground ink need opaque theme tokens

- **问题**：Reduced-transparency/no-blur fallback rules forced white surfaces in dark mode. Reader badges used a translucent surface token as text ink, reducing contrast even when their colors appeared theme-aware.
- **死胡同**：A dark canvas token cannot repair a body-level hard-coded white fallback; moving the fallback only onto the body also misses a shadow root that defines its own palette.
- **有效路径**：Define opaque light/dark materials alongside the shared palette, including shadow hosts, and use opaque page colors for badge text and diagram canvases. Respect prefixed and unprefixed backdrop support; retain transparent materials in the normal path.
- **通用规则**：Translucent materials, opaque fallback surfaces and foreground ink have different contracts. Test accessibility/fallback branches as well as normal rendering and never invert chart/image pixels to emulate dark mode.
- **牵连**：`design-tokens.css`, `glass-system.css`, `night-mode.css`, `orderflow.css`, `langlang.css`, `rektproof.css`; browser contrast and reduced-transparency checks.
