# Independent original6253 exit checks

Audit date: 2026-09-14.  Scope is a read-only inspection of the frozen triple
exit engine and four already-produced `original6253` events.  It does not
replay the cohort, alter an exit rule, or use a network source.

## Contract read

`spike_v1_triple_exit.py` uses the following ordering for every held bar:

1. take the previous bar's native and overlay stops and select the tighter one;
2. test that stop against this bar's open/low (a gap fills at the open);
3. only if it survives, update the actual-entry MFE/MAE, native signal-close
   path, and overlay; the resulting stop applies on the next bar.

The native path receives `signal_close` and `reference_signal_risk`, preserving
the V1 signal-close contract.  The overlays use `entry` and
`initial_risk = entry - initial_stop`, so their R thresholds and their reported
net R use the executable following-open risk.  This separation is causal.

Engine SHA-256: `cefacf20b8ebb9aa8b1d2de59b0ac201df1f6a8aa73f33d25fa175e3e3f8d404`.
Study SHA-256: `8ce4de2c4630a0d23a37562aa6050f5183e4943298cf13e5ec1afc8df593023f`.

## Four hand calculations

All prices below come from `original_bars()` after it checked the selected
featured cache's source SHA against the original covered-ledger receipt.  The
result row is in the linked per-stream `outcomes.csv.gz`.  Formulae are
`gross = exit / entry - 1`, `net = gross - 0.002`, and
`net_R = net / (initial_risk / entry)`.

| Case | Event / cached source | Causal calculation and result |
| --- | --- | --- |
| MAE gap exit | `bf1ef…e8fb88`, Binance GALAUSDT 60m; [outcomes](../results/original6253/streams/637771753fa94438/outcomes.csv.gz) | Entry 2025-03-02 17:00 UTC = 0.021620; stop = 0.019055; actual R = 0.002565.  On 2025-03-03 14:00, low 0.019850 gives MAE `(0.021620 - 0.019850)/0.002565 = 0.690058R`, so only the *next* bar receives adverse stop `0.021620 - 0.65×0.002565 = 0.01995275`.  The 15:00 next open is 0.019890, below that stop, hence gap fill 0.019890.  Gross = -0.08001850; net = -0.08201850; actual-risk fraction = 0.11864015; net R = -0.69132164.  These equal the triple row (`adverse65`, `next_open_gap`). |
| BE exit | `9e32c…222c0`, Binance COMMONUSDT 30m; [outcomes](../results/original6253/streams/1a5cc91e772d085b/outcomes.csv.gz) | Entry 2026-01-10 17:30 UTC = 0.003154; actual R = 0.000153.  The completed 20:00 bar high 0.003242 gives MFE `(0.003242 - 0.003154)/0.000153 = 0.575163R`, which arms a 0.003154 entry stop from 20:30 onward.  At the 22:00 bar, open 0.003187 is above and low 0.003146 is below the active BE stop, so fill is 0.003154 (not a gap).  Gross = 0; net = -0.002; risk fraction = 0.04850983; net R = -0.04122876.  These equal the triple row (`be05`, `intrabar_stop`). |
| Lock-50 exit | `b9ae6…8fc15`, Binance BNTUSDT 240m; [outcomes](../results/original6253/streams/68eb355184c671d9/outcomes.csv.gz) | Entry 2026-08-19 16:00 UTC = 0.285800; actual R = 0.021900.  The 2026-08-22 04:00 high 0.342400 establishes MFE `(0.342400 - 0.285800)/0.021900 = 2.584475R`; next-bar lock stop is `0.285800 + 0.5×2.584475×0.021900 = 0.314100`.  The 2026-08-26 12:00 bar has open 0.321000 > 0.314100 and low 0.313100 <= 0.314100, so it fills at 0.314100.  Gross = 0.09902029; net = 0.09702029; risk fraction = 0.07662701; net R = 1.26613699.  These equal the triple row (`lock50`, `intrabar_stop`). |
| Same-bar stop precedes new protection | `06b78…ccfcc`, OKX BSB-USDT-SWAP 60m; [outcomes](../results/original6253/streams/167b1fca088a09c6/outcomes.csv.gz) | Entry 2026-04-20 17:00 UTC = 0.276700; old initial stop = 0.231940; actual R = 0.044760.  The entry bar's high only reaches 0.296300, or 0.437891R, so no BE is known for the next bar.  At 18:00 the old active stop is still 0.231940; that bar opens 0.294100 and lows to 0.230100, so it exits at 0.231940.  Its high 0.374900 would be 2.193923R, but is not used: the old stop had already fired.  Baseline and triple are therefore identical: gross = -0.16176364, net = -0.16376364, risk fraction = 0.16176364, net R = -1.01236372. |

## Provenance bindings

| Case | Original receipt SHA-256 | Source OHLC SHA-256 | Featured cache SHA-256 |
| --- | --- | --- | --- |
| GALA 60m | `fc795b9731935c282d6607cd4394c1d2309104be43fc719f756b42a964985d61` | `ed253795f9e127f99ec42cd7e7d74df6b10fc004d9e9f19302e3f92c29394ebd` | `27ea354e247a98b0ed10e0bc1912727dfb9c4a003ec2809493b0417b76560e80` |
| COMMON 30m | `ed66a1c3f18db1bbdeab9ccb08a57eb4523a446a5aedb4695dad3b03288c03bf` | `a303e18dc3215d220515ad29f3178c3eb451584e7c05018085598e105ff3e059` | `04b491f93fe54ee0b28fc17fb6314ac89ee027d66a1933bd71c3ed046a4f8d1d` |
| BNT 240m | `d564baa3f607f0aa487ac3ce18d3e9fd6939ab00d52da3bb92d9e2b60f299854` | `8fe86a49ee2098f47c97f4a97e99fcfd1438648725808d93c040348cf6a1be22` | `12098d406ce6a0b04c42ecc769bfe80d6786db925be60d0d86ea8832788e0830` |
| BSB 60m | `6e000bf35e48ba0969a185a1c93365b5f6243e50e00ac16a86d8b99edfb93fc1` | `ad3e40e119e4d2a4a99e0967a2197e28aa6fc775ed9a44d9969fd71509c86c55` | `6524555e39a508594bd1a2c8b25f099ea48bcac4c9491d495d9e64c9718f34b5` |

The event ledger read for this audit has SHA-256
`ca27280ec1ab2a98809052bf281854db18b24986ae4779494b0d88299f0b59ac`.

## Scope reconciliation and conclusion

The prior version of this audit incorrectly attributed the difference between
the direct sum and the primary statistic to `cross_cut_excluded`.  That field
is counted in the primary table but is not its return filter.  The source of
the difference is the explicit `closed()` contract in
`spike_v1_triple_stats.py`: `~censored & finite(net_r)`.

| Arm | All rows (n, sum net R) | Closed finite (n, sum net R) | Censored finite (n, sum net R) | Non-finite n |
| --- | --- | --- | --- | --- |
| baseline | 6,253; 862.13203146 | 6,170; 838.17847032 | 83; 23.95356114 | 0 |
| triple | 6,253; 1208.09205736 | 6,237; 1201.25378970 | 16; 6.83826766 | 0 |

Thus the requested primary results are exactly the closed finite sums.  The
censored rows retain a marked-to-final-complete-bar `net_r` in the per-stream
outputs, but primary return statistics do not include those marked values.
`cross_cut_excluded` remains a descriptive count only for this reconciliation.

Within the code boundary and four receipt-bound manual samples, no causal
ordering, gap-fill, cost, or actual-entry-risk error was found.  This is a
bounded event audit; it does not establish cohort-wide correctness beyond the
existing parity and statistics checks.
