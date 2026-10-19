# SPIKE V13.1 → OKX API

Independent local execution module for Notion task 59. It reads the existing
signal center and keeps a separate durable exchange ledger. The monitor remains
notification-only for automated signals. No model promotion or candle writer
is added. The owner-confirmed manual entry described below uses a different
OKX account and an independent worker, private runtime and ledger.

Owner's 2026-10-16 settings:

- All signal-center symbols and 15m / 30m / 1H / 4H; both directions, including
  altcoins and any stock/commodity swaps already present in that source.
- No symbol whitelist, maximum position count or aggregate risk percentage cap.
- Allocation: 125 USDT (owner increased it from 50 USDT). Each entry sizes
  initial-stop price risk at 2% of `min(125, current USDT equity)`, rounded down
  to exchange contract increments. At 125 USDT equity, the target is 2.5 USDT.
  This excludes fees, funding and slippage. Insufficient margin/minimum-size
  failures are journaled, not rounded up or funded automatically.
- Cross-margin hedge mode. Each signal owns a virtual lot and an exchange attached
  stop; same-symbol lots may coexist across periods and in both directions.
  OKX shares USDT collateral across the account and leverage across each
  instrument family. These are not independently liquidatable subaccounts, and
  125 USDT is a sizing allocation, not a ring-fenced loss limit. Use an account dedicated to this adapter; external
  positions/orders fail ownership reconciliation and are never adopted.
- Maximum leverage permitted by both sides' applicable position tiers.
  Cross sizing reads currency-level `availEq` and `imr`; collateral is checked
  again after changing shared leverage, before any entry order.
  This can place liquidation before the strategy stop. Neither planned 1R nor
  the configured allocation is a guarantee about realized loss.

## Owner-confirmed YOLO entries (2026-10-17)

In `#yolo-detect`, select a saved shape and choose **预览做多/做空**. Opening the
dialog only reads the account and market and stores a short-lived preview.
The owner must click **确认实盘做多/做空** for each entry. **实盘记录** shows
the durable request, fills, and verified protection after leaving the dialog.

The account bar and every ticket say **均线密集专用账户**. Signal-center account
data comes from `OKXExecution`; manual account data and `/api/manual-live`
come only from `OKXManualExecution`. Both live under
`~/Library/Application Support/Fable/`, with distinct credentials, execution
ledgers, queues, locks and logs. Neither worker consumes the other entry source.
The manual runtime requires a UID-bound `manual_account_policy` with version 1
and mode `manual_only_shared`. Missing or mismatched identity fails closed.
The signal runtime preserves its original account and cutoff.

The owner's existing FIL/SAND positions and BTC/ETH pending orders remain
owner-managed. Manual entry rejects any instrument occupied by external
positions or pending orders, including opposite-side positions. It never adopts
those lots. Later mutations verify exact system-owned quantities and conflicting
orders before proceeding; reads cannot atomically reserve an instrument against
concurrent owner edits.

- 1R is 3% of total trading-account equity, converted from OKX `totalEq` USD
  through the same balance receipt's USDT `eq / eqUsd`. Funding-wallet assets
  are not included. This does not change SPIKE's 125 USDT / 2% contract.
- Default SL is the latest fully closed 5m low for a long, high for a short.
  A manually entered SL is supported; a crossed/invalid SL rejects the preview.
- TP defaults to 3R, with 5R available. Both exits attach as a full-lot OCO.
  After entry, TP is calibrated once to actual fill price and rounded toward
  entry to the price tick. Manual lots never use SPIKE trailing/opposite exits.
- Maximum valid leverage, exact contract increments, and available margin are
  rechecked. Execution cannot increase reviewed quantity, monetary risk budget,
  or posted margin. Actual fill slippage/fees/funding can exceed estimated risk.
- Preview expires after 60 seconds. Editing terms requires another preview.
  A quote timestamp at most five seconds ahead (the existing preflight clock tolerance) waits for the local clock, then
  still must pass the original 0–5000 ms age gate and original preview deadline.
  Larger clock leads and delayed waits fail closed; timestamps are never patched.
  Atomic confirmation and the existing pre-send journal prevent duplicate
  orders or blind retries of unknown exchange responses. A skipped, zero-fill
  attempt requires a new preview and another owner click.
- The owner explicitly removed the loopback-only restriction. Remote use follows
  the existing login-free public workspace access; same-origin/custom JSON
  checks prevent cross-origin form submission, **not visitor authentication**.
  The public workspace can confirm orders. No API credentials are exposed.
- `/api/manual-live` persists private queue entries only. The locked CLI worker
  is the sole exchange writer. No signal training/promotion or scheduled AI task
  is implied. Research detection clocks remain context, not automatic freshness.

An order is shown as protected only after live exchange OCO fields match both
exits and the expected lot. A stale receipt is shown as awaiting refresh.
Missing/invalid owned protection initiates an owned-lot exit; this is not a
guarantee of the exit fill or protection against liquidation.

## Local setup

Run from the repository with its existing `.venv`; no new dependency is needed.
The live configuration and private credentials were initialized locally, and
the owner activated the worker on 2026-10-16. For a new installation:

```sh
.venv/bin/python -m yoyo.okx_execution init
```

Enter credentials in your own terminal (all three inputs are hidden):

```sh
.venv/bin/python -m yoyo.okx_execution credentials
.venv/bin/python -m yoyo.okx_execution preflight
.venv/bin/python -m yoyo.okx_execution preview
.venv/bin/python -m yoyo.okx_execution status
```

Use an OKX API key with Read and Trade, without Withdraw. Never paste credentials
into chat, Notion, shell arguments or the repository. The directory
`~/Library/Application Support/Fable/OKXExecution` is mode 0700; configuration,
credentials and ledger are owner-only. `status` does not load credentials.
Credential replacement is deliberately a separate owner action.

Current independent session labels are `com.fable.okx-execution.session`
(signal center) and `com.fable.okx-manual-execution.session` (manual YOLO).
The latter's private `worker-manual.plist` passes the global `--runtime` option
before `run`. Its manual policy selects `ManualEngine`; the default signal
runtime selects `Engine`. Stopping one session does not stop the other. The
manual policy does not authorize automatic signals or adoption of existing lots.

The account must use Futures account mode (`acctLv=2`) and long/short position
mode. Preflight reads these settings; it never changes them. Regional API hosts
can be selected when initializing a new runtime (`--host`); hosts are limited to
the official global/US/EU endpoints. Live and demo credentials cannot be mixed.

## Explicit execution

Private authentication and read-only account checks passed on 2026-10-16.
On 2026-10-17, exchange GET receipts confirmed filled STABLE and USO entries
and their live attached stops. This does not yet verify stop triggering,
amendment or a complete live exit round trip. Offline tests are not exchange
acceptance. The following command is an execution action by
the owner after account integration has been checked; it is not a status check:

```sh
.venv/bin/python -m yoyo.okx_execution run --ack EXECUTE-125-USDT-LIVE
```

The first start freezes a cutoff at the current time. It does not replay old
signals. Subsequent starts keep the original ledger and use the existing
30-minute freshness gate. Never delete/reset the ledger to resolve an uncertain
order: OKX permits reusing historical `clOrdId` values after orders leave the
pending state, so doing so can duplicate a filled entry.

Quote freshness uses the response-receipt clock, with the original five-second
limit. If a signal quote leads the local receipt clock by at most the existing
five-second preflight tolerance, the worker waits once, then rechecks the
original quote-age and signal-expiry gates. The raw receipt and exchange times
remain unchanged; validation time and elapsed wait are recorded separately.
Larger leads, stale quotes, and signals expiring during the wait still fail
closed. After leverage readback and account reconciliation, the worker obtains
a fresh quote and recalculates size using the same frozen stop and 2% rule.
If the required leverage tier changes, no entry is submitted on that attempt.
Transient preparation GET failures are recorded as `retry_pending` with
2/4/8/16/30-second backoff, bounded by the original signal expiry. These rows
own no positions; manage-only mode never retries entries. Any recorded entry
intent or ambiguous POST remains on the reconciliation path and is not resent.
Historical `skipped`/`rejected` rows stay terminal; this fix does not replay them.
The account execution table shows signal identity, an enumerated failure reason,
and the next retry time without exposing raw private API errors.

The monitor stores successful candle-read observations separately from compressed
seed revisions. An unchanged 4H prefix remains current when a matching recent
observation exists; failed reads cannot refresh it. The original 30-minute
freshness limit and the completed-candle coverage check still apply. Management
failures and recoveries are logged with their observation times.

To stop new entries while continuing management:

```sh
.venv/bin/python -m yoyo.okx_execution run --ack EXECUTE-125-USDT-LIVE --manage-only
```

Stop the previous process before switching modes; an OS lock permits one worker.
Ctrl-C stops local management, **not** exchange positions. Confirmed exchange
stops remain, but trailing and raw-opposite management require the worker and
signal source to stay online. Inspect `status` after any worker error. Pending
or ambiguous actions need reconciliation, not a blind retry/reset. Partial
exit, liquidation or manual inventory changes require owner inspection; this
version refuses to infer which virtual lot to charge for an unexplained change.

For a separate demo environment, use `--runtime /absolute/outside-repo/path`
before each subcommand, initialize with `init --demo`, enter demo credentials,
and use `--ack EXECUTE-DEMO`. Nothing switches an existing runtime's environment.

## Execution semantics and known boundaries

The signal's frozen initial stop is retained, rounded outward to the exchange
tick. Entries use market orders after detection, not a historical reference
open. Actual fills define management R. Closed-bar 2R arming, 1H 2ATR / other
periods 4ATR, monotone trail and raw-opposite exits reuse the monitor's causal
OHLC/ATR/raw-side builder. Stop updates and exits execute when observed; there
is no claim of exact reference next-open fills or identical reference returns.

Entry includes an attached SL using last-trade-price triggering and market
execution. The API creates the attachment after the parent fills completely;
the worker verifies its actual ownership, quantity and trigger. Partial entries
are canceled/reconciled and their actual filled quantity is closed. Protection
failure requests an owned-lot close. This cannot eliminate exposure during a
network outage or an exchange rejection.

Raw-opposite exits cancel only the owned attached stop, verify cancellation,
then submit a hedge close for that lot. There is an unprotected interval between
cancel and close; if that close is rejected or uncertain, new entries stop and
the ledger requires attention. Never assume the receipt of a cancel or order
submission is a fill. Exchange-triggered stops reconcile their actual order
receipts, not chart performance. An uncertain amendment retains the previously
confirmed exchange stop while blocking additional entries.

The repository's existing paper manifest includes the whole `yoyo` tree. Adding
this module changes that manifest; it does not silently repin or restart old
paper runs. The execution service is not installed for boot or deployed to a
VPS by this change.

The 2026-10-17 quote/retry repair replaced the already-running terminal worker
with the session-only LaunchAgent `com.fable.okx-execution.session`, retaining
its original ledger, cutoff and configuration. Its plist is in the private
runtime (`worker-session.plist`), not `~/Library/LaunchAgents`; `KeepAlive` is
false and it is not installed for login/boot. Logs are in the private runtime's
`logs/worker-session*.log`. The previous terminal's Ctrl-C no longer controls
this worker. The owner can stop this session worker with:

```sh
launchctl bootout gui/$(id -u)/com.fable.okx-execution.session
```

As with Ctrl-C, stopping the worker leaves exchange orders/positions intact and
stops local trailing/opposite management. Do not launch a second worker; the
existing OS lock still enforces one writer.

The 2026-10-18 recovery found that a later update had launched the signal worker
in a Codex PTY instead of restarting the existing LaunchAgent. That chat session
was subsequently torn down, leaving no independent executor running. The
owner-authorized recovery restored `com.fable.okx-execution.session` after
GET-only reconciliation of eight owner-confirmed manual closes. The original
ledger, signal cutoff, account configuration and remaining protection were
preserved. Private receipts and the pre-recovery database are retained under
the runtime's `recovery-20261018/` directory.

For an authorized recovery of this already-loaded, stopped job, use
`launchctl kickstart gui/$(id -u)/com.fable.okx-execution.session` after checking
the ledger and exchange state. Do not replace it with a long-running Codex
`exec_command` / PTY session. Verify the launchd-owned PID, runtime lock,
advancing heartbeat, entry readiness and reconciliation errors after starting.
The existing session-only installation and `KeepAlive=false` remain unchanged;
this is not a new login/boot activation or permission to reset uncertain trades.

## Verification

The signal center at `http://127.0.0.1:8766/#signals` includes a read-only OKX
panel: currency equity/available balance/unrealized PnL, all-account positions
and pending orders, worker liveness, and the latest 50 owned execution records.
The independent `GET /api/okx-execution/status` reader never starts the worker,
imports its engine, initializes its ledger, or sends exchange POSTs. It caches
private GETs for 30 seconds and only refreshes on demand; it does not join the
signal scan pulse. Missing values remain unknown; failures retain a visibly
stale prior snapshot. An idle lock file or old heartbeat is not proof of a
running worker, and liveness does not prove new entries are enabled.

Owner enabled remote read-only display on 2026-10-16. The existing login-free
public gateway shows the same account panel as the local signal center;
anyone with that public link can view balances, positions and execution records.
The endpoint retains same-origin browser checks and no-store responses.
No credentials, account IDs, private paths, raw exchange errors or execution
controls are exposed. Signal reference returns
remain separate. Per-record realized net amounts require complete exchange PnL
and USDT fee receipts, exclude funding, and never use reference R.

```sh
.venv/bin/python -m pytest -q tests/okx_execution tests/monitor/test_v130_signals.py tests/boundaries/test_layer_imports.py
node --test tests/monitor/frontend_manual_live.test.cjs tests/monitor/frontend_okx_execution.test.cjs tests/monitor/frontend_module_loader.test.cjs tests/monitor/frontend_yolo_detect.test.cjs
```

Official API contracts checked on 2026-10-16:

- [Authentication, account and trade endpoints](https://www.okx.com/docs-v5/en/)
- [Regional domains and linear contract quantity](https://www.okx.com/docs-v5/)
- [Attached algo IDs and position-tier fields](https://www.okx.com/docs-v5/log_en/)

This is an execution adapter and fault-injection test suite, not a profitability
experiment or a strategy production-eligibility approval.
