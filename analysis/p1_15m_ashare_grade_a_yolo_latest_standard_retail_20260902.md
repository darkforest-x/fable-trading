# 最新全 A 股 15m 命中：普通沪深主板账户过滤版（2026-09-02）

> **过滤完成：31 条原始模型命中保留 18 条，剔除 13 条需要额外板块权限的股票。**
> 本报告把“普通用户”保守定义为：已有基础沪深 A 股交易账户，但不假设已开通科创板、
> 创业板或北交所权限。原始扫描与图册均未覆盖、未重跑模型。
> **Telegram 原图交付完成：18/18，均以 PNG document 无压缩发送。**

在保留的 18 条中只有 **1 条 LONG：600336 澳柯玛**；其余 17 条是模型的 SHORT 形态类，
不能理解为“可以买入”。所有条目仍是 crypto 模型跨市场到 A 股的 OOD 完成态研究提案，
不是投资建议。

## 过滤口径

| 板块 | 本版处理 | 官方个人投资者准入依据 |
|---|---|---|
| 沪主板 | 保留 | 基础沪市 A 股账户口径 |
| 深主板 | 保留 | 基础深市 A 股账户口径 |
| 科创板 | 剔除 | 上交所 2026 交易规则第 6.2 条：个人投资者申请权限前 20 个交易日日均资产不低于 50 万元、证券交易满 24 个月，并需签署风险揭示书 |
| 创业板 | 剔除 | 深交所：新申请个人投资者前 20 个交易日日均资产不低于 10 万元、证券交易满 24 个月 |
| 北交所 | 剔除 | 北交所适当性办法第 5 条：通常为前 20 个交易日日均资产不低于 50 万元、证券交易满 24 个月；即使适用科创板权限例外，仍需另行开通北交所权限 |

官方来源：

- [上海证券交易所交易规则（2026 年修订）第六章](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)
- [深圳证券交易所创业板个人投资者适当性问答](https://investor.szse.cn/knowledge/t20200513_577026.html)
- [北京证券交易所投资者适当性管理办法](https://www.bse.cn/jygl_list/200018386.html)

另外采用保守名称门：若出现 `ST` / `*ST` / 退市名称则剔除。本批 31 条中没有此类名称，
因此实际剔除全部来自板块权限。

## 保留的 18 条

置信度是 YOLO 框分数，不是盈利概率。

| 原排名 | 代码 | 名称 | 板块 | 类别 | 置信度 | 确认 K |
|---:|---:|---|---|---|---:|---:|
| 2 | 000682 | 东方电子 | 深主板 | SHORT | 0.8982 | 8 |
| 5 | 605488 | 福莱新材 | 沪主板 | SHORT | 0.8800 | 9 |
| 6 | 603335 | 迪生力 | 沪主板 | SHORT | 0.8670 | 8 |
| 7 | 002738 | 中矿资源 | 深主板 | SHORT | 0.8517 | 9 |
| 8 | 603833 | 欧派家居 | 沪主板 | SHORT | 0.8309 | 9 |
| 10 | 601702 | 华峰铝业 | 沪主板 | SHORT | 0.8187 | 8 |
| 11 | 600708 | 光明地产 | 沪主板 | SHORT | 0.8125 | 8 |
| 12 | 000913 | 钱江摩托 | 深主板 | SHORT | 0.7849 | 8 |
| 13 | 002849 | 威星智能 | 深主板 | SHORT | 0.7608 | 9 |
| 17 | 603381 | 永臻股份 | 沪主板 | SHORT | 0.7066 | 8 |
| 19 | 600336 | **澳柯玛** | 沪主板 | **LONG** | **0.7012** | 7 |
| 21 | 605369 | 拱东医疗 | 沪主板 | SHORT | 0.6620 | 8 |
| 23 | 603806 | 福斯特 | 沪主板 | SHORT | 0.6277 | 8 |
| 24 | 601061 | 中信金属 | 沪主板 | SHORT | 0.5532 | 8 |
| 25 | 002984 | 森麒麟 | 深主板 | SHORT | 0.5275 | 9 |
| 27 | 600111 | 北方稀土 | 沪主板 | SHORT | 0.4134 | 9 |
| 29 | 601633 | 长城汽车 | 沪主板 | SHORT | 0.3719 | 9 |
| 30 | 603212 | 赛伍技术 | 沪主板 | SHORT | 0.2913 | 9 |

板块计数：沪主板 13、深主板 5；方向计数：LONG 1、SHORT 17。

## 过滤后的全部图

![普通沪深主板账户过滤总览第 1 页](../experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/standard_retail_mainboard/overview_page_01.png)

![普通沪深主板账户过滤总览第 2 页](../experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/standard_retail_mainboard/overview_page_02.png)

## Telegram 原图交付

Owner 在本轮明确要求“把检测原图发到 tg”。2026-09-02 14:17 北京时间完成发送：

- 只发送过滤后保留的 18 张逐事件原始 PNG，没有发送被剔除的科创板、创业板或北交所图片；
- 每张均调用 Telegram document 通道，避免图片通道重压缩；LONG 1 张、SHORT 17 张；
- 发送前逐张核对冻结清单 SHA256，发送后回执覆盖 18/18，18 个图像哈希互不重复；
- 交付合同 SHA256 为
  `5d8002ff8bd94cf60a7c441ca01489dd5b07c03b366a33f8294ed5d6658c78ad`；
- 回执为
  `experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/standard_retail_mainboard/telegram_delivery_receipt.json`，
  SHA256 为 `76bca704414455613169834e2e646da01e5e1a3f04d4ac6d379cc40e41fc4122`；
- 回执不保存 bot token 或 chat id；完整交付再次执行会 fail closed，中断时只续发尚无回执的图片。

本次外部动作仅限 Owner 已授权的图片交付；没有下单、模型推理、追加 holdout 读取、阈值/权重
变更、ACTIVE/frozen/forward 切换、promote 或部署。

## 被剔除的 13 条

| 原排名 | 代码 | 名称 | 板块 | 类别 | 原因 |
|---:|---:|---|---|---|---|
| 1 | 920166 | 海圣医疗 | 北交所 | LONG | 需额外板块权限 |
| 3 | 688310 | 迈得医疗 | 科创板 | SHORT | 需额外板块权限 |
| 4 | 920116 | 星图测控 | 北交所 | LONG | 需额外板块权限 |
| 9 | 688360 | 德马科技 | 科创板 | SHORT | 需额外板块权限 |
| 14 | 920046 | 亿能电力 | 北交所 | LONG | 需额外板块权限 |
| 15 | 688626 | 翔宇医疗 | 科创板 | SHORT | 需额外板块权限 |
| 16 | 920158 | 长江能科 | 北交所 | LONG | 需额外板块权限 |
| 18 | 920505 | 九菱科技 | 北交所 | LONG | 需额外板块权限 |
| 20 | 301283 | 聚胶股份 | 创业板 | SHORT | 需额外板块权限 |
| 22 | 300652 | 雷迪克 | 创业板 | SHORT | 需额外板块权限 |
| 26 | 920188 | 悦龙科技 | 北交所 | LONG | 需额外板块权限 |
| 28 | 688623 | 双元科技 | 科创板 | SHORT | 需额外板块权限 |
| 31 | 920017 | 星昊医药 | 北交所 | LONG | 需额外板块权限 |

合计：北交所 7、科创板 4、创业板 2。

## 这项过滤能保证什么、不能保证什么

能保证的是：输出代码属于冻结宇宙里的沪深主板，并且名称没有 `ST` / `*ST` / 退市标记；
板块分类和图像 SHA 可机械重放。

不能保证的是：你的具体券商账户一定有权限、股票当前没有停牌、没有涨跌停封单、有足够资金、
满足 100 股申报单位或订单一定成交。那些需要在下单时读取实时券商状态；本轮没有获得真金操作
授权，也没有连接账户。

此外，17 条 `SHORT` 只是模型方向类别。普通 A 股现金账户不能据此直接做空；若目标是找普通
用户可直接买入的 LONG 候选，本版只剩 **600336 澳柯玛**，而它仍未经 A 股收益验证。

## 复现与验证

```bash
# Builder 已先提交在 main：7a432b5
python3 scripts/filter_ashare_signals_for_standard_retail.py --build
python3 scripts/filter_ashare_signals_for_standard_retail.py --verify

# 只校验 Telegram 交付合同，不产生外部动作
python3 -m scripts.send_15m_ashare_standard_retail_to_telegram

# 只有取得 Owner 对本批文件的明确发送授权后才可执行；完整回执会拒绝重复发送
python3 -m scripts.send_15m_ashare_standard_retail_to_telegram --send

python3 scripts/md_to_html.py \
  analysis/p1_15m_ashare_grade_a_yolo_latest_standard_retail_20260902.md \
  --out-dir analysis/html
```

验证结果：18 张单图 SHA、2 页总览像素和 ZIP CRC 全部通过；网络读取 0、模型推理 0。
这是原 checkpoint holdout 使用 #8 的**同一结果后处理**，没有新增 holdout 消费，没有修改
原始 31 条、模型、阈值、权重、ACTIVE、frozen 或 forward。

交付文件：

- `experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/standard_retail_mainboard/signals.csv`
- `experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/standard_retail_mainboard/excluded.csv`
- `experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/standard_retail_mainboard/standard_retail_mainboard_charts_18.zip`
- `experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/standard_retail_mainboard/verification.json`
- `experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/standard_retail_mainboard/telegram_delivery_receipt.json`
