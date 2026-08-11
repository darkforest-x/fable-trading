# P2 Owner确认误报第三训练臂与独立连续Canary（2026-08-12）

## 结论先行

- Owner于2026-08-11 23:14 CST授权的第三训练臂
  `owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft`已在RTX 3060完整跑满40轮，正常退出；
  `best.pt`本地/远端SHA-256均为
  `52cd38fda253f052c3c8eb712d93557c0125dceb336fb4cd58136236dca32afe`。
- Mac在与R1逐文件相同的冻结val上独立复验，R2的P/R/mAP50/mAP50-95为
  **0.8475 / 0.7525 / 0.8774 / 0.7413**。相对R1，mAP50-95基本不变，precision、recall和
  mAP50略降；固定val不能证明误报已经解决。
- 新的非重叠pre-holdout连续块覆盖2026-05-03 12:15–23:45 UTC。R1和R2各扫描215币、
  10,105个bar endpoints、80,840个W12–19窗口；conf=0.25、NMS=0.70、去重规则完全相同。
- R2相对R1的raw detections从3,964降至3,538（**-10.75%**），去重事件从223降至195
  （**-12.56%**），折算仍为 **398.3 events/day**，93/215币触发。密度明确不合格。
- 跨模型事件配对显示：R2的195个事件中163个仍与R1落在同币核心中点±5根范围；R1独有60个，
  R2新生32个，净减少28个。R2保留了 **83.59%** 的自身事件为旧问题，只是小幅替换误报，
  不是解决泛滥。
- 裁决：**R2失败，禁止promote、部署或靠提高conf美化结果。** 531个Owner确认误报替换证明方向
  有轻微作用，但跨块泛化不足。未读取holdout、未改ACTIVE、未发TG、未下单。

## 单变量训练合同

| 项目 | R1 hard-negative臂 | R2 Owner确认误报替换臂 |
|---|---:|---:|
| train positive | 1,143 | 1,143 |
| train easy negative | 1,143 | 1,143 |
| train hard negative | 2,286 | 2,286 |
| hard组成 | 916 Owner-long + 1,370模型背景 | 531 Owner确认误报 + 852 Owner-long + 903模型背景 |
| W12–19分布 | 冻结 | 逐桶相同 |
| val | 202正 + 200 easy负 | 逐文件SHA相同 |
| 初始化/训练配方 | Stage A；40ep/batch8/seed0/AdamW 1e-4 | 完全相同 |
| 禁用增强 | flip/mosaic/mixup/HSV全0 | 完全相同 |
| 唯一变量 | — | 同W桶替换531个hard来源 |

R1与R2的804个val图片/标签文件内容树SHA均为
`7a60c784e51451c22781c31f8961e163c690c389b6311d8031cb2fd2ba93d6b3`。R2不是通过换val、
换正例数量、换W分布或换配方获得结果。

## 训练与固定val结果

| 模型 / 评估 | Precision | Recall | mAP50 | mAP50-95 |
|---|---:|---:|---:|---:|
| R1 / Mac同脚本重验 | 0.8626 | 0.7770 | 0.8980 | 0.7405 |
| R2 / epoch35记录 | 0.8578 | 0.7426 | 0.8865 | 0.7498 |
| R2 / 3060最终best复验 | 0.8365 | 0.7596 | 0.8780 | 0.7432 |
| R2 / Mac独立best复验 | 0.8475 | 0.7525 | 0.8774 | 0.7413 |
| R2−R1 / Mac | -1.51pp | -2.46pp | -2.06pp | +0.08pp |

R2训练40/40总耗时3423.32秒，曲线最佳为epoch35；last epoch的P/R/mAP50/mAP50-95为
0.8571/0.7475/0.8732/0.7312。3060与Mac复验接近，没有设备或版本漂移。mAP50-95与R1几乎相同，
但连续密度只改善约一成，再次证明平衡val mAP不是本项目的晋升门。

## 新独立Canary合同

| 项目 | 冻结值 |
|---|---|
| 历史R1 canary endpoints | 2026-05-03 00:15–12:00 UTC |
| 本轮新 endpoints | 2026-05-03 12:15–23:45 UTC，0 endpoint重叠 |
| 时长 | 11.75小时 / 每币47个bar endpoints |
| 币种 | 215 / 215，无stale |
| bar endpoints / 模型 | 10,105 |
| W12–19 exposures / 模型 | 80,840 |
| conf / NMS IoU | 0.25 / 0.70，未调参 |
| 事件去重 | 同币核心中点±5根；保留首次跨门决策 |
| 最大物理读取时间 | 2026-05-03 23:45 UTC |
| holdout边界 | 2026-05-04 00:00 UTC |
| holdout读取 | 0；canonical data写入0 |

早段K线仅作为120根均线和短窗渲染所需的因果上下文；本轮被计分的decision endpoints与旧canary
完全不重叠。该块没有参与R2训练、hard-negative选择或best epoch选择。R1与R2由四个3060分片
同时扫描，合并器验证币种零交叉、同权重SHA和同协议后才生成对照。

## 连续密度结果

| 指标 | R1 | R2 | R2−R1 |
|---|---:|---:|---:|
| raw detections | 3,964 | 3,538 | -426（-10.75%） |
| raw / 1000窗口暴露 | 49.035 | 43.765 | -10.75% |
| deduplicated events | 223 | 195 | -28（-12.56%） |
| events / 1000 bar endpoints | 22.068 | 19.297 | -12.56% |
| 全市场折算events/day | 455.5 | 398.3 | -12.56% |
| 触发币种 / 215 | 98 | 93 | -5（-5.10%） |
| 触发币单币事件 median/p90/max | 2 / 4 / 6 | 2 / 4 / 5 | 基本不变 |
| 核心4–7根占比 | 91.93% | 97.44% | +5.51pp |
| 首次延迟3–5根占比 | 96.86% | 97.95% | +1.09pp |
| 首次conf median / p90 | 0.3667 / 0.6002 | 0.3298 / 0.5287 | 整体下移 |
| peak conf median / p90 | 0.5629 / 0.8090 | 0.4846 / 0.7845 | 整体下移 |

R2没有通过破坏核心4–7根或3–5根确认几何换取静默；这两项反而略好。但93个币、195个事件、
398.3 events/day仍与“少而准”相差很远。置信度整体下移也不能成为事后抬高阈值的理由；conf仍保持
0.25，任何阈值实验必须另立预注册和独立块。

## 跨模型事件重合审计

| 类别 | 事件数 |
|---|---:|
| R1与R2匹配事件 | 163 |
| 仅R1存在（被R2抑制） | 60 |
| 仅R2存在（新生触发） | 32 |
| R2事件中旧问题保留率 | 83.59% |
| 事件集合Jaccard | 63.92% |

匹配规则与事件去重一致：同币且`abs(core_mid_i差) <= 5 bars`，一对一优先最小核心距离，再最小
decision距离。匹配事件核心距离median/p90/max为1/3/5根。R2确实抑制了60个R1事件，但同时产生
32个不同事件，说明模型没有学到足够广泛的“什么不是目标”，而是在主动学习样本附近重新划边界。

## 必报指标状态

- val AUC、置换检验p、top-decile毛/净收益、胜率、单特征基线：N/A；本轮是YOLO检测密度实验，
  没有LightGBM排序或交易收益裁决。
- 匹配随机对照：N/A；没有根据未来收益筛选事件，也没有把检测事件写成订单。
- event precision / recall：N/A；新canary没有独立Owner真值。密度已经足以判生产失败，但不能把
  195个事件自动全部叫做假阳性。

## 解读

1. **Owner确认hard比旧模型背景更可信，但531个只替换23.23%的hard槽位，跨块净降12.56%。**
   当前错误不是少数可记忆的局部形态，而是更广泛的视觉决策边界问题。
2. **mAP保持、密度失败。** R2的mAP50-95与R1只差+0.08pp，连续事件仍398/day；继续围绕val
   优化不会回答生产问题。
3. **净事件数会掩盖边界迁移。** 只报223→195会漏掉“压掉60、又新生32”；以后每个主动学习臂
   都必须报告retained/suppressed/new三分解。
4. **下一轮不能直接复制R2再加更多同类hard。** 先对163保留、32新生、60被抑制三层做特征和
   语义审计，确认是覆盖不足、正类过宽、渲染丢信息，还是单类YOLO目标本身不可分。

## 风险与诚实声明

- 新canary只有11.75小时，不能代表所有市场状态；但398.3 events/day已经高到无需读取holdout即可
  判定不可生产。
- 主动学习审核页允许Owner查看未来48根作为人工语义参考；R2训练图和本轮canary输入都没有未来K线。
- 本轮没有调conf、框后延迟、W分布或事件去重；没有读取holdout、修改ACTIVE/frozen、promote、
  部署、发TG或下单。
- R2权重保留用于失败归因与后续对照，不能被命名为owner_best或production模型。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
export PYTHONPATH=.:../yoyo-trading

# Mac固定val同脚本复验R1/R2
.venv/bin/python scripts/eval_owner_short_gold_center_model.py \
  --weights analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft/weights/best.pt \
  --data datasets/owner_short_gold_center_hardneg_r2_ownerconfirmed/data.yaml \
  --out analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft/mac_val_metrics.json \
  --device mps --imgsz 960 --batch 8 --workers 2

# 物理有界的新pre-holdout快照
.venv/bin/python scripts/backtest_owner_short_gold_center_recent.py historical \
  --out-dir analysis/output/owner_short_gold_center_preholdout_canary_20260503_pm_v1 \
  --end 2026-05-03T23:45:00Z --context-bars 420 \
  --evaluation-scope preholdout_postval_canary

# R1/R2分别在3060按shard-index 0/1运行，固定hours=11.75、W12–19、conf=0.25、iou=0.70；
# 取回后用同一脚本merge，再运行严格合同对照：
.venv/bin/python scripts/compare_owner_short_canary.py \
  --r1-dir analysis/output/owner_short_gold_center_preholdout_canary_20260503_pm_v1/merged_r1 \
  --r2-dir analysis/output/owner_short_gold_center_preholdout_canary_20260503_pm_v1/merged_r2 \
  --snapshot-summary analysis/output/owner_short_gold_center_preholdout_canary_20260503_pm_v1/fetch_summary.json \
  --out analysis/output/owner_short_gold_center_preholdout_canary_20260503_pm_v1/r1_r2_comparison.json

.venv/bin/pytest -q tests
python3 scripts/md_to_html.py \
  analysis/p2_owner_short_gold_center_hardneg_r2_canary_20260812.md --out-dir analysis/html
```

## 下一步

停止R2晋升，也不立即开第四训练臂。先把新canary按“R1/R2共同保留163、仅R2新生32、仅R1被
抑制60”分层，抽取真正送入模型的因果图做Owner审核和特征审计。若共同保留层仍大多是不对，说明
当前hard覆盖或单类目标表达不足；若共同保留层真目标占比高，则问题转为事件去重/候选生成，不应
继续把它们当hard negative。该诊断完成前不改阈值、不读取holdout。
