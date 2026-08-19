# 来源资产裁决

> 机器可读版本：`docs/consolidation/source_asset_registry.json`（C6 生成）
> 逐文件台账：`reports/consolidation/migration_ledger.jsonl`

五档裁决：

| 分类 | 含义 |
|---|---|
| `DIRECT_PORT` | 语义、依赖和测试均兼容，字节原样迁移 |
| `ADAPT_AND_PORT` | 能力有价值，但必须适配统一 contract、数据层或 Python 3.9 |
| `REFERENCE_ONLY` | 只登记来源、哈希和结论，不复制代码或大产物 |
| `HISTORICAL_REPORT` | 迁入报告、结论与复现命令，不接入默认运行路径 |
| `REJECT` | 重复、泄漏风险、已被替代、不可复现或会污染主线 |

本文件随阶段增补。C7 时与 migration ledger 逐条核对。

## 全局约束

- **权重只登记一次**。已在本仓的物理文件（`models/*.pt`）登记同一 artifact_id 与
  SHA，不创建第二份副本；不在 3060 上的权重登记为 `REFERENCE_ONLY` 并记存储位置。
- **大产物不进 git**。`tools/consolidation/port_asset.py` 默认拒绝 >2 MiB 的文件，
  也拒绝写入被 .gitignore 吞掉的目标路径。
- **不复制任何密钥**。`.env`、API key、交易所密钥、TG token、SSH 私钥一律不迁移；
  扫描结果只记路径与类型，不输出内容。

## C1 — 治理骨架

| 来源 | 资产 | 裁决 | 理由 |
|---|---|---|---|
| `yoyo-trading` | `yoyo/`（55 个 .py，整包） | `DIRECT_PORT` | canonical package 迁回。本仓 63 个文件 import 它，不迁则 ACTIVE 仓不可运行。55/55 字节一致。 |
| `yoyo-trading` | `tests/test_layer_boundaries.py` | `ADAPT_AND_PORT` | 迁为 `tests/boundaries/test_layer_imports.py`，扩充任务书 §5 的方向规则，并把 `yoyo/contracts/protocol.py → src.judgment` 这条既存越界记为具名债务而非静默放行。 |
| `yoyo-trading` | `configs/source_repo.json` | `REJECT` | 跨仓只读指针，收敛后指向自己。已加测试禁止 `yoyo/` 再引用它。 |
| `yoyo-trading` | `yoyo_trading.egg-info/`、`uv.lock` | `REJECT` | 打包产物与另一个仓的锁文件。 |
| `yolo-xx` | `docs/asset_registry_v2.json`（157 条含 SHA-256） | `REFERENCE_ONLY` | 设计被本仓 `artifacts/registry.yaml` 吸收；157 条原始登记留在来源仓，归档后仍可查。 |
