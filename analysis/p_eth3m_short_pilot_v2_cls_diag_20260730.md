# ETH 3m short-start v2 图像分类诊断训练报告

日期：2026-07-30
实验：`eth3m_short_pilot_v2_cls_diag_20260730`
结论：**FAIL（静态 val 第一门失败）**

## 一句话结论

v2 在 RTX 3060 完成一次预注册诊断训练，但固定 `p(short_start) >= 0.50` 时，val 的 8 个正例
全部漏掉：**TP=0、FP=0、TN=34、FN=8**。训练集 95 张却全部分类正确，说明模型记住了训练
来源，却没有跨时间泛化。Ultralytics 显示的 top1 80.95% 恰好等于多数类 34/42，不能视为成功。

## 授权、边界与输入

- Owner 明确授权“直接去3060跑吧”，并允许与既有 PID 93656 的 v10 wide dump 并发；原任务未停止。
- 原始审计集：137 张（train 95：22 是/73 不是；val 42：8 是/34 不是），29 个独立正事件。
- 训练视图：`datasets/eth_3m_short_pilot_v2_cls_letterbox960/`。每张原始 1280×742 图等比例缩放
  到 960×557，再白底补成 960×960；左右不裁切，最右端决策 T 保留。
- weak/review 150 张、连续 smoke 7,089 bars 与 holdout 均未进入训练或本轮固定门验收。
- 预注册：`analysis/eth3m_short_pilot_v2_cls_prereg.json`；阈值固定 0.50，禁止 sweep。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/prepare_eth3m_short_pilot_v2_cls.py --verify-only

FABLE_3060_HOST=zzc@192.168.1.5 \
  bash scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh

FABLE_3060_HOST=zzc@192.168.1.5 \
  bash scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh --status

MPLCONFIGDIR=/private/tmp/mpl-eth3m-cls PYTHONPATH=. .venv/bin/python \
  scripts/evaluate_eth3m_short_pilot_v2_cls.py --device cpu --batch 8
```

## 冻结训练配方与运行结果

| 项目 | 值 |
|---|---:|
| 模型 | `yolo11n-cls.pt`（官方预训练，SHA256 `c62d41bf…`） |
| 图像 / batch | 960 / 4 |
| optimizer / lr0 | AdamW / 1e-4 |
| epochs / patience | 100 / 20 |
| seed | 42，deterministic |
| 增强 | flip、HSV、scale、translate、autoaugment、erasing、mosaic/mixup/cutmix 全关 |
| 实际 | 21 epoch 早停；best=epoch 1；远端 exit=0 |
| best.pt SHA256 | `3ce89b668096e79eb00ae0ee8b4913024f91f46356626d22cbe11d3a98c30056` |

3060 最高观测显存约 1.5GB；并跑未造成 OOM。结尾的 `PyDataFrame is not defined` 只发生在
Ultralytics 绘制 `results.csv` 后处理，权重、CSV、最终验证和进程退出码均已正常落盘，不影响本轮指标。
远端原始日志和退出码已只读复制到
`analysis/output/eth3m_short_pilot_v2_cls_diag_20260730/remote_train.log` 与
`remote_exit_code.txt`；日志 SHA256 为 `b8e6487b…`，退出回执内容为 `0`。远端 `best.pt` 也以
`remote_best.pt` 原样保存在同一证据目录，并与本地文件逐字节比较一致，SHA256 均为上表值。

## 固定阈值结果

| 集合 / 对照 | TP | FP | TN | FN | Precision | Recall | Balanced Acc | ROC AUC | AP |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train，模型 p=0.50 | 22 | 0 | 73 | 0 | 100% | 100% | 100% | 1.000 | 1.000 |
| **val，模型 p=0.50** | **0** | **0** | **34** | **8** | **0%** | **0%** | **50%** | 0.629 | 0.309 |
| val，永远判 `no_start` | 0 | 0 | 34 | 8 | 0% | 0% | 50% | N/A | N/A |
| val，首次跌破六 MA 因果规则 | 5 | 0 | 34 | 3 | 100% | 62.5% | 81.25% | N/A | N/A |

val 所有图片的正类概率都低于 0.50，最大值仅 0.4368；8 个真阳性的平均概率为 0.0456，34 个
真阴性的平均概率为 0.0429。AUC 0.629 只表示极弱的相对排序信息，不能替代预注册开火门；本轮
不允许事后把阈值降到概率分布内部。

## 验收门

| 预注册门 | 要求 | 实际 | 判定 |
|---|---:|---:|---|
| val TP | ≥6/8 | 0/8 | **FAIL** |
| val FP | ≤2/34 | 0/34 | PASS，但来自全不报 |
| 连续 smoke 原始开火 | ≤3/天 | 未运行 | STOPPED |
| 18-bar 合并事件 | ≤1/天 | 未运行 | STOPPED |
| 30 事件 owner 认可率 | ≥60% | 未运行 | STOPPED |

静态第一门失败后按 fail-fast 停止；没有用 smoke 反推阈值，也没有进入人工复核。

## 解释

1. **不是没拟合，而是没泛化。** 同一个 best.pt 在 train 达到 TP22/FP0，却在后续时间 val
   变成 TP0/FP0，符合小样本、来源选择偏差或市场状态漂移，而不是简单增加 epoch 可以解决。
2. **简单规则仍更强。** 正例本就来自 owner-yes 池内“第一次收盘跌破六 MA”的提案；该规则在
   val 捕获 5/8 且没有 FP。图像模型不仅没有学到规则外信息，固定门下连规则覆盖也没有保住。
3. **训练配方存在待验证风险，但不是既定病因。** `results.csv` 的 epoch-1 `lr/pg2=0.077023`，
   来自 warmup bias 路径，可能影响小样本校准；当前只能记为假设。若实验，必须经 owner 批准，
   且只改 `warmup_bias_lr` 一个变量，不能同时改阈值、采样或 loss。

## 经济性与匹配随机对照

本轮目标是 owner 定义的“当前 tip 是不是及时做空启动”，未加载未来收益或障碍标签，因此
top-decile 净收益、置换 p、PF、胜率与同币×时间块×波动桶匹配随机对照均为 **N/A**。把这些
指标从别的池移植过来会伪造证据；只有模型先通过检测静态门和连续密度门后，才可另立经济性实验。

## 风险与诚实声明

- 有效正例只有 29 个独立事件，且正负都来自 v10 候选池；不是普通连续盘口的完整分布。
- 30 个正图是对固定 timing 包的聊天整批确认，不是 30 条逐图 Label Studio 金标。
- 当前结果不能 promote、不能写 `models/ACTIVE`、不能触发实盘，也不能以调低阈值“补救”。
- 本轮没有读取 holdout。此前已登记的全局 holdout 第 12 次误耗与本训练无关。
- 第一次远程 WMI 曾返回 PID 103004，但 staging 未执行、日志不存在，训练实际未开始；修成
  `STAGE_OK` 哈希回执后才由 PID 104384 真正训练。该假启动不计为第二个模型实验。

## 下一步需要 Owner 决策

**推荐：先扩充跨时间、跨状态的当前-tip 金标，再训练。** 重点新增普通连续盘口 true-tip 负例，
以及“形态像但已经迟到”的负例；正例需覆盖不同波动/趋势状态。不要继续用相邻 T±n 自动造标签。

另一个便宜但证据等级较低的选项，是只把 `warmup_bias_lr` 固定为 0 做一次单变量诊断复跑；即便
改善固定门，它仍不能消除 29 个事件与 v10 来源混杂，不能代替扩集。

机器可读证据：`analysis/output/eth3m_short_pilot_v2_cls_diag_20260730/summary.json`。
