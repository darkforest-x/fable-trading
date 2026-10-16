# SPIKE V13.1 → OKX API

Independent local execution module for Notion task 59. It reads the existing
signal center and keeps a separate durable exchange ledger. The monitor remains
notification-only. No model promotion, candle writer or public trading endpoint
is added.

Owner's 2026-10-16 settings:

- All signal-center symbols and 15m / 30m / 1H / 4H; both directions, including
  altcoins and any stock/commodity swaps already present in that source.
- No symbol whitelist, maximum position count or aggregate risk percentage cap.
- Allocation: 125 USDT (owner increased it from 50 USDT). Each entry sizes
  initial-stop price risk at 2% of `min(125, current USDT equity)`, rounded down
  to exchange contract increments. At 125 USDT equity, the target is 2.5 USDT.
  This excludes fees, funding and slippage. Insufficient margin/minimum-size
  failures are journaled, not rounded up or funded automatically.
- Isolated hedge mode. Each signal owns a virtual lot and an exchange attached
  stop; same-symbol lots may coexist across periods and in both directions.
  OKX aggregates same-side margin/positions, so these are not independently
  liquidatable subaccounts. Use an account dedicated to this adapter; external
  positions/orders fail ownership reconciliation and are never adopted.
- Maximum leverage from the exchange's applicable aggregate position tier.
  This can place liquidation before the strategy stop. Neither planned 1R nor
  the configured allocation is a guarantee about realized loss.

## Local setup

Run from the repository with its existing `.venv`; no new dependency is needed.
The live configuration was initialized locally for this task. Credentials were
subsequently saved privately; the execution worker has not been started. For a
new installation:

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

The account must use Futures account mode (`acctLv=2`) and long/short position
mode. Preflight reads these settings; it never changes them. Regional API hosts
can be selected when initializing a new runtime (`--host`); hosts are limited to
the official global/US/EU endpoints. Live and demo credentials cannot be mixed.

## Explicit execution

Private authentication and read-only account checks passed on 2026-10-16. The
account is currently in net position mode; the required long/short mode has not
been configured. Attached-stop creation/update and actual trading round trips
have **not** been tested. Offline tests are not exchange acceptance. The following command is an execution action by
the owner after account integration has been checked; it is not a status check:

```sh
.venv/bin/python -m yoyo.okx_execution run --ack EXECUTE-125-USDT-LIVE
```

The first start freezes a cutoff at the current time. It does not replay old
signals. Subsequent starts keep the original ledger and use the existing
30-minute freshness gate. Never delete/reset the ledger to resolve an uncertain
order: OKX permits reusing historical `clOrdId` values after orders leave the
pending state, so doing so can duplicate a filled entry.

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

## Verification

```sh
.venv/bin/python -m pytest -q tests/okx_execution tests/monitor/test_v130_signals.py tests/boundaries/test_layer_imports.py
```

Official API contracts checked on 2026-10-16:

- [Authentication, account and trade endpoints](https://www.okx.com/docs-v5/en/)
- [Regional domains and linear contract quantity](https://www.okx.com/docs-v5/)
- [Attached algo IDs and position-tier fields](https://www.okx.com/docs-v5/log_en/)

This is an execution adapter and fault-injection test suite, not a profitability
experiment or a strategy production-eligibility approval.
