# Strategy plugins and forward simulation

The workspace exposes a strategy catalog and independent forward runs on the
same local frontend. The interfaces borrow ideas from [Freqtrade strategies](https://www.freqtrade.io/en/stable/strategy-customization/),
[Nautilus models](https://nautilustrader.io/docs/latest/concepts/behavioral_models/)
and [LEAN modules](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview).
No third-party engine source was copied or new dependency installed.

## Contracts

- `strategies.py`: explicit allowlist, adapter identity, supported markets and
  periods, research references, source/dependency identity. A new strategy needs
  an adapter and behavioral tests before `paper_supported` can be enabled.
- `paper_source.py`: read-only monitor events/checkpoints and the existing
  frozen exit evaluator. It never uses the monitor's projected performance as
  a simulated fill. Unsupported protocols fail closed.
- `paper_store.py`: separate SQLite run, intent, transition and input records.
  Each run freezes the plugin, code bundle, universe, timeframes, 20 bp cost,
  entry fill policy and research links. There is no live-account mode.
- `paper_worker.py`: process-isolated consumer. It reads existing closed data;
  it does not add a scanner, download candles, emit notifications, modify the
  VPS forward log, or change production model eligibility.

## Time and execution

`signal_close <= detected_at <= observed_at < scheduled_entry_open`.
The worker admits only new signals after the run's activation boundary, using
the existing monitor freshness budget. A simulated order is scheduled at the
first chart open strictly after the worker actually records the signal.
The open price is reconciled once that bar is available as a closed bar. This
is a bar-based simulated market fill, not a recorded exchange transaction.

The historical replay still enters at the open following signal confirmation.
The forward adapter deliberately cannot use that historical price if discovery
was late. Both reuse the existing exit evaluator and fixed cost assumptions;
entry delay therefore remains a separately visible difference. Exits are
precommitted bar-model rules evaluated in market event time and reconciled
after receiving closed OHLC. They do not model scanner-to-order latency at
every stop or opposite-signal exit; transition timestamps record when the
worker learned the outcome. The initial
stop is the original signal's stop, while initial R is measured from the
simulated entry. An invalid risk distance rejects entry.

A stream has at most one pending/open position per run. Different symbols and
timeframes use independent unit notional; sum R is not account equity. Funding,
order-book impact, queue priority, position sizing and margin are not modeled.
No aggregate result here proves outperformance of a matched random control.

Pause cancels orders scheduled for future opens and stops admitting signals;
existing or already-due intents continue reconciliation. Resume establishes a
new admission boundary, without backfilling signals seen during the pause.
Stop preserves every receipt and censors positions without inventing an exit.
Changing strategy means creating a new run; historical runs are not rewritten.

The worker has a singleton OS lock and runs independently of the HTTP service.
A service restart relaunches existing active runs; the first poll reconciles
previously persisted intents before looking for new signals. Missing bars,
revised already-consumed bars, or changed strategy source remain explicit.
A version failure requires a new run, rather than silently hot-swapping code.
A stopped or failed run never contributes a fabricated closed trade.

## Local operations

Run data lives under the monitor runtime's `research_workspace/paper/`.
`paper.sqlite3` retains consumed OHLCV observations, decisions and transitions;
`sources/<hash>.zip` retains exact source snapshots. These files are private
local state and are not committed. The UI offers a JSON run export and the API
also offers `/api/research/paper/runs/<id>/sources`.

For an explicit one-pass diagnostic using the same saved run state:

```sh
.venv/bin/python -m yoyo.research_workspace.paper_worker \
  --root "$PWD" \
  --runtime "$HOME/Library/Application Support/Fable/ImpulseMonitor/research_workspace/paper" \
  --monitor-runtime "$HOME/Library/Application Support/Fable/ImpulseMonitor" --once
```

Legacy V7/V8 observations remain available through the simulator's history
link. Watch structures and pre-activation history remain signal-center views;
they are not standalone strategy engines and are not promoted into fills.
