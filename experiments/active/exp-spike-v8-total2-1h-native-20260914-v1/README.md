# CRYPTOCAP:TOTAL2 1H — Frozen V8 native-strategy harness

This is a **research-only Pine v6 strategy harness** for a manually opened
TradingView `CRYPTOCAP:TOTAL2` one-hour chart. TOTAL2 is an index and no
tradable venue, fill source, fee schedule, or instrument tick contract has
been frozen. Therefore the Strategy Tester equity/profit is not account P&L.
The visible table reports index-path R only: gross R from native fills and a
separate fixed `0.20%` round-trip-cost sensitivity (`net R = gross R -
0.002 × entry / frozen stop distance`). It is not an executable portfolio or
an approval to trade.

## Source contract

- Frozen signal source: `yoyo/evaluation/pine/spike_burst_v8.pine`
- SHA-256: `ca50a66e5ecfa16b6088be56936ac8c5cffb6c548f9a114d67364c9f42a439cc`
- The source block through `rawSignalSide` / `signalSide` is copied verbatim,
  except that the one required `indicator(...)` declaration is replaced by
  `strategy(...)`. Display code, plots, drawings, labels, alerts, and risk-box
  UI after that boundary are omitted.
- Signal remains bidirectional V8: BB admission plus the 3-ATR rope-distance
  gate controls entries. Raw V6 opposite confirmations remain exit events even
  if they fail V8 admission.
- The observed TOTAL2 Data Window volume is only evidence that the current bar
  exposes a `Vol` field. Its historical completeness and economic semantics
  are **unverified**. Do not interpret a result as a validated volume study
  until the visible history and volume field are audited.

## Frozen native execution semantics

1. A completed `signalSide` confirmation submits a long/short market order;
   TradingView's default `process_orders_on_close=false` fills it at the next
   bar open.
2. The stop price is frozen at confirmation close from the completed signal
   five-bar extreme ± `0.2 ATR`, capped with the signal-close ± `2 ATR` rule,
   rounded outward to the chart tick. R uses the actual native next-open fill
   versus that frozen stop. A next-open gap that makes this risk non-positive
   is retained as an explicit invalid-risk diagnostic, not repaired.
3. A close at `>=2R` arms a close-based `close ± 4 ATR` protection. The order
   update submitted at that close becomes operative no earlier than the next
   bar. It only tightens. There is no fixed TP and no partial TP.
4. A raw opposite V6 confirmation schedules `strategy.close` for the next
   open, independently of the new V8 entry gate.

## Virtual quantity, not a capital backtest

TOTAL2 is roughly trillion-scale. The declaration intentionally uses a fixed
**one virtual index unit**, `initial_capital=1`, and `margin_long=margin_short=0`
to prevent TradingView's default capital/margin guard from converting valid
signals into an apparent zero-trade result. This setting has no economic
meaning and is why only the table's dimensionless R fields may be reported.
Ignore Strategy Tester equity, cash P&L, return percentage, margin and drawdown
fields. `syminfo.mincontract` remains chart-defined and is not claimed to be a
trading contract.

## Important emulator limits

TradingView's broker emulator knows OHLC bars, not real intrabar order path.
A stop and a raw-opposite confirmation can be ordered differently from the
Python frozen ledger on the same bar; gaps, stop fills, reversal ordering,
slippage, and commission treatment are model assumptions. The strategy's
native commission is deliberately zero so the table can show gross R and an
explicit fixed cost sensitivity. Do not compare its equity directly with the
V8 exchange ledger or call it a serial account backtest.

## Operator procedure (manual; no export workaround)

1. Use the existing TradingView chart, set `CRYPTOCAP:TOTAL2`, interval `1H`.
   Leave the active unfinished bar out of the recorded sample.
2. Open Pine Editor, create a **new unsaved** script, paste
   `spike_v8_total2_1h_native.pine`, add it to the chart, and confirm it
   compiles. Do not overwrite a saved user indicator/layout.
3. Use Strategy Tester only on the chart history TradingView has loaded. Record
   exact visible first/last **closed** bar timestamps, timezone, bar count,
   Strategy Tester closed-trade count, table values (including raw V6 → BB-admitted → final V8 and valid/≥1.5 three-bar-volume counts), and whether Volume is
   populated throughout the used warmup/evaluation range.
4. A zero trade, compilation failure, missing/zero volume, history gap, or
   unknown volume semantics is a result/limitation. Do not substitute ETH,
   exchange OHLCV, or an altcoin aggregate.

## Observed run (2026-09-14)

`run_receipt_verified.json` contains the final native screenshot reading and
native trade-list DOM transcription: 1,794 closed hours; one closed long;
gross 38.928R and net 38.491R under the fixed cost sensitivity. These are
index-path results, not account profits. Full readiness first occurs at
2026-08-14 07:00 UTC; the delay and Volume semantics remain unaudited.

`run_receipt.json` is the rejected original transcription; `audit_receipt.json`
is an intermediate insufficient audit. Preserve both; neither overrides the
final receipt. The actual CUA image has no saved local file, so source hashing
and arithmetic validation must not be described as an independent screenshot
review. See `analysis/p1_spike_v8_total2_1h_native_20260914.md`.
