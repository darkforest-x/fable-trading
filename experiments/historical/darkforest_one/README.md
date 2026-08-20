# darkforest-one — 极简因果引擎（已归档）

| 项 | 值 |
|---|---|
| 来源仓 | `darkforest-x/darkforest-one` |
| 冻结 commit | `fd36dd1adc5844f241122c3853eb4d3e675a9c11` |
| 最终状态 | `superseded` |
| 机器摘要 | [`summary.json`](summary.json) |
| holdout | **0 次消耗** |

## ⚠️ 对任务书的事实更正

收敛任务书 §8.1 把以下四条列为"必须保留的历史结论"：

```
P2 经济门失败
accepted candidate 的 OOS 净收益为负
accepted candidate 输给 matched controls
P3 被阻塞
```

**这四条在本仓库里都没有发生。** 在冻结 commit `fd36dd1` 上核验
（`main` 与两个 remote agent 分支全查）：

- [`ROADMAP_at_freeze.md`](ROADMAP_at_freeze.md) 的 P2 六个条目**一个都没勾**
- `reports/generated/` 只有 `.gitkeep`
- 全仓没有 LightGBM、标签、walk-forward 代码

真实状态是 **P1 完成、P2 从未开始**。任务书 §8.1 自己写了"若实际最新结果不同，
以真实 source commit 为准"，所以注册表记的是真实状态。

把没发生的失败写进永久实验日志，等于注入四条伪造结论——比留白危险得多。

## 实际做到了什么

P1 gate 通过：

- OKX 15m 历史摄取，分页有界、可重试
- 幂等增量更新
- 原子 Parquet 快照 + 内容哈希 & 产物哈希写进 JSON manifest
- 拒绝畸形、冲突、有缺口、未收盘、未来、过期的 bar 序列
- 因果 MA-density 候选数据集 + 密度诊断 + **匹配随机对照**
- 相同输入产出相同 bar / 特征 / 候选 / 哈希

## 迁进来的东西

| 能力 | 落点 | 裁决 |
|---|---|---|
| closed-bar 连续性 / 可用性检查 | `yoyo/data/continuity.py` | `ADAPT_AND_PORT` |
| 产物血统 manifest + 内容哈希 | `yoyo/artifacts/lineage.py` | `ADAPT_AND_PORT` |
| 匹配对照的分层与**确定性选择** | `yoyo/evaluation/matched_controls.py` | 设计吸收 |
| walk-forward + purge | `yoyo/evaluation/walk_forward.py` | 设计吸收 |
| typed / fail-closed 配置 | — | `REFERENCE_ONLY` |

### 两处刻意的行为改动

1. **缺口改为登记而非抛错**。darkforest-one 只吃一个币（ETH-USDT-SWAP），可以整条
   序列拒绝；本仓扫 200+ 个上市时间不同的币，抛错会让最新上市的币掀翻整轮扫描。
   需要拒绝的 builder 调 `assert_continuous()`。
2. **匹配对照采用它的 sha256 确定性选择，而不是 yoyo-eth 的 rng 抽样**——
   rng 只有在整个循环按相同顺序重放时才复现。

### 为什么 typed config 只登记不迁

`governance/config.py` 用 pydantic + `typing.Self`，绑死 Python 3.11。
本仓 venv 是 3.9，且 CLAUDE.md 禁止新增重型依赖。
**fail-closed 的语义已用 stdlib 实现**在 `yoyo/contracts/{artifacts,pattern,holdout}.py`：
必填字段、封闭词表、拒绝而非猜测、promotion 需要哈希与 holdout 出身。

## 明确拒绝的

第二套完整 package 根、第二个 CLI（`dfone`）、第二份 ETH canonical 数据、
未接入实际系统的 paper 外壳。任务书 §8.1 明列，此处执行。
