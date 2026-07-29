# ETH 3m short-start pilot v2 数据集审计

日期：2026-07-29

## 一句话结论

**v2 已按 owner 明确证据重构并通过独立结构验证，但只够做诊断 pilot。** train/val 共有
137 张：30 张 owner 批量确认的当前 T 正例、107 张 Label Studio owner-no 当前 tip
负例；正例按重叠 3h 区间只有 29 个独立事件。相邻时点规则候选
150 条全部无 target、退出训练。

## 为什么从框检测改成当前-tip二分类

- 用户实际问题是“现在是不是可入场的做空启动”，不是“整张图哪里存在某个对象”。
- v1 的 107 张 owner-no 中有 69 张历史区域含已知正形态；把它们写成 YOLO 整图空标签会产生
  矛盾监督。
- v1 严格 OOS 连续盘口 raw fire 99.74%，说明静态框 mAP 没有约束事件密度。
- 因此 v2 是有 owner 授权记录的目标重置，不是与 v1 可直接比较的单变量调参。

## 标签合同

| 训练角色 | 数量 | 证据 | 是否进 train/val |
|---|---:|---|---|
| `short_start` | 30 | 固定 calibration30 当前 T；owner 批量确认“看过了都来的急” | 是 |
| `no_start` | 107 | Project 53 Label Studio owner-no 当前 tip | 是 |
| T-1/T+1/T+2/T+3/原 v10 | 150 | 没有逐时点人工结论 | 否；仅待复核 |

检测扫描的 `tip/tip-1/tip-2` 是框定位容差，**不是**信号寿命。v2 初稿曾把它误转成
T/T+1/T+2 正、T+3 负；反方复核后已纠正，不能训练那一版。

批量确认回执绑定 calibration manifest、移动端 HTML、30 张 review 图和 30 张 causal 图 SHA256；
它仍诚实标记为聊天整批确认，不冒充 30 条逐行 Label Studio 标注。

## 数据统计

| Split | 图片 | 是 | 不是 | 独立正事件 | 全局事件组 |
|---|---:|---:|---:|---:|---:|
| train | 95 | 22 | 73 | 21 | 53 |
| val | 42 | 8 | 34 | 8 | 18 |

- 200 根 3m 因果输入，图像最后一根就是决策 T。
- train/val 按事件顺序切分；实际 embargo 378 bars，硬门
  260 bars（200 输入 + 60 人工未来窗）。
- 连续开发期 smoke 7,089 bars，保持无标签，绝不自动转负例。

## 独立验证

- 状态：`passed`；失败项：无。
- 图片文件、尺寸、class/path/target、SHA256、事件跨 split、输入因果窗、标签未来窗、holdout 边界、
  weak 标签为空和 owner receipt 哈希全部由独立验证器复算。
- 相关测试与复现命令见下节。

## 复现命令

```bash
MPLCONFIGDIR=/private/tmp/mpl-eth3m-v2 PYTHONPATH=. .venv/bin/python \
  scripts/build_eth3m_short_pilot_dataset_v2.py --out datasets/eth_3m_short_pilot_v2
PYTHONPATH=. .venv/bin/python scripts/validate_eth3m_short_pilot_dataset_v2.py
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_build_eth3m_short_pilot_dataset_v2.py \
  tests/test_build_eth3m_short_pilot_dataset.py \
  tests/test_build_eth3m_entry_timing_calibration30.py \
  tests/test_analyze_eth3m_v10_yes_no_labels.py
PYTHONPATH=. .venv/bin/python scripts/build_eth3m_short_pilot_v2_dataset_report.py
```

## 你提供的两份研究报告如何进入本轮

采用了可直接审计的工程建议：因果裁切、统一渲染、禁用破坏时序的翻转/mosaic、事件级时间切分。
没有采用 ARIMA 合成、旋转框、改 IoU 损失或报告中的高准确率/高收益数字：这些建议不能修复当前
标签证据不足，而且 `[cite]` 还不是本仓库可复核的实验材料。

## 本阶段不适用指标

尚未训练，所以 val AUC、事件精度、raw-fire/day、top-decile 扣成本收益、置换 p、匹配随机对照
全部为 N/A。数据构建验证通过不等于模型验收通过。

## 风险与诚实声明

1. 有效正样本只有 29 个独立事件，样本非常少，只能回答
   “有没有可学习信号”，不能宣称模型成熟。
2. 正例来自 v10 owner-yes 后的第一次六 MA 下破提案，负例来自 v10 owner-no，存在来源/规则捷径；
   后续必须用连续 smoke 与简单规则基线揭穿捷径。
3. 普通连续盘口还没有人工 true-tip 负例；smoke 无标签，因此只能测密度，不能算精度或进训练。
4. v2 不是 formal gold，不可 promote，不可切 ACTIVE，不可据此触碰 holdout。
5. 并行审计助手曾误读另一个 1m 文件的表头和 3 行 2026-07-15 holdout 数据；未用于本数据集，
   但已按纪律保守登记为全局 holdout 第 12 次误耗。

## 下一步（需 owner 决策）

是否启动**诊断性 classification pilot**。若启动，训练前先冻结连续盘口 raw-fire 密度、事件级人工
精度、T 时点来得及率三个验收门；不允许训练后看结果再倒推阈值。正式扩集则需要新标普通 true-tip
负例和“形态正确但已经太晚”的负例。
