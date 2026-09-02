# P1：最新全 A 股 15m Grade-A YOLO 跨市场扫描（2026-09-02）

> **结论先行：**在 2026-09-02 11:30 CST 已完成的上午最后一根 15m K 线处，
> 冻结的 Grade-A full40 native-1280 模型从 5,494 只可评分股票中得到 **31 个模型命中**：
> **8 LONG / 23 SHORT**。全部图见下方四页总览。
>
> 这些是**加密货币模型迁移到 A 股后的分布外、完成态研究提案**，不是已验证 A 股信号，
> 也不是买卖建议。模型在核心后还看了 7–9 根确认 K；A 股普通现货通常也不能直接执行
> `SHORT`。本轮没有收益标签、回测、调参、训练、promote、部署、forward 写入或下单。

实验：`exp-15m-ashare-grade-a-yolo-latest-20260902-v1`

这是该 Grade-A full40 native-1280 checkpoint 的 **holdout 使用 #8**。Owner 在 2026-09-02
明确要求读取“最新大 A 数据”、跑模型并给图；授权只覆盖这一次冻结扫描，不授权用结果调门。

## 哪些股票命中

置信度是 YOLO 框分数，**不是上涨/下跌概率，更不是盈利概率**。

| 排名 | 代码 | 名称 | 板块 | 类别 | YOLO 置信度 | 核心后确认 K | 图号 |
|---:|---:|---|---|---:|---:|---:|---:|
| 1 | 920166 | 海圣医疗 | 北交所 | LONG | 0.9030 | 7 | 001 |
| 2 | 000682 | 东方电子 | 深主板 | SHORT | 0.8982 | 8 | 002 |
| 3 | 688310 | 迈得医疗 | 科创板 | SHORT | 0.8981 | 8 | 003 |
| 4 | 920116 | 星图测控 | 北交所 | LONG | 0.8830 | 7 | 004 |
| 5 | 605488 | 福莱新材 | 沪主板 | SHORT | 0.8800 | 9 | 005 |
| 6 | 603335 | 迪生力 | 沪主板 | SHORT | 0.8670 | 8 | 006 |
| 7 | 002738 | 中矿资源 | 深主板 | SHORT | 0.8517 | 9 | 007 |
| 8 | 603833 | 欧派家居 | 沪主板 | SHORT | 0.8309 | 9 | 008 |
| 9 | 688360 | 德马科技 | 科创板 | SHORT | 0.8221 | 8 | 009 |
| 10 | 601702 | 华峰铝业 | 沪主板 | SHORT | 0.8187 | 8 | 010 |
| 11 | 600708 | 光明地产 | 沪主板 | SHORT | 0.8125 | 8 | 011 |
| 12 | 000913 | 钱江摩托 | 深主板 | SHORT | 0.7849 | 8 | 012 |
| 13 | 002849 | 威星智能 | 深主板 | SHORT | 0.7608 | 9 | 013 |
| 14 | 920046 | 亿能电力 | 北交所 | LONG | 0.7226 | 8 | 014 |
| 15 | 688626 | 翔宇医疗 | 科创板 | SHORT | 0.7193 | 8 | 015 |
| 16 | 920158 | 长江能科 | 北交所 | LONG | 0.7068 | 9 | 016 |
| 17 | 603381 | 永臻股份 | 沪主板 | SHORT | 0.7066 | 8 | 017 |
| 18 | 920505 | 九菱科技 | 北交所 | LONG | 0.7025 | 7 | 018 |
| 19 | 600336 | 澳柯玛 | 沪主板 | LONG | 0.7012 | 7 | 019 |
| 20 | 301283 | 聚胶股份 | 创业板 | SHORT | 0.6692 | 9 | 020 |
| 21 | 605369 | 拱东医疗 | 沪主板 | SHORT | 0.6620 | 8 | 021 |
| 22 | 300652 | 雷迪克 | 创业板 | SHORT | 0.6476 | 9 | 022 |
| 23 | 603806 | 福斯特 | 沪主板 | SHORT | 0.6277 | 8 | 023 |
| 24 | 601061 | 中信金属 | 沪主板 | SHORT | 0.5532 | 8 | 024 |
| 25 | 002984 | 森麒麟 | 深主板 | SHORT | 0.5275 | 9 | 025 |
| 26 | 920188 | 悦龙科技 | 北交所 | LONG | 0.4428 | 9 | 026 |
| 27 | 600111 | 北方稀土 | 沪主板 | SHORT | 0.4134 | 9 | 027 |
| 28 | 688623 | 双元科技 | 科创板 | SHORT | 0.3965 | 9 | 028 |
| 29 | 601633 | 长城汽车 | 沪主板 | SHORT | 0.3719 | 9 | 029 |
| 30 | 603212 | 赛伍技术 | 沪主板 | SHORT | 0.2913 | 9 | 030 |
| 31 | 920017 | 星昊医药 | 北交所 | LONG | 0.2757 | 7 | 031 |

分数分布：最大 0.9030，中位数 0.7068，最小 0.2757，均值 0.6822。31 个事件中，
20 个核心长度为 4 根，11 个为 5 根；确认长度为 post7 / post8 / post9 的事件分别为
5 / 13 / 13 个。

## 全部图

图中红框是原始 YOLO 框，橙色区域是冻结结构映射后的 4/5 根核心，右侧是模型已看见的
确认 K。每张单图均有独立 SHA；四页总览和 31 张原图也打包在交付 ZIP 中。

![A 股命中总览第 1 页](../experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/overview_page_01.png)

![A 股命中总览第 2 页](../experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/overview_page_02.png)

![A 股命中总览第 3 页](../experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/overview_page_03.png)

![A 股命中总览第 4 页](../experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/overview_page_04.png)

## 数据怎么取得

### 本轮实际数据链路

本轮没有安装新库，也没有静默换源。请求形状逐字段锚定到 AKShare 当前源码：

- 股票宇宙：东方财富 `clist/get`，AKShare 的沪深京 A 股过滤串
  `m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048`，冻结时 5,908 行；
  未按涨跌幅、成交额、ST 或模型结果筛选。
- K 线：同一来源的 `stock/kline/get`，15m、前复权 `qfq`、每只请求最近 512 根。
- 上游代码锚：AKShare commit `8e95744b79ae22326308ccd2b4e62650c5b53c55`，
  `stock_hist_em.py` SHA256
  `d2a4c09d55d9362c8c7e58ec82f78d198cf6d2c2daf004033eef42ded915050d`。
- 东方财富返回的分钟时间是收盘标签。本轮先用同源上证指数冻结交易日程，再将每个标签减
  15 分钟转为仓库的 `open_time`；每只股票最后 160 个收盘标签必须与参考日程逐字一致。
  不补 K、不插值、不把停牌或陈旧行情冒充最新行情。
- 先冻结全量 CSV 字节和 SHA，之后断网式推理；推理阶段网络读取为 0。

AKShare 是封装层，东方财富这些端点并没有公开、受支持的 API 契约，因此本轮原始 CSV 与
receipt 才是可复现事实。AKShare 官方仓库与本轮钉住的实现分别见
[akfamily/akshare](https://github.com/akfamily/akshare) 和
[固定 commit 的 stock_hist_em.py](https://github.com/akfamily/akshare/blob/8e95744b79ae22326308ccd2b4e62650c5b53c55/akshare/stock_feature/stock_hist_em.py)。

### 覆盖率与排除

| 项 | 数量 |
|---|---:|
| 冻结沪深京 A 股宇宙 | 5,908 |
| 可评分 | **5,494（92.99%）** |
| 排除 | 414 |
| 陈旧的最后一根 | 308 |
| 同源请求最终失败 | 47 |
| 无可保留 15m K | 28 |
| 与参考日程不一致 | 13 |
| 非法 15m 收盘标签 | 8 |
| 历史不足 | 7 |
| 其他 | 3 |

所以“31 个”不是对 5,908 只的绝对穷尽结论，而是对 5,494 只合格快照的结果；47 只仍有
同源网络失败，未用另一来源偷偷补齐。

可评分宇宙为北交所 333、创业板 1,396、沪主板 1,694、科创板 596、深主板 1,475。
命中分布为北交所 7、创业板 2、沪主板 13、科创板 4、深主板 5。

## 扫描漏斗与机械验收

| 阶段 | 数量 | 说明 |
|---|---:|---|
| 可评分股票 | 5,494 | 每只恰好一个最新端点 |
| W18 + W19 图 | 10,988 | native 1280 |
| 原始 YOLO 框 | 288 | conf ≥ 0.25，NMS IoU 0.70 |
| 结构合法框 | 281 | 86 LONG / 195 SHORT |
| 语义门通过框 | 47 | 同一事件可能被 W18/W19 重复提出 |
| 5 根内事件去重 | **31** | 8 LONG / 23 SHORT |

独立重放验证通过：5,494 份 K 线 SHA、281 个结构输入像素、281 个语义决定、31 张图像
像素与 SHA 全部一致；验证阶段网络读取 0、模型推理 0。

语义门最常见的失败项为 `ma_spread_end=165`、`ma_envelope=126`、`ma_slope=77`、
`max_body=41` 和 `core_progress=26`。这说明 288 个原框中的大多数没有被直接叫作结果。

## 零假设对照与必报指标

本轮是**跨市场、单时点的无未来结果扫描**，没有读取之后的 A 股 K 线来造收益标签，因此
val AUC、top-decile 毛/净收益、胜率、单特征基线和“同币 × 同时间块 × 同波动桶”的匹配
随机入场都不适用。把这些栏位留成收益数字会是假证据。

对应的严格零假设对照是预注册的方向翻转：固定每个结构框、时间、像素和置信度，只把
LONG/SHORT 倒置再跑同一个语义门。去重事件层实际方向通过 31 个，翻转方向通过 0 个；
31 个配对不一致项的双侧 exact sign / McNemar p = `9.313225746e-10`。

这个 p 值只说明**语义门确实在使用方向一致性，而不是不分方向地放行**；它不验证检测框
对不对，更不验证未来收益。没有 A 股 Owner Gold 和未来结果，本轮不能报 precision、recall、
PF 或 alpha。

## 与上一版本对照

这是本 checkpoint 第一次按“沪深京全 A 股、同一最新端点、冻结日程、前复权”合同运行，
没有同口径上一版。不能把此前的 OKX/crypto 扫描数与本表直接比较，因为市场、交易时段、
波动结构、复权和股票宇宙全部不同。

## 现成数据源与热门开源项目

以下 GitHub 热度是 2026-09-02 的近似快照，只用于回答“热门”，不代表数据质量或生产资格。

| 项目 | 约 Stars | 它解决什么 | 本项目建议 |
|---|---:|---|---|
| [Microsoft Qlib](https://github.com/microsoft/qlib) | 48.2k | 量化研究、数据处理、训练/回测框架 | 适合自带并冻结数据后做研究；不要把框架当实时数据商 |
| [vn.py](https://github.com/vnpy/vnpy) | 45.1k | 量化交易与多接口执行框架 | 真做券商/交易接口时评估；不是行情真实性担保 |
| [AKShare](https://github.com/akfamily/akshare) | 22.4k | 多公开网页数据的 Python 封装 | **快速研究首选**；必须冻结原始字节、时间语义和失败清单 |
| [Tushare](https://github.com/waditu/tushare) | 15.4k | 带 token/权限体系的金融数据接口 | 需要更稳定的历史/实时分钟权限时优先评估 |
| [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS) | 11.1k | A 股量化研究/交易平台 | 可参考架构，数据与执行仍需单独验真 |
| [mootdx](https://github.com/mootdx/mootdx) | 2.2k | 通达信在线/本地行情读取 | 可作研究备选，不建议作为本轮主源 |

Tushare Pro 官方文档的 `rt_min` 支持 1/5/15/30/60 分钟、单次最多 1,000 行，但需要相应
权限；历史分钟接口同样支持这些周期、单次最多 8,000 行：
[实时分钟](https://tushare.pro/document/2?doc_id=374)、
[历史分钟](https://tushare.pro/document/1?doc_id=234)。

Qlib 是研究基础设施，不是可靠的“今天最新全 A 股”来源；其官方数据文档也明确区分数据层、
自备数据和 prepared data：
[Qlib data component](https://github.com/microsoft/qlib/blob/main/docs/component/data.rst)。

本项目当前的现实选择是：**临时研究用 AKShare/同源 Eastmoney + 严格快照；要持续或生产化，
换成有权限、SLA 和复权说明的数据服务，并做双源对账。** 不应把网页抓取直接接到真钱路径。

## 复现命令

```bash
# 0. 代码与预注册卡必须先在 main 上提交；本轮 builder commit：062956425cff...
git branch --show-current

# 1. 冻结全市场快照；失败可用同一命令续跑，同一 frozen universe/cutoff 不变
python3 scripts/scan_15m_ashare_yolo_latest.py --fetch --workers 8

# 2. 只读本地快照跑模型；不联网
python3 scripts/scan_15m_ashare_yolo_latest.py --scan --batch-size 16

# 3. 独立重放结构、语义、像素与图 SHA；不联网、不推理
python3 scripts/scan_15m_ashare_yolo_latest.py --verify

# 4. 交付 HTML
python3 scripts/md_to_html.py \
  analysis/p1_15m_ashare_grade_a_yolo_latest_20260902.md \
  --out-dir analysis/html
```

冻结输入 receipt：
`analysis/output/ashare_15m_yolo_latest_20260902_v1/fetch_receipt.json`

结果账本：
`experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/signals.csv`

31 张原图、四页总览和结果账本打包：
`experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/ashare_signal_charts_31.zip`

独立验证：
`experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/verification.json`

模型：`analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/`
`ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt`，
SHA256 `862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838`。

## 风险与诚实声明

1. **跨市场 OOD 是第一风险。** 模型训练域是 24×7 crypto 15m，不是 A 股；A 股有午休、
   隔夜、涨跌停、停牌、公司行为和不同微观结构。根数相同不等于时间域相同。
2. **这不是新鲜 tip。** 模型合同允许 2–9 根 post-core；本轮 31 个全是 post7–9。
   数据在 11:30 完整可用，但多数核心在前一交易日下午；若核心跨隔夜，连续的“交易 bar”
   在现实钟表上并不连续。
3. **LONG 明显集中在北交所。** 可评分池里北交所只有 333 / 5,494（6.1%），却占
   7 / 8 个 LONG。它可能在吃波动率、价格尺度、跳空或板块风格 shortcut，不能先叫 alpha。
4. **SHORT 不是普通 A 股现货卖空指令。** 它最多是形态类别或规避/对冲研究候选；是否可融券、
   成本和额度均未检查。
5. **前复权会随未来公司行为修订历史值。** 本轮可复现权威是已冻结 CSV 的 SHA，不是以后
   再请求一次“同日期 qfq”就必然相同。
6. **覆盖不是 100%。** 414 只被明确排除，其中 47 只是同源请求失败；不得把“未评分”当
   “无信号”。
7. **没有 A 股结果验证。** 31 张只是待人审的候选。不能从检测分数推导胜率、收益或排序价值。
8. **安全状态未变。** `models/active_bundle.json` 仍不存在，生产运行 0 模型；本轮未训练、
   未改阈值/权重、未 promote、未部署、未写 forward、未发 Telegram、未下单。

## 下一步选项（需 Owner 决策）

- **A（推荐）：只做 31 张 Owner 形态复审。** 逐张标“像 / 不像 / 边界”，先测跨域 morphology
  precision；不得根据好看的几张改门后重扫这次已消费快照。
- **B：建立独立 A 股 pre-holdout Gold。** 用更早日期冻结候选和安全负例，按时间切分；这是
  新数据项目，不等于直接拿 crypto 权重生产化。
- **C：接 Tushare Pro 或有 SLA 的授权源做双源 parity。** 先比较代码身份、复权、停牌、
  15m 时间戳和 OHLC，再谈持续扫描。
- **D：只把这次当一次性图册。** 不继续训练、不升级为信号，保留为跨市场失败/偏移证据。

任何新训练、阈值变化、收益回测读取未来区间、ACTIVE/frozen/forward 变更或实盘动作，均需
另行明确授权。
