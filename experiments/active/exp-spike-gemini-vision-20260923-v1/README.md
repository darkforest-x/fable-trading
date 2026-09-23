# SPIKE Vision Lab

Independent local workbench for Zhipu visual judgments and owner review.
The original experiment ID is retained; new requests use BigModel, while prior
Gemini records retain their original model and results.
Application source and frontend: `yoyo/vision_research/`.

From the repository root on macOS (persistent local service):

```bash
.venv/bin/python -m yoyo.vision_research.manage start
```

The user LaunchAgent survives terminal/chat closure, restarts a crashed process,
and starts after login. It only serves this manual workbench. Use `manage status`,
`manage restart`, or `manage stop` to inspect, restart, or stop it for the current
login session. Its plist contains no API key. Logs are in
`~/Library/Logs/Fable/SpikeVisionResearch/`.

For foreground development on any supported OS, run
`.venv/bin/python -m yoyo.vision_research.server --port 8771` instead, and keep that
terminal running. Do not run both launch modes on the same port.

Open http://127.0.0.1:8771. The existing SPIKE service on port 8766 is read-only;
this command does not start or change its scanner or notifications. If SPIKE
is unavailable, image upload still works.

The chart is the primary workspace, with a narrow candidate list and results
below. Page and interactive chart use the same SPIKE palette and system font.
Version 0.3.0 groups the workbench, shared references, history and model settings
in a persistent sidebar. Its light/dark switch remembers the choice in browser
storage. On narrow screens navigation moves above the chart, and the candidate
list collapses. History includes original image thumbnails; evidence and raw API
inputs/outputs remain accessible without mixing them into the main chart tools.
Lucide 0.468.0 SVG icons are bundled with their license under `static/vendor/lucide/`.
TradingView Lightweight Charts 4.2.0 is bundled locally, including its license
and notice; the chart supports pan, zoom, crosshair and resetting the view.
Its time axis uses UTC+8. Uploaded images and saved inference snapshots retain
their original pixels when the interface theme changes.

Set `ZHIPU_API_KEY` (or `BIGMODEL_API_KEY`) in the server environment, or enter it
in the local settings page. Saving writes a private `runtime/private/settings.json`
(mode 0600, directory 0700) atomically. The server reloads it after a restart;
leaving the key input blank preserves the saved value. Saved settings take
precedence over environment values. Keys never appear in browser storage,
exports or the research database. Unsaved environment configuration remains
supported; `ZHIPU_MODEL` overrides the built-in default when no setting is saved.

Manage reusable images on the Global References page; there is no four-image
cap. Supported Zhipu vision models accept 50 images per request (one candidate
and up to 49 references), each under 5 MB. This workbench additionally limits
combined decoded PNG data to 12 MiB. The set persists
in the local SQLite store across refreshes and service restarts. Each recognition
keeps the reference revision and images it actually used. Unsaved edits must be
saved first, and stale revisions are rejected rather than silently substituted.

On its first start, the service installs five boxed exemplars bundled in
`default_references_v3/`: AUCTION/CHZ long, FIL/TRUST/JELLYJELLY short.
Each box excludes the first launch candle. FIL/TRUST retain their right boundary
and tighten the left by one candle; MEME and the previous long examples are
replaced. The old `default_references/` pack remains unchanged.
The manifest records original images, frozen OHLC prefix hashes, individual
core/launch candle bounds, and confirmation level. These charts include later context and are
retrospective references, not decision-time samples or individually adjudicated
gold geometry. Click a reference to inspect it at full size. An edited or
deliberately cleared library is never automatically reseeded.

Rebuild with `.venv/bin/python -m yoyo.vision_research.reference_pack` from a
checkout where the output directory does not yet exist. Commit the builder and
`references_v3.json` first; existing output is never overwritten. Selection and
new box geometry remain pending individual Owner review, even where an earlier
sample's general shape was accepted. The source V7 pool used retrospective
continuation gates, so selection is not evidence of predictive accuracy.

Select a candidate or upload a PNG/JPEG/WEBP image, edit the criteria, then
explicitly start recognition. The interactive chart is captured at that moment
and shown as the exact submitted snapshot; return to the chart to pan/zoom again.
Only
this action sends selected pixels and criteria to Zhipu's
`https://open.bigmodel.cn/api/paas/v4/chat/completions` API. No search, order or notification tools
are available to the model. Connection testing sends one short text completion with a small token budget;
it consumes model usage and does not establish image-recognition quality. Failed recognition records retain
the HTTP status, an allowlisted provider error code and elapsed time. Documented billing, authentication and quota errors remain distinguishable;
unknown HTTP statuses remain visible for diagnosis. Requests are not retried.

Model Settings now includes an API exchange viewer. From version 0.2.0 onward,
recognition and connection tests preserve the serialized request body before
sending and the full response body before JSON/decision validation. This includes
provider errors, reasoning fields and detailed token usage. Authorization headers
are never recorded; any echo of the active API key in a body is replaced and the
trace marked redacted. A timeout has an input but no fabricated output. Old runs
have no raw trace and are not reconstructed from their parsed decisions.

`GET /api/exchanges` returns compact metadata; `/api/exchanges/{id}` loads the
full bodies and `/api/exchanges/{id}/export` downloads them with image Base64.
Large raw bodies are kept in a separate local SQLite table, not in the normal
run-list response. The UI folds Base64 only for the readable request preview;
the export retains it. Records survive restart, with unfinished requests marked
interrupted. A run's `api_exchange_id` identifies its exact API exchange.

Version 0.2.1 requests `response_format: {"type": "json_object"}` for
`glm-5.3-flash` only. Its model guide advertises structured output while the
generic API guide labels that parameter text-only; the combination was verified
with a real four-image request on 2026-09-23. Other model IDs retain their current
request format. The parser also accepts one complete JSON object wrapped in a
single JSON/unlabelled Markdown code fence. It never repairs truncation, extracts
an answer from surrounding prose, or treats reasoning as the final answer.
Finish-state and strict Decision/box validation still apply, and the recorded raw
response is unchanged. The old NEIRO 20:31 failure predates raw logging, so its
precise formatting defect is unknown; fresh replays are separate records.

Each current recognition remains independent: one candidate and all saved
references are included on every request. Saving references locally avoids
re-uploading them by hand, but creates no server-side conversation. Zhipu's
[Responses API](https://docs.bigmodel.cn/cn/guide/develop/responses/introduction)
offers `store` / `previous_response_id` continuation; it is not enabled here.
[Context caching](https://docs.bigmodel.cn/cn/guide/capabilities/cache) can reduce
repeated computation, but is not permanent image memory or a guarantee that
historical image context is free. Inspect actual `cached_tokens` in the raw usage.

The workspace polls the selected chart every 10 seconds while visible. The
candidate list refreshes every 30 seconds. Live charts combine SPIKE's confirmed
MA seed with the same OKX public candle endpoint, including the forming candle.
Only the selected market is fetched; no scanner, notifications or market files
are written. SMA uses trailing closes and EMA continues the confirmed seed;
each provisional update starts again from that seed. Missing or revised
confirmed bars cause an explicit error rather than fabricated/fallback prices.

Recognition defaults to the 12 bars after signal close (15m = 3h, 30m = 6h,
1H = 12h, 4H = 48h). The page can select 6/12/24/48 bars; the API accepts 1–96.
Expired signals remain viewable; recognition requires widening the range.
This is a research UI window, independent of production freshness gates.
No recurring model calls are made. After recognition, the submitted image stays
frozen until returning to the live chart; later prices never rewrite that run.

`GET /api/signals/{id}/chart?mode=live&post_signal_bars=12` returns an immutable
snapshot ID alongside up to 120 OHLC/MA rows. `POST /api/analyze` binds the
capture to `chart_snapshot_id` and `expected_chart_sha256`; it checks the
90-second snapshot lifetime and recognition expiry at submission. Refreshes
in another request do not replace a selected snapshot. Records distinguish
signal close, actual observation time, forming/closed state and visible end;
a forming candle's future scheduled close is not the observation time.
The original `mode=signal_close` (API default) and image endpoint keep their
historical causal semantics. Browser pixels remain explicitly unverified
input, not an attestation of numerical parity, even when the source row hash
and snapshot identity are validated.
Uploaded-image time boundaries are unverified. Images are decoded,
oriented and stripped of metadata without silent resizing; the stored image
is what the model sees. Limits: under 5 MB/image, 6 million pixels, 12 MiB combined.

Images, inference records, token usage and append-only review histories stay
in ignored `runtime/`. Export downloads a record as JSON. Provider decisions
and human reviews remain separate; neither becomes a training label or
production signal. No accuracy or trading outcome is asserted.

`VISION_RESEARCH_RUNTIME` changes the independent ledger directory.
`SPIKE_READONLY_URL` accepts loopback HTTP origins only. Zhipu's origin is fixed; there is no fallback to Google.

```bash
.venv/bin/python -m pytest tests/vision_research -q
node --check yoyo/vision_research/static/app.js
node --check yoyo/vision_research/static/theme.js
node --check yoyo/vision_research/static/chart.js
```

Provider sources: [chat completions](https://docs.bigmodel.cn/api-reference/模型-api/对话补全),
[GLM-5.3-Flash vision](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash).
The default is `glm-5.3-flash`; its thinking mode cannot be disabled. The client
uses a bounded reasoning budget and validates the returned JSON against the
local `Decision` schema. JSON mode is limited to the live-verified model as
described above. No provider-side storage opt-out is asserted.
Saved credentials include the provider identity; legacy Gemini keys are never
sent to BigModel. The supplied owner key stays in ignored private local settings.
Offline contract tests do not establish live access or recognition quality.

Chart API: [TradingView Lightweight Charts 4.2](https://tradingview.github.io/lightweight-charts/docs/4.2),
[chart screenshots](https://tradingview.github.io/lightweight-charts/docs/4.2/api/interfaces/IChartApi#takescreenshot).
