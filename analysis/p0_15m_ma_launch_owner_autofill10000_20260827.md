# P0：15m 严格均线密集启动 10,000 张自动样例包（2026-08-27）

## 结论

已完成 **10,000 张**严格 15 分钟形态图，不是只能生成 50 张。最终为 **5,000 LONG + 5,000 SHORT**，每张都是 **1280×742 原始 PNG、恰好一个红框、框宽 4 或 5 根 K**；10,000 个事件、图片 SHA 和框身份均唯一。完整 HTML 分成 100 页，每页 100 张，并附 100 张等距抽样总览。

这批继续冻结 Owner 已接受的 v7 五十张作为参考族，形态门和最大相似度 `0.5` 没有放宽。扫描 17,186,076 根严格 pre-holdout 15m K 线后，得到 14,117 个唯一核心端点；同币同方向固定一小时事件 NMS 后剩 11,381 个，其中 LONG 5,146、SHORT 6,235，最终选出各 5,000。也就是说，**10,000 已经接近当前冻结标准的多头容量上沿，多头只剩 146 个严格余量**。

机器与独立文件复核均通过：10,000/10,000 图片实际可解码，尺寸和红框像素逐张吻合 manifest；100 个 HTML 页面中的 10,000 条图片链接全部存在；holdout OHLCV 读取/落盘为 0，YOLO label、训练、3060 作业、ACTIVE/frozen、forward、部署和交易状态全部未改。

入口：

- 全量画廊：`experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/results/public/index.html`
- 100 张总览：`experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/results/contact_sheet_sample100.jpg`
- 精确 manifest：`experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/results/review_manifest.jsonl`
- manifest SHA256：`60bb7350c446ee1a406efbd48a4d7e7426ac4838e35b901bee3073019b327e76`

## 本轮口径

Owner 原话是“都还可以，只能生成50张吗，搞10000张”。本轮把它解释为：v7 已交付 50 张是被接受的**参考形态族**，可用相同语义自动扩充到 10,000 张；这不等于 Owner 逐张确认了新增 10,000 个核心边界，也不授权把它们直接转成训练标签。

冻结项：

- 参考：v7 的 50/50 已接受样例，方向归一化但绝不倒放时间；
- 核心：4–5 根 K，核心右端仍为 canonical anchor 的 `-2`；
- 相似度：14 个归一化形态/序列量，最大距离 `0.5`；
- 形态门：MA 包络、核心实体/进度、`+1/+2/+3/+5` 释放、MA 斜率和价格贴近 MA 的全部 v7 阈值；
- 去重：同币、同方向、固定一小时事件簇只保留距离最小的一个；
- 渲染：前文 10–14 根、后文 4–6 根作确定性分散，每张一个 4–5 根红框；
- 安全：holdout 禁读、无 label、无训练、无 promote、无生产写入。

## 数据来源与时间纪律

| 来源 | 有效源/币 | 15m 行数 | 月档 | 精确重复 1m 行 | 剔除不完整 15m 组 | holdout 行 |
|---|---:|---:|---:|---:|---:|---:|
| 冻结现有 source audit | 237 | 10,147,779 | 不适用 | 不适用 | 不适用 | 0 |
| OKX 官方月档 2023-07…2025-05 | 109/192 | 4,665,568 | 1,631 | 3,712 | 43 | 0 |
| OKX 官方补档 2021-09…2023-06 | 44/192 | 2,372,729 | 820 | 0 | 160 | 0 |
| **扫描合计** | **390 个文件源** | **17,186,076** | **2,451** | **3,712** | **203** | **0** |

OKX 月档文件名按 UTC+8 月历解释，再按 UTC 15 分钟边界聚合。只有同一时间戳的整行内容完全一致时才删除重复分钟；任何冲突重复会 fail closed。补档和原月档时间段互不重叠。最终核心时间范围为 `2021-09-03T00:00:00Z` 至 `2026-05-03T18:15:00Z`，严格早于仓库 holdout `2026-05-04T00:00:00Z`。

## 容量失败与修订记录

构建器三次在渲染前 fail closed，均没有留下半成品交付或放宽形态：

1. 首轮在 2023-07 之后的历史上，事件 NMS 后 LONG 只有 4,494，离 5,000 差 506；因此补入同一 OKX 官方源、同一冻结币种宇宙的 2021-09…2023-06 历史。
2. 补档后严格 LONG 总数已够，但我额外加的“所有币合计每 UTC 小时每方向只能一个”只有 2,965 个信号小时，数学上不可能填 5,000。这个约束单位错误，已删除；同币同方向的一小时 NMS 保留。
3. 十个时间桶每桶精确 500 的自加约束也与市场实际产量矛盾，可达 LONG 桶数是 `[418,456,495,420,500,500,500,500,487,500]`。改为每桶至少 300，再按最小距离全局补足，同时保留每币/每天上限。

以上修订都只改数据覆盖或选择分布，没有改参考族、形态阈值、相似度、核心宽度、同币事件 NMS 或渲染语义。对应通用结论已写入 `docs/learnings/`。

## 扫描与选择结果

| 指标 | v7 已交付 50 | 本轮 10,000 |
|---|---:|---:|
| 参考族 | Owner #42/#44 两个方向参考 | Owner 接受的 v7 全部 50 张 |
| 输出 | 25 LONG + 25 SHORT | 5,000 LONG + 5,000 SHORT |
| 严格池（事件 NMS 后） | 106（旧候选池口径） | 11,381 |
| 方向池 | 25/25 交付 | LONG 5,146 / SHORT 6,235 |
| 核心宽度 | 4根 22 / 5根 28 | 4根 5,153 / 5根 4,847 |
| 唯一币种 | 47 | 229 |
| 核心时间 | 截止 2026-04-13 | 2021-09-03…2026-05-03 |
| 距离（最小/中位/最大） | 未作同表冻结 | 0.0000 / 0.3960 / 0.4954 |
| 图片尺寸 | 1280×742 | 1280×742 |
| 每图红框 | 1 | 1 |
| 标签/训练 | 0 / 0 | 0 / 0 |

最终时间桶真实计数：

- LONG：`[406,439,484,409,500,574,578,608,468,534]`；每币最多 64、每天最多 73；
- SHORT：`[423,417,445,390,501,500,564,597,713,450]`；每币最多 66、每天最多 59。

框在画面中的位置没有固定：前文 10/11/12/13/14 根分别为 1,941/2,036/2,028/1,987/2,008 张，后文 4/5/6 根分别为 3,276/3,360/3,364 张。这个分布避免所有红框落在同一横坐标，但不改变核心本身。

## 像素与 HTML 验收

| 验收项 | 结果 |
|---|---:|
| manifest 行 / PNG 文件 | 10,000 / 10,000 |
| 实际唯一图片 SHA | 10,000 |
| 实际尺寸 1280×742 | 10,000 / 10,000 |
| 实际红框像素与 manifest 一致 | 10,000 / 10,000 |
| 每图恰好一个框 | 10,000 / 10,000 |
| 框包含核心 wick 与六条 MA | 10,000 / 10,000 |
| HTML 分页 | 100 |
| HTML 可解析图片链接 | 10,000 / 10,000 |
| YOLO `.txt` | 0 |
| 输出体积 | 526 MB |

抽样总览为 2,560×1,800 JPEG，SHA256 `61388f5e0a7b94a7ce3f1ace791bdfa99dcf11d0ff3e476ac6e779a9de5d6f4e`。全量索引 SHA256 `29c468fd6ff161a38d1bec7cd57975e7ed8b6d0879d83ab86c3335176297e906`。

## 零假设对照

本轮是 P0 形态检索和渲染审计，没有入场、出场、TP/SL、成本或模型分数，因此 val AUC、top-decile 毛/净收益、胜率、单特征收益基线、匹配随机入场对照和收益置换检验都**不适用**；不能为了填表编造经济指标。

同等严格的非方向性零假设是：对同一核心使用相反方向归一化，再计算到同一 50 张参考族的距离。结果为 **10,000/10,000 正确方向距离都更小**，双侧精确 sign-test `p = 1.00247455E-3010`。这证明方向字段不是随意附加，但不能证明交易收益或实盘优势。

## 风险与诚实声明

1. 这 10,000 张是根据 v7 已接受形态族自动检索的 **P0 示例**，不是逐样本 Owner Gold；`training_eligible=false / production_eligible=false`。
2. 检索使用核心之后 `+1/+2/+3/+5` 的已完成历史来确认释放，因此它是 completed-pattern retrieval，不是盘口因果 detector。缩短图片窗口也不会消除这五根未来可见性。
3. 10,000 中不同币可能在同一市场小时共振；已保留为不同资产事件。未来若获准训练，必须按时间块 group split/权重控制相关性，不能随机切分。
4. 目前没有为这批生成负样本、YOLO label、train/val 或模型；直接拿 PNG 开训会重复此前“未经 Gold 就训练”的错误。
5. 没有读取 holdout，也没有消费任何配置的 holdout 次数。

## 复现命令

```bash
# 1) 官方安全月档：UTC+8 月历 -> UTC 15m，全部早于 2025-06-01
python3 -m src.data.fetch_okx \
  --archive-monthly-start 2023-07 \
  --archive-monthly-end 2025-05 \
  --archive-max-exclusive 2025-06-01T00:00:00Z \
  --archive-source-audit experiments/active/exp-15m-ma-launch-candidate9000-v1/results/source_audit.json \
  --out-dir data/kline_preholdout_archive15m \
  --workers 12

python3 -m src.data.fetch_okx \
  --archive-monthly-start 2021-09 \
  --archive-monthly-end 2023-06 \
  --archive-max-exclusive 2025-06-01T00:00:00Z \
  --archive-source-audit experiments/active/exp-15m-ma-launch-candidate9000-v1/results/source_audit.json \
  --out-dir data/kline_preholdout_archive15m_202109_202306 \
  --workers 12

# 2) 扫描、NMS、平衡选择、可断点渲染、QA、HTML
python3 -m scripts.build_15m_ma_launch_owner_autofill10000

# 3) 单元/回归测试
python3 -m pytest -q \
  tests/test_ma_launch_owner_autofill10000.py \
  tests/test_fetch_okx_archives.py \
  tests/test_ma_launch_owner_autofill_review.py

# 4) 报告 HTML
python3 scripts/md_to_html.py \
  analysis/p0_15m_ma_launch_owner_autofill10000_20260827.md \
  --out-dir analysis/html
```

关键输入/产物 SHA：

- 主月档 summary：`d50f7e46358ff358bce66685d1d46a977c7f06a03a5b6565832d857d53508a54`
- 早期补档 summary：`aba6eb477195563976a6a07e27dcf13048c888769efd4cdb669bbd8d7608d72c`
- 构建 manifest：`60bb7350c446ee1a406efbd48a4d7e7426ac4838e35b901bee3073019b327e76`
- scan receipt：`bd91a0704c7252176b908320251134d8537076469f2dc103f608a81e5a5ff00d`
- visual QA：`9769b602254558d9f5ae7ddedb919cdf18d237157ec3a71ec09cea89bdb35b96`

## 下一步

本轮允许停在“10,000 张严格示例和 HTML 已生成”。Owner 不需要审核表，也没有隐含训练任务。若以后要把它升级为训练集，下一步必须另行定义并执行逐样本类别/核心边界 Gold 门、负样本排除区、时间 group split 和训练授权；本轮不自动继续。
