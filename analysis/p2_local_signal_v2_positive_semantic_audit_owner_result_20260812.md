# Local Signal V2 语义审核结果：Positive基本成立，连续判别边界失败

## Executive Summary

- **结论属于情况B。** Owner完成200/200张走势辅助YES/NO审核后，旧Positive Pool为85/100 YES，而当前连续Canary只有11/100 YES。主要矛盾不是“正样本几乎都错”，而是模型离开训练正例分布后把大量普通形态判成目标SHORT。
- **R2方向没有改善语义。** 50个R1/R2共同保留候选仅6个YES；25个R2新生候选0个YES；25个R1被R2抑制的候选反而有5个YES。R2既产生纯NO的新候选，也丢掉部分Owner认可的R1信号。
- **不能再无脑开R3。** R1到R2已经增加Owner确认hard negative，但连续边界仍失败。下一步应先用本轮冻结裁决做表示与边界诊断；只有定位到可控的单一变量，才设计下一训练臂。
- **6%模型纵轴是嫌疑变量，不是已证实病因。** Canary真实波幅不足1%的39张仅2张YES，但真实波幅≥4%的8张同样0张YES；低波幅分辨率可能受损，却不能单独解释全部误报。

本轮没有训练、修改权重、conf、NMS、窗口、标签、ACTIVE、部署或下单；holdout读取0。

## 1. 这把尺子完整且可用

审核单位为一个独立事件；Owner在左侧查看截至decision的自适应因果图，在右侧查看物理分离、只供人工参考的未来走势。YES/NO会受未来走势辅助，因此下文称为“Owner语义YES率”，不冒充实时causal precision。

| 完整性检查 | 结果 |
|---|---:|
| manifest行数 / 唯一ID | 200 / 200 |
| verdict日志行数 / 唯一ID | 200 / 200 |
| manifest与verdict ID集合 | 精确一致 |
| YES / NO / SKIP | 96 / 104 / 0 |
| 缺失`reviewed_at` | 0 |
| 首条 / 末条裁决 | 2026-08-12 11:49:29 / 11:54:52 CST |
| 因果图止于decision | 200 / 200 |
| 未来对照早于holdout | 200 / 200 |
| training eligible / holdout读取 | 0 / 0 |

Owner裁决日志SHA-256：`7a350788230567acde054c3a8ee40e16eba095a4f60d2d156ff0385b25cb88a7`。

## 2. 85%对11%的断层把问题定位到连续边界

| 审核组 | YES | NO | SKIP | YES率 | 95% Wilson参考区间 |
|---|---:|---:|---:|---:|---:|
| 旧Positive Pool | 85 | 15 | 0 | **85.0%** | 76.7%–90.7% |
| 当前Canary Candidate | 11 | 89 | 0 | **11.0%** | 6.3%–18.6% |

Positive的85%说明当前目标不是完全被旧pad200语义污染；15个NO仍需从后续高纯Positive Gold Set中排除，但它不是89% Canary NO的主要解释。Positive与Canary相差74个百分点，模型对训练风格正例和连续市场候选的语义表现完全不同。

**含义：** 下一阶段不应推倒已有Positive，也不应把全部1,345张继续视为同等可信。正确做法是冻结85个YES为高纯正例种子、15个NO为污染证据，同时把连续边界作为主问题。

## 3. R2新候选全部失败，R1被抑制集合仍含真信号

| Canary内部来源（审核后解盲） | 审核数 | YES | NO | YES率 | 95% Wilson参考区间 |
|---|---:|---:|---:|---:|---:|
| R1/R2 common retained | 50 | 6 | 44 | 12.0% | 5.6%–23.8% |
| R2 new | 25 | 0 | 25 | **0.0%** | 0.0%–13.3% |
| R1 suppressed | 25 | 5 | 20 | **20.0%** | 8.9%–39.1% |

这不是“R2更严格所以更好”。如果是更好的收缩，R2新生集合不应全部为NO，且被R2抑制的R1集合不应有20% YES。R2在当前连续分布上改变了边界，但改变方向与Owner语义不一致。

Canary使用50/25/25诊断配额，并为避免holdout要求每张至少16根安全未来，因此总11%不能直接外推到255事件或398 events/day。按163/32/60母池数量做的朴素加权敏感性约为12.4%，但它仍不是概率抽样后的市场precision，只能说明结论不会因配额差异翻转。

**含义：** R2继续blocked，不promote；R1也因密度过高不能恢复为可用检测器。下一步研究的是二者为何错，而不是在二者之间选一个上线。

## 4. conf排序很弱，调阈值没有证据成为解法

| 内部confidence层 | Canary审核数 | YES | NO | YES率 |
|---|---:|---:|---:|---:|
| low | 38 | 3 | 35 | 7.9% |
| mid | 35 | 4 | 31 | 11.4% |
| high | 27 | 4 | 23 | 14.8% |

置信度从low到high只有约7个百分点改善，high层仍有23/27为NO。它表明模型分数对Owner语义有一点排序能力，但远不足以靠调高conf解决；这也与此前conf 0.45令事件减少但召回崩塌的结果一致。

**含义：** 本轮没有也不应修改conf。后续实验成功标准必须看固定conf下的连续候选语义率与密度，不再用自家val mAP或阈值美化替代。

## 5. 6%纵轴可能伤害低波幅分辨率，但不是唯一病因

| 因果窗口真实总波幅 | Canary审核数 | YES | NO | YES率 |
|---|---:|---:|---:|---:|
| <1% | 39 | 2 | 37 | **5.1%** |
| 1%–2% | 38 | 5 | 33 | 13.2% |
| 2%–4% | 15 | 4 | 11 | **26.7%** |
| ≥4% | 8 | 0 | 8 | 0.0% |

模型renderer强制纵轴至少覆盖现价6%。在<1%真实波幅窗口里，形态只占约六分之一画面，像素分辨率明显较低；其YES率也最低。但≥4%组同样0/8，且各组样本量较小、币种与cohort混杂，所以不能宣称把6%改成auto-Y就一定解决误报。

**含义：** 纵轴合同值得成为单变量候选，但训练前要先做只读表示诊断：比较YES/NO的蜡烛像素占用、均线间距、框位置与decision延迟，确认错误是否稳定集中在尺度压缩，而不是直接把Owner审核renderer搬进模型。

## 6. 推荐下一步：先定位边界，再决定唯一训练变量

1. **冻结本轮96 YES与104 NO。** 保留完整来源和SHA，继续保持`training_eligible=false`；任何训练集转换都需Owner另行批准。
2. **做一次只读边界诊断。** 在这200张上比较R1/R2状态、真实波幅、模型像素占用、均线密集度/斜率、核心框位置和decision延迟；不训练、不调conf、不读holdout。
3. **根据诊断只选一个训练变量。** 如果错误稳定集中在低像素占用，设计“固定6% vs 因果自适应尺度”的单变量离线臂；如果尺度解释不了，则优先研究任务拆分或正负类定义边界，不继续复制hard negative。
4. **保持R1/R2 blocked。** 在新的连续pre-holdout块达到Owner语义率与密度门之前，不promote、不部署、不让L2掩盖L1候选泛滥。

## 7. 仍需Owner决定的问题

- 是否批准下一阶段先做上述**只读边界诊断**；该动作不训练、不读holdout。
- 若诊断支持尺度问题，是否批准建立一个只改模型纵轴renderer的单变量训练臂。
- 85个Positive YES、11个Canary YES、104个NO是否允许在下一阶段转换为候选Gold/Hard-negative；当前仍未转换。

## 8. 风险与诚实声明

- 本轮是未来走势辅助的Owner语义审核，不是实时causal precision测量；后见信息会影响判断。
- Positive与Canary均为分层诊断样本，不是简单随机样本；Wilson区间只是二项参考区间，不是严格survey-weighted区间。
- v2为保证Canary至少16根安全未来替换了v1中31个未来不足的Canary事件，时间分布偏向较早候选；因此不报告“每日真实YES数”。
- 0/25 R2-new是强烈警报，但其95%上界仍约13.3%；不能解释为总体永远0%。
- 6%纵轴与NO率相关不等于因果；≥4%组也全部NO，表明至少还有其他边界问题。
- 本报告没有自动训练、R3/R4、阈值修改、promote、ACTIVE、部署、TG、交易、forward log清理或holdout读取。

## 9. 复现与机器证据

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/python scripts/summarize_local_signal_v2_semantic_review.py

PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/pytest -q tests
```

机器证据：

- `analysis/output/local_signal_v2_positive_semantic_review200_v2/owner_verdicts.jsonl`
- `analysis/output/local_signal_v2_positive_semantic_review200_v2/owner_review_joined.jsonl`
- `analysis/output/local_signal_v2_positive_semantic_review200_v2/owner_review_diagnostics.json`
- `analysis/output/local_signal_v2_positive_semantic_review200_v2/causality_audit.json`
- `analysis/output/local_signal_v2_positive_semantic_review200_v2/sampling_audit.json`

| Owner完成后产物 | SHA-256 |
|---|---|
| `owner_verdicts.jsonl` | `7a350788230567acde054c3a8ee40e16eba095a4f60d2d156ff0385b25cb88a7` |
| `owner_review_joined.jsonl` | `e6eae060d825f95b3018aed7ca4e8955e4bae60728f6b4948ed49def05307023` |
| `owner_review_summary.json` | `d06f0165e5903abbf00a7746c7b70c6f445f64429c66d001913a4c21b23451d3` |
| `owner_review_diagnostics.json` | `d06f0165e5903abbf00a7746c7b70c6f445f64429c66d001913a4c21b23451d3` |

本轮不是L2或收益实验，因此val AUC、置换检验p、top-decile毛/净收益、胜率、单特征收益基线和匹配随机入场对照均为N/A；没有用缺失的交易指标替代语义结论。
