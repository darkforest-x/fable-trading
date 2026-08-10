# LEGACY_PIPELINE_MAP — Local Signal V2 P0

**Date**: 2026-08-07 · **HEAD**: see `git rev-parse HEAD` at freeze time  
**Scope**: map only; does not modify ACTIVE / owner_best / forward_log.

| Spec component | Location | Notes |
|---|---|---|
| K-line load | `~/yoyo-trading/yoyo/data/loader.py` | `list_series` / `load_series`; `data/kline_cache` symlink + `data/kline_fetched` |
| MA features | `yoyo/layers/l1_detection/data.py:add_mas` | SMA/EMA 20/60/120 |
| Chart render | `yoyo/layers/l1_detection/render.py:render_chart` | 1280×742; no grid/text/signal overlay |
| Pixel↔bar | `make_chart_transform` / `ChartTransform` | reversible x_at / y_at |
| Legacy 200-bar labels | `datasets/dense_owner_v14_pad200` + `data/golden_pool.json` | owner boxes |
| Box→bar recover | `scripts/build_w20_midbox_dataset.py:resolve_pad_window` | MAD ≤ 5.0 vs stored PNG |
| Stage A mid-box | `scripts/build_w20_midbox_dataset.py` → `datasets/dense_owner_w20_midbox` | **P0 FAIL** (future bars, hash split, holdout leak) |
| Stage B V1 | `scripts/build_local_signal_v2_stageb.py` → `datasets/local_signal_v2_stageb` | **P0 FAIL**：negative windows 跨时间块 |
| Stage B strict-negative V2 | `scripts/build_local_signal_v2_stageb_strictneg_v2.py` → `datasets/local_signal_v2_stageb_strictneg_v2` | Mode C；全样本严格时间切分；P0 PASS |
| Legacy symbol split | `src/detection/owner_eval.py:split_of` | sha1(symbol) — **not** used for Stage B |
| Live tip scan | `yoyo/layers/l1_detection/scan.py` / candidates | tip / tip-1 / tip-2 only (rule 12) |
| YOLO train | `scripts/train_w20_midbox_on_3060.sh` → 下发本仓 `src/detection/train.py` | 禁止远端 trainer 漂移；flip/mosaic/mixup/HSV 全关；no auto-promote |
| L2 judgment | `models/frozen_*.json` + ACTIVE symlink | L2 freeze; L1 = owner_best |
| Forward / paper | `yoyo/layers/l4_execution/` + VPS `data/forward_log.csv` | VPS sole writer |
| Layer boundary tests | `~/yoyo-trading/tests/test_layer_boundaries.py` | AST enforced |

## Baseline freeze (hashes at P0 start)

| Artifact | Path | sha256 prefix |
|---|---|---|
| commit | `main` | `0595dd2…` (pre-V2 commits; uncommitted work separate) |
| Legacy detector | `models/owner_v10_chain.pt` | `b9a84b5f5ebf0032dfa8…` |
| Stage A cold | `analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt` | (disk) |
| Stage A hardneg | `analysis/output/w20_overnight/cycle_hardneg_c1/weights/best.pt` | (disk) |
| Stage A pos manifest | `datasets/dense_owner_w20_midbox/w20_manifest.json` | (disk) |

## Iron rules preserved

1. Holdout ≥2026-05-04 not in train (Stage B enforces).  
2. Time split only for Stage B.  
3. No promote without owner.  
4. New code: scripts + reports under this repo; render/MA stay in yoyo.
