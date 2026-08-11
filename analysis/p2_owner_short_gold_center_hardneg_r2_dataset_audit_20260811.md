# P2 Owner确认误报第三训练臂数据审计（2026-08-11）

## 结论先行

第三训练臂数据集已构建并通过技术检查，**尚未启动训练**。

- Owner最后一张新时间块审核页裁决为：26 target、2 rebox、172 hard negative、0 pending；协议、源SHA、200个ID和声明计数全部一致。
- 四张train-time审核页累计得到584个Owner确认hard negative，全部唯一、全部位于冻结train时间、0 Owner框保护区重叠、0 holdout，训练因果图不含未来K线。
- 为保持与第二臂严格单变量可比，本轮不改变2,286个hard总数，也不改变W12–19分布。584个确认误报中纳入531个；W18/W19超出原桶容量的53个按桶内当前模型触发置信度排序后暂存，没有复制、改W或挤占别的桶。
- 新train仍为1,143正例 + 1,143 easy negative + 2,286 hard negative，共4,572张；val仍为202正例 + 200 easy negative，共402张。
- 新hard构成为531个Owner确认误报、852个Owner-long方向反类、903个旧模型排序背景。531个确认误报占全部hard槽位23.23%。
- base正例、easy negative和完整val共5,376个图片/标签文件逐文件SHA一致；0联合SHA重复、0语义区间重复、0 train/val图片SHA交叉。
- 已冻结与第二臂完全相同的Stage A初始化和训练配方；只等待Owner单独授权3060训练，不自动promote、部署或读取holdout。

实际训练输入200张审计页：`analysis/html/p2_owner_short_gold_center_hardneg_r2_audit200_20260811.html`。页面显示的就是会送入YOLO的图片，无橙框、无预测框、无未来48根。

## 最新Owner裁决与累计参考

| train-time审核来源 | target | rebox | hard negative | 本轮纳入hard |
|---|---:|---:|---:|---:|
| 首张难负例200 | 18 | 0 | 182 | 159 |
| 正例检索100 | 45 | 0 | 55 | 54 |
| 第二张难负例200 | 25 | 0 | 175 | 168 |
| 新时间块200 | 26 | 2 | 172 | 150 |
| **合计** | **114** | **2** | **584** | **531** |

114个target和2个rebox继续作为语义/几何参考，没有加入冻结的1,143个训练正例。rebox的最终框几何尚未解决，更不能自动变成正标签。

各来源“裁决数”与“纳入数”不同，是因为最后选择必须遵守冻结W桶；并非丢弃Owner裁决。未纳入的53个仍保存在`confirmed_deferred_manifest.jsonl`，后续若重新设计窗口分布，必须作为一个新变量另立实验。

## 为什么是531，不是强塞584

确认误报的窗口分布为：

| W | 确认可用 | 原hard槽位 | 本轮纳入 | 暂存 |
|---:|---:|---:|---:|---:|
| 12 | 152 | 348 | 152 | 0 |
| 13 | 76 | 580 | 76 | 0 |
| 14 | 22 | 478 | 22 | 0 |
| 15 | 86 | 228 | 86 | 0 |
| 16 | 6 | 392 | 6 | 0 |
| 17 | 65 | 136 | 65 | 0 |
| 18 | 68 | 42 | 42 | 26 |
| 19 | 109 | 82 | 82 | 27 |
| **合计** | **584** | **2,286** | **531** | **53** |

若把584个全部塞入，W18至少要从42变成68、W19至少从82变成109，同时必须减少其他W，共改变53个窗口长度槽位。那样“hard来源”和“窗口分布”会同时变化，无法判断训练结果来自哪个变量。

本轮在每个W内部按当前模型真实触发的`event_conf_max`降序选取，未选择新的生产阈值。纳入531个的置信度min/p10/p50/p90/max为0.2506/0.3092/0.5171/0.7894/0.9412。暂存53个仅来自W18/W19，范围0.2510–0.3338；它们在各自桶内低于入选边界，不代表全局低于所有入选样本。

## 第二臂与第三臂同表对照

| 配置 | train正 | easy负 | hard负 | hard组成 | W分布 | val | 状态 |
|---|---:|---:|---:|---|---|---:|---|
| 第二臂R1 | 1,143 | 1,143 | 2,286 | 916 Owner-long + 1,370 model background | 冻结 | 202/200 | 已训练；连续密度失败 |
| 第三臂R2 | 1,143 | 1,143 | 2,286 | 531确认误报 + 852 Owner-long + 903 model background | 与R1逐桶相同 | 202/200 | 数据就绪；未训练 |

R2按W的最终构成为：

| W | 确认误报 | 保留Owner-long | 保留模型背景 | 合计 |
|---:|---:|---:|---:|---:|
| 12 | 152 | 89 | 107 | 348 |
| 13 | 76 | 195 | 309 | 580 |
| 14 | 22 | 211 | 245 | 478 |
| 15 | 86 | 121 | 21 | 228 |
| 16 | 6 | 165 | 221 | 392 |
| 17 | 65 | 71 | 0 | 136 |
| 18 | 42 | 0 | 0 | 42 |
| 19 | 82 | 0 | 0 | 82 |
| **合计** | **531** | **852** | **903** | **2,286** |

构建时另发现一组旧R1背景与新确认误报具有相同币种、开始时间、结束时间和W，但因渲染链版本不同，图片SHA不同。它已按“语义区间重复”排除旧行并用同桶另一行补齐，最终语义重复为0。

## 数据与无前视检查

| 检查 | 结果 |
|---|---:|
| train图片 | 4,572 |
| val图片 | 402 |
| manifest图片/标签缺失 | 0 |
| manifest SHA不符 | 0 |
| hard非空标签 | 0 |
| 联合image+label SHA重复 | 0 |
| hard语义区间重复 | 0 |
| train/val图片SHA交叉 | 0 |
| base逐文件SHA一致 | 5,376 / 5,376 |
| Ultralytics `check_det_dataset` | PASS |
| hard最晚结束时间 | 2026-03-13 13:15 UTC |
| holdout读取 | 0 |

人工审核时Owner可以看独立的未来48根走势，这是**标签信息**；训练图片只复制`causal_input_path`，不复制`future_review_path`或`causal_review_path`。所以模型输入仍只包含decision bar及之前的数据。该差异已在manifest中明确记录为`owner_label_future_review_available=true`与`future_data_in_training_image=false`。

## 冻结训练配方审计

| 项 | 第三臂冻结值 |
|---|---|
| 初始化 | Stage A best，SHA `c0e94f47df125e298b044d9f10acd0b8e4f525ccd6143ce34f8d174af802bf1a` |
| 模型 | YOLO11s延续微调 |
| imgsz / batch / seed | 960 / 8 / 0 |
| epochs / patience | 40 / 10 |
| 优化器 | AdamW，lr0=1e-4，lrf=0.01，warmup=0.5 |
| 方向破坏增强 | fliplr/flipud/mosaic/mixup/copy-paste/HSV全部0 |
| 其余既有轻微几何项 | translate=0.02，scale=0.1，degrees/shear/perspective/erasing=0 |
| rect | true |
| 自动promote | false |

训练器和3060包装脚本SHA均已写入`training_preregistration.json`；包装脚本`bash -n`通过。训练run预注册名为`owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft`。本报告不把数据集就绪写成模型已经修好。

## 项目测试与机器产物

- 全量测试：**674 passed，2 skipped**。
- 数据摘要SHA：`6220e04cf22a7e7ab40d0404113ea7308b44b4e5fe60841a1f186d28cbcb5f54`。
- hard manifest SHA：`0e85e23e4f2f972cf1999b507a28ab99eaf2986a2bc3b51e07a5de64e85da8c0`。
- 暂存manifest SHA：`4554efe06cbe67cc3f66609f9ee66e92e6288752c3202c931391afac29b665fa`。
- 训练预注册SHA：`ab2ecfbea5ac6d5ae1b1dca210b2cabf138f642410e0f517891d3ac6d26f7d9c`。
- 200张实际输入审计HTML SHA：`2bd614084a8a09535da1b2c2a7ef052000524502e57ac545ebbf2e2d351e5a81`；200张卡片、200张图片引用、0缺失。

## 必报指标状态

本轮只构建数据，没有训练和收益回测。

| 指标 | 本轮结果 | 原因 |
|---|---|---|
| YOLO val P/R/mAP | N/A | 新模型尚未训练 |
| 连续窗口event density / FP1000 | N/A | 等训练后在新冻结pre-holdout块验证 |
| val AUC / 置换检验p | N/A | 不是L2排序实验 |
| top-decile毛/净收益、胜率 | N/A | 不产生交易结果 |
| 单特征基线 / 匹配随机对照 | N/A | 本轮不作方向性收益结论 |

## 风险与诚实声明

- 531个Owner确认误报显著提高了hard证据质量，但只替换23.23%的hard槽位，**不能保证**训练后密度一定达标。
- 审核池是当前模型触发后的主动学习偏置样本，不是市场总体分布；训练后仍须在从未用于选样的pre-holdout时间块验证。
- W18/W19确认误报集中说明模型错误分布与窗口长度有关。本轮为可比性冻结W分布；以后若要改变W采样，必须另立单变量实验。
- 当前val仍是原Owner正例与easy背景，mAP可能很高但不足以裁决生产资格。晋升仍只认真正独立事件精度、连续密度与最终Owner门。
- 未读取holdout、未修改ACTIVE/frozen、未部署、未发TG、未下单。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
export PYTHONPATH=.:/Users/zhangzc/yoyo-trading

.venv/bin/python scripts/build_owner_short_gold_center_hardneg_r2.py --mode assemble
.venv/bin/python scripts/build_owner_short_gold_center_hardneg_r2.py --mode audit

.venv/bin/python -m pytest -q tests
bash -n scripts/train_w20_midbox_on_3060.sh

python3 scripts/md_to_html.py \
  analysis/p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.md \
  --out-dir analysis/html
```

数据集构建器拒绝覆盖既有输出；从零复现时应在独立临时输出路径运行，或先由Owner明确批准清理当前生成物。

## 下一步

当前唯一下一步是Owner单独授权第三臂3060训练。授权后严格执行已冻结命令；训练完成先做Mac固定val复验，再扫一个从未用于本轮选样的pre-holdout连续时间块。若连续密度仍高，继续主动学习，不通过调高conf掩盖。

