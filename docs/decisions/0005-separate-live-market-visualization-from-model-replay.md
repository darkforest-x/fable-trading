# ADR 0005: Separate live market visualization from model replay

## Status

Accepted; amended by ADR 0006

## Date

2026-09-03

## Owner authorization

“检测时间 04:00:00 啥意思？中间的 K 线图能不能做成和 TradingView 一样实时的。”

## Context

The console originally displayed the exact raster sent to L1. Its caption
shortened the replay event timestamp to `04:00:00`, hiding both the date and
timezone. The image was correct evidence for pixel parity, but it was not an
interactive market chart and looked like a current detection even though the
event was `2026-04-29T04:00:00Z`.

The owner wants a TradingView-style real-time chart. Current public candles
must not be joined to a historical model verdict: the current L1/L2 adapter is
only proven for one hash-pinned pre-holdout event, and the selected L2 research
experiment remains `REJECT`. The request authorizes live market visualization,
not live model scoring, forward writes, execution, holdout evaluation, or a
freshness-gate change.

## Decision

1. The chart has two explicit modes. `实时行情` is the default interactive
   view; `模型回放` preserves the exact L1 raster and boxes.
2. The live view uses Lightweight Charts 5.2.1 with candlesticks, volume, the
   six L1 renderer-contract overlays (SMA/EMA 20, 60, and 120), crosshair, pan,
   zoom, auto-resize, and visible TradingView attribution. The six names stay
   in the top legend; per-series labels are hidden from the right price axis.
3. A read-only `/api/market/candles/{symbol}` endpoint fetches up to 300 recent
   OKX 15m candles in memory. The browser then subscribes anonymously to OKX's
   `candle15m` Business WebSocket channel and updates the current bar. REST
   polling remains as reconciliation and fallback.
4. Live candles are never persisted, sent to either model, written to
   `forward_log`, or exposed to the executor. The UI states this at the chart,
   caption, event log, and footer.
5. Historical result timestamps show the complete event date in Beijing time
   and UTC. `2026-04-29T04:00:00Z` is rendered as
   `2026/04/29 12:00 北京时间` plus `2026-04-29 04:00 UTC · 不是当前时间`.
6. The live chart's push receipt time and current candle start time are separate
   fields; neither is labelled as a model detection time.

## Consequences

- Users get a familiar real-time chart without losing the exact-pixel replay
  needed to audit L1.
- The page may simultaneously show current market prices and a historical model
  verdict, so labels must always retain `历史裁决`, `REPLAY ONLY`, and
  `实时行情未送入模型`.
- Network failure degrades to polling and then an explicit offline state. It
  never falls back to fabricated candles.
- Live model inference remains a separate future project requiring its own
  causal input contract and owner approvals.

## Alternatives considered

- Replace the replay image with live candles and leave the verdict beside it:
  rejected because it visually claims an event alignment that does not exist.
- Embed the full TradingView widget: rejected because it would not preserve the
  console styling or allow a controlled future model overlay seam.
- Poll REST only: rejected as the primary path because OKX provides an anonymous
  candlestick WebSocket whose current candle can update as often as once per
  second.
- Run the latest research models on each live update: rejected because this
  request did not authorize live scoring and the L2 experiment is not
  production eligible.

## Sources

- Lightweight Charts v5 series API:
  https://tradingview.github.io/lightweight-charts/docs/migrations/from-v4-to-v5
- Lightweight Charts real-time `update` API:
  https://tradingview.github.io/lightweight-charts/docs/api/interfaces/ISeriesApi#update
- Lightweight Charts responsive sizing and attribution:
  https://tradingview.github.io/lightweight-charts/docs/api/interfaces/ChartOptionsBase
  and https://tradingview.github.io/lightweight-charts/docs/api/interfaces/LayoutOptions
- OKX recent candles and candlestick WebSocket:
  https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks
  and https://www.okx.com/docs-v5/en/#websocket-api-public-channel-candlesticks-channel
