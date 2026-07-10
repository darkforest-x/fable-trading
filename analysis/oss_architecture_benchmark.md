# 开源架构基准与隔离试点

**日期**：2026-07-10
**范围**：交易执行/回测、数据与模型可复现、视觉数据质量、轻量编排。
**约束**：不读取 judgment holdout，不替换 ACTIVE，不安装重型框架，不改阈值、成本或 TP/SL。

## 结论先行

当前不应把 fable 迁移到另一套交易或 MLOps 框架。最有价值的路径是借鉴成熟项目的
局部契约：数据内容寻址、确定性事件、前视检查、人工标注闭环。首个试点采用 DVC 的
内容寻址思路，但不安装 DVC：冻结模型前把训练 CSV 复制到 SHA-256 命名的不可变路径，
元数据只引用该快照。

## 官方项目基准

以下版本为 2026-07-10 读取官方 GitHub 仓库时的 HEAD；许可证来自 GitHub 官方仓库元数据。

| 项目 | HEAD / 许可证 | 可借鉴能力 | fable 决策 |
|---|---|---|---|
| [Freqtrade](https://github.com/freqtrade/freqtrade) | `5549c189` / GPL-3.0 | 加密货币 dry-run、回测、lookahead/recursive analysis、WebUI | **适配模式，不引入**：未来模拟盘交叉验证可复用前视检查思路；GPL 与完整交易栈替换成本过高 |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | `321b5341` / LGPL-3.0 | 确定性事件驱动、回测与实盘同构、Rust 核心 | **后续参考**：P3 demo 执行器借鉴事件/订单状态机，不替换当前研究栈 |
| [LEAN](https://github.com/QuantConnect/Lean) | `b9f616b4` / Apache-2.0 | 完整算法交易引擎、经纪商适配、组合与执行模型 | **拒绝当前迁移**：能力完整但 C#/Python 双栈和部署体量远超当前需求 |
| [vectorbt](https://github.com/polakowo/vectorbt) | `bf7aff6d` / GitHub 元数据 `NOASSERTION` | 大规模向量化参数扫描 | **拒绝当前接入**：本项目的主要风险是 val 过度选择，扩大扫描反而恶化证据质量 |
| [Qlib](https://github.com/microsoft/qlib) | `d5379c52` / MIT | 量化研究、数据/模型工作流、ML 实验 | **拒绝当前迁移**：股票横截面研究取向与单一形态事件主线不匹配 |
| [DVC](https://github.com/iterative/dvc) | `f74c1c0e` / Apache-2.0 | 数据/模型版本、内容指纹、可复现流水线 | **立即适配模式**：实现无依赖的内容寻址训练数据快照 |
| [MLflow](https://github.com/mlflow/mlflow) | `3134c00f` / Apache-2.0 | 实验跟踪、模型注册、评估数据集版本 | **暂缓**：现有 JSON 实验注册表足够，当前不值得增加服务和数据库 |
| [Label Studio](https://github.com/HumanSignal/label-studio) | `63fdee07` / Apache-2.0 | 人工标注、预标注、导出 | **已采用**：VPS 人工改框入口 |
| [FiftyOne](https://github.com/voxel51/fiftyone) | `c44a1917` / Apache-2.0 | 难例筛选、模型/标注质量审计 | **已采用**：只在本地做 hard-case triage |
| [CVAT](https://github.com/cvat-ai/cvat) | `6db56e5d` / MIT | 团队标注、QA、自动标注 | **暂不迁移**：80 图单人审查下运维成本高于 Label Studio |
| [SAHI](https://github.com/obss/sahi) | `4daec27e` / MIT | 切片推理和小目标误差分析 | **固定实验**：只在 E2.1b 后执行预注册参数基准，不调参 |
| [Prefect](https://github.com/PrefectHQ/prefect) | `b75c0606` / Apache-2.0 | 重试、缓存、调度、可观察工作流 | **暂不引入**：systemd/定时任务和白名单 runner 尚未复杂到需要控制平面 |

## 排名

1. **DVC 内容寻址模式**：直接修复冻结数据集被覆盖后无法复现的问题，零新增依赖。
2. **Freqtrade 前视/递归检查模式**：在模拟盘前增加独立交叉检查，比替换回测器更有价值。
3. **NautilusTrader 确定性订单状态机**：等前向终审通过后用于 P3 demo 执行设计。

SAHI 属于检测实验而不是架构迁移，按独立任务执行。

## 隔离试点：内容寻址训练数据快照

### 基线问题

旧冻结工件只记录源 CSV 的哈希；源路径后来被重写，导致元数据正确报警但原训练字节已经
无法恢复。MA206 当前文件哈希一致，但未来重新 freeze 仍可能重复该问题。

### 实现

- `snapshot_dataset()` 将源数据复制到 `data/frozen_datasets/<sha256>.csv`；
- 同一内容复用同一路径，不同内容生成不同路径，旧字节不被覆盖；
- `train_frozen_artifact()` 只从快照训练，并在 artifact v2 元数据记录快照路径、源路径和哈希；
- 不改现有 ACTIVE，也不重新训练模型。

### 验证

失败基线：新增测试在导入 `snapshot_dataset` 时失败。实现后：

```text
23 passed
```

测试证明同一内容幂等、源文件修改后生成新快照、旧快照内容保持不变；现有 frozen model
与 model-hub 指纹测试同时通过。

## 风险与诚实声明

- 这是数据可复现试点，不提高策略收益或 YOLO mAP；
- 未安装、运行或性能比较上述完整框架，结论是基于官方能力、许可证和当前 fable 缺口的架构适配判断；
- 内容寻址快照仍依赖本机 `data/` 保存；远端备份属于后续数据治理，不在本试点范围；
- 不用 vectorbt/Hyperopt 扩大扫描，是为了减少已经严重的验证集选择偏差。
