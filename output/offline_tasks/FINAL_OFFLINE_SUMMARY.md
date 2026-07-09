# Final Offline Summary

Generated: 2026-07-10 ~03:05 CST  
Scope: SWAP universe expansion finish + post-expand data audit + YOLO tooling already completed.  
**Not in scope:** holdout evaluation, retrain, threshold changes, loader BLOCKED mutations.

## 1. SWAP 15m universe expansion — DONE

| Item | Value |
|---|---|
| Started | 2026-07-09 22:00:53 CST |
| Finished | 2026-07-10 03:00:28 CST |
| Screen / log | `fable_expand_swap_15m_fixed_20260709_220053` / `expand_swap_15m_fixed_20260709_220053.log` |
| Live OKX USDT-SWAP | **401** |
| Planned missing | **347** |
| Pre-expand SWAP 15m files | **54** |
| Post-expand SWAP 15m files | **399** |
| Incomplete `.part.csv` | **2** — `ANIME_USDT_SWAP`, `MANA_USDT_SWAP` |
| Coverage | **99.5%** of live universe |
| Notes | SSL timeouts + 2 batch Tracebacks; xargs continued; retries recovered most symbols |

History tiers (unique symbols, bar count in filename):

| Tier | Count (all) | Non-blocked |
|---|---:|---:|
| ≥35k bars (~400d) | 181 | 179 |
| ≥8k bars (~90d) | 286 | 274 |
| <2k bars | 31 | 30 |

Detail report: [`swap_universe_expansion_report.md`](./swap_universe_expansion_report.md) (**FINAL**).

## 2. Data audit — DONE

Command (main repo):

```bash
cd /Users/zhangzc/fable-trading && PYTHONPATH=. python3 scripts/data_audit.py
```

| Metric | Value |
|---|---:|
| Series total | 1049 |
| Flagged | 603 |
| Structural flagged | 299 |
| Blacklist candidates (all) | 200 |
| OKX SWAP 15m (loader-visible) | 363 |
| OKX SWAP 15m stale | 1 |
| Part files | 2 (ANIME, MANA) |
| New SWAP15 thin-stock candidates (not in BLOCKED) | **40** |

Outputs:

- `/Users/zhangzc/fable-trading/analysis/output/data_audit.csv`
- `/Users/zhangzc/fable-trading/analysis/output/data_audit_summary.json`
- `/Users/zhangzc/fable-trading/analysis/p2_data_audit_report.md`

Codex quick watcher also wrote:

- `fable-trading-codex/output/offline_tasks/data_audit_after_expand_summary.json`
- `fable-trading-codex/output/offline_tasks/data_audit_after_expand.csv`  
  (399 swap files; 0 gap / 0 bad OHLC file-level flags)

`loader.BLOCKED_BASES` = **55** (already includes P2-12 owner-approved equity/ETF bases). New 40 candidates are **advisory** only.

## 3. YOLO tooling eval — ALREADY DONE (earlier same night)

| Item | Result |
|---|---|
| Task finish | 2026-07-09 22:35:28 CST (`yolo tools task finished`) |
| Dataset | `datasets/dense_15m_full` |
| Weights | `dense_15m_full_s/weights/best.pt` |
| FiftyOne import probe | ok, 1255 samples |
| Direct YOLO (n=80, conf=0.30) | recall-like IoU50 **0.7938**, pred/gt **1.09** |
| SAHI sliced (same sample) | recall-like IoU50 **0.7732**, pred/gt **1.84** |
| Verdict | **Do not promote SAHI** — more FPs, no match gain |
| Label audit (18-img sample) | Geometry issues (wide boxes, edge clip, split/merge); no mass false labels |

Artifacts:

- `output/offline_tasks/yolo_tooling_eval_summary.md`
- `output/offline_tasks/yolo_sahi_direct_comparison_20260710.md`
- `output/offline_tasks/yolo_tooling_eval_report.json`
- `output/offline_tasks/yolo_label_audit_findings.csv`
- `output/offline_tasks/yolo_label_audit_recommendations.md`
- `output/offline_tasks/yolo_tooling_feasibility.md`

## 4. Recommendation rollup

| Topic | Action |
|---|---|
| Mainline / forward_track universe | Keep **liquid crypto subset** (existing discipline) |
| Expanded 399-file set | **Research only** after non-blocked + history + liquidity filters |
| New 40 thin stock SWAPs | Owner PR to extend `BLOCKED_BASES` (optional, recommended) |
| ANIME / MANA | Retry fetch when convenient |
| SAHI | Diagnostic only; no main path |
| Labels | Cleanup geometry / segment→bbox before next train |
| Holdout / retrain / thresholds | **Not done** (guardrails) |

## 5. Next manual inputs (owner)

1. Approve or reject adding the 40 new SWAP15 zero_vol stock candidates to `BLOCKED_BASES`.
2. Approve any research allowlist promotion to mainline via **single-variable** experiment only.
3. Approve label-rule / auto_label threshold changes before any YOLO retrain.
4. Approve adding forward tracking to daily scheduler (if still pending).

## 6. Key paths

| Path | Notes |
|---|---|
| `/Users/zhangzc/fable-trading` | Main repo; reports + `scripts/data_audit.py` |
| `/Users/zhangzc/fable-trading-codex` | Codex worktree; expand/yolo logs |
| `/Users/zhangzc/fable-trading/data/kline_fetched` | Shared kline cache (symlink target) |
| `output/offline_tasks/swap_universe_expansion_report.md` | FINAL expansion report |
| `output/offline_tasks/FINAL_OFFLINE_SUMMARY.md` | This file |
