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
The top-right light/dark switch remembers the choice in browser storage.
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

On its first start, the service installs the existing boxed exemplars bundled in
`default_references/`. The manifest records original paths, hashes, core geometry
and confirmation level. Pixels and boxes are copied unchanged; normalization
only strips container metadata. These charts include later context and are
retrospective references, not decision-time samples or individually adjudicated
gold geometry. Click a reference to inspect it at full size. An edited or
deliberately cleared library is never automatically reseeded.

Select a candidate or upload a PNG/JPEG/WEBP image, edit the criteria, then
explicitly start recognition. The interactive chart is captured at that moment
and shown as the exact submitted snapshot; return to the chart to pan/zoom again.
Only
this action sends selected pixels and criteria to Zhipu's
`https://open.bigmodel.cn/api/paas/v4/chat/completions` API. No search, order or notification tools
are available to the model. Connection testing sends one short text completion with a small token budget;
it consumes model usage and does not establish image-recognition quality. Failed recognition records retain
the HTTP status, an allowlisted provider error code and elapsed time without
storing provider response text. Documented billing, authentication and quota errors remain distinguishable;
unknown HTTP statuses remain visible for diagnosis. Requests are not retried.

SPIKE input charts use up to 120 closed bars ending exactly at the selected
signal close, with existing causal SMA/EMA values and no signal annotations.
Insufficient historical cache produces an error, never a newer substitute
chart. The chart endpoint exposes only those causal OHLC/MA rows. Browser
captures retain the source rows and their SHA-256 in the record; their pixels
are explicitly unverified browser input, not an attestation of numerical parity.
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
local `Decision` schema. Vision requests do not assume support for text-only
`response_format` fields. No provider-side storage opt-out is asserted.
Saved credentials include the provider identity; legacy Gemini keys are never
sent to BigModel. The supplied owner key stays in ignored private local settings.
Offline contract tests do not establish live access or recognition quality.

Chart API: [TradingView Lightweight Charts 4.2](https://tradingview.github.io/lightweight-charts/docs/4.2),
[chart screenshots](https://tradingview.github.io/lightweight-charts/docs/4.2/api/interfaces/IChartApi#takescreenshot).
