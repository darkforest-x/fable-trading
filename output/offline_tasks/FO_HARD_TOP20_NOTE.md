# FO hard list top20 (pre-E2.1-best, old preds mistakenness)

Source: `output/offline_tasks/fiftyone_hard/top50_mistakenness.tsv` (built from prior val preds, not new E2.1 best).
After E2.1 train finishes + finalize exports `preds_val_e21_best`, **recompute** mistakenness before acting.

| rank | sample |
|---:|---|
| 1 | `BTC_USDT_015460.png	mistakenness=0.34956` |
| 2 | `ALLO_USDT_014360.png	mistakenness=0.3486985` |
| 3 | `CFX_USDT_015360.png	mistakenness=0.3482305` |
| 4 | `UNI_USDT_014960.png	mistakenness=0.3477635` |
| 5 | `DOT_USDT_016960.png	mistakenness=0.347464` |
| 6 | `OP_USDT_015560.png	mistakenness=0.346223` |
| 7 | `LTC_USDT_016960.png	mistakenness=0.344937` |
| 8 | `LINK_USDT_017160.png	mistakenness=0.3442895` |
| 9 | `ADA_USDT_014460.png	mistakenness=0.34424` |
| 10 | `WLD_USDT_014560.png	mistakenness=0.343034` |
| 11 | `NEAR_USDT_014260.png	mistakenness=0.3430165` |
| 12 | `DOGE_USDT_017260.png	mistakenness=0.342832` |
| 13 | `HYPE_USDT_017160.png	mistakenness=0.34181249999999996` |
| 14 | `TRUMP_USDT_014860.png	mistakenness=0.341219` |
| 15 | `UNI_USDT_016960.png	mistakenness=0.341049` |
| 16 | `AAVE_USDT_016660.png	mistakenness=0.34082` |
| 17 | `ETHFI_USDT_014760.png	mistakenness=0.340496` |
| 18 | `AVAX_USDT_014460.png	mistakenness=0.340403` |
| 19 | `NIGHT_USDT_014860.png	mistakenness=0.339764` |
| 20 | `ADA_USDT_015960.png	mistakenness=0.3396615` |

## Tentative themes (manual scan of names only)
- Mix of majors (BTC/ETH/SOL-adjacent names) and alts; not a single thin-equity cluster.
- Several repeated symbols (UNI×2, AVAX×2, DOGE×2, ADA×2, LTC×2) → symbol-level hard regimes, not one-off chart bugs.
- **Do not** expand BLOCKED or change label geometry from this list alone.
- Next single-variable (approved family only): after E2.1 formal val, if hard boxes still wide-tail → consider MAX_DENSE_BARS 12→10 note-only proposal; if pad edges → leave X_PAD=6.

Written: 2026-07-09T22:06:48.802941+00:00
