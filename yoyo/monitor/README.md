# Fable · Impulse Monitor

Local, notification-only OKX all-live-perpetual monitoring, authorized by the
owner on 2026-09-08. This service has no exchange credentials or order endpoints.

## Open and operate

Open **http://127.0.0.1:8766** on this Mac. The backend serves the frontend and
read-only API from the same loopback origin. There is no separate frontend build.

From `/Users/zhangzc/fable-trading`:

```bash
.venv/bin/python -m yoyo.monitor.manage install
.venv/bin/python -m yoyo.monitor.manage status
.venv/bin/python -m yoyo.monitor.manage restart
.venv/bin/python -m yoyo.monitor.manage stop
```

`install` is idempotent for an identical configuration; it does not replace an
unexpected existing LaunchAgent. `stop` unloads only this service; `install`
loads it again. The LaunchAgent starts on login and restarts a crashed process.
`restart` reloads source, preserving the SQLite journal and deduplication keys.
Do not run multiple server instances against the same runtime directory.

For foreground development, stop the managed instance first:

```bash
.venv/bin/python -m yoyo.monitor.server --port 8766
```

The existing repository `.venv` supplies Python 3.9, FastAPI, Uvicorn, requests,
pandas and NumPy. This delivery did not install or change dependencies.

## What the signals mean

The owner corrected the monitoring definition on 2026-09-08: **a signal is
only the first confirmed bar where IMACD md leaves exactly zero**.

- **Zero-axis start (`zero_breakout`)**: previous md is exactly 0; current md is
  positive (upward start) or negative (downward start), after calculation warmup.
  Tiny nonzero values qualify immediately without waiting for an ATR band.
- Continuing on the same side, returning to zero, crossing the signal line, or
  directly switching signs with no preceding zero bar does not trigger it.
- Six-MA density and the confirmed higher timeframe are background annotations,
  not gates. The message includes the preceding exact-zero run and signal price.
- Legacy dense entries, near-zero releases, trend ends and wick retests remain
  chart observations, but are not primary signals and never send Telegram.

The signal API, 24-hour count and Telegram queue use only the current
`imacd-zero-axis-monitor-v2` protocol and `zero_breakout` kind. Previous event
journals and sent-message receipts are retained, not reclassified as new starts.
The first calibrated exchange-time activation is persisted. Reconstructed bars
at or before that cutover are historical and cannot be sent after an upgrade;
restarts preserve the same cutover. Old pending notifications are marked skipped.
The delivery worker independently checks the event contract before sending.

All displayed signal prices are confirmed candle **closing prices**, not fills.
1H receives 4H context; 4H receives UTC daily context. Higher-timeframe values
must already have been available at the local candle's **open**. Missing higher
history is unknown. Daily boundaries can differ from a manually selected Pine
chart/session; finite startup history can also cause marginal state differences.
The monitor does not claim profitable or TradingView tick-for-tick equivalent
signals. It does not modify the saved Pine indicator.

## Data and timing

The universe refreshes hourly from public `SWAP` instruments in `live` state,
including USDT and coin-margined contracts. Symbols are not ranked or excluded
by recent returns or volume. An initial 720 closed bars per 1H/4H/1Dutc stream
are fetched into RAM. The engine needs bar index 340 before readiness; very new
contracts remain explicitly warming up. A gap resets warmup instead of creating
a fake candle; conflicting confirmed quotes fail closed.

Each completed scan is followed by a 120-second wait. At most eight public GET
requests per second are made across eight workers. Fully unchanged candles use
the in-memory result. During one process lifetime the original recurrence seed
is retained. On restart the finite history is fetched again; the UI explicitly
shows initialization and preserves past signals.

Only signals no more than **30 minutes** past close can enter the TG queue;
delivery rechecks the same limit. The frontend uses that same freshness limit.
Seven days of historical events are initially shown without notifying old
signals. The page polls every 15 seconds. Scanner health and stale chart state
are exposed independently from HTTP service availability.

Public contract: [OKX market data](https://www.okx.com/docs-v5/en/#order-book-trading-market-data).

## Telegram and privacy

The existing `yoyo.notify._load()` reads the owner's gitignored config or
`TG_BOT_TOKEN` / `TG_CHAT_ID` environment. No credentials enter frontend JSON,
source, logs, SQLite or acceptance artifacts. Exchange API keys are unnecessary.

SQLite atomically inserts an immutable event and its outbox row. Repeated scans,
concurrent jobs and restarts do not requeue the same identity. Telegram 429s
honor `retry_after`; definite rejection is failed. Timeout, malformed response,
5xx or interrupted sending is **unknown**, not automatically resent. This
avoids blind duplicates but can lose a notification when delivery cannot be
confirmed; the signal and uncertain status remain visible. A successful delivery
requires an actual Telegram `message_id` receipt.

Public contract: [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage).

## Runtime locations

- SQLite: `~/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3`
- Singleton lock: same directory, `service.lock`
- Logs: `~/Library/Logs/Fable/ImpulseMonitor/`
- LaunchAgent: `~/Library/LaunchAgents/com.fable.impulse-monitor.plist`
- Code: `yoyo/monitor/`; tests: `tests/monitor/`

The VPS-owned OHLCV cache, forward log and production executor are not written.
No raw K-line files are persisted. Event rows contain derived state and the
signal price. The independent hourly Codex heartbeat `imacd-mac` checks health
and reports only a meaningful change; routine market scanning itself does not
depend on an active Codex conversation.

`caffeinate -is` prevents idle sleep (system-sleep assertion while on AC power).
Leave this Mac plugged in and connected. Closing its lid, shutting it down,
logging out or losing network can interrupt monitoring. A phone's localhost is
not this Mac; the provided page is intentionally local to this computer.

## Verification

```bash
.venv/bin/python -m pytest -q tests/monitor
curl -fsS http://127.0.0.1:8766/api/health
curl -fsS http://127.0.0.1:8766/api/status
```

Tests include future perturbation, HTF availability, frozen focus bands, wick
semantics, failure/recovery at unchanged timestamps, concurrent SQLite insertion,
uncertain delivery, restart recovery, old-message suppression and clock rejection.
No economic performance metric is implied by passing these software tests.
