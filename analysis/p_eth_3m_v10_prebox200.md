# ETH 3m · v10 有框预标 200 张

日期：2026-07-29  
状态：HTML 已生成；Label Studio 任务已后台准备但**未导入**；未耗 holdout。

## Owner 最终交付口径

- 只展示 v10 实际检出并画框的 ETH 3m 图片，共 200 张。
- 200 张全部显示红框；无随机/数值/未来发现候选，无隐藏框，无盲重复。
- 单张白底图，风格对齐 Owner 提供的 Claude 参考图：K线 + 六条均线 + 红框 + 信号竖虚线 + 右侧未来 3 小时。
- 不画入场、止盈、止损、箭头、收益文字、成交量副图或背景填充。
- 形态框与 outcome 后台分栏保存；HTML 不展示置信度和结果数字。

## 结果

| 项目 | 结果 |
|---|---:|
| 任务数 | 200 |
| v10 conf 门 | 0.30 |
| conf 最低 / 中位 / 最高 | 0.303 / 0.630 / 0.889 |
| 框右沿 = exact tip | 200/200 |
| 因果模型窗口 | 200 根原生 3m bar |
| 人工未来窗 | 60 根 = 3 小时 |
| HTML 大小 | 约 15.0 MiB |

扫描门允许 tip/tip-1/tip-2，但最终选出的 200 张全部自然落在 exact tip。为忠实交付“所有有框的图”，只排除完全相同的同一根，不再把同一趋势相邻开火合并成事件。

## 数据与 holdout

- 数据：`data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv`
- 脚本在任何推理或 outcome 计算前，先物理裁掉 `>= 2026-05-04 00:00 UTC` 的行。
- 200 张的最大 `future_end = 2026-05-01 23:54 UTC`，严格早于 holdout。
- `holdout_consumed=false`；本包可用于开发期人工标注。

## 产物

- 手机 HTML：`datasets/eth_3m_v10_prebox200/v10_prebox200_mobile.html`
- 200 张人审图：`datasets/eth_3m_v10_prebox200/review_images/`
- 200 张干净因果训练图：`datasets/eth_3m_v10_prebox200/causal_images/`
- 后台 manifest：`datasets/eth_3m_v10_prebox200/manifest.csv`
- 待导入任务：`datasets/eth_3m_v10_prebox200/label_studio/tasks.json`
- 双标签界面：`datasets/eth_3m_v10_prebox200/label_studio/label_config.xml`
- 生成器：`scripts/build_eth_3m_v10_prebox200.py`

## Label Studio 后台契约

- A · `shape = valid / invalid / uncertain / bad_data`，并编辑/删除 v10 预测框。
- B · `outcome = strong_drop / weak_drop / fail / rebound / outcome_uncertain`。
- 每个任务恰有一个 v10 prediction；200/200 causal PNG 与 200/200 review JPEG 已存在。
- Owner 未确认 HTML 前不执行导入。

## 复现

```bash
MPLCONFIGDIR=/tmp/fable-mpl-cache .venv/bin/python \
  scripts/build_eth_3m_v10_prebox200.py \
  --device cpu --batch-size 8 --conf 0.30 \
  --primary-anchors 12000 --min-gap-bars 1
```

## QA 与诚实声明

- `manifest.csv` 200 行、task_id 200 个唯一值；Label Studio JSON 200 个任务、200 个预测框。
- HTML 内嵌 200 张 JPEG，无外部图片依赖；页面是纯静态长图，0 个按钮、0 段 JavaScript，手机端直接下滑查看 200 张。
- 抽查 task 001 / 100：白底、红框、均线、竖虚线和未来 3h 显示正确，无交易线和背景填充。
- v10 是 15m 模型，迁移 ETH 3m 仍属 OOD；这些是 teacher prelabels，不是金标，也不代表精度或收益。
- 本轮不训练、不调阈值、不评估、不 promote、不改 ACTIVE、不接实盘。
