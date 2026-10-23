# P0 Kronos 架构、证据与可迁移性审计（2026-09-01）

## 一页结论

**Kronos 值得借鉴，但现在不应接入训练、实盘或 ACTIVE。** 本轮最有价值的
迁移不是“再加一个价格预测器”，而是三件更基础、也更适合当前 P0/P1 阶段的东西：

1. 把连续 K 线压成**粗粒度 + 细粒度两级离散 token**，为未来的 L1.5 全局形态表征提供
   候选；
2. 把“源码、权重、采样器、随机种子、`eval()`、时间边界、评估器”合并成一个可拒绝漂移的
   **研究合同**；
3. 把外部模型隔离成**离线 sidecar**，只输出有 lineage 的研究特征，不让第三方依赖或随机
   生成路径进入四层生产代码。

已经安全落入本仓的不是新模型，而是上述边界与审计能力：

- `yoyo/contracts/kronos_research.py`：源码、模型、tokenizer、推理、holdout 与资格的 fail-closed
  合同；
- `configs/research/kronos_small_forecast_v1.yaml`：完整固定的研究配置；
- `scripts/audit_kronos_research_contract.py`：离线核对 Git/Hugging Face 身份、哈希、大小和依赖漂移；
- `scripts/audit_kronos_sampling_stability.py`：只用合成 K 线的单变量采样稳定性审计；
- `tests/contracts/test_kronos_research_contract.py`：10 条拒绝漂移测试；
- `docs/decisions/0002-keep-kronos-as-a-pinned-research-sidecar.md`：架构决策。

本轮**没有读取任何市场数据或 holdout，没有训练、调参、promote、部署或改 ACTIVE，也没有改
主环境依赖**。因此下面对 Kronos 的结论是架构/工程审计，不是收益验收。

---

## 1. 审计对象与源码身份

| 项目 | 本轮固定值 | 结论 |
|---|---:|---|
| 官方仓库 | `shiyu-coder/Kronos` | MIT，可用于研究与改造 |
| 上游 commit | `67b630e67f6a18c9e9be918d9b4337c960db1e9a` | 本地 `external/Kronos` 与官方当前 `master` 一致 |
| 上游最后提交 | 2026-04-13，修复批维训练 | 仍在维护，但无 release/tag，必须钉 commit |
| Kronos-small revision | `901c26c1332695a2a8f243eb2f37243a37bea320` | config 与 98,980,656-byte 权重哈希一致 |
| Tokenizer-base revision | `0e0117387f39004a9016484a186a908917e22426` | config 与 15,842,368-byte 权重哈希一致 |
| 身份审计 | 18/18 通过 | 本地源码、origin、clean 状态、MIT 与两组模型文件均逐项核对 |

上游源码以固定 commit 的
[README](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/README.md)、
[核心模型](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model/kronos.py)
和
[基础模块](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model/module.py)
为准；论文依据为[AAAI 2026 正式版](https://ojs.aaai.org/index.php/AAAI/article/view/39730)
与[扩展版](https://arxiv.org/abs/2508.02739)。模型文件按
[Kronos-small 固定 revision](https://huggingface.co/NeoQuasar/Kronos-small/tree/901c26c1332695a2a8f243eb2f37243a37bea320)
和
[Tokenizer-base 固定 revision](https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base/tree/0e0117387f39004a9016484a186a908917e22426)
核对，而不是依赖会漂移的仓库名。

截至 2026-09-01 的 GitHub API 快照显示约 3.83 万 stars、6382 forks；这说明社区关注度高，
**不构成交易有效性证据**。仓库没有正式 release/tag，模型卡也没有把代码、论文和权重锁成一个
统一版本，因此本仓必须自行固定四类身份。

---

## 2. Kronos 到底是什么

### 2.1 它不是传统“预测下一根收盘价”的网络

Kronos 把市场序列当成一种离散语言。公开 predictor 实际输入每根六维向量：

```text
x_t = [open, high, low, close, volume, amount]
```

若没有 `amount`，公开代码用 `volume × mean(open, high, low, close)` 派生。每个输入窗口按自身
六列均值/标准差做 z-score，再裁到 `[-5, 5]`。时间戳另外拆成 minute、hour、weekday、day、
month 五个离散日历字段。

整体数据流是：

```text
只含 decision bar 及以前的 OHLCVA
              │
              ▼
      窗口内 z-score + clip
              │
              ▼
  causal Transformer tokenizer encoder
              │
              ▼
  20-bit BSQ code / bar
     ┌────────┴────────┐
     ▼                 ▼
 coarse s1: 10 bits   fine s2: 10 bits
  (1024 类)            (1024 类)
     └────────┬────────┘
              ▼
  decoder-only causal Transformer + 日历 embedding
              │
     P(s1_next | past)
              │
     P(s2_next | past, s1_next)
              │
              ▼
       autoregressive token path
              │
              ▼
 tokenizer decoder → OHLCVA → 反标准化 → 多路径均值
```

概率分解是它最重要的结构思想：

```text
P(s1_t, s2_t | history, calendar)
= P(s1_t | history, calendar)
  × P(s2_t | s1_t, history, calendar)
```

粗 token 先描述较大尺度状态，细 token 再补充局部细节。论文消融中，顺序 coarse-to-fine
优于两个 token 并行预测；这比某个单一 benchmark 数字更值得我们参考，因为它提示“形态/状态”
与“局部微结构”可以分层表达。

### 2.2 Tokenizer：离散化不是附属预处理，而是第一阶段模型

Tokenizer 是一个 Transformer autoencoder：

- 输入 6 维，`d_model=256`、`ff_dim=512`、4 heads；公开配置写 4 个 encoder/decoder
  layers，但实现使用 `range(n_layers - 1)`，实际各执行 3 个 block；
- encoder 输出经 Binary Spherical Quantization（BSQ）成为 20 个二值维度；前 10 bit 和后
  10 bit 分别形成 coarse/fine token；
- decoder 同时从 coarse 部分和完整 code 重建输入，通过双重重构目标逼出层级；
- BSQ 不需要显式保存约百万行向量 codebook，bit 组合隐式定义空间，适合高基数离散状态。

一个需要记录的复现差异：正式论文描述的 BSQ group size 是 5，而当前公开
Tokenizer-base `config.json` 是 4。对推理应服从已发布权重的 config；对“完全重现论文训练”则
不能假装两者相同。

### 2.3 Predictor：小型 causal language model

本轮选择的 Kronos-small 是 24.7M 参数、最大 context 512：

- 8 层，`d_model=512`、8 heads、`ff_dim=1024`；
- RMSNorm、RoPE、SiLU-gated feed-forward；
- coarse 与 fine embedding 融合；
- minute/hour/weekday/day/month temporal embedding；
- 所有 self-attention 使用 `is_causal=True`；
- 每个未来 bar 先从 coarse logits 做 temperature/top-p multinomial sampling，再让 fine head
  依赖该 coarse token，最后解码整条路径；多条路径在连续 OHLCVA 空间取均值。

因此输出本质是**条件分布的随机样本均值**，不是固定回归值。`torch.multinomial` 没有公开 seed
参数；seed 必须在调用方设置。

### 2.4 训练规模与论文任务

论文报告约 121.1 亿条观测、96,569 个资产、45+ 交易所和 7 种频率；其中 Binance
现货/永续约 997 个资产、12.37 亿条观测，包含 1m/5m/15m/30m/1h/日/周。扩展版说明预训练
时间延伸到 2024-06，测试从 2024-07 开始。这个规模说明它有广泛的跨市场先验，但不能替代
本项目的 owner Gold 和净收益验收。

论文中的 15m forecasting 基准使用 lookback 160、horizon 32；投资实验主要是 A 股日频、
long-only top-k/drop-n，并按每次交易 0.15% 成本。它回答的是“广义 K 线预测/截面排序是否有
信息”，不是“在我们的 MA-launch 候选中，扣 0.2% 往返成本后 top-decile 是否为正且
`p<0.01`”。

---

## 3. 官方实现中必须绕开的坑

### 3.1 README 与论文的采样配置不一致

| 来源 | Temperature | top-p | 路径数 |
|---|---:|---:|---:|
| README / predictor 默认 | 1.0 | 0.9 | 1 |
| 论文 forecasting profile | 0.6 | 0.9 | 10 |
| 2026-07-30 本仓旧实验 | 1.0 | 0.9 | 1 |
| 新研究合同 | 0.6 | 0.9 | 10 |

配置不同就是不同 intervention。后续不能把 README 路径称为“复现论文配置”，也不能把两者的
结果并在同一版本号下。

### 3.2 `from_pretrained()` 后仍是 training mode

本轮实际加载固定权重后，`model.training == True` 且 `tokenizer.training == True`。公开
`KronosPredictor` 构造器只负责移动设备，不调用 `eval()`。上游 regression test 和 qlib test
会显式调用 `eval()`，README 示例没有。small 配置的 predictor dropout 非零，所以运行模式是
结果身份的一部分。

新合同强制显式 `model.eval()` 和 `tokenizer.eval()`；合成审计也记录切换前后状态。

### 3.3 两条 fine-tuning 路径的因果性不一致

上游
[Qlib dataset](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/finetune/dataset.py#L107-L117)
明确只用过去 lookback 计算均值/标准差，再把同一尺度应用到未来训练目标。这符合无前视。

但上游
[CSV fine-tune dataset](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/finetune_csv/finetune_base_model.py#L107-L127)
先取 `lookback + future` 完整窗口，再对整个窗口求均值/标准差。未来价格因此参与输入尺度，不能
进入本项目。即使以后获准训练，也只能自己实现 past-only adapter，不能复制该便捷路径。

### 3.4 依赖合同不能直接并入主环境

上游精确要求与本仓当前 `.venv` 有 6 处漂移：

| 包 | 本仓当前 | 上游固定 |
|---|---:|---:|
| einops | 0.8.2 | 0.8.1 |
| huggingface-hub | 1.8.0 | 0.33.1 |
| matplotlib | 3.9.4 | 3.9.3 |
| pandas | 2.3.3 | 2.2.2 |
| tqdm | 4.70.0 | 4.67.1 |
| safetensors | 0.7.0 | 0.6.2 |

本轮现有环境能完成推理，不代表它与上游训练环境等价。未来市场实验应使用独立 venv/subprocess
sidecar，并把版本写入 artifact receipt；禁止为了 Kronos 改主环境的跨机数值合同。

---

## 4. 本仓 2026-07-30 旧实验：原结果保留，证据边界重画

### 4.1 当时测了什么

旧流程对每个候选读取 signal bar 及之前 400 根 15m K 线，让 Kronos-small 以
`T=1.0, top_p=0.9, sample_count=1` 预测后 12 根，再汇总成 6 个路径特征。它在 pre-holdout
池生成 18,255 行，耗时 2.57 小时，约 0.5% 候选因失败/退化未进入 join。

历史结果不改写：

| 旧 arm | top-decile 相对池提升 | 正折 | ATR 对照 | 超对照 |
|---|---:|---:|---:|---:|
| BASE 28+19 | +23.49 bp | 14/15 | -19.24 bp | +42.73 bp |
| BASE + Kronos 6 | +35.61 bp | 14/15 | -11.33 bp | +46.94 bp |
| Kronos 6 only | -9.92 bp | 0/15 | +0.11 bp | -10.03 bp |

同折 `BASE + Kronos - BASE` 均值只有 +2.42 bp，`t=1.13`，8/15 折为正；历史脚本报告置换
`p=0.0333`，因此按当时预登记的 `p<0.01` 门槛判为未通过。**这个“未通过”继续成立。**

### 4.2 为什么不能再把它说成“Kronos 架构已被否定”

代码审计发现四个限制：

1. **测试的不是论文采样器**：旧 builder 固定 `T=1.0, n=1`，没有 seed，未显式
   `eval()`；它只代表一次 README 风格随机路径。
2. **置换预算不可能回答门槛**：plus-one p 值为 `(r+1)/(n+1)`。30 次置换的最好结果是
   `1/31=0.03226`，数学上不可能证明 `p<0.01`。
3. **零假设与真实统计量不同形**：每次置换只跑一折，而真实量取 15 折中位数；两边不是同一个
   statistic。脚本还用未校正的 `mean(null >= real)`，允许伪造 `p=0`。
4. **“匹配随机对照”名不副实**：实际只按 ATR bucket 抽样，没有按 symbol、time bucket、
   volatility bucket、horizon、cost 五轴匹配。horizon/cost 虽在该池固定，symbol 与时间 beta
   仍没有被对照隔离。

所以合规表述应是：

> 旧实验足以拒绝当时那套“单路径预测 → 六特征”的配置；其中 `Kronos-only 0/15` 是强烈的
> 负向线索。但其置换与对照不能满足当前验收纪律，也从未测试离散 token、重构残差或 causal
> hidden state，故不能外推成整个表征架构无效。

这不是事后救模型。任何未来新接口都必须当新变量重新预登记；旧负结果仍留在比较表中。

---

## 5. 本轮新证据：合成输入的采样稳定性

### 5.1 设计

为避免偷看市场和 holdout，本轮构造固定的 400 根合成 15m OHLCV，预测 12 根。每个 profile
使用 5 个固定 seed，共生成 15 条路径。三个 profile 是单变量梯子：

1. README：`T=1.0, n=1`；
2. 只改温度：`T=0.6, n=1`；
3. 只改路径数：`T=0.6, n=10`。

这里的 `end_return` 只是“合成输入最后收盘到生成路径最后收盘的比例”，不是收益、label、
alpha 或交易回报。

### 5.2 结果

| profile | seeds | 终点变化均值 | 跨 seed 标准差 | min | max | 平均耗时/条* | 唯一路径 | OHLC 非法项 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| README `T1,n1` | 5 | +0.79399% | 0.20014% | +0.59397% | +1.11691% | 4.40s | 5/5 | 0 |
| 只降温 `T0.6,n1` | 5 | +0.22191% | 0.30249% | -0.07863% | +0.66313% | 2.03s | 5/5 | 0 |
| 论文 `T0.6,n10` | 5 | +0.22429% | 0.05106% | +0.17081% | +0.29147% | 13.56s | 5/5 | 0 |

\* 固定顺序的单轮工程计时，第一 profile 含明显冷启动噪声，不是严格性能 benchmark。更可比的
`T0.6,n10` 相对 `T0.6,n1` 约 6.7 倍耗时。

论文 profile 的跨 seed 标准差是 README profile 的 `0.2551×`，即在这一个合成输入上降低约
74.5%。但每个 profile 仍有 5/5 不同路径哈希：十路径均值降低方差，没有把生成器变成确定函数。

**可得结论**：若未来仍测试 forecast 特征，不能再用无 seed 的单路径结果；论文 profile 更稳但
明显更慢。**不可得结论**：它会提升本项目 AUC、top-decile 净收益或实盘表现。

---

## 6. 对 fable-trading 的迁移矩阵

| Kronos 元素 | 现在怎么处理 | 价值 | 主要风险 |
|---|---|---|---|
| 两级 coarse/fine token | **参考并保留为未来 L1.5 候选** | 把全局状态与局部细节分层，和视觉局部框互补 | token 语义未经 owner Gold 验证 |
| causal Transformer hidden state | **未来优先测试** | 单次前向、无未来 bar、比生成 12 根路径省时 | 512 维易过拟合；降维必须 fold-contained |
| token transition / entropy / surprise | **未来优先测试** | 可形成紧凑、可解释的状态变化特征 | surprise 频率只能在训练折估计 |
| tokenizer reconstruction residual | **未来可测试** | 可能度量“异常/不熟悉形态” | 异常不等于可交易，需匹配对照 |
| 多路径未来预测 | **保留为低优先级研究臂** | 能表达分布与不确定性 | 慢、随机、旧实证弱、与目标错位 |
| 官方 CSV fine-tune | **拒绝** | 无 | future-dependent normalization |
| 官方 Qlib/A 股 backtest | **只参考工程结构** | 有 past-only normalization 先例 | 市场、频率、组合与成本都不匹配 |
| Web UI / 图形预测 | **不接入** | 演示价值 | 容易把视觉 plausible 当经济证据 |
| 直接进 L2/forward/ACTIVE | **禁止** | 无 | 违反 P0/P1、层间合同、新鲜度与 promote 纪律 |

### 为什么“表征优先于预测”

我们的问题是条件选择：L1 已给出一个 owner-defined 形态候选，L2 要判断它相对同币同时间同波动
背景是否具有扣成本后的增量价值。Kronos 原任务则是从广域市场历史生成未来路径。二者共享过去
K 线，但 loss、标签、成本和决策单位不同。

一遍 tokenizer/predictor encoder 就能提供“当前窗口像哪种市场语言状态”的信息，不必让随机
sampler 猜 12 根未来，再把整条生成路径压成 6 个手工统计量。这同时减少：

- 采样方差；
- 约 10 路 autoregressive 解码的延迟；
- “生成路径长得合理”被误当成“对我们的 rare-event 排序有用”的中间假设。

但表征也**尚未被证明有效**。它只是比重跑旧 forecast arm 更有信息增益的下一个实验方向。

---

## 7. P0/P1 之后才允许执行的实验设计

当前不执行。owner 若在 P0/P1 通过后批准，应先冻结事件集、目标、成本、时间切分、对照和接受
门槛，再按单变量顺序运行：

### 7.1 推荐路线：确定性 L1.5 representation

1. **R0 / BASE**：当前合法 L2 特征，不加 Kronos。
2. **R1 / coarse token only**：每个事件只看 decision bar 及以前；加入 coarse token 变化率、
   recent run length、训练折估计的 current-token surprise。
3. **R2 / + fine token**：只增加 fine token 对应统计。
4. **R3 / + reconstruction residual**：只增加 past-only 标准化空间的当前/近期残差。
5. **R4 / hidden state 替代上述统计**：作为独立路线，不与 R1-R3 打包；任何 PCA/投影只在每个
   训练折 fit。

每一步都保留 `Kronos-only` arm，防止 BASE 自己承担全部效果。输出单位是 event，一事件一行；
features artifact 必须写 input hash、source/model/tokenizer revision、context end、seed/eval、schema
和生成时间。L2 只经 `yoyo/data/` 读取 artifact，不 import Kronos。

### 7.2 若仍要复核 forecast arm

先复刻旧候选集与 6 特征定义，再逐次只改一个条件：

1. 显式 `eval()`；
2. 固定 seed；
3. `T: 1.0 → 0.6`；
4. `sample_count: 1 → 10`；
5. `lookback: 400 → 160`；
6. `pred_len: 12 → 32`。

第 5、6 步只是对齐论文 15m forecasting 设定，不代表更适合交易。每步必须写结果，无论成败；
不得只保留最后一个“论文风格”组合。

### 7.3 所有经济臂共同验收

- pre-holdout 时间 walk-forward；未经 owner 明确批准，不读 `>=2026-05-04`；
- 同一事件、同一 entry/outcome resolver、同一 0.2% 往返成本；
- `BASE`、`BASE+KR`、`KR-only` 同表；
- top-decile 毛/净收益、胜率、AUC 参考值、单特征基线；
- 同币 × 同时间块 × 同波动桶 × 同 horizon × 同 cost 的匹配随机入场；
- 真实和置换都重跑完全相同的跨折统计量，plus-one 10,000 次，要求 `p<0.01`；
- 一旦结果好得反常，先查时间、标准化、fold reducer、重复事件和预训练边界。

---

## 8. 数据统计与本轮结果对照

### 8.1 数据统计

| 项目 | 数量/范围 |
|---|---:|
| 本轮读取真实市场候选 | 0 |
| 本轮读取 holdout | 0 |
| 本轮训练/调参 | 0 |
| 合成输入 | 1 条 × 400 根 15m bar |
| 合成预测 horizon | 12 bars |
| profile / seeds / 生成路径 | 3 / 5 / 15 |
| 模型身份硬检查 | 18，全部通过 |
| 合同测试 | 10，全部通过 |
| 旧实验 pre-holdout join | 18,255 行，仅作历史引用，未重读/重跑 |

### 8.2 与上一轮 Kronos 工作同表对照

| 维度 | 2026-07-30 旧工作 | 2026-09-01 本轮 | 变化解释 |
|---|---|---|---|
| 目标 | forecast 6 特征的经济筛选 | 架构、身份、稳定性与接入边界 | 本轮非方向性，不复跑收益 |
| 源码/权重 | 名称，未固定 revision/hash | commit + 两组 revision + SHA/size | 消除上游漂移 |
| 模式/seed | 未显式 eval；无 seed | 两者强制；收据记录 | 消除隐式运行态 |
| sampler | README `T1,n1` | 合同为论文 `T0.6,n10` | 明确配置差异，不冒充同模型 |
| 置换 | 30 次，单折 null，naive p | 合同强制 10,000 次、同 statistic、plus-one | 能解析 `p<0.01` |
| 对照 | ATR bucket | 五轴合同 | 对齐现行 beta 防线 |
| 资格 | 报告写不 promote | 代码强制三项 eligibility=false | 由文字约束升级为可测试合同 |
| 结论 | 精确配置未通过 | 结论保留；表征路线尚未检验 | 不扩大负结果的外推范围 |

### 8.3 项目模板要求的指标说明

本轮是**非方向性架构/复现审计**，没有新市场样本或收益，因此 val AUC、top-decile 毛/净收益、
胜率、单特征经济基线与匹配随机入场均**不适用**，不能留空或编造。历史经济数字仅在 §4 原样
引用，不能当成本轮结果。

同等严格的零假设/负向对照是：

- 把固定 revision、hash、论文采样配置、`eval()`、五轴对照、holdout 或资格任一项突变，合同
  必须拒绝；10 条测试全部通过；
- 对 18 个本地身份项逐字节核对，任何一个错即失败；本轮 18/18 通过；
- 用 5 个不同 seed 验证生成器确实随机，而不是把一次路径误当确定值；三个 profile 均 5/5
  唯一路径；
- 验证 15 条合成预测的有限值、正价格和 high/low 包络；非法项为 0。

---

## 9. 风险与诚实声明

1. **本轮没有 alpha 证据。** 74.5% 的方差下降只发生在一个合成输入上，不能外推到真实候选、
   经济收益或跨机器 bitwise parity。
2. **预训练大不等于适合本项目。** 12B 级语料能带来广域先验，也可能偏向高频常见状态；我们的
   rare MA-launch 语义仍由 owner Gold 定义。
3. **论文指标不可移植。** forecasting 指标、RankIC 和 A 股组合回测都不是本项目成功标准；仍需
   top-decile 扣成本增量与匹配 beta 对照。
4. **公开实现并非完整可复现包。** 无 release、README/论文 sampler 不同、BSQ group size 不同，
   所以必须以 pinned source + released config/weights + 本仓 receipt 为证据单位。
5. **延迟不适合当前 pulse。** 本机合成测试 `T0.6,n10` 约 13.56s/事件；即使 batch 后更快，若
   放进全宇宙实时扫描也很容易破坏 `<15min` 脉冲预算。本 ADR 不授权该接法。
6. **旧负结果仍重要。** 证据边界收紧不等于给模型第二次机会；若 future representation arm 也
   不能在严格对照下提供增量，应继续停止投入。
7. **holdout 未消费。** 本轮读取 0 行；没有“看一眼”。
8. **没有登记成 active experiment。** 本轮是源码/架构审计而非市场实验；为避免把无数据审计
   冒充训练实验，没有修改 `experiments/registry.yaml` 或 `artifacts/registry.yaml`。

---

## 10. Owner 下一步选项

| 选项 | 建议 | 现在会发生什么 |
|---|---|---|
| A. 接受本 ADR，保持 research-only | **推荐** | 保留合同、审计脚本和架构路线；P0/P1 继续，不训练 |
| B. P0/P1 后先测 deterministic representation | 推荐的下一实验 | 届时另做 owner 批准、预登记、独立 venv 和单变量实验 |
| C. P0/P1 后复核旧 forecast arm | 次选 | 按 §7.2 逐项改，成本高且旧实证弱 |
| D. 现在接入 forward/ACTIVE 或 fine-tune | 不建议且当前禁止 | 与阶段、层间、新鲜度、训练/promote 纪律冲突 |

本轮默认执行 A；B/C 只写设计，不偷偷启动。

---

## 11. 完整复现命令

以下命令从本仓根目录运行。前两步只下载固定公共文件；若本机缓存已经存在会复用。它们不读取
项目市场数据。

```bash
cd /Users/zhangzc/fable-trading

.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    "NeoQuasar/Kronos-small",
    revision="901c26c1332695a2a8f243eb2f37243a37bea320",
    allow_patterns=["config.json", "model.safetensors"],
)
snapshot_download(
    "NeoQuasar/Kronos-Tokenizer-base",
    revision="0e0117387f39004a9016484a186a908917e22426",
    allow_patterns=["config.json", "model.safetensors"],
)
PY

git -C external/Kronos rev-parse HEAD
git -C external/Kronos remote get-url origin
git -C external/Kronos status --porcelain

PYTHONPATH=. .venv/bin/python scripts/audit_kronos_research_contract.py
PYTHONPATH=. .venv/bin/python scripts/audit_kronos_sampling_stability.py

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/contracts/test_kronos_research_contract.py

python3 scripts/md_to_html.py \
  analysis/p0_kronos_architecture_transfer_audit_20260901.md \
  --out-dir analysis/html
```

核心收据：

- `analysis/output/kronos_architecture_transfer_20260901/contract_audit.json`
- `analysis/output/kronos_architecture_transfer_20260901/sampling_stability.json`

本报告源文件用于 Git 历史与复现；交付 owner 的浏览版本为
`analysis/html/p0_kronos_architecture_transfer_audit_20260901.html`。

---

## 12. 验收结果

| 检查 | 结果 |
|---|---:|
| Python compile（合同、两个审计脚本、合同测试） | 通过 |
| Kronos 合同专项 | 10 passed |
| `tests/boundaries + tests/causality + Kronos contract` | 277 passed、2 skipped |
| 本仓完整 `tests/` | 1903 passed、4 skipped、3 failed |
| Markdown → HTML | 通过，22 KB |

完整测试的 3 个失败都不在本轮文件：

- 两个 registry conclusion 测试与当前已修改的 `experiments/registry.yaml` 中 holdout consumer
  集合不同步；该 registry 是进入本轮前已有的其他工作区修改，本轮没有覆盖它；
- `tests/test_binance_um_archives.py` 要求 `non_15m_gaps`，而当前已提交实现
  `yoyo/data/binance_um_archives.py` 返回通用键 `non_bar_gaps`；两文件均无本轮 diff。

没有为了让全仓显示绿色而修改这三处无关状态。与本轮新增合同直接相关的 compile、合同、边界和
因果检查全部通过。
