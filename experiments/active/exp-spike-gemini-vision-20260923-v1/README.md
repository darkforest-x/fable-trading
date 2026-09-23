# SPIKE Vision Lab

Independent local workbench for Gemini visual judgments and owner review.
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

Set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in the server environment, or enter it
in the local settings page. Page-entered keys live only in process memory and
must be entered again after a restart. They are never saved in browser storage,
exports or the database. `GEMINI_MODEL` overrides the default model.

Select a candidate or upload a PNG/JPEG/WEBP image, optionally add up to four
reference images, edit the criteria, then explicitly start recognition. Only
this action sends selected pixels and criteria to Google's
`generativelanguage.googleapis.com` API. No search, order or notification tools
are available to the model. Connection testing checks key/model access only.

SPIKE input charts use up to 120 closed bars ending exactly at the selected
signal close, with existing causal SMA/EMA values and no signal annotations.
Insufficient historical cache produces an error, never a newer substitute
chart. Uploaded-image time boundaries are unverified. Images are decoded,
oriented and stripped of metadata without silent resizing; the stored image
is what the model sees. Limits: 8 MB/image, 6 million pixels, 12 MB combined.

Images, inference records, token usage and append-only review histories stay
in ignored `runtime/`. Export downloads a record as JSON. Provider decisions
and human reviews remain separate; neither becomes a training label or
production signal. No accuracy or trading outcome is asserted.

`VISION_RESEARCH_RUNTIME` changes the independent ledger directory.
`SPIKE_READONLY_URL` accepts loopback HTTP origins only. Gemini's origin is fixed.

```bash
.venv/bin/python -m pytest tests/vision_research -q
node --check yoyo/vision_research/static/app.js
```

Provider sources: [images](https://ai.google.dev/gemini-api/docs/image-understanding),
[JSON schema](https://ai.google.dev/gemini-api/docs/structured-output),
[REST response](https://ai.google.dev/gemini-api/docs/get-started),
[stateless requests](https://ai.google.dev/gemini-api/docs/interactions-overview).
Offline contract tests do not establish live access or recognition quality.
