# Verify glass materials in the built browser

- **问题**：The source declared glass blur, but the built Chrome page computed `backdrop-filter: none` on the floating navigation.
- **死胡同**：Translucent white backgrounds and source declarations looked correct. They did not prove that the browser applied blur.
- **有效路径**：Inspect the computed property and emitted CSS. With standard declarations before the WebKit fallback, the current minifier retained only the prefixed declaration on those selectors. Putting the prefixed fallback first and the standard property last preserved both in the build.
- **通用规则**：Keep standard CSS after vendor fallbacks. Validate visual effects against computed styles in a production build, not source text or opacity alone.
- **牵连**：`yoyo/discipline_app/mobile/src/fib.css`, Apple Glass screen `51ce1f55f38c4604a82b2425e50ad740`. No market or execution changes.
