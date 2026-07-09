# SWAP Universe Expansion Report (INTERIM)

Date: 2026-07-10 ~00:52 CST  
Status: **IN PROGRESS** — `fable_expand_swap_15m_fixed_20260709_220053` still running  
Update this file to FINAL when log contains `expand swap fixed finished` and post-expand audit completes.

## Snapshot numbers

| Metric | Value |
|---|---:|
| Live OKX USDT-SWAP (universe CSV) | **401** |
| Planned missing at task start | **347** |
| Fetched `okx_*_USDT_SWAP_15m_*.csv` now | **~190** (rising) |
| Expand log symbols with `: done` | **~136** (batch progress continues) |
| Enough history ≈400d (≥35k bars) | **~102** |
| Enough history ≈90d (≥8k bars) | **~142** |
| Very short (<2k bars) | **6** (e.g. APLD/BSP/DATA/BOT/CAP/ARX) |
| `loader.BLOCKED_BASES` count | **33** (stables + equity/metal wrappers already blocked) |

Sources: `output/offline_tasks/okx_swap_universe.csv`, `data/kline_fetched/`, `src/data/loader.py`.

## Usability tiers (preliminary)

1. **Core liquid crypto (already used historically ~54–60)**  
   Full 400d history, funding partially available, judgment mainline.

2. **Long-history alt SWAP (~100 with ≥35k bars)**  
   Usable for expanded candidate pools **if** liquidity/volume filters pass post-audit.

3. **Mid/short listings (many stock-ticker SWAPs, <90d)**  
   High listing churn, thin books, stock-like bases already dominate P2-12 blacklist candidates (EWZ/CGNX/DKNG/AAPL…). **Do not auto-include in mainline.**

4. **Too new / stub series (<2k bars)**  
   Exclude from any train/val; optional watchlist only.

## Exclusions

- Existing `BLOCKED_BASES`: stables (USDC/DAI/…), equity/index wrappers (NVDAX/SPYX/…), metals (XAU/XAG/PAXG), etc.
- P2-12 recommended **additional** stock/ETF thin SWAPs — still **not** written into loader (owner decision pending).
- Expand task itself does **not** change BLOCKED; it only fetches.

## Recommendation (interim)

| Option | Verdict |
|---|---|
| Include all 401 | **No** — noise, stock SWAPs, short history, funding gaps |
| Liquid crypto subset | **Yes for mainline** — keep current judgment universe discipline |
| Filtered expanded subset | **Yes for research-only pools** after post-expand data audit |

**Practical default after finish:**

1. Wait for post-expand `data_audit_after_expand_*`.
2. Build a **research allowlist** = long-history + non-blocked + non-stock-ticker bases.
3. Keep frozen mainline / forward_track on the **existing liquid SWAP set** until owner promotes a new universe with a single-variable retest.

## Remaining offline watchers

- `fable_post_expand_data_audit_20260709_220937` — waiting on expand finished marker
- `fable_final_summary_rerun_20260710_001757` — waiting on expand + audit

When expand finishes, re-run counts and replace INTERIM → FINAL in this file header.
