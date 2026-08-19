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

## C2 — Gold 标注与 Causal Onset

| 来源 | 资产 | 裁决 | 理由 |
|---|---|---|---|
| `yolo-xx` | `src/yolo_xx/pis/{events,common}`（8 个 .py） | `DIRECT_PORT` | Causal Onset v3 schema / validator / no-auto-onset 迁移 / progressive-reveal review pack。保持 `events/` 与 `common/` 并列结构，相对 import 不变，字节一致；只改顶层目录名 `pis` → `onset`。 |
| `yolo-xx` | `src/yolo_xx/pis/events/render_frames.py` | `ADAPT_AND_PORT` | 删掉 `fable_root`/`yoyo_root` 的 sys.path 跨仓桥，直接 import `yoyo.data.loader` 与 `yoyo.layers.l1_detection.{data,render}`。桥的存在理由（渲染器在另一个仓）已被收敛消除。`ohlcv_root` 仍是显式参数——库代码不许自己猜数据在哪。 |
| `yolo-xx` | `tests/pis/events/*`（3 个） | `ADAPT_AND_PORT` | 只改 import 前缀；断言（含 render-time blinding 全部检查）一字未动。 |
| `yoyo-trading` | `configs/labelstudio_gold_v1.xml`、`configs/gold_annotation_v1.json` | `DIRECT_PORT` | 标注界面与协议是 owner 屏幕与 gold schema 之间的合同，必须和解析其导出的代码放在一起。 |
| `yoyo-trading` | `tools/{build_labelstudio_tasks,convert_labelstudio_export,audit_gold_dataset,audit_legacy_*}.py` + 2 个 HTML | `DIRECT_PORT` | 已经 import `yoyo.datasets.*`，收敛后本地可解析，无需适配。迁入 `tools/review/`。 |
| `yoyo-trading` | `manifests/*.json`（8 份，均 < 2 MiB） | `DIRECT_PORT` | 是 `experiments/registry.yaml` 引用的 gold core 身份文件；没有它们注册表行指向空。 |
| `yoyo-trading` | `manifests/legacy_label_migration_v3.jsonl`（2.4 MiB） | `REFERENCE_ONLY` | 超过 2 MiB 阈值。2.6 KB 的 summary JSON 承载结论并已入库；来源仓只读归档不删除，逐行数据按 commit + SHA 仍可取回。 |
| `yoyo-trading` | `tests/test_gold_annotation.py` | `ADAPT_AND_PORT` | 一行 import 跟随 `tools/review/` 迁移，其余不动。 |
| `yoyo-trading` | `tests/test_{local_v2_render,window_render,position_spread,legacy_audit}.py` | `DIRECT_PORT` | 覆盖随 `yoyo` 包一起迁回的 datasets/render 模块。 |
| 本仓新建 | `yoyo/contracts/pattern.py` | 新增（非迁移） | 任务书 C2.1 要求的 canonical PatternEvent。**刻意不是第三套 schema**：gold row 与 onset v3 record 各自保留，本模块只做跨层时间语义 + 五条规则 + 两个适配器，让两者收敛到一处而不是分叉成三处。 |
