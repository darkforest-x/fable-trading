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

## C3 — 数值基线、匹配对照、因果测试与经济门

| 来源 | 资产 | 裁决 | 理由 |
|---|---|---|---|
| `yoyo-eth` | `src/yoyo_eth/`（12 个 .py） | `DIRECT_PORT` | 因果指标、松压缩扫描器（三种 trigger）、27 个因果特征、MFE/MAE short_utility 标签、anchored walk-forward、matched random control。字节一致：MVP/P02/P03 三份报告的数字就是这段代码跑出来的，改一个字符那些报告的复现命令就不再复现。 |
| `yoyo-eth` | `configs/mvp.yaml` | `DIRECT_PORT` | 被迁代码读的全部旋钮，含 owner 决策的成本值与冻结的 `data_end_boundary`。 |
| `darkforest-one` | `src/darkforest_one/data/validator.py` | `ADAPT_AND_PORT` | → `yoyo/data/continuity.py`。改为对本仓 loader 返回的 pandas frame 工作（任务书 C3.1 单一数据接口），3.11 → 3.9。**一处行为差异是刻意的**：缺口改为登记而非抛错——darkforest-one 只吃一个币可以整条拒绝，本仓扫 200+ 个上市时间不同的币，抛错会让最新上市的币掀翻整轮扫描；需要拒绝的 builder 用 `assert_continuous()`。 |
| `darkforest-one` | `src/darkforest_one/governance/manifest.py` | `ADAPT_AND_PORT` | → `yoyo/artifacts/lineage.py`。3.9 化 + 对齐本仓注册表词汇，并加两条本仓自己付过学费的规则：`assert_builder_landed_first()`（产物早于 builder 入库 = 复现声明未经验证）与**逐轴**可复现性（w20_midbox 2635/2635 图字节一致但 405 个 split 落点全错，一个 bool 装不下这两件同时为真的事）。 |
| `darkforest-one` | `governance/config.py`（pydantic + `typing.Self`） | `REFERENCE_ONLY` | fail-closed 配置的设计值得学，但实现绑死 pydantic 与 Python 3.11；本仓 venv 是 3.9 且 CLAUDE.md 禁止新增重型依赖。fail-closed 的**语义**已由 `yoyo/contracts/{artifacts,pattern,holdout}.py` 用 stdlib 实现。 |
| `darkforest-one` | `src/darkforest_one/{cli,candidate,strategy,execution}/` | `REJECT` | 第二套完整 package 根、第二个 CLI、第二份 ETH 数据、未接入实际系统的 paper 外壳。任务书 §8.1 明列禁止。 |
| 本仓新建 | `yoyo/contracts/holdout.py` | 新增（非迁移） | 收敛后 holdout 边界在本仓有 **11 处定义、6 个不同名字**（`HOLDOUT_START` / `HOLDOUT_CUTOFF` / `HOLD_DEFAULT` / `ACCEPT_START` / `holdout_start_exclusive` / `data_end_boundary`）。今天全部一致，但没有任何东西让它们一致。本模块是唯一定义；**不改动其余 11 处**（那是在动运行中的代码），改由 `tests/causality/test_holdout_boundary_is_single_valued.py` 逐个读出来比对，任何一处漂移当场红。 |
| 本仓新建 | `yoyo/evaluation/{walk_forward,matched_controls,permutation,economic_gates}.py` | 新增（非迁移） | 四个仓在用四套写法回答同样四个问题。合并为一套：purge/embargo 切分（yoyo-eth 的 fold 布局 + darkforest-one 的 purge 设计 + **新增的事后泄漏断言**，两个来源仓都只依赖切分函数写对、没有事后校验）；匹配对照（采用 darkforest-one 的 sha256 确定性选择，优于 yoyo-eth 依赖循环顺序的 rng）；置换检验（p 值用 (r+1)/(n+1)，永不返回 0）；三重经济门（净收益 > 0、p < 0.01、跑赢匹配对照，缺一不可，且对照组是**必填参数**——设成可选就等于给"忘了带对照"留了个看起来正常的短签名）。 |

## C4 — Pattern Teacher 与 proposal 语义隔离

| 来源 | 资产 | 裁决 | 理由 |
|---|---|---|---|
| `fable-trading`（本仓已有） | `models/owner_v10_chain.pt` 等 3 个权重 | `REFERENCE_ONLY`（登记不复制） | 物理文件已在本仓，登记同一 artifact_id + SHA-256，不创建第二份。v10_chain 的摘要与 yolo-xx `reports/pattern_teacher_asset_inventory.md` 独立记录的一致，且该清单已与 3060 上的 `best.pt`、`base_hts.pt` 三方核对——三个文件一个摘要。 |
| `yolo-xx` | 3060 上的 59 个检测权重（`C:\fable`） | `REFERENCE_ONLY` | 任务书 §8.3 禁止把权重副本复制进来。登记为 `storage_uri: host://windows-3060/...`，并让"权重已被清除"不能再当作前提（见 `docs/learnings/purge-records-are-claims-not-facts.md`）。 |
| `yolo-xx` | `src/yolo_xx/{predict,scan_predict,scan_set,train}.py` | `REJECT` | 已被替代的 bbox-only 默认 CLI；本仓 `yoyo/layers/l1_detection/candidates.py` 是主线检测路径，任务书 §8.3 明列禁止迁入。 |
| `yolo-xx` | `src/yolo_xx/outcome.py` 及收益/判断层支线 | `REJECT` | 旧 outcome / 交易判断支线。yolo-xx 自己的 README 已声明这些是历史资产，不被新 core import、不作默认 CLI、不决定验收结论。 |
| 本仓新建 | `yoyo/contracts/candidates.py` | 新增（非迁移） | CandidateProposal 合同。核心是把 `available_at` 定义为**生成器最后一根输入 K 线的收盘时间**，不是它画的框的右边界——两者之差恰好等于生成器看到的未来，混同就是"事后检测冒充新鲜信号"（铁律 12）。`production_eligible=True` 与 `training_eligible=True` 在构造函数里直接抛错，不是靠人记得。 |
| 本仓新建 | `yoyo/layers/l1_detection/teacher/` | 新增（非迁移） | PatternTeacher Protocol + 注册闸门。teacher 只能通过 `artifacts/registry.yaml` 里登记过的 artifact 使用，且加载时校验磁盘文件仍然哈希得上——"这批候选是哪个模型产的"永远答得出来，被人偷换过的权重在加载时就被抓住，而不是在报告里被发现。 |

## C5 — 兼容壳与语义去重

| 对象 | 裁决 | 理由 |
|---|---|---|
| `scripts/` 里 35 个 `sys.path.insert(0, ~/yoyo-trading)` 跨仓桥 | **删除** | 不是整洁问题。`for p in (PROJECT, _YOYO): sys.path.insert(0, ...)` 先插 PROJECT 再插 _YOYO，**结果 yoyo-trading 排第一**——这 35 个脚本一直在 import 另一个仓的 `yoyo`，而测试验的是本仓那份。两边一样时无害；yoyo-trading 一冻结、本仓继续走，就静默分叉，`render.py` 的像素也在其中。全部删除后 35 个脚本 compileall 通过，`tests/boundaries/test_no_cross_repository_bridges.py` 防止长回来。 |
| `src/` 下 23 个转发壳 | **保留** | 数百个调用点仍在用；本轮不改写。新增 `tests/boundaries/test_legacy_shims_forward.py`：逐个 import，并用 `is` 断言转发出来的是**同一个对象**——壳偷偷长出自己那份拷贝，正是"一个语义一个实现"在没人改调用点的情况下失效的方式。 |
| 文件 SHA-256 的 7 个实现 | **暂留 + 钉住** | 3 MB 随机文件实测 7 个摘要完全一致。合并要动 7 处调用点（含 `protocol.py`——`yoyo` 搬出本仓时特意内联了它，为的就是不向 judgment 层借），属独立一轮工作。 |
| SMA / EMA 的 3 个实现 | **保留** | 实测六条均线最大绝对差 **0.000e+00**，唯一差异是列名下划线。合并意味着在检测路径上改列名，而那条路径的像素不许动一位。 |
| ATR 的 2 个实现 | **保留 + 量化 + 上报** | **不一致**：warmup 播种不同，bar 14 差 0.109，200 根后耗尽。ATR 定义 TP/SL 障碍距离，且差异方向取决于取数起点——ATR 变成了「bar + 从哪开始读」的属性。已钉住，**需 owner 在三个选项中裁决**。见 `docs/consolidation/DUPLICATE_SEMANTICS.md` §4 与 `docs/learnings/two-atrs-agreeing-late-still-disagree-where-it-matters.md`。 |
| 成本常量 177 处引用 | **不动** | CLAUDE.md 明文：`scripts/` 下支撑已发布报告的实验脚本故意保留内联副本。这不是债，是记录。 |
