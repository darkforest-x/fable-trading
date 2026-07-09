# SWAP Universe Expansion Report (FINAL)

Date: 2026-07-10 ~03:05 CST  
Status: **COMPLETE** — expand log marker `expand swap fixed finished` at `Fri Jul 10 03:00:28 CST 2026`  
Expand log: `/Users/zhangzc/fable-trading-codex/output/offline_tasks/expand_swap_15m_fixed_20260709_220053.log`  
Data dir (shared): `/Users/zhangzc/fable-trading/data/kline_fetched`  
Post-expand audit: `PYTHONPATH=. python3 scripts/data_audit.py` (main repo)

## Snapshot numbers

| Metric | Value |
|---|---:|
| Live OKX USDT-SWAP (universe CSV) | **401** |
| Planned missing at task start | **347** |
| Pre-expand fetched SWAP 15m files | **54** |
| Fetched `okx_*_USDT_SWAP_15m_*.csv` now | **399** |
| Still incomplete (`.part.csv` only) | **2** (`ANIME`, `MANA`) |
| Coverage of live universe | **399 / 401 (99.5%)** |
| Expand log `: done` lines | **345** (some batch restarts after SSL timeouts) |
| Transient batch failures | **2** Tracebacks (`socket.timeout` / SSL); xargs continued next batches |
| `loader.BLOCKED_BASES` count | **55** (stables + metals + equity wrappers + P2-12 owner-approved thin stocks) |

Sources: `output/offline_tasks/okx_swap_universe.csv`, `data/kline_fetched/`, `src/data/loader.py`, `analysis/output/data_audit_summary.json`.

## Usable history tiers (live, by filename bar count)

| Tier | Bars (≈ days @ 15m) | All SWAP files | Non-blocked only |
|---|---:|---:|---:|
| Full ~400d | ≥35k | **181** | **179** |
| Mid ≥90d | ≥8k | **286** | **274** |
| Usable stub+ | ≥2k | **368** | **332+** |
| Too short | <2k | **31** | **30** |

Notes:

- Max bars in fetch is typically **38499** (~400d request cap).
- Blocked bases with files on disk: **37** (still present as CSV; loader skips them).
- Short-history samples: APLD/BSP/OSCR/UNH/ON/SIMO/TTWO/VVV/DATA/BOT/… (mostly new listings or equity wrappers).

## Official data audit (`scripts/data_audit.py`)

| Metric | Value |
|---|---:|
| Generated (UTC) | 2026-07-09 19:03 |
| Series total (all bars/sources) | **1049** |
| Flagged | **603** |
| Structural flagged | **299** |
| Blacklist candidates (all) | **200** |
| OKX SWAP 15m series (loader-visible) | **363** |
| OKX SWAP 15m stale (>48h) | **1** |
| Unfinished `.part.csv` | **2** (ANIME, MANA) |
| New SWAP15 blacklist candidates (not yet in BLOCKED) | **40** |

### New SWAP15 blacklist candidates (advisory only)

All **40** are zero-volume / thin book equity-or-ETF-style SWAPs; **none** are already in `BLOCKED_BASES`. Top by zero_vol_share:

| symbol | zero_vol_share |
|---|---:|
| ISRG_USDT_SWAP | 0.463 |
| ROK_USDT_SWAP | 0.422 |
| SONY_USDT_SWAP | 0.275 |
| TTMI_USDT_SWAP | 0.268 |
| SHLD_USDT_SWAP | 0.248 |
| XLE_USDT_SWAP | 0.216 |
| TWLO_USDT_SWAP | 0.213 |
| USO_USDT_SWAP | 0.199 |
| TSEM_USDT_SWAP | 0.199 |
| ONDS_USDT_SWAP | 0.196 |
| RIVN / OSCR / SOFTBANK / TER / RDDT / … | 0.06–0.19 |
| META / MSFT / ORCL / NFLX / XPD | 0.06–0.10 |

Full list in `analysis/output/data_audit_summary.json` → `blacklist_candidates_swap15`.  
**Do not auto-write into loader** without owner approval.

Quick codex post-expand scan (separate watcher): 399 swap files, **0** gap files, **0** bad OHLC files, 210 with any zero-volume bars → `output/offline_tasks/data_audit_after_expand_*.{csv,json}` in codex worktree.

## Usability tiers (final)

1. **Core liquid crypto (historical mainline ~54–60)**  
   Full 400d history, funding partially available, judgment mainline. Keep frozen for forward_track until single-variable retest promotes a larger set.

2. **Long-history expanded crypto (~179 non-blocked with ≥35k bars)**  
   Usable for **research-only** candidate pools after excluding BLOCKED + new thin stock candidates. Prefer volume/liquidity filters on top of bar-count.

3. **Mid listings (~274 non-blocked ≥8k bars)**  
   OK for exploratory ranking; not for train/val without history-length guards.

4. **Stock/ETF/thin SWAPs (P2-12 blocked + 40 new audit candidates)**  
   High zero_vol, listing churn. Exclude from mainline. Owner may extend `BLOCKED_BASES` with the new 40.

5. **Too new / stub series (<2k bars) + incomplete ANIME/MANA**  
   Exclude from train/val; optional watchlist. Retry ANIME/MANA fetch later.

## Exclusions already in loader

`BLOCKED_BASES` (**55**): stables (USDC/DAI/…), metals (XAU/XAG/PAXG/…), tokenized equity wrappers (NVDAX/SPYX/…), plus P2-12 owner-approved thin equity/ETF bases (EWZ/CGNX/DKNG/AAPL/AMZN/… — see `src/data/loader.py`).

Expand task itself does **not** mutate BLOCKED; it only fetches.

## Recommendation (FINAL)

| Option | Verdict |
|---|---|
| Include all 401 / all 399 fetched | **No** — stock SWAPs, short history, thin books, incomplete series |
| Liquid crypto subset (current mainline discipline) | **Yes for mainline / forward_track** |
| Filtered expanded subset (non-blocked + ≥35k bars + drop new zero_vol stock candidates) | **Yes for research-only pools** (~179 → slightly fewer after volume filter) |
| Promote expanded set to production train | **Not yet** — needs owner single-variable retest + funding coverage check |

**Practical default:**

1. Keep frozen mainline / forward_track on the **existing liquid SWAP set**.
2. Research allowlist = non-blocked + ≥35k bars + not in the 40 new zero_vol stock candidates.
3. Owner decision: whether to append the 40 thin stock bases into `BLOCKED_BASES` (recommended, separate PR).
4. Retry `ANIME_USDT_SWAP` and `MANA_USDT_SWAP` 15m fetch (only remaining incomplete).
5. No holdout evaluation, no retrain, no threshold changes as part of this expansion.

## Artifacts

| Path | Role |
|---|---|
| `fable-trading-codex/output/offline_tasks/expand_swap_15m_fixed_20260709_220053.log` | Expand run log + finish marker |
| `fable-trading-codex/output/offline_tasks/missing_swap_symbols_20260709_220053.txt` | 347 planned symbols |
| `fable-trading-codex/output/offline_tasks/okx_swap_universe.csv` | Live 401 universe |
| `fable-trading/analysis/output/data_audit.csv` | Full audit rows |
| `fable-trading/analysis/output/data_audit_summary.json` | Audit summary + candidates |
| `fable-trading/analysis/p2_data_audit_report.md` | Regenerated P2-12 style report |
| `fable-trading/output/offline_tasks/swap_universe_expansion_report.md` | This FINAL report |
| `fable-trading/output/offline_tasks/FINAL_OFFLINE_SUMMARY.md` | Offline work rollup |
