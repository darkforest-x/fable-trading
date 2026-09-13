# ETH V8 low-timeframe cost diagnostic

The sole baseline input is the existing `run_20260913_v3` V8 closed-trade
ledger: ETH 3m OKX and ETH 5m Binance are reported independently, by their
existing development/later folds. The separate ETH 3m `run_v2` fixed-entry
pairs explain why the next-bar 1R break-even exit worsened outcomes. They do
not turn the result into a new serial V8 replay.

The one frozen admission-cost diagnostic is `fee_r <= 0.5`, where
`fee_r=0.002/initial_risk_frac`. It corresponds to a frozen initial risk
fraction of at least 0.4%. `initial_risk_frac` includes the actual next-open
entry fill, so it is an entry-time check and must not be described as known at
signal close. It is applied only as a historical existing-trade subset; it does
not simulate fills, freed capacity, or replacement trades. The four fee-R
buckets are descriptive and will not select another threshold.

Every table reports count, win rate, net-R sum/mean, PF in R and nominal-return
units, realized >=10R, and the sum excluding the best trade. Direction, exit
reason, and confirmation-month tables expose stability. The study consumes
previously authorized, nonblind historical ledgers, reads no OHLCV, and changes
no strategy parameters or production behavior.
