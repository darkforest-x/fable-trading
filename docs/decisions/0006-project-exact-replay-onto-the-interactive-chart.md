# ADR 0006: Project exact replay onto the interactive chart

## Status

Accepted; amends ADR 0005

## Date

2026-09-03

## Owner authorization

“模型回放的 K 怎么和实时不太一样呢，要保持一样的效果。”

## Context

ADR 0005 separated current public market data from pre-holdout model replay,
but it left the exact white-background YOLO raster as the replay's primary
visual. The live mode used a dark, interactive Lightweight Chart. Switching
between them therefore changed candle styling, grid, axes, controls, and
interaction even though both represented 15m candles with the same six-MA
contract.

Replacing model inference input with the dark chart would break pixel parity.
Likewise, rebuilding SMA/EMA values from the 19-bar replay slice would be
incorrect: SMA60 and SMA120 require history before the displayed window. A
model box expressed in normalized raster pixels also cannot be pasted as a
fixed CSS percentage because its position must continue to match the data
after pan, zoom, or resize.

## Decision

1. Exact inference remains unchanged. The worker reconstructs and hash-checks
   the canonical 1280×742 L1 raster before running YOLO.
2. The replay response adds a display-only `replay_chart` object containing
   the same window's candles, six moving-average slices, projected boxes,
   input hash, and window length. All values come from the verified frozen
   event; the browser does not reopen data or rerun indicators.
3. Live and replay primary views share one chart factory and one six-line style
   contract. Replay retains crosshair, drag, zoom, volume, axes, and fit-window
   behavior.
4. Each normalized YOLO box is inverted through the exact render transform
   into fractional bar and price bounds. The browser interpolates fractional
   bar bounds from adjacent candle time coordinates and projects price bounds
   through the candle series, updating the overlay on range, size, and direct
   chart-scale interactions.
5. The original white raster remains available in a collapsed evidence panel
   with its verified pixel hash; it is no longer the default replay canvas.

## Consequences

- Switching modes keeps the same visual and interaction language while the
  live/replay data boundary remains explicit.
- The signal rectangle shown on the interactive chart is traceable to the
  detector output rather than redrawn by eye.
- The API payload grows by one small replay window, but no new data source,
  holdout read, persistent write, or inference path is introduced.
- Future adapters must provide the same display seam or explicitly omit it;
  the UI may fall back to the exact raster for older compatible responses.

## Alternatives considered

- Restyle the canonical L1 renderer: rejected because it changes model pixels
  and invalidates the pinned hash contract.
- Recalculate all moving averages in the browser: rejected because the short
  replay window lacks the required 60/120-bar warmup history.
- Paste normalized boxes as fixed CSS percentages: rejected because chart axes
  and right-side scales make canvas percentages differ from data coordinates,
  and the box would detach during interaction.
- Hide the exact raster entirely: rejected because pixel evidence remains the
  strongest audit trail for model input parity.
