# 小周期 YOLO → 冻结 L2 因果桥准备报告 — 2026-08-04

## 一句话结论

可以把 1m/2m/3m/5m 完整窗口 YOLO 的候选送入历史 v11 LightGBM，再把通过候选路由到下一 15m/30m 边界做**研究测试**；但旧 PF6.61 不能继承。旧结果来自 YOLO + 回归判断层整链，而且 full-window 框时间曾被回填成更早的信号时间。新桥只认整个小周期窗口闭合后的 `available_at`，明确把历史 v11 标成 `long / legacy_unaligned / paper_only / execution_eligible=false`。

本轮只完成代码、安全门、合成测试和真实模型机械烟测。没有安全的小周期 pre-holdout 快照，所以没有训练、候选池、收益或 PF 结论。

## 历史事实复核

| 版本/链路 | 当时记录 | 后续核验 | 当前裁决 |
|---|---:|---:|---|
| v11 YOLO + v11 regression | accept 703 笔，PF 6.61，胜率 77.1% | full-window 前视被堵后同链 PF 1.13、胜率 25.5% | PF6.61 作历史故障证据，不作先验 |
| v16 causal tip YOLO | 纯检测 PF 0.78 | + v11 L2 PF 0.60；top5% PF 0.48 | 实时化旧链已被证伪 |
| ETH 3m pilot | val mAP 可观 | OOS 开火率 99.74%，相对匹配随机 -0.80bp | 拒绝 |

原 `owner_v11_chain.pt` 已无法在当前项目、迁移目录及 Git 历史找到，因此不能真实复现当时 detector。当前可用的 `eth3m_short_pilot_v1_mac_cold.pt` 仅用于验证推理机械路径，不代表“找回 v11”。

## 冻结 L2 明确身份

- 配置：`tp5_sl2_swap_yolo_v11_reg`
- 模型：`models/frozen_tp5_sl2_swap_yolo_v11_reg_20260718.txt`
- 模型 SHA-256：`4d617a08cd7311cc5d6156d0053e7b85ff2aa1658076b96424ebb736c4c25a41`
- metadata SHA-256：`5ef0bf837a292ee0302077be390f98ee10484d990bc46b12e453c3617c244cb2`
- objective：`regression`
- side：`long`
- feature schema：`judgment_28_v1`，28 项
- feature semantics：metadata 无字段，按既有冻结加载契约诚实解析为 `legacy_unaligned`
- best iteration：61
- threshold：`0.02022141847381547`
- score semantics：`predicted_realized_ret`
- threshold operator：`>=`

桥本身不负责选择或加载 metadata；调用者必须显式传入已经构造的 `FrozenArtifact`。本轮真实烟测由外部脚本用既有 `load_artifact(yolo_v11_pool_config(...), exact_metadata_path)` 构造 v11 artifact，再由桥按 artifact 的精确模型路径加载并核对模型 SHA。桥不读取 `models/ACTIVE`，不使用 default/latest fallback，也不读取 artifact 的历史 dataset。metadata 文件 SHA 是报告侧核验事实，当前桥不会再次验证或写入评分输出；因此 artifact 构造仍是必须受控的上游边界。

## 因果时间契约

```text
小周期完整窗口闭合
        │ available_at（唯一事实时间）
        ▼
schema-v2 detection candidate
        │ 只取 open_time+15m <= available_at 的连续 15m bars
        ▼
最新已闭合 15m 特征行 + 冻结 v11 L2
        │
        ├─ next_15m_open：严格晚于 available_at
        └─ next_30m_open：严格晚于 available_at
```

`box_start_time/box_end_time/xywhn` 只描述图中对象，不能改变 `available_at`。例如 3m 候选在 `14:57Z` 才可用时，L2 最晚只能使用 `14:30–14:45Z` 的 15m bar，下一 15m/30m 路由边界均为 `15:00Z`。

30m 只是路由边界；v11 是 15m 特征模型，不能把输入直接换成 30m bar。

## 跨项目边界

- `yolo-xx` 只生成/审计 schema-v2 detection manifest；不包含 LightGBM、收益、回测或交易逻辑。
- `yoyo-trading/yoyo/layers/l2_judgment/candidate_bridge.py` 只做候选适配与冻结 L2 研究评分；不发现 ACTIVE、不加载行情、不训练、不导入执行层。
- 数据、模型、报告和 learnings 留在 `fable-trading`。

候选 ID 绑定 detector weights、dataset manifest、source、image 及 detection 内容的 SHA-256。15m bars 必须携带 source/symbol/timeframe/snapshot SHA；币种或来源不一致立即拒绝。评分输出明确 `research_only=true`、`paper_only=true`、`execution_eligible=false`。

## 真实冻结模型烟测

本轮使用真实 v11 metadata/LightGBM 文件和纯合成、连续的 15m OHLCV；没有读取任何历史行情或 artifact dataset。模型由 `load_verified_booster(explicit_model_path)` 在同一次操作中执行加载前 stat/hash、明确路径加载和加载后 stat/hash；裸 fake、同字节不同路径、加载中漂移及 artifact 路径/hash 不一致均被独立复核拒绝。真实仓输出：

- `model_config_name=tp5_sl2_swap_yolo_v11_reg`
- `model_side=long`
- `feature_semantics=legacy_unaligned`
- `feature_bar_open_time=2026-04-27T14:30:00Z`
- `feature_bar_closed_at=2026-04-27T14:45:00Z`
- `next_15m_open=next_30m_open=2026-04-27T15:00:00Z`
- `threshold=0.02022141847381547`
- `score=0.011242893075204857`，`passed=false`
- `execution_eligible=false`

合成 bars 的 score 只证明真实模型可被显式加载并按因果时间打分，不能解释为模型效果。

真实 YOLO 权重烟测与真实 v11 L2 烟测是两条独立的机械验证：前者沿用了旧 pilot 数据集，其 `predictions.json` 为 schema v1，不含 dataset/source/availability 的 schema-v2 身份链；后者使用手工构造的 candidate mapping 和合成 bars。因此本轮**没有**完成“真实权重 → schema-v2 → L2”的端到端验收，不能把两个 smoke 拼成一条已验证实链。

## 测试矩阵

合并后需记录以下最终计数：

| 测试 | 目的 | 状态 |
|---|---|---|
| yolo-xx full pytest | 数据/manifest/predict/train gate | **38 passed** |
| yoyo bridge + layer boundary | 时间、身份、层间契约 | **74 passed** |
| yoyo full pytest | 回归保护 | **164 passed** |
| real YOLO weight smoke | 真实 Ultralytics result/path/原始框 | **passed；1 detection** |
| real frozen v11 smoke | verified loader、因果特征、研究输出 | **passed；机械范围独立验收 accepted** |

## 复现命令

本机统一使用 `fable-trading/.venv` 中已经锁定的依赖；命令不读取行情、holdout 或 artifact dataset，也不会训练：

```bash
cd /Users/zhangzc/yolo-xx
PYTHONDONTWRITEBYTECODE=1 /Users/zhangzc/fable-trading/.venv/bin/python \
  -m pytest -q -p no:cacheprovider

cd /Users/zhangzc/yoyo-trading
PYTHONDONTWRITEBYTECODE=1 /Users/zhangzc/fable-trading/.venv/bin/python \
  -m pytest -q -p no:cacheprovider \
  tests/test_candidate_bridge.py tests/test_layer_boundaries.py
PYTHONDONTWRITEBYTECODE=1 /Users/zhangzc/fable-trading/.venv/bin/python \
  -m pytest -q -p no:cacheprovider

cd /Users/zhangzc/fable-trading
.venv/bin/python scripts/md_to_html.py \
  analysis/p_mtf_yolo_l2_bridge_prep_20260804.md --out-dir analysis/html
```

真实 YOLO 权重机械烟测的完整命令保存在 `yolo-xx/reports/multitimeframe_detector_prep_20260804.md`。真实 v11 L2 烟测所用输入为测试内生成的合成连续 15m OHLCV；上述 yoyo 测试覆盖相同因果时间、verified-loader、模型身份及禁止执行契约。报告中的真实模型数值是本轮额外机械运行证据，不是经济指标。

机器可读独立复核 receipt：`analysis/output/p_mtf_yolo_l2_bridge_independent_acceptance_20260804.json`。其中 `accepted` 只覆盖代码机械契约，不覆盖真实权重端到端链、数据可用性、模型效果或全主机过程审计。

## 实现提交

- `yolo-xx`：`97ad14ee46000cf0cba03785e1316b3ef91d3865`（多周期数据集、不可变 source manifest、强审计与推理身份链）
- `yoyo-trading`：`c6393a013562d057c742645f7151e2c15ba722ff`（schema-v2 候选到冻结 v11 L2 的因果桥）

报告提交因内容寻址无法在自身正文内记录自身 hash；最终交付时另行列出。以上两个实现提交均在 `main`，均未 push。

## 指标与对照

本轮是准备与安全审计，不是模型实验，因此以下项目均为 N/A：新候选数、正类率、val AUC/mAP、置换检验、top-decile 毛/净收益、胜率、PF、匹配随机对照。旧 PF 与 pilot 数字只用于历史故障复核，不是本配置结果。

首个经济实验必须在安全的 5m pre-holdout snapshot 上进行，并带同币 × 同时间块 × 同波动桶匹配随机入场对照；之后才按 3m→2m→1m 单变量推进。

## 风险与诚实声明

- 旧 v11 L2 在事后候选时点训练；改成真实 `available_at` 后存在必然的分布迁移。能跑不等于有效。
- v11 模型方向是 `long`，当前 yoyo 主线是 `short`；本桥输出不得被当作 short 信号。
- 完整窗口滚动时，同一历史框可能跨窗口重复出现；正式扫描前需要 first-seen/dedupe。
- 当前单候选计算会重复算特征与模型 hash；大规模运行前需要按 symbol 批量化，但这不是小样本机械验收阻断。
- bars snapshot SHA 由可信数据提供者声明；真实实验仍依赖不可变数据流程。
- metadata SHA 当前由报告/调用侧核验，bridge 只绑定 artifact 中的模型路径与模型 SHA；正式批量入口必须继续使用受控 artifact factory。
- 按本轮执行记录：未读 holdout、未训练、未调阈值、未创建/修改 active bundle、未改 ACTIVE、未部署、未下单、未 push。可见工作区没有新增权重/run/ACTIVE 变更作为旁证，但这不是对整台主机历史行为的完备审计。

## 下一步与停止点

1. 外部准备物理 pre-holdout 5m snapshot + manifest；禁止读取现有混合 CSV 后截断。
2. fixture → 5m 小样本 dry build/audit → full build，单周期单模型，自动 dense-rule labels，不要求人工盘口打标。
3. 先做 5m detector 的稀疏性、位置泛化及匹配随机增量；不过门即停止。
4. 通过后才把 schema-v2 候选送入本桥，重新做 pre-holdout 的 15m/30m 路由比较。

在安全快照到位前，本轮到代码与机械验收即停止，不制造收益数字。
