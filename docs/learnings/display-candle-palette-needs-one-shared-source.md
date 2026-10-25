# Display candle palettes need one shared source

- **Problem**: Monday used muted green/rose candles while inspection, market and position charts retained separate bright colors; volume and SVG fallback rendering also differed.
- **Dead end**: Patching only the main candlestick constructor leaves preview, live updates, histogram and fallback paths inconsistent. A global color replacement would also alter unrelated risk annotations and frozen model inputs.
- **Effective path**: Put the existing Monday palette in `SpikeChartTools.colors`; consume it for candle bodies, wicks and display volume across interactive views and SVG fallback. Remove the local Monday override and test fallback output against the shared definition.
- **Rule**: Audit all rendering paths for the visual primitive, including streaming updates and fallback output, while preserving separate semantic colors and immutable model-rendering contracts.
- **Scope**: Monitor chart modules and `vision_research/static/chart-tools.js`; no training renderer, detection threshold, order or risk semantics changed.
