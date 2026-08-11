# P2 新训练时间块难负例扩挖 200 张报告

## Technical Summary

第二张难负例扩充页经 Owner 裁决为 25 target / 175 hard negative 后，train-time Owner 参考累计到 88 正 / 412 负。由于 412 只占既有 2,286 hard 槽位的 18.02%，本轮没有开训，而是按原计划转向五个从未使用的冻结 train 时间块。

- 五块合计 881 symbol-block、42,288 bar endpoints、338,304 个 W12–19 因果窗口。
- 固定第二臂权重、conf=0.25、NMS IoU=0.7、5-bar 事件去重，共 10,533 条 raw 检测，去重为 589 个事件。
- 剔除 22 个触碰任一 Owner 框 ±12 bars 的事件，安全候选池 567。
- 使用累计 1,308 个正例参考与 666 个负例参考，只读取 decision bar 及之前的 OHLC、六条均线和预测框排序。
- 固定选出 200 个唯一事件、131 个币。C02 真实只有 12 个去重事件，因此全量保留，缺额确定性均分到其余四块，配额为 47/12/47/47/47；没有复制、降阈值或静默换时段。
- 选样完成后才附加未来 48 根人工审核图。600/600 图像存在、200/200 未来图完整。
- 0 holdout、0 labels、0 training-eligible、0 production 变更；未训练、未 promote。

Owner 审核入口：`analysis/html/p2_owner_short_train_hardneg_newblocks200_v3_20260811.html`。

## 五个新时间块扫描结果

| 时间块 | 币种 | endpoints | W12–19 exposures | raw | 去重事件 | events/1000 endpoints |
|---|---:|---:|---:|---:|---:|---:|
| C01 2025-06-15 | 149 | 7,152 | 57,216 | 3,577 | 173 | 24.189 |
| C02 2025-08-15 | 157 | 7,536 | 60,288 | 52 | 12 | 1.592 |
| C03 2025-10-15 | 170 | 8,160 | 65,280 | 1,059 | 94 | 11.520 |
| C04 2025-12-15 | 197 | 9,456 | 75,648 | 983 | 66 | 6.980 |
| C05 2026-02-15 | 208 | 9,984 | 79,872 | 4,862 | 244 | 24.439 |
| **合计** | **881 symbol-block** | **42,288** | **338,304** | **10,533** | **589** | **13.928** |

C02 与 C05 的事件密度相差 15.35 倍。该差异不是通过阈值或候选后处理制造的，五块使用完全相同的检测合同。它说明模型误报/触发分布强烈依赖行情状态，第三臂必须保留多时间块覆盖，不能只从最密集时段挖负例。

## 稀疏块不硬凑，缺额确定性重分配

初始目标是每块 40 张，但 C02 总共只有 12 个去重事件。为了保持单变量纪律，本轮没有采取以下做法：

- 不降低 conf；
- 不改变 W12–19 或 5-bar 去重；
- 不复制 C02 事件；
- 不静默替换为另一个“更好凑数”的日期。

规则是：每块先取 `min(40, 安全可用数)`；不足总数 200 的缺额，按 C01→C05 固定顺序 round-robin 分配给仍有容量的块。最终实际配额由 summary 质量门重新计算：

| 时间块 | 审核页数量 |
|---|---:|
| C01 | 47 |
| C02 | 12 |
| C03 | 47 |
| C04 | 47 |
| C05 | 47 |
| **合计** | **200** |

## 累计参考与选样合同

| 参考来源 | 正例 | 负例 | 能否直接回流训练 |
|---|---:|---:|---|
| 冻结 train Owner 金标 | 1,143 | 0 | 正例已冻结 |
| post-val Owner 语义参考 | 77 | 254 | 否，只作距离参考 |
| 三张 train-time Owner 审核页 | 88 | 412 | 后续 dataset builder 审计后才可 |
| **距离参考合计** | **1,308** | **666** | 本轮仅用于因果排序 |

每个候选把真实 W12–19 窗口插值到固定 19 点，使用 open/high/low/close 与 SMA/EMA 20/60/120；价格以 decision bar close 中心化，并按可见范围归一化。另加入窗口长度、预测核心长度、确认延迟和框几何。特征函数只切到 `decision_time`，单测验证改变 decision 后的数据不会改变向量。

hard-negative affinity 定义为“到正例邻域距离 − 到负例邻域距离”；数值仅用于审核优先级，不是标签。最终 200 张 affinity p10 / median / p90 = -0.465 / 0.694 / 4.711。

## 质量门与血缘

| 质量门 | 结果 |
|---|---:|
| 200 个唯一事件 | PASS |
| 动态分块配额与真实容量一致 | PASS |
| 与此前 500 个 train-time 已审事件零重复 | PASS |
| 全部 decision 在冻结 train 内 | PASS |
| 未来审核终点也在冻结 train 内 | PASS |
| 0 Owner 框 ±12 bars 重叠 | PASS |
| 选样无未来 K | PASS |
| 600/600 图像存在 | PASS |
| 200/200 未来图完整 48 根 | PASS |
| 自动 training-eligible | **0** |
| labels 创建 | **0** |
| holdout 读取 | **0** |
| 项目测试 | **668 passed / 2 skipped** |

关键 SHA256：

- 权重：`029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537`
- 安全候选池：`50c643c74fcd83c73f5c570a87d602ab8708c150bc1cccee640f83bbe441424c`
- 选择结果：`78771b7645f57e172f4024f2b4f644305ba8408ab4a9cfd1dfe62ff4b25c7801`
- 审核 manifest：`21700589b7b4c54a40933c66b8b7d2f63a6dc072f3936d6b6ab4baa0a12aba2e`
- 机器摘要：`1dd129859a4d3c85f8bdf4f7aa8b7a169a1949c020cab5d341b6538e66ea79b7`
- 审核 HTML：`d286658c35bd8f21617a1da43b55efdde7204f96b809aa33ee1821a9885aba16`

## HTML 交付检查

静态检查确认：200 张审核卡、401 个页面内图片引用（每张 causal review + future review，另含弹窗占位）、0 缺失资源；磁盘上三类图像总计 600 张。页面含 `1/2/3/Z` 快捷键、分类后自动下一张、撤销、筛选和完整 JSON 导出。

本机浏览器控制接口的安全策略拒绝自动导航到 `file://`，因此本轮没有伪装成“浏览器交互测试通过”；没有绕过该策略另起本地服务。页面生成器与此前已实际使用的审核页相同，静态结构和资源门通过，Owner 仍需直接打开 HTML 做最终人工使用确认。

## 必报模型与交易指标状态

本轮没有训练新模型，也没有做收益回测。

| 指标 | 本轮结果 | 原因 |
|---|---|---|
| val AUC | N/A | YOLO 主动学习选样，不是 L2 排序模型 |
| 置换检验 p | N/A | 无收益排序 |
| top-decile 毛/净收益 | N/A | 无交易标签 |
| 胜率 | N/A | 无交易回测 |
| 单特征基线 | N/A | 本轮只做形态距离排序 |
| 匹配随机对照组 | N/A | 不作方向性收益结论 |

## 风险与诚实声明

- 这 200 张仍是难负例偏置主动学习样本，Owner 最终判错比例不能冒充全市场误报率。
- 当前只审模型已触发事件，没有漏检分母，不能报告 recall。
- post-val 的 254 负例没有回流 train；只作为形态参考，符合时间切分。
- C02 的稀疏性说明固定每块 40 不是数据事实；动态配额保留了该块，但其统计权重低于其余块，正式 dataset builder 仍需按时间块/W桶控制训练采样。
- 即使新 200 全部是难负例，累计也只有 612 个，仍远少于 2,286 hard 槽位。第三臂到底采用“部分替换”还是继续扩挖，必须在 Owner 裁决和完整数据审计后决定。
- 第二臂权重继续禁止 promote；未修改 ACTIVE、阈值、训练配方或生产配置。

## 下一步

Owner 在 HTML 中逐张审核：`1=对`、`2=框偏`、`3=不对`，`Z=撤销`。本页目标是收集误报，因此多数按 3 是预期，并不代表页面失败。

审核完成后：

1. 冻结源 SHA、200 ID 和裁决计数；计算累计唯一 hard-negative 及时间块/币种/W桶覆盖。
2. 若覆盖仍不足，继续扩新 train 块；不得复制难负例凑 2,286。
3. 若覆盖足够，构建第三臂候选数据集，保持正例、easy negative、冻结 val 和全部训练配方不变，只改变 hard-negative 组成/替换比例。
4. 数据集和训练脚本审计完成后，单独请求 Owner 授权 3060 训练；当前审核授权不等于训练授权。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
export PYTHONPATH=.:/Users/zhangzc/yoyo-trading

blocks=(C01_20250615 C02_20250815 C03_20251015 C04_20251215 C05_20260215)
scan_ends=(2025-06-15T12:00:00Z 2025-08-15T12:00:00Z 2025-10-15T12:00:00Z 2025-12-15T12:00:00Z 2026-02-15T12:00:00Z)
audit_ends=(2025-06-16T00:00:00Z 2025-08-16T00:00:00Z 2025-10-16T00:00:00Z 2025-12-16T00:00:00Z 2026-02-16T00:00:00Z)

for i in 1 2 3 4 5; do
  block=${blocks[$i]}
  .venv/bin/python scripts/backtest_owner_short_gold_center_recent.py historical \
    --out-dir analysis/output/owner_short_train_hardneg_blocks_v2/$block/scan_snapshot \
    --end ${scan_ends[$i]} --context-bars 420 --evaluation-scope train_hardneg_mining
  .venv/bin/python scripts/backtest_owner_short_gold_center_recent.py historical \
    --out-dir analysis/output/owner_short_train_hardneg_blocks_v2/$block/audit_snapshot \
    --end ${audit_ends[$i]} --context-bars 468 --evaluation-scope train_hardneg_mining
  .venv/bin/python scripts/backtest_owner_short_gold_center_recent.py scan \
    --snapshot-dir analysis/output/owner_short_train_hardneg_blocks_v2/$block/scan_snapshot/kline_snapshot \
    --out-dir analysis/output/owner_short_train_hardneg_blocks_v2/$block/merged \
    --weights analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_hardneg_r1_ft/weights/best.pt \
    --hours 12 --window-min 12 --window-max 19 --conf 0.25 --iou 0.7 \
    --imgsz 960 --device mps --batch 32 --evaluation-scope train_hardneg_mining
done

.venv/bin/python scripts/build_owner_short_train_hardneg_newblocks_review.py
.venv/bin/python -m pytest tests -q
python3 scripts/md_to_html.py \
  analysis/p2_owner_short_train_hardneg_newblocks200_v3_report_20260811.md \
  --out-dir analysis/html
```

3060 实跑把每块按 `--shard-count 4` 分片，完成后用同一脚本的 `merge` 子命令合并；合并事件与单进程合同相同。

## 产物

- Owner 审核 HTML：`analysis/html/p2_owner_short_train_hardneg_newblocks200_v3_20260811.html`
- 本报告 HTML：`analysis/html/p2_owner_short_train_hardneg_newblocks200_v3_report_20260811.html`
- 安全候选池：`analysis/output/owner_short_train_hardneg_newblocks200_v3/candidate_pool.jsonl`
- 200 张选择记录：`analysis/output/owner_short_train_hardneg_newblocks200_v3/selected_candidates.jsonl`
- 审核 manifest：`analysis/output/owner_short_train_hardneg_newblocks200_v3/review_manifest.jsonl`
- 机器摘要：`analysis/output/owner_short_train_hardneg_newblocks200_v3/summary.json`
