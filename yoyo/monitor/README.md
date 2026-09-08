# Fable · Impulse Monitor

Local, notification-only OKX all-live-perpetual monitoring, authorized by the
owner on 2026-09-08. Active signal periods are **15m / 1H / 4H**. This service
has no exchange credentials or order endpoints.

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

The owner explicitly requires a **visible startup marker on the actual
TradingView price chart** before Telegram. The observed chart on 2026-09-08
uses IMACD V2.2 with `showFocus=true`, `focusMinBars=12`, `focusAtrBand=.10`,
`showMarks=false`. Its visible marker is **focus release**, not raw zero
axis departure or a hidden original-system entry.

- **Visible chart start (`tv_start`)** clones the confirmed `focusRelease`
  price label. At least 12 near-zero bars qualified the zone, its ATR band is
  frozen, both previous IMACD lines were still inside that band, and current
  md strictly leaves it in the marked direction.
- Previous md can already be nonzero. A raw `zero_breakout` inside the band
  does not show this marker and never sends Telegram. A signal-line-only exit,
  an unqualified zone, and subsequent glow bars do not trigger the marker.
- Density and HTF remain background because the observed visible-marker
  branch is independent of the hidden ordinary system entries and exits.
- The signal API, main arrows, counts and notifications all use `tv_start`
  under `imacd-tv-visible-start-monitor-v3`. Original observations and old
  receipts remain historical and are never relabelled as current signals.

The profile ID is `imacd-v2.2-focus12-band0.10-marks-off`. This is an observed
settings snapshot, **not automatic synchronization of future TradingView
setting edits**. Match any future settings change explicitly before relying
on correspondence. The current settings were read and left unchanged.

A concrete chart comparison is ETH 4H on 2026-08-19: the visible label is
44 near-zero bars / close 1922.23, on the 08:00 UTC opening candle (12:00 UTC
close). Raw zero departure at price 1911.20 occurs two 4H candles earlier
and is correctly excluded. The notification includes both candle open and
confirmation time so the user can locate the actual label on the chart.

The first calibrated exchange-time activation is persisted. Reconstructed
bars at or before that cutover are historical and cannot be sent after an
upgrade; restarts preserve the same cutover. Old pending notifications are
marked skipped. The delivery worker independently checks the marker contract.

All displayed signal prices are confirmed candle **closing prices**, not fills.
15m receives 1H context; 1H receives 4H context; 4H receives UTC daily context. Higher-timeframe values
must already have been available at the local candle's **open**. Missing higher
history is unknown. Daily boundaries can differ from a manually selected Pine
chart/session; finite startup history can also cause marginal state differences.
The monitor does not claim profitable or TradingView tick-for-tick equivalent
signals. It does not modify the saved Pine indicator.

## Data and timing

The universe refreshes hourly from public `SWAP` instruments in `live` state,
including USDT and coin-margined contracts. Symbols are not ranked or excluded
by recent returns or volume. An initial 720 closed bars per 15m/1H/4H/1Dutc stream
are fetched into RAM. The engine needs bar index 340 before readiness; very new
contracts remain explicitly warming up. A gap resets warmup instead of creating
a fake candle; conflicting confirmed quotes fail closed. The same 12-bar focus
rule applies on every period: on 15m that is three hours. This does not change
the indicator thresholds. Seven days is the journal retention window, not a
guarantee of seven days of initialized signals: 720 bars minus 340 warmup bars
initially covers about four days of eligible 15m observations.

Each newly added signal period has its own persisted exchange-clock activation.
Both queue insertion and TG/Bark delivery reject 15m closes at or before that
activation, including the most recent historical bar. Restart preserves this
cutover. Existing 1H/4H channel activations and receipts are unchanged.

Each completed scan is followed by a 120-second wait. At most eight public GET
requests per second are made across eight workers. Fully unchanged candles use
the in-memory result. During one process lifetime the original recurrence seed
is retained. On restart the finite history is fetched again; the UI explicitly
shows initialization and preserves past signals.

With N live contracts, a fully cold initialization needs about 12N candle
requests (four streams, three pages each), versus the previous 9N. At the
unchanged 8 requests/second, 473 contracts imply a request-budget floor near
710 seconds; parsing/calculation/network latency add to this. The usual 15m
boundary refresh needs about N requests (59 seconds at 473 contracts); the
daily aligned boundary can need 4N (237 seconds). Add the 120-second scan wait
and notification queue delay. The 30-minute freshness gates remain identical
in the scanner, delivery workers and frontend. Initial warming can take over
12 minutes; inspect progress before restarting. This service is independent
of the VPS forward pulse. In-memory history retains its original seed, so RAM
and recalculation cost grow over time and are visible through scan duration.

Only signals no more than **30 minutes** past close can enter the TG queue;
delivery rechecks the same limit. The frontend uses that same freshness limit.
Seven days of historical events are initially shown without notifying old
signals. The page polls every 15 seconds. Scanner health and stale chart state
are exposed independently from HTTP service availability.

Public contract: [OKX market data](https://www.okx.com/docs-v5/en/#order-book-trading-market-data).

## Telegram, Bark and privacy

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

Bark is an additional independent channel. Its owner-provided device key lives
only in `~/Library/Application Support/Fable/ImpulseMonitor/bark.json` with
mode 0600, separately from other project notification configurations. Restart
this service to load a changed configuration. The frontend exposes configured
state and counts, never the key or the private endpoint.

Only a new eligible `tv_start` after Bark's first calibrated-clock activation
can enter `bark_outbox`. Existing observations and Telegram receipts are not
replayed. Both channel rows are created atomically with a new event, then
handled by independent workers. Telegram remains enabled. No startup/test
notifications are generated. Failure or uncertainty in one channel cannot
consume the other's queue or success receipt.

Bark uses JSON POST to the official `/push` endpoint, with redirects disabled.
Its HTTP 200 / code 200 / server timestamp confirms server acceptance; it is
not a device display or read receipt. Definite client rejection is failed,
429 retries respect a bounded Retry-After, and uncertain delivery is not
automatically resent. Notifications contain the symbol, timeframe, direction,
close price, preparation count, candle times and the TradingView link.
Public contract: [Bark API V2](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md).

## Runtime locations

- SQLite: `~/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3`
- Singleton lock: same directory, `service.lock`
- Logs: `~/Library/Logs/Fable/ImpulseMonitor/`
- LaunchAgent: `~/Library/LaunchAgents/com.fable.impulse-monitor.plist`
- Code: `yoyo/monitor/`; tests: `tests/monitor/`

The VPS-owned OHLCV cache, forward log and production executor are not written.
No raw K-line files are persisted. Event rows contain derived state and the
signal price. The extra Codex heartbeat `imacd-mac` is paused following the
owner's question about unnecessary recurring AI checks. Market scanning,
Telegram and Bark run in the local service without an active Codex conversation.

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
