# Fable · Impulse Monitor

Local, notification-only OKX all-live-perpetual monitoring, authorized by the
owner on 2026-09-08. Active signal periods are **15m / 1H / 4H**. This service
has no exchange credentials or order endpoints.

## Open and operate

Open **http://127.0.0.1:8766** on this Mac. The backend serves the frontend and
read-only API from the same loopback origin. There is no separate frontend build.

The startup page presents confirmed signals as selectable cards: contract and
period, direction, original close, preparation bars, and Beijing confirmation
time. Fresh cards precede older records. Freshness requires the API flag and the
runtime's existing time limit, with elapsed time measured from the calibrated
status clock. Failed synchronization leaves cached records visible but removes
fresh emphasis. Notification receipts remain independent for TG and Bark.

Search, period and direction filters apply to the latest 2,000 loaded records;
cards load in batches of 24. The observation page has its own paginated cards
and is explicitly separate from confirmed startups. Only the selected contract
loads a structure chart. On narrow screens, selecting a card jumps to that
chart; the return button restores focus to the originating card. Static edits
take effect on a browser page reload without restarting the monitor.

Client behavior checks: `node --test tests/monitor/frontend_cards.test.cjs`.
These DOM-stub tests do not replace real browser layout and keyboard checks.

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
pandas and NumPy. Pillow renders notification snapshots. This delivery did not
install or change dependencies.

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
and is correctly excluded. The notification caption shows confirmation time;
the image's time axis labels candle opens.

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

Telegram uses a compact three-line caption: symbol/period/direction,
confirmed close/preparation bars, and Beijing confirmation time. A single
TradingView button replaces the raw URL. Each new eligible event includes a
1080-square PNG with up to 120 actual candles, six thin moving averages,
the startup marker and price, and IMACD blue/orange lines with a visible zero
axis. The chart ends at the signal candle; no future candles are included.

The renderer validates the target timestamp, close, and indicator values before
delivery. Rendering failure falls back to the compact text before HTTP. There
is never a second text send after an uncertain photo upload. New PNG bytes and
their SHA-256 are persisted with the event/outbox transaction so a 429 retry
uses the same picture. Existing historical/sent events are not backfilled or
replayed; an old pending event without media can use text. Snapshot counters
measure generated media, not photo-delivery receipts. Derived PNG storage grows
with notified events; raw candle arrays remain in RAM.

SQLite atomically inserts an immutable event, PNG and its outbox row. Repeated scans,
concurrent jobs and restarts do not requeue the same identity. Telegram 429s
honor `retry_after`; definite rejection is failed. Timeout, malformed response,
5xx or interrupted sending is **unknown**, not automatically resent. This
avoids blind duplicates but can lose a notification when delivery cannot be
confirmed; the signal and uncertain status remain visible. A successful delivery
requires an actual Telegram `message_id` receipt, plus photo metadata for images.

Public contract: [Telegram Bot API](https://core.telegram.org/bots/api#sendphoto).

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

### Open a structure in Mac TradingView

The watch card's **查看结构** button and the detail pane's **Mac TradingView**
button send the selected card's exact OKX swap and `15m` / `1H` / `4H` interval
to `POST /api/tradingview/open`. Clicking the card body still opens spike's
preview. The detail pane retains an explicit web fallback. Rendering, scanner
updates and refresh never activate the desktop app.

The owner authorized an AppleScript bridge on 2026-09-08. Mac Desktop 3.4.0
supports the [clipboard menu workflow](https://www.tradingview.com/support/solutions/43000673907-how-to-open-a-tradingview-chart-link-in-desktop-app/),
not a chart deep link through its registered login URL scheme. The bridge uses
the desktop window's internal menu: the macOS menu dispatch returned without
navigating in local tests. It reads live accessibility bounds for the title-bar
button; it does not assume absolute screen coordinates or modify a saved layout.
The generic chart URL reused the owner's current `综合过滤` layout in direct
Mac tests (BTC-USDT 4H and TRUTH-USDT 15m).

The service must run in this Mac's unlocked GUI session with TradingView
installed and signed in. In **System Settings → Privacy & Security → Automation**,
the installed caffeinate-wrapped LaunchAgent requires **caffeinate → System
Events** enabled. Accessibility permission must also be allowed for the process
macOS identifies. Shell/Codex authorization does not imply LaunchAgent
authorization. On 2026-09-08, the live page correctly showed failure because
this Automation switch was observed off; direct-script success is not a passed
end-to-end LaunchAgent test. Enable the switch, click a card, and verify the
actual chart symbol and interval before calling deployment acceptance complete.

Only same-origin POSTs carrying `X-Spike-Action: open-tradingview` are accepted;
the service remains loopback-only. Inputs are restricted swap/interval values,
and the URL is argv data, never shell or AppleScript source. One action runs at
a time. The script checks a 15-second deadline with 3-second per-event timeouts;
Python has a 25-second outer timeout. The clipboard is restored on ordinary
success/error unless the user copied something else. Hard process/OS failures
cannot guarantee restoration. The response says **requested**, not that market
data loaded. Errors are sanitized and surfaced; no automatic retries occur.

Validation:

```bash
.venv/bin/python -m pytest tests/monitor/test_tradingview.py -q
node --test tests/monitor/frontend_cards.test.cjs
osacompile -o /tmp/spike-tradingview-bridge.scpt yoyo/monitor/tradingview.applescript
```

### Appearance

The header's **外观主题** picker supports **深色**, **浅色**, and **跟随系统**
(default). An explicit choice is stored only in this browser's `spike.theme`
localStorage key and synchronized to other spike tabs on the same origin.
Restricted storage falls back to a working, nonpersistent selection. The theme
controller runs before CSS first paint to avoid flashing the wrong appearance.
Cards, status states, chart lines/candles/zero axis and the expanded chart dialog
share semantic CSS color tokens; switching does not reload chart data or reset
filters/selection. The Mac TradingView app's own theme remains independent.

```bash
node --test tests/monitor/frontend_theme.test.cjs tests/monitor/frontend_cards.test.cjs
```
