# SWAP Universe Expansion Report (FINAL)

**Date**: 2026-07-09T19:04:19.175091+00:00  
**Status**: **FINISHED** — expand log marker `expand swap fixed finished` at 2026-07-10 03:00:28 CST

## Numbers

| Metric | Value |
|---|---:|
| Live OKX plan (original missing list) | 347 |
| Fetched `okx_*_USDT_SWAP_15m_*.csv` | **399** |
| ≥~400d bars (≥35000) | **181** |
| ≥~90d bars (≥8000) | **286** |
| Very short (<2000 bars) | **31** |
| Among fetched in BLOCKED_BASES | **37** |
| Usable non-blocked ≥90d | **274** |
| BLOCKED_BASES size (loader) | **55** |

## Post-expand data audit (scripts/data_audit.py)

| Metric | Value |
|---|---:|
| series_total | 1049 |
| flagged | 603 |
| structural_flagged | 299 |
| part_files leftover | [{'path': 'ANIME_USDT_SWAP_15m.part.csv', 'approx_rows': 24899, 'size_bytes': 2075977}, {'path': 'MANA_USDT_SWAP_15m.part.csv', 'approx_rows': 24499, 'size_bytes': 1868552}] |
| okx_swap15_n | 363 |
| okx_swap15_stale | 1 |

Report: `analysis/p2_data_audit_report.md`  
CSV: `analysis/output/data_audit.csv`

## Recommendation

1. **Mainline judgment/forward**: keep liquid crypto SWAP set (historical ~54–60 + proven pool); do **not** auto-include all 399.
2. **Research expanded pool**: non-blocked bases with ≥90d history (~274 symbols) OK for discovery-only experiments after volume filters.
3. **Stock/thin listings**: remain in BLOCKED or short-history exclude; do not reverse BLOCKED without owner.
4. **Short stubs** (<2k bars): watchlist only.

## Shortest samples (examples)

- APLD: 308 bars
- BSP: 314 bars
- OSCR: 318 bars
- UNH: 320 bars
- ON: 321 bars
- SIMO: 321 bars
- TTWO: 321 bars
- VVV: 626 bars
- DATA: 689 bars
- BOT: 693 bars
- MUU: 703 bars
- MVLL: 704 bars

## Discipline

- No holdout eval from this expansion.
- No automatic mainline universe switch.
