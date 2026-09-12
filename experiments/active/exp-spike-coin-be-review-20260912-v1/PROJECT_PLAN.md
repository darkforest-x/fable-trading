# SPIKE coin-level break-even review — descriptive plan

Owner request (2026-09-12): provide coin-by-coin evidence and actual exit
examples for BTC, ETH, SOL, ZEC, PEPE, WIF, TAO, plus fixed historical
examples SOPH, USELESS, BICO, DOGE, and SUI.  These assets are fixed before
this report is built.  Chart examples are deliberately selected after the fact
by a stated exit mechanism and fixed coverage quota; they are not a return-rank
winner reel and cannot estimate a success rate.

## Scope and authorization

- This is a descriptive slice of the already completed, owner-authorized
  `exp-spike-exit-policy-20260912-v1` all-date historical replay.  This is the
  first review read of its already-authorized holdout-era rows for this slice;
  it does not rerun or score a new trading rule, select thresholds, or fetch
  markets.  Development/validation are reused chronological research, never
  new blind evidence.
- Source is its `config.json` raw frozen replay pointer, 3,531 completed source
  streams, and receipt-bound engine/post-processing outputs.  The builder
  checks the config, upstream manifest, post manifest, and each selected
  stream's trade/fill SHA-256 identities before writing a slice.
- V1 means `v1_common_long`: the archived long-only admissions replayed through
  the shared exit engine.  It is not the original native V1 return stream.
- Comparison policies are unchanged `baseline`, `be1_price`, and `be1_cost`.
  Price break-even is not called net break-even: fees can leave it negative.
- `PEPE` includes the frozen `1000PEPE` Binance contract alias in all-venue
  detail, while retaining its original `asset` and `symbol`; OKX's PEPE swap
  remains separately identifiable.  No table may call a single symbol "all
  exchanges" when the alias is excluded.

The first local build before alias normalization contained 78 streams and is
superseded.  The final reproducible build will include the three frozen Binance
`1000PEPEUSDT` streams, for 81 selected streams; it preserves the source asset,
symbol, and unscaled price in every detailed row.

## Fixed reporting contract

- Detail dimensions: venue × asset × timeframe × cohort × policy × period.
  Main human table prioritizes OKX 1H; all venues and periods remain CSV.
- Event statistics use entry-time development/validation membership.  The
  independent-account return and close drawdown use calendar close NAV and
  carry an open holding over the cut date.  Therefore these two period views
  cannot be added together for an arithmetic reconciliation.
- Each stream is a separately endowed hypothetical account: 1% target risk,
  1x entry-notional cap, 0.2% round-trip cost.  No cross-stream compounded
  portfolio is constructed.
- Charts are post-hoc explanatory cases, not success-rate estimates.  They show
  the same frozen entry and actual baseline/BE exits, including outcomes where
  BE only reduces loss or cuts a later trend.  Later candles are review context.
