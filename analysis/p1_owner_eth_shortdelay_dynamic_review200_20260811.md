# P1 Owner ETH 空头动态短窗 200 张扩展、一审与逐图改框（2026-08-11）

## 结论先行

Owner明确回复“确认”，冻结为只做空并认可前一轮绿/橙/红代表板方向；多头镜像排除、但不得
作为空头负例。按该授权，原先冻结的200个Stage-A train事件已全部重渲染为W14–22动态短窗，
事件集合、post配额与core宽度未改变。

Codex逐板视觉一审结果：

| 状态 | 数量 | 当前用途 |
|---|---:|---|
| `short_keep` | 40 | 旧核心暂可保留的空头候选 |
| `short_rebox_pending` | 61 | 空头语义可能成立，但旧框吃入启动K，等待逐图重画 |
| `short_hard_negative` | 25 | 不够干净的空头难负例候选 |
| `mirror_excluded` | 74 | 多头镜像隔离，既不作正例也不作负例 |

61张橙桶现已完成逐图改框proposal：先给冻结图中的每根K线编号，再逐张找启动首根，把新核心
结束在它前一根；没有使用统一左移。重渲染后，新核心4/5/6/7根分别为2/56/2/1张，完整窗口
W14–20，post仍为3–5根。橙色实线是新proposal，红色虚线是当前窗口中仍可见的旧框。

Owner确认的是类别协议与代表板方向，不是200张逐样本标签。因此全部保持
`sample_owner_confirmed=false`、`training_eligible=false`、`production_eligible=false`；本轮没有训练。

当前200张只是语义校准包，不是最终训练集。它们来自Stage-A的2,020个train正事件中符合旧框后
3–5根条件的316个候选；Stage-A可追溯正事件总池为2,378个（train 2,020 / val 358）。本机原始
缓存有602个CSV、约1.2GB，其中456个15m文件，237个至少365天，最长约430天。“3–5”指3–5根
15m K线（45–75分钟），不是3–5天。

## Owner确认范围

确认回执：
`analysis/output/owner_eth_shortdelay_codex_firstpass_v1/owner_confirmation_receipt.json`

已确认：

- 当前数据集只做空；
- 认可绿=可保留、橙=形态像但需改框、红=难负例方向；
- 多头镜像排除且不得当空头负例；
- 允许把冻结事件扩为200张动态短窗。

未授权：训练、holdout、生产、逐张Owner金标、ACTIVE/promote/部署/下单。

## 数据守恒

| 项目 | 结果 |
|---|---:|
| 冻结事件 | 200 |
| 唯一event_id | 200 |
| 唯一symbol | 112 |
| post=3 / 4 / 5 | 80 / 65 / 55 |
| core=5 / 7 | 100 / 100 |
| pre=6 / 7 / 8 / 9 / 10 | 各40 |
| W范围 | 14–22 |
| Stage-A val图/标签读取 | 0 / 0 |
| holdout行物化 | 0 |
| contact sheets | 8张，每张25样本 |

所有事件来自修复后的Stage-A train split；动态完整窗口不得晚于150 bars purge后的train边界。
原始CSV继续按每张图最终需要的bar做前缀读取，未物化holdout行。

## 一审方法

Codex按8张冻结contact sheet逐图视觉复核，没有使用收益、模型置信度、未来标签或固定均线/K线
阈值。分类只回答：

1. 当前是否为Owner确认的空头语义；
2. 旧核心是否已经吃入明显启动K；
3. 是否太乱或没有足够干净的平台，应该降为难负例候选；
4. 是否属于被明确排除的多头镜像。

61张`short_rebox_pending`没有使用统一偏移批量修正。它们进入独立队列，必须逐图选择4–7根核心；
这样不会把“框右端前移”误写成新的固定坐标模板。

## 61张逐图改框结果

流程分两步：

1. 生成7张带本地K线序号的工作板，旧框只作为被拒绝的红色参考；
2. 逐图确定启动首根与它之前的最短充分核心，从原始连续15m K线重裁动态窗口，再画橙色新框与
   红色虚线旧框对照。

| 项目 | 结果 |
|---|---:|
| 逐图边界 | 61 / 61 |
| 新核心4 / 5 / 6 / 7根 | 2 / 56 / 2 / 1 |
| post 3 / 4 / 5 | 25 / 20 / 16 |
| pre 6 / 7 / 8 / 9 / 10 | 8 / 12 / 12 / 15 / 14 |
| 新完整窗W | 14–20 |
| 新框中心比例 | 53.3%–71.9% |
| 起止位移组合 | 14种 |
| 相对旧核心，右端提前 | 1–8根 |
| 唯一event / symbol | 61 / 44 |
| 实际时间范围 | 2025-06-08 20:30 UTC – 2026-03-18 12:15 UTC |
| holdout行物化 | 0 |

这里的4–7根只是允许范围，不是把所有样本裁成固定长度；起止点来自逐图语义判断。框中心仍会
随pre/core/post组合自然变化，并非固定最右或固定正中。框后3–5根只承担短延迟确认，不进入核心。

## Owner审核页：训练短窗与未来走势双图隔离

专用确认HTML逐张并排显示：左图是训练时真正使用的短窗；右图从相同起点额外延伸48根15m K线
（12小时），紫色竖线标记训练截止，右侧未来只帮助Owner人工判断。页面支持逐张“认可新框 / 还要
改 / 剔除”，也支持浏览后全部认可并复制JSON回对话。

未来审核图单独写入`review_future_only/`和独立manifest，不进入任何训练`images/labels`目录。
生成未来图前后，61张冻结训练图的SHA逐字节一致；未来目录不存在`labels/`，最大未来时间为
2026-03-19 00:15 UTC，holdout行读取为0。

## 质量门

- 200/200完成四桶分区，索引无重复、无缺失；
- mirror=74只进入排除桶，不计入hard negative；
- 一审rebox=61先标记`rebox_required_pending_per_image_geometry`；逐图改框后更新为
  `codex_rebox_proposal_pending_owner_sample_review`，仍未晋升；
- 61/61边界proposal覆盖完整；核心宽度全部在4–7根，post全部在3–5根；
- 14种起止位移，证明不是统一坐标平移；61个新核心右端均早于旧核心右端；
- 新窗口W14–20，完整窗口结束不晚于各自冻结旧窗口；
- 协议确认与逐样本确认分开记录；
- 200/200训练与生产资格均为false；
- 未读取holdout，未修改任何阈值、成本、障碍、新鲜度或实盘配置。
- 项目测试：`pytest -q tests`为619 passed、2 skipped；改框与Owner确认页相关定向测试全部通过。

## 非适用指标

本轮未训练、未推理、未回测，因此mAP、precision/recall、AUC、置换p、收益、胜率与匹配随机
对照均不适用。一审比例不能解释为模型精度或真实市场基准率。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_owner_eth_shortdelay_review200.py

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/review_owner_eth_shortdelay_review200.py

PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_build_owner_eth_shortdelay_review200.py \
  tests/test_review_owner_eth_shortdelay_review200.py

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/rebox_owner_eth_shortdelay_review200.py

PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_rebox_owner_eth_shortdelay_review200.py

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_owner_eth_shortdelay_review61_gate.py
```

主要产物：

- `analysis/output/owner_eth_shortdelay_dynamic_review200_v1/manifest.jsonl`
- `analysis/output/owner_eth_shortdelay_dynamic_review200_v1/sheets/`
- `analysis/output/owner_eth_shortdelay_review200_codex_firstpass_v1/first_pass.jsonl`
- `analysis/output/owner_eth_shortdelay_review200_codex_firstpass_v1/representative_review200_firstpass16.png`
- `analysis/output/owner_eth_shortdelay_review200_codex_firstpass_v1/queues/`
- `analysis/output/owner_eth_shortdelay_review200_rebox_v1/indexed_manifest.jsonl`
- `analysis/output/owner_eth_shortdelay_review200_rebox_v1/proposal_manifest.jsonl`
- `analysis/output/owner_eth_shortdelay_review200_rebox_v1/workboards/`
- `analysis/output/owner_eth_shortdelay_review200_rebox_v1/proposal_boards/`
- `analysis/output/owner_eth_shortdelay_review200_rebox_v1/review_future_only/`
- `analysis/html/p1_owner_eth_shortdelay_review61_owner_gate_20260811.html`

## 风险与诚实声明

- 40/61/25是Codex一审，不是Owner逐样本金标；视觉边界可能仍与Owner细微语义不一致。
- 61张改框几何已经完成，但仍是Codex proposal，不是Owner逐样本金标；不能把101张空头候选
  写成101张训练正例。
- 25张只是难负例候选；正式使用前仍要确认它们不是边界可修复的正例。
- 个别橙框可能仍比Owner心中的“完美平台”更宽或更杂；precision优先时，Owner可以把它们降为
  难负例，而不是勉强保留正例。
- 当前200张是审查样本，不是时间独立val，更不是holdout。
- 7张PNG对照板已按原始分辨率目视检查；当前应用的浏览器安全策略拒绝自动打开`file://` HTML，
  因此HTML只完成标签/表格/关键文本的结构验证，未声称完成真实浏览器视觉QA。
- 仓库根目录直接运行`pytest -q`会额外收集`external/Kronos`并因其未安装`qlib/model`而中止；
  项目自身`tests/`完整通过。该第三方依赖问题不属于本轮改框变更，也未被静默掩盖。

## 下一步

Owner目视7张逐图改框对照板，确认橙框是否符合“核心结束于启动前、框后只留3–5根确认”的方向；
若认可，再明确是否授权40个keep、61个新框与25个难负例成为正式样本标签。授权后才生成训练集与
独立时间val；随后回到完整历史池扩正例与难负例，不能直接拿200张校准包开训；不读取holdout。
