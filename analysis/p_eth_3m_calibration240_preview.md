# ETH 3m 双视图 240 张校准包预览

日期：2026-07-29  
状态：HTML 已生成；**尚未导入 Label Studio**；不耗 holdout。

## Owner 已冻结口径

- 人工未来窗固定为 **3 小时 = 60 根原生 3m bar**。
- 检测层保存 `shape + box`；后续结果保存 `outcome`，两者字段与用途分离。
- 人工可看未来；模型输入固定为截至候选时刻的 200 根，未来像素为 0。

## 产物

- 自包含手机 HTML：`analysis/output/eth_3m_calibration240_preview/index.html`
- 私有审计清单：`analysis/output/eth_3m_calibration240_preview/private_manifest.csv`
- 汇总：`analysis/output/eth_3m_calibration240_preview/summary.json`
- 生成器：`scripts/build_eth_3m_dual_view_calibration.py`

HTML 大小约 14.5 MiB，内嵌全部 240 张 JPEG，不依赖本机图片路径。页面一次只显示一张，支持上一张、下一张和编号跳转。

## 数据与物理隔离

输入：`data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv`

- 实际可用开发数据：2026-03-13 12:15～2026-05-03 23:57 UTC
- 24,715 根连续原生 3m bar；最大间隔 3 分钟；无重复时间戳
- 最晚候选允许到 2026-05-03 20:57 UTC，保证 60 根未来全部 `< 2026-05-04 00:00 UTC`
- 实际任务最晚未来终点为 2026-05-03 23:45 UTC
- 脚本先删除全部 holdout 行，再计算指标、候选和 outcome；`holdout_consumed=false`

## 240 张组成

240 个任务 = **216 个独立事件 + 24 个盲重复**。

| 候选源（独立事件） | 数量 | 占比 |
|---|---:|---:|
| v10 exact-tip | 65 | 30.1% |
| 因果数值候选 | 54 | 25.0% |
| 未来下跌发现候选 | 43 | 19.9% |
| 分层随机背景 | 54 | 25.0% |

- v10 只保留框右沿等于最后一根的 exact-tip，未为凑数放宽条件。
- 65 个 v10 独立事件中，33 个显示预框，其余隐藏；置信度不显示。
- 候选源、未来收益数字、重复关系只在私有 manifest，HTML 不显示。
- 盲重复沿用相同的预框显示状态；24 组重复的 JPEG SHA-256 均相同。

## 标签契约

问题 A（检测层）：`valid / invalid / uncertain + box`。形态成立但后来失败，仍可以是形态正例。

问题 B（结果层）：`strong_drop / weak_drop / fail / rebound`。清单另存 1h/3h 收益、3h 最大下探与最大反抽，但 HTML 不展示这些数值，避免锚定。

## 复现

```bash
MPLCONFIGDIR=/tmp/fable-mpl-cache .venv/bin/python \
  scripts/build_eth_3m_dual_view_calibration.py \
  --device cpu --batch-size 8 --v10-probe-limit 3000
```

## QA

- 240/240 图片存在，HTML 内嵌 240 个 `data:image/jpeg`。
- 240 个 task_id 唯一；216 个 event_id 唯一；24 个盲重复组各一份重复任务。
- 盲重复像素不一致数 = 0。
- `max(future_end) < holdout_start` 为真。
- 目视抽查无预框任务与有预框任务：上图无未来；下图未来区从橙色线后开始；红框坐标映射一致。
- 生成脚本通过 `py_compile`。

## 风险与诚实声明

- 当前本机原生 3m 开发数据只有约 52 天，足够做标注语义和可学习性校准，**不足以冻结最终两年训练/验证切分**。
- downside 候选允许用未来发现事件，但未来只用于候选覆盖和独立 outcome；模型图不含未来，且它们不是自动金标。
- v10 是 15m OOD teacher，仅占候选源；红框只是可删除的建议，不是真值。
- 本轮不训练、不评估、不调阈值、不 promote、不改 ACTIVE、不接实盘。Owner 确认 HTML 后，才生成并导入 Label Studio 任务。

