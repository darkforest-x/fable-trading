# Frozen Protocol: df1-eth-15m-short-v1

## Market scope

- Exchange: OKX
- Instrument: ETH-USDT-SWAP
- Timeframe: 15 minutes
- Direction: short only
- Input: closed OHLCV bars in UTC
- Canonical history start: `2024-01-01T00:00:00Z`
- Canonical storage: contiguous 15m Parquet snapshot plus a verified lineage manifest
- Canonical volume: OKX `vol` field; for the swap instrument this is contract volume
- Data lineage hash: protocol + market + data configuration only

## Candidate baseline

The initial candidate source is `ma-density-v1`, based on the normalized spread of SMA/EMA 20,
60, and 120. Candidate generation is causal and produces no order by itself.

The point-in-time feature contract is:

- SMA and EMA 20/60/120 must all be finite; partial moving-average warm-up is ineligible.
- `ATR14` is the 14-bar simple mean of true range.
- `ma_bandwidth_atr` is `(max(SMA/EMA) - min(SMA/EMA)) / ATR14`.
- normalized volatility is `ATR14 / close`.
- `volatility_percentile` is the weak empirical percentile of the current normalized volatility
  inside the last 2,880 finite observations: `count(value <= current) / 2,880`.
- The rolling window includes the current closed bar. It never uses a full-sample rank.
- `signal_time` is the source bar's `available_time`; every feature input must have
  `available_time <= signal_time`.
- A candidate fires when `ma_bandwidth_atr <= 0.35` and
  `volatility_percentile <= 0.40`. Both boundaries are inclusive.
- Every qualifying bar is retained; P1 applies no cooldown and never reads labels, returns, or
  the next bar.

## Matched random controls

`volatility-bucket-random-v1` creates one offline research control for every candidate. Controls
do not feed candidate generation or order policy. They are selected from the same frozen canonical
snapshot and may be earlier or later than their candidate, but every control feature is causal at
its own `available_time` and selection never reads outcomes or labels.

A valid control is feature-eligible, satisfies the same low-volatility threshold, and fails the
candidate rule only because `ma_bandwidth_atr > 0.35`. It must exactly match the candidate on:

- UTC year-month;
- UTC four-hour block (`hour // 4`);
- weekday versus weekend;
- right-closed normalized-volatility decile;
- sign of `price_distance_to_ma_center`.

Within the exact stratum, the control with the smallest SHA-256 selection value over the frozen
control seed, matcher version, candidate ID, and control signal time is chosen. A control may be
reused across different candidates; each candidate still receives exactly one control. An empty
pool fails the build without writing partial artifacts. Zero candidates remains a valid, explicit
zero-row research artifact.

Candidate artifacts are a Parquet dataset, deterministic diagnostics JSON, and JSON lineage
manifest. The manifest binds semantic candidate/control hashes and file hashes to the verified
canonical content/config hashes, candidate config, source commits, protocol, and cutoff.

## Initial execution assumptions

- Entry: next-bar open after an accepted signal
- Stop: 2 ATR
- Target: 4 ATR
- Timeout: 48 bars
- Risk budget: 0.20% of simulated equity
- Maximum concurrent positions: 1
- Round-trip fee assumption: 8 bps
- Round-trip slippage assumption: 4 bps
- Same-bar stop and target collision: pessimistic resolution

## Versioning rule

Changing the symbol, timeframe, direction, candidate definition, label, entry timing, cost model, stop, target, timeout, feature availability, model, or threshold requires a new manifest and—when semantics change—a new protocol version.

## Promotion rule

Research artifacts may enter replay or paper only after deterministic regeneration, walk-forward validation, matched-control comparison, versioned manifests, and regression tests. No automatic promotion is allowed.
