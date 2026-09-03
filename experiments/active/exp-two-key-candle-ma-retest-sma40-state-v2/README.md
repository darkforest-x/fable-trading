# SMA40 two-key-candle state machine — fresh pre-holdout V2

V1 treated the six-line SMA/EMA 20/60/120 rope as the primary reference. The
owner's two TradingView anchors falsified that representation: the long K1 and
K2 interact cleanly with the visible SMA40(HL2) while remaining inside a much
wider six-MA rope. V2 therefore makes SMA40(HL2) the primary line and keeps the
six-line rope only as context.

The primary score and grade threshold are frozen from the two owner anchors
and public indicator semantics, not selected from outcomes. A permissive but
literal SMA40 morphology gate is followed by a 0–100 score that preserves:

- K1 displacement through SMA40 with large body/range/volume and a close near
  the directional extreme;
- K1-to-K2 distance of 3–6 completed 1h bars, no intervening close through the
  wrong side, limited extension and continuous MA-side candle colour;
- K2 wick rejection through SMA40, small body, close back on the intended side,
  and a usable extreme-based stop;
- MA Shift state transition from K1 directional acceleration to K2 aligned
  oscillator sign but counter-directional deceleration;
- confirmed 10/10 Market Break state aligned at K2.

The 70-point primary grade and preregistered 80/90-point sensitivity arms are
committed before opening the sealed local pre-holdout window 2026-03-01 through
2026-05-03. No row at or after the 2026-05-04 holdout boundary may be read,
scored or displayed.

One ETH-only implementation preflight computed candidate counts through the
safe boundary before this commit but did not inspect fresh-period returns.
ETH is therefore excluded from the sealed-window primary evaluation and is
reported only as a diagnostic; the remaining 53 symbols retain the frozen
profile chronology.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/research_two_key_candle_ma_retest_sma40_v2.py
PYTHONPATH=. .venv/bin/python scripts/validate_two_key_candle_ma_retest_sma40_v2.py
```
