# Agent runner backoff must use semantic errors

- **Problem**: A successful Grok batch entered a five-hour failure backoff even though the CLI returned exit code 0 and all expected artifacts were produced.
- **Dead end**: Matching broad words such as `timeout` anywhere in the full agent log also matches harmless OpenTelemetry shutdown warnings, so observability noise becomes control flow.
- **Effective path**: Separate exit-status failure from explicit retryable service errors, remove known telemetry lines before classification, and lock both sides with fixtures: telemetry timeout must stay on the short cooldown while HTTP 429 must back off.
- **General rule**: Background agent schedulers should classify failures from process status and narrow provider error signatures, never from generic words in mixed stdout and telemetry logs.
- **Affected areas**: `.omo/runtime/run_grok_two_day.sh`, `scripts/lib/grok_retry_policy.zsh`, Grok CLI telemetry, five-hour failure backoff.
