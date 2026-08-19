# 模型清单 — 我们到底训出了什么（2026-08-20）

> 全树实测，按 SHA-256 去重。不是凭记忆列的。
> 复现：
> ```bash
> python3 tools/consolidation/audit_models.py
> ```

## 一句话

**111 个不同的权重文件，其中 107 个是训练产物、4 个是 COCO 底座。
外加 17 个 LightGBM 判断层模型。**

**但生产上没有模型在跑**：`models/active_bundle.json` 不存在，
执行器 `require_active_bundle()` fail-closed。`models/ACTIVE` 是**研究指针，不是生产权威**。

## 一、L1 检测层（YOLO）— 111 个权重

### 按家族

| 家族 | 去重后 | 说明 |
|---|---|---|
| `owner_v*` 检测器主链 | **29** | v6→v7s→v8→v9→v10→…→v16，主线 |
| `hardneg` 困难负例 | 17 | w96/w200 多轮 |
| `owner_short_star` | 12 | short 专用分支，含当前 `owner_best.pt` |
| `side_short_tip` | 8 | 带方向的 tip 实验 v1b/v2/v3 |
| 分类器（cls） | 7 | 含 W10 分类器 |
| R3 gold 微调 | 6 | yoyo-trading 的 v3gold ft/cold |
| smallwin | 5 | 小赢实验（Mac） |
| paired A/B | 4 | 配对对照 |
| COCO 底座 | 4 | yolo11n/s、yolo11n-cls、yolo26n |
| local_signal_v2 | 3 | hardneg r1/r2 |
| 其他 | 16 | exemplar、hts_chain、eth3m pilot 等 |

文件数 149（同一模型多处存放），**不同模型 111**。
存放最多的一个存了 14 份（`owner_short_star_v10.pt`）。

### 主链血统与固定尺子

```
yolo11s (COCO)
   → owner_v7_chain
   → owner_v8_chain    frozen-F1 0.650
   → owner_v9_chain    frozen-F1 0.627
   → owner_v10_chain   frozen-F1 0.645   ★ 当前 Pattern Teacher
   → owner_v11_chain   frozen-F1 0.658   ✗ 权重永久丢失（Mac MPS 训的，两头皆空）
   → owner_v12_htip    frozen-F1 0.650
   → v13 / v14_pad200 / v15_tipval / v16_tipuni_cold
```

**frozen-F1 从 0.650 → 0.627 → 0.645：没有涨。** 十几版之后固定尺子基本平的。

### ⚠️ 两条让这些数字大打折扣的污染

**1. `optimizer='auto'` 的 lr bug —— 修复前的全部 chain 模型「实为底座 + 1 个 warmup epoch」。**
epoch 3 就被打飞，而**貌似合理的 frozen-F1 把这件事掩盖了数月**。
`owner_v10_chain` 不受影响（`args.yaml` 里 `lr0: 0.0001` 是显式值，属修复后）。
即：v8/v9 那两个 frozen-F1 数字，量的可能根本不是训练出来的模型。

**2. 训练分布含未来 —— 训练图里信号右侧带着启动后文。**
2026-08-05 ablation 实测：v10 在 **tip 复现率 10%，完整上下文 62%**。
frozen-eval 自身也在有后文的分布上量，所以 **frozen-F1 预测不了盘口表现**。

这两条合起来的结论就是 `CLAUDE.md` 铁律 12 的由来：
**自家 val / mAP / 旧 frozen-F1 不得作生产裁决。**

### 唯一有 owner 语义确认的数字

owner 手标的 1313 个框在 val 期做空 **PF 8.98、胜率 79%**；
完全相同语境下随机进场只赢 32%。**owner 的判别力是真实的**——
问题从来不在「形态存不存在」，在「模型能不能在盘口认出来」。

## 二、L2 判断层（LightGBM）— 17 个

### 9 个冻结工件（`models/frozen_*.txt`）

| 工件 | 目标 | 分位 | 角色 |
|---|---|---|---|
| `..._yolo_v10_reg_20260731` | regression | q90 | **ACTIVE** |
| `..._yolo_v11_reg_20260718` | regression | q90 | ACTIVE_PREV / SHADOW_V11 |
| `..._yolo_v8_reg_20260716` | regression | q90 | SHADOW_V8 |
| `..._yolo_reg_20260715` | regression | q90 | — |
| `..._yolo_20260715` | 二分类 | q90 | SHADOW_BINARY |
| `..._swap_ma206_20260710` | — | q90 | 规则候选池 |
| `..._swap_20260709` | — | q90 | 规则候选池 |
| `frozen_tp5_sl2_2026-07-09` | — | — | 早期 |
| `frozen_scaled_25_t3_2026-07-09` | — | — | 早期 |

### ACTIVE 的实际内容（值得看清楚）

```
config            tp5_sl2_swap_yolo_v10_reg
threshold_val_q90 -0.0004397          ← 阈值是负的
best_iteration    1                   ← LightGBM 只留了 1 棵树
score_semantics   predicted_realized_ret
holdout           not consumed; pool ends 2026-05-03
owner_decision    L2 用 v10 池回归（2026-07-31），与 L1 short_star_v10 对齐
note              Walkforward not all-net-positive (rho_mean=0.043).
                  Not a tip-smoke claim for detector quality.
```

**`best_iteration: 1` 和 `rho_mean=0.043` 放在一起读**：
这个 L2 早停在第一棵树，且 walk-forward 的排序相关性 ≈ 0.04。
它是 owner 定向切换的（与 L1 v10 对齐），工件自己的 note 也写明
**不构成检测器质量的主张**。

### 5 轮 L2 evolution（`experiments/`）

`r1_feedback_pilot3` / `r3_walkforward_inner9`（两版）/ `r4·r5_crosssection_confirm3` /
`r5·r6_data_expansion_inner12·15`。

r1 的数字有代表性：**ROC-AUC 0.5992，但 top-decile 净收益 −0.00061**。
——AUC 接近 0.6 而钱是负的，正是 `CLAUDE.md` 开篇那条「把 AUC 当成功标准」的实例。

### 3 个 yoyo-eth LightGBM（已归档）

`artifacts/model.txt`（MVP）、`iteration_v1/anchored_model.txt`、`legacy_model.txt`。
结论已登记为 `rejected`：**压缩池跑输匹配随机对照 34.5bp（val）/ 37.1bp（test）**。

## 三、W10 分类器（yoyo-trading，3060）

停在 23/100 epoch，best epoch 3，**val top1 91.4% / val loss 0.248**，test 没跑。
3 天 holdout 试跑：126 信号、119 已平仓、**maker 净 +0.0453 / taker 净 −0.0023、胜率 31.9%**。

`training_eligible` 仍是 **false** —— 协议 17.6 要的 DIRECT 抽检错误率未知（迁移产出 DIRECT=0）。
**未 promote、未部署。** 三天试跑不是验收：换个费率路由符号就翻。

## 四、状态总表

| | 数量 | 生产资格 |
|---|---|---|
| L1 检测器权重（去重） | 111（107 训练 + 4 底座） | **0 个 production_eligible** |
| L2 冻结工件 | 9 | ACTIVE 指针指向 1 个，但那是**研究权威** |
| L2 evolution 轮次 | 5 | 0 |
| yoyo-eth LightGBM | 3 | 0，已 `rejected` |
| W10 分类器 | 1 | 0，`training_eligible=false` |
| **生产在跑的模型** | **0** | `active_bundle.json` 不存在，执行器 fail-closed |

`artifacts/registry.yaml` 里登记的 4 项权重资产，`production_eligible` 全部为 false，
由 `tests/contracts/test_registries.py::test_nothing_in_the_registries_is_production_eligible_yet` 守着。

## 五、诚实声明

1. **111 这个数字是「不同的权重文件」，不是「111 次有意义的实验」。** 同一次训练的
   `best.pt` / `last.pt` 会是两个不同哈希，多轮 sweep 的中间产物也各算一个。
2. **绝大多数模型没有可比的验收数字。** 各版自家 val mAP 不可跨版本比较（考卷不一致，
   §已知污染 3），frozen-F1 在有后文的分布上量，两者都不作生产裁决。
3. **3060 上还有 59 个权重没取回本机**，登记为 `REFERENCE_ONLY`
   （`storage_uri: host://windows-3060/C:/fable`）。本清单只覆盖本机可见的。
4. **v11 权重永久丢失**，其 frozen-F1 0.658 是本链最高值，但无法复核也无法复现。
5. 本清单**只盘点存在什么，不对任何模型下质量判决**。真正的裁决口径是
   `ROADMAP.md` 的三重经济门，而目前没有任何模型走完过。
6. **发现一个残留 worktree**：`.claude/worktrees/peaceful-ardinghelli-b84300`
   （分支 `claude/directory-structure-confusion-bafc06`，HEAD 停在 `9170987`）。
   它使全树 `.pt` 从 149 虚增到 318。`CLAUDE.md` 铁律 13 禁止留 worktree——
   **是否删除是 owner 决策**，本轮只报告不动。
