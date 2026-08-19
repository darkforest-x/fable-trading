# 开源 AI 框架评估 —— 哪些真能帮到这个项目（2026-08-20）

> 方法：**先定位瓶颈，再找工具。** 不列时髦框架清单。
> 每条裁决都对着本项目的一个具体失败或阻塞。

## 先说结论

| 库 | 治什么 | 裁决 |
|---|---|---|
| **cleanlab** | 金标 DIRECT 错误率未知（`training_eligible=false` 的**唯一**原因） | **拿** |
| **aeon**（eTSC 模块） | 「尽早识别」这件事有成熟子领域，我们在手搓 | **拿（先做对照基线）** |
| **CPCV**（purged-cross-validation / skfolio） | 单路径结论不复现（+0.465 → +0.065） | **拿** |
| TS 基础模型（Chronos-2 / MOMENT） | tip 处的数值表征 | **试，但先做污染评估** |
| DVC | 数据集版本化 | 缓 —— 现有 manifest+SHA 能工作 |
| MLflow / Aim | 实验跟踪 | 缓 —— registry.yaml 够用 |
| Snorkel | 弱监督 | **不拿**（与铁律冲突，见下） |
| vectorbt / backtesting.py / nautilus | 回测 | **不拿**（会造出第二套 outcome 语义） |

---

## 一、瓶颈定位（来自本项目自己的记录）

1. **金标质量未知**。固定 W10 金标冻结在 1,247/1,402，其余门全过，
   `training_eligible` 卡在 false，唯一原因是**协议 17.6 要的 DIRECT 抽检错误率未知**
   （迁移产出 DIRECT=0）。
2. **tip 处认不出来**。旧 detector 完整上下文复现 62%，**tip 只有 9–10%**。
   111 个模型、frozen-F1 十几版横着走（0.650→0.627→0.645）。
3. **单路径结论不复现**。yoyo-eth 的 anchored ρ=+0.465 在 4 折 walk-forward 下
   只剩加权均值 +0.065；其报告自列风险第 2 条就是「结论对切点敏感」。

这三条决定了该拿什么。**不是缺模型，是缺「标签有多干净」「早识别怎么量」「结论稳不稳」的方法。**

---

## 二、Tier 1：现在就拿

### 1. cleanlab —— 直接对着 `training_eligible=false` 那个洞

[Confident learning](https://pypi.org/project/cleanlab/) 用交叉验证的样本外预测概率
估计「噪声标签 ↔ 真标签」的联合分布，输出**每个样本的标签质量分**和疑似错标掩码。
**这正是协议 17.6 要的那个错误率**，而且是估出来的、不是抽样猜的。

它还有 [object detection 模块](https://docs.cleanlab.ai/v2.6.6/tutorials/object_detection.html)：
给定框标签 + 模型预测（不需要图片本身）算每张图的标签质量分。
本项目的金标就是框，这条直接对得上。

**落地前提（重要）**：cleanlab 要的是**「标签 + 样本外预测概率」配对**。
现有的 `analysis/output/fixed_w10_evolution_*/predictions.jsonl` 是
**102,024 条无标签推理输出**（只有 `p_signal`），**不能直接喂**。
可行路径是导出已训练 W10 分类器在 **val(350) + test(450)** 上的预测——
那 800 条按构造就是样本外，**不需要新训练**，不触碰 P0/P1 的训练禁令。

### 2. aeon —— 「早识别」不是我们的独创问题

[aeon 有专门的 early time series classification (eTSC) 模块](https://www.aeon-toolkit.org/en/stable/examples/classification/early_classification.html)，
定义是「用尽可能少的观测、尽可能高的精度完成分类」。

对照 `CLAUDE.md` 纪律 12 对 Local Signal V2 的要求：
「核心结束后只允许 3–5 根确认…验收分别报告 delay 3/4/5 的首次命中，精确度优先」。
**这是同一个问题的两种说法。** 这个子领域有算法、有基准、有实现，
而本项目一直把它当成一个 YOLO 调参问题。

**建议用法不是换模型，是先要一条对照基线**：把同一批金标喂给 aeon 的 eTSC 标准方法，
看「领域标准做法在 delay 3/4/5 上是多少」。有了这个数，
「我们的检测器 tip 复现 10%」才知道是差在方法还是差在问题本身。

### 3. CPCV —— 单路径结论不复现的解药

[Combinatorial Purged CV](https://en.wikipedia.org/wiki/Purged_cross-validation)
（López de Prado, AFML 第 12 章）系统性构造多组 train/test 组合，
purge 掉标签窗重叠的样本、embargo 掉测试后的一段，
**产出的是 OOS 表现的分布，而不是一条路径**。

本项目 `yoyo/evaluation/walk_forward.py` 已经实现了 anchored + purge + embargo，
但仍是**单条路径**。而项目历史上最贵的教训之一恰恰是：
同一批事件换个切点，ρ 从 +0.219 翻成 −0.151。
**CPCV 会把这件事变成一个可以直接报告的分布宽度，而不是下一轮才发现的意外。**

可选实现：[purged-cross-validation](https://github.com/eslazarev/purged-cross-validation)（有 JOSS 论文）
或 [skfolio 的 `CombinatorialPurgedCV`](https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html)。
注意组合数是阶乘增长，需要控制折数。

---

## 三、Tier 2：试，但先做一件事

### TS 基础模型（Chronos-2 / MOMENT / TimesFM）

2026 年的做法是**冻结 embedding + 线性探针**做分类，成本很低，
正好可以低风险试探「纯数值表征能不能在 tip 认出形态」——
yoyo-eth 用 27 个手工特征 + LightGBM 失败了，但那不等于表征学习也失败。

**⚠️ 但对本项目有一个特殊风险，必须先评估。**
2026 年 2 月一篇[基准审查](https://arxiv.org/html/2510.13654v2)对 22 个 TSFM 做了
训练/测试集血缘分析，发现**相关序列的时间重叠普遍存在**，
「一个模型的训练集有时是另一个模型的测试集」。

这些模型在公开数据上预训练，**很可能见过同一批 crypto OHLCV**。
如果是这样，它在我们 holdout 上的表现就是被污染的——
而**本项目现有的因果测试一条都抓不到这个**：
`test_future_mutation` 验的是我们的特征不偷看未来，
管不了模型权重里已经烘进去了什么。

**所以顺序是：先查预训练语料是否含 crypto/OKX，再谈用不用。**
查不清就只能当 `production_eligible=false` 的研究工具。

---

## 四、Tier 3：明确不拿

- **Snorkel**（弱监督标注函数 + 生成模型合成标签）——
  能力很强，但**与铁律直接冲突**：本项目明令「规则或模型只能生成 proposal，
  不得自动变成 Gold」，`yoyo/contracts/pattern.py` 规则 5 在构造函数里强制。
  Snorkel 的核心恰恰是「用规则合成训练标签」。语义冲突，不是依赖问题。
- **vectorbt / backtesting.py / nautilus_trader** ——
  `yoyo/contracts/outcomes.py` 是唯一的障碍/出场实现，同 bar TP/SL 抢先口径是 owner 决策。
  引入第二套回测语义 = 一个问题两个答案，正是刚做完的收敛在消除的东西。
  真要交叉验证，写一次性对照脚本，不进主线。
- **各类 LLM agent 编排框架** —— 不治上面任何一个瓶颈。

---

## 五、依赖隔离（实测发现，务必照做）

**不能直接 `pip install` 进主 venv：**

| 库 | 副作用 |
|---|---|
| cleanlab | numpy **2.0.2 → 1.26.4**（降级） |
| aeon | pandas 2.3.3 → 2.2.3，新增 numba + llvmlite |

主 venv 的 numpy/torch/ultralytics 版本是 **Mac↔3060 训练契约的一部分**——
`scripts/train_on_3060.sh` 两端版本不一致直接拒绝开训，理由是
「结果无法与历史曲线对照」。降 numpy 会同时打断这个契约，并可能改变数值结果。

**做法**：评估类依赖一律进独立 venv。已验证可行：

```bash
python3 -m venv /tmp/fable_eval_venv
/tmp/fable_eval_venv/bin/pip install -r requirements-eval.txt
```

装完复验主 venv 未受影响：numpy 仍是 2.0.2、pandas 仍是 2.3.3（已实测通过）。

---

## 六、建议的第一步（只做一件）

**导出 W10 分类器在 val+test 上的 800 条样本外预测，跑 cleanlab，得到那个错误率。**

理由：
- 它解开的是**当前唯一的硬阻塞**（`training_eligible=false`）
- **不需要新训练**（val/test 按构造就是样本外），不触碰 P0/P1 禁令
- 依赖最轻（cleanlab 只多 3 个包），且在隔离 venv 里
- 结果无论好坏都有用：错误率低 → 金标可以申请转训练资格；
  错误率高 → 说明 P0 的重复标注稳定性问题比想的严重，那也是必须知道的

**这一步需要 owner 点头**，因为它读金标并会产生一个可能改变 `training_eligible` 的数字。

## 七、风险与诚实声明

1. **本文没有跑任何实验**，只做了库可用性与依赖影响的实测。
   所有关于「能帮上什么」的判断都是基于项目已记录的失败，不是基于新证据。
2. **aeon 的 eTSC 基线也会受同一批标签质量的影响。** 如果金标本身有噪声，
   换方法不会解决问题——所以 cleanlab 排在 aeon 前面。
3. **CPCV 的组合数阶乘增长**，在本项目的事件量级（val 44 / test 40 那种规模）上
   反而可能不稳定。要先在大池子上用。
4. **TS 基础模型的污染风险目前无法证伪**，在查清预训练语料之前，
   任何用它得到的 holdout 数字都不能当验收。
5. 本文列的都是**候选**。除 cleanlab 在隔离 venv 里验证过可导入之外，
   没有任何一个被接入本项目的运行路径。

## Sources

- [cleanlab · PyPI](https://pypi.org/project/cleanlab/)
- [Finding Label Errors in Object Detection Datasets — cleanlab](https://docs.cleanlab.ai/v2.6.6/tutorials/object_detection.html)
- [Early time series classification with aeon](https://www.aeon-toolkit.org/en/stable/examples/classification/early_classification.html)
- [aeon-toolkit/aeon — GitHub](https://github.com/aeon-toolkit/aeon)
- [Purged cross-validation — Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation)
- [eslazarev/purged-cross-validation — GitHub](https://github.com/eslazarev/purged-cross-validation)
- [skfolio CombinatorialPurgedCV](https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html)
- [mlfinlab combinatorial.py](https://github.com/hudson-and-thames/mlfinlab/blob/master/mlfinlab/cross_validation/combinatorial.py)
- [Challenges and Requirements for Benchmarking Time Series Foundation Models](https://arxiv.org/html/2510.13654v2)
- [The 2026 Time Series Toolkit: 5 Foundation Models](https://machinelearningmastery.com/the-2026-time-series-toolkit-5-foundation-models-for-autonomous-forecasting/)
