# P2 Owner-short Hard-Negative Canary 331事件审核包（2026-08-11）

## 结论先行

- 第二训练臂在独立pre-holdout连续canary产生的 **331个去重事件已全部逐张渲染**，覆盖140个币，
  不是抽样、不是331笔订单，也没有按未来涨跌筛选。
- 每个事件固定分成三份物理文件：模型当时的原始因果输入、仅供人工看的橙框副本、最多48根的
  未来走势对照。共331×3=993张PNG；未来数据不在训练图、标签或候选训练manifest中。
- 审核页提供“真目标 / 延续重复 / 框不准 / 明确负例”四类按钮、筛选和JSON导出。默认331张
  全部未确认，只有Owner导出的裁决才能改变语义；本轮没有自动预选任何一张。
- 本审核包只是为331个剩余触发建立错误分类和校准依据，**不是第三训练臂数据集**。331个事件只来自
  一个12小时独立时间块，数量和行情覆盖都不足以直接承担下一轮全部hard negatives。
- 未读取holdout、未写训练labels、未改ACTIVE、未promote、未训练新权重、未部署或下单。

## 331个事件从哪里来

第二训练臂以冻结参数扫描2026-05-03 00:15–12:00 UTC的连续市场窗口，共得到8,268条原始
YOLO命中。按“同币种 + 预测核心中点相距不超过5根”的冻结规则聚类，并保留每组首次跨过
conf=0.25门的决策，得到331个独立候选事件。

| 口径 | 数量 |
|---|---:|
| 原始YOLO命中 | 8,268 |
| 去重候选事件 | 331 |
| 覆盖币种 | 140 |
| 首次置信度 median / p90 | 0.3627 / 0.6553 |
| 事件峰值置信度 median / p90 | 0.6344 / 0.8699 |

排序只用于让Owner先看高峰值置信度事件；331个事件全部保留，排序没有改变样本集合，也没有用未来
收益或未来K线选样。首次置信度是事件第一次跨门时的分数，峰值置信度是同一去重簇内最高分；
审核页同时展示两者，避免把峰值误当成决策当刻置信度。

## 人工审核合同

| 按钮 | 含义 | 是否可直接写成训练负例 |
|---|---|---|
| 真目标 | 形态和核心位置符合Owner目标 | 否；应保留为正例候选 |
| 延续/重复 | 上一启动事件的相邻延续，不是新事件 | 否；先用于事件去重规则诊断 |
| 框不准 | 形态可能正确，但模型核心框边界有偏差 | 否；需要Owner重框或回到原金标几何 |
| 明确负例 | 当时可见形态就不是目标 | 仅为hard-negative候选；仍需数据去重和时间切分 |

页面右侧未来最多48根只帮助人工判断“后续是否真的启动/是否只是震荡”，不能反过来把“后来跌了”
自动等价为真目标。训练输入只允许使用左侧对应的原始因果文件；审核页中带橙框的左图也是单独副本，
避免框线像素污染训练输入。

## 数据与隔离审计

| 检查项 | 结果 |
|---|---:|
| manifest唯一事件 | 331 / 331 |
| 原始因果输入PNG | 331 |
| 橙框审核副本PNG | 331 |
| 未来审核PNG | 331 |
| 总PNG | 993 |
| 完整48根未来 | 328 |
| 仅47根未来 | 3 |
| 最大物理读取时间 | 2026-05-03 23:45 UTC |
| holdout开始 | 2026-05-04 00:00 UTC |
| holdout materialized rows | 0 |
| labels目录 | 0 |
| `training_eligible=true` | 0 |
| Owner预选裁决 | 0 |

所有事件的因果图严格截止于各自首次决策时刻；只有独立`future_review_only/`目录含决策后的K线。
审核HTML引用的是带框副本和未来对照，训练原图另存在`causal_input/`，没有任何标签目录。

## 浏览器与代码验收

- 实际浏览器加载331张卡片，首尾C001–C331均存在；页面无console error或warning。
- 实测点击C001“真目标”后，统计变为`target=1 / pending=330`；筛选只显示C001；JSON导出包含
  冻结protocol、源事件SHA、331个完整decision和正确计数。
- 验收后已清除浏览器测试选择并刷新，交付页恢复`pending=331`。
- 全量测试：`639 passed, 2 skipped`。

## 必报指标状态

- val AUC、置换检验p、top-decile毛/净收益、胜率、单特征基线：N/A；本轮产物是YOLO候选的
  人工语义审核界面，没有训练LightGBM或形成交易排序。
- 匹配随机对照：N/A；没有把331个候选事件当订单，也没有用未来收益作本轮裁决。
- event precision / recall：待Owner完成逐事件审核后才有可信分子；331仍不是全市场召回分母。

## 风险与诚实声明

- 331个事件全部来自一个12小时pre-holdout时间块，适合发现主要误触发类型，但不覆盖不同波动、
  趋势、币种生命周期和市场阶段。
- 不能把331张全部默认标负。若里面存在真目标、延续事件或框偏事件，自动写负会直接污染模型。
- 即使Owner把331张全部审完，第三臂还应从多个未使用的pre-holdout时间块补充多样化候选；以错误类型
  覆盖和新增时间块触发密度趋稳为停止条件，不以机械凑数宣布足够。
- 当前第二臂权重仍是密度失败，禁止promote；本审核页不会自动发起训练。

## 复现命令

```bash
# 物理截断的审核未来快照；最大时间仍早于holdout
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/backtest_owner_short_gold_center_recent.py historical \
  --out-dir analysis/output/owner_short_gold_center_preholdout_canary_audit_20260503_v1 \
  --end 2026-05-03T23:45:00Z --context-bars 600

# 用冻结331事件渲染三份物理隔离图片和审核HTML
PYTHONPATH=. .venv/bin/python scripts/build_owner_short_hardneg_canary_review.py

# 测试与报告转换
PYTHONPATH=.:/Users/zhangzc/fable-trading .venv/bin/pytest -q tests
python3 scripts/md_to_html.py \
  analysis/p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.md \
  --out-dir analysis/html
```

## 交付物与下一步

- Owner审核入口：`analysis/html/p2_owner_short_gold_center_hardneg_canary_review331_20260811.html`
- 冻结清单：
  `analysis/output/owner_short_gold_center_hardneg_canary_review331_v1/review_manifest.jsonl`
- 机器审计摘要：
  `analysis/output/owner_short_gold_center_hardneg_canary_review331_v1/summary.json`

Owner先逐张完成四类裁决并复制页面底部JSON。收到裁决后，先统计真目标precision、延续重复率、框偏率
和明确负例率，再据此设计跨多个未使用pre-holdout时间块的补挖策略。第三训练臂的正式数据构建和开训
是下一次单独动作，不能由本审核页自动触发。
