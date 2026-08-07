# P0_AUDIT — Local Signal V2

> 规范 §19 要求本路径；仓库对照报告见  
> `analysis/p0_local_signal_v2_audit_20260807.md`（Stage A 失败审计）与  
> Stage B 重建完成后更新的 `analysis/p0_local_signal_v2_stageb_report.md`。

## 状态

- **Stage A** (`datasets/dense_owner_w20_midbox`): **P0 FAIL**（因果 95% 未来 K / 非时间切分 / holdout 泄漏 / hardneg 无 manifest）
- **Stage B** (`datasets/local_signal_v2_stageb`): **重建中** → 审计 `analysis/output/p0_local_signal_v2_stageb_audit.json`

## 交付清单（规范 §14 P0）

| # | 交付物 | 路径 |
|---|---|---|
| 1 | Legacy map | `reports/LEGACY_PIPELINE_MAP.md` |
| 2 | Baseline freeze | 同上 + ACCEPTANCE_DECISION |
| 3 | Event schema | Stage B manifest fields: event_id, mid_global, confirm_delay, decision_bar, … |
| 4 | Causal sampler | `scripts/build_local_signal_v2_stageb.py` |
| 5 | Preview | `analysis/output/local_signal_v2_stageb_preview/` |
| 6 | Tests | `tests/test_local_signal_v2_stageb.py` + `tests/test_w20_midbox_causality.py` |
| 7 | Gate audit | `scripts/audit_local_signal_v2.py` |
| 8 | Decision | `reports/ACCEPTANCE_DECISION.json` |

## 硬门槛

见审计 JSON 的 `gates` 与 `p0_pass`。全绿后自动进入 P1（owner 2026-08-07 授权）。
