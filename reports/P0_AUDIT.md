# P0_AUDIT — Local Signal V2

## 当前状态（2026-08-10）

- **Stage A** (`datasets/dense_owner_w20_midbox`)：P0 FAIL（未来 K / 非时间切分 / holdout 污染等历史问题）。
- **Stage B V1** (`datasets/local_signal_v2_stageb`)：**P0 FAIL**。正样本虽按时间切分，但 317 条 train negatives 晚于 train block，296 条 val negatives 早于 val block；旧审计只看正样本而误报通过。
- **Stage B strict-negative V2** (`datasets/local_signal_v2_stageb_strictneg_v2`)：**P0 PASS（8/8）**。审计见 `analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json`。
- **Builder HEAD**：`471f854`；数据与报告在该提交之后重新生成。

## 交付清单（规范 §14）

| # | 交付物 | 路径 / 状态 |
|---|---|---|
| 1 | Legacy map | `reports/LEGACY_PIPELINE_MAP.md` |
| 2 | Baseline freeze | `reports/ACCEPTANCE_DECISION.json` |
| 3 | Event + manifest schema | `datasets/local_signal_v2_stageb_strictneg_v2/manifest.jsonl` |
| 4 | Causal sampler | `scripts/build_local_signal_v2_stageb_strictneg_v2.py` |
| 5 | 20–50 event preview | 24 events / 24 symbols，`analysis/output/local_signal_v2_stageb_strictneg_v2_preview/` |
| 6 | Tests | `tests/test_local_signal_v2_stageb.py` 等 |
| 7 | Gate audit | `scripts/audit_local_signal_v2.py`（失败返回非零） |
| 8 | P0 报告 | `analysis/html/p0_local_signal_v2_stageb_strictneg_v2_report.html` |

P0 通过后按交接规范 §14 **停止等待 owner 验收**，不自动进入大规模训练。
