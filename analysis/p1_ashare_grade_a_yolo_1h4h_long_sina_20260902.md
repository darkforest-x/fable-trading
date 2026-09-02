# A 股普通主板 1h / 会话 4h 多头扫描（2026-09-02）

## 结论先行

截至 **2026-09-02 15:00 CST** 的已完成行情，本轮在普通账户可搜索的沪深主板池中交付
**16 个 LONG 研究候选：1h 12 个、会话 4h 4 个**。搜索股票时只输入表中的 **6 位代码**，
例如输入 `600576`，不要输入模型内部键 `SH600576`，也不要加 `.SH`、`.SZ` 或币种后缀。

这 16 个候选全部来自冻结的普通沪深主板身份表；科创板、创业板、北交所、未知板块、
`ST/*ST/S*ST/SST`、`PT` 和名称含“退”的证券均已在模型推理前排除。本轮内部审计还发现
4 个 SHORT 事件，但按 Owner 指令没有交付。

必须把含义说准：这是 **crypto 15m 训练的 Grade-A 形态检测器跨市场、跨周期 OOD 扫描**，
不是 A 股验证过的盈利模型。表中的置信度是 YOLO 对图形框的相似度排序，**不是上涨概率、
胜率或买入建议**。实验 `exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2` 消耗该 checkpoint
的 1h holdout **#11** 和会话 4h holdout **#12**；此前 Eastmoney 长度不足的 #9/#10 仍计消费。

## 可直接搜索的票号

### 1h：12 个，均在最新端点 2026-09-02 15:00 CST 可见

| 排名 | 6 位代码 | 股票名称 | 市场 | 代表框置信度 | W/core/确认 | 原图 |
|---:|---:|---|---|---:|---|---|
| 1 | **600576** | 祥源文旅 | 沪市主板 | 0.8966 | W18 / 4 / 8 | `001_1h_SH600576_long.png` |
| 2 | **002553** | 南方精工 | 深市主板 | 0.8848 | W18 / 4 / 3 | `002_1h_SZ002553_long.png` |
| 3 | **601566** | 九牧王 | 沪市主板 | 0.8620 | W18 / 4 / 8 | `003_1h_SH601566_long.png` |
| 4 | **601136** | 首创证券 | 沪市主板 | 0.8527 | W18 / 4 / 9 | `004_1h_SH601136_long.png` |
| 5 | **002639** | 雪人集团 | 深市主板 | 0.8480 | W18 / 4 / 4 | `005_1h_SZ002639_long.png` |
| 6 | **600435** | 北方导航 | 沪市主板 | 0.8420 | W19 / 5 / 8 | `006_1h_SH600435_long.png` |
| 7 | **002416** | 爱施德 | 深市主板 | 0.8379 | W19 / 5 / 3 | `007_1h_SZ002416_long.png` |
| 8 | **601860** | 紫金银行 | 沪市主板 | 0.7245 | W18 / 4 / 8 | `008_1h_SH601860_long.png` |
| 9 | **603868** | 飞科电器 | 沪市主板 | 0.6472 | W19 / 5 / 7 | `009_1h_SH603868_long.png` |
| 10 | **002945** | 华林证券 | 深市主板 | 0.5913 | W18 / 4 / 8 | `010_1h_SZ002945_long.png` |
| 11 | **002265** | 建设工业 | 深市主板 | 0.5086 | W19 / 5 / 4 | `011_1h_SZ002265_long.png` |
| 12 | **600693** | 东百集团 | 沪市主板 | 0.4652 | W18 / 4 / 8 | `012_1h_SH600693_long.png` |

“确认”是完整检测窗中核心形态之后、已经收盘并被模型看见的 K 线数，不是未来预测期限。

### 会话 4h：4 个，允许按 Owner 口径保留数日前出现的事件

| 排名 | 6 位代码 | 股票名称 | 市场 | 代表/峰值置信度 | 首次—最近可见时间（CST） | 端点距最新交易日 | 原图 |
|---:|---:|---|---|---:|---|---:|---|
| 1 | **600908** | 无锡银行 | 沪市主板 | 0.5674 / 0.5674 | 09-02 15:00—09-02 15:00 | 0 | `013_4h_SH600908_long.png` |
| 2 | **002881** | 美格智能 | 深市主板 | 0.3206 / 0.3677 | 09-01 15:00—09-02 15:00 | 0 | `014_4h_SZ002881_long.png` |
| 3 | **603565** | 中谷物流 | 沪市主板 | 0.7782 / 0.7782 | 08-27 15:00—08-31 15:00 | 2 | `015_4h_SH603565_long.png` |
| 4 | **000983** | 山西焦煤 | 深市主板 | 0.4472 / 0.4690 | 08-27 15:00—08-31 15:00 | 2 | `016_4h_SZ000983_long.png` |

事件代表框按“**最近检测端点优先，再取该端点最高置信度**”选择，另保留整个事件峰值，避免
用几天前的高分框冒充最新框。4h 不是墙钟连续 4 小时：它是同一交易日四根 60m bar
（10:30、11:30、14:00、15:00 收盘）严格聚合，完整可用时间为 15:00 CST。

## 总览图

![1h LONG 总览第 1 页](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/overview_1h_page_01.png)

![1h LONG 总览第 2 页](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/overview_1h_page_02.png)

![4h LONG 总览](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/overview_4h_page_01.png)

## 16 张检测原图

每张图左侧是完整因果上下文，右侧 inset 是模型实际看到的 W18/W19 输入；红框保留 YOLO
原始定位，标题同时写了 6 位搜索代码、时间周期、可用时间和 OOD 警告。图包路径：
`experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/ashare_1h4h_long_charts_16.zip`，
SHA-256 `c3952ab63db689ab6f4702326d094c8d527d34654c813fd5a821ee90aab43488`。

### 600576 祥源文旅 · 1h

![600576 祥源文旅 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/001_1h_SH600576_long.png)

### 002553 南方精工 · 1h

![002553 南方精工 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/002_1h_SZ002553_long.png)

### 601566 九牧王 · 1h

![601566 九牧王 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/003_1h_SH601566_long.png)

### 601136 首创证券 · 1h

![601136 首创证券 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/004_1h_SH601136_long.png)

### 002639 雪人集团 · 1h

![002639 雪人集团 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/005_1h_SZ002639_long.png)

### 600435 北方导航 · 1h

![600435 北方导航 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/006_1h_SH600435_long.png)

### 002416 爱施德 · 1h

![002416 爱施德 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/007_1h_SZ002416_long.png)

### 601860 紫金银行 · 1h

![601860 紫金银行 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/008_1h_SH601860_long.png)

### 603868 飞科电器 · 1h

![603868 飞科电器 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/009_1h_SH603868_long.png)

### 002945 华林证券 · 1h

![002945 华林证券 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/010_1h_SZ002945_long.png)

### 002265 建设工业 · 1h

![002265 建设工业 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/011_1h_SZ002265_long.png)

### 600693 东百集团 · 1h

![600693 东百集团 1h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/012_1h_SH600693_long.png)

### 600908 无锡银行 · 会话 4h

![600908 无锡银行 4h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/013_4h_SH600908_long.png)

### 002881 美格智能 · 会话 4h

![002881 美格智能 4h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/014_4h_SZ002881_long.png)

### 603565 中谷物流 · 会话 4h

![603565 中谷物流 4h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/015_4h_SH603565_long.png)

### 000983 山西焦煤 · 会话 4h

![000983 山西焦煤 4h 原始检测图](../experiments/active/exp-ashare-grade-a-yolo-1h4h-long-sina-20260902-v2/results/charts/016_4h_SZ000983_long.png)

## 数据如何获取、为什么这次能跑完

本轮采用 [AKShare 股票数据文档](https://akshare.akfamily.xyz/data/stock/stock.html)公开说明的
新浪分钟行情路径；AKShare 是可直接复用的现成开源入口。为冻结上游语义，本轮同时钉住其
[Sina 实现源码](https://github.com/akfamily/akshare/blob/8e95744b79ae22326308ccd2b4e62650c5b53c55/akshare/stock/stock_zh_a_sina.py)：
分钟接口请求 `period=60`、`datalen=1970`，复权因子从 `qfq.js` 读取。

流程不是“抓到什么就喂什么”，而是：

1. 先从已冻结的 A 股身份表筛出 3,111 只普通沪深主板（沪 1,666、深 1,445）。
2. 先抓上证综指参考序列和沪深两个哨兵；参考实际得到 1,970 根 1h、492 个完整会话 4h，
   覆盖 2024-08-21 14:00 至 2026-09-02 15:00 CST。
3. `qfq.js` 只按严格 JSON 数据解析，不执行 JavaScript；每根历史 bar 只使用该日期当时已生效的
   复权因子，避免用未来除权事件反写过去输入。
4. Eastmoney 在线连接在本机收到空响应后，按 Owner 明确批准使用同日冻结缓存做重叠校验，
   没有静默换口径。上证参考 127 个共享点的收盘价相对差中位数为
   `7.62e-7`、p99 为 `1.31e-6`；浦发银行和平安银行各 126 根 QFQ 60m 的 OHLC 相对差中位数
   都为 0，p99 分别为 0.001111 和 0.000916，均在预注册门限内。
5. 通过预检后才扇出个股；1h 可用 3,021 只（97.11%），4h 可用 2,903 只（93.31%）。

[上交所交易时间](https://english.sse.com.cn/start/trading/schedule/)与
[深交所交易时间](https://www.szse.cn/www/investor/knowledge/stock/deal/t20191204_572383.html)
用于核对 A 股午间休市边界；因此 4h 按交易会话聚合，不把 11:30—13:00 的休市误当成连续 bar。

## 数据质量与扫描漏斗

| 项目 | 1h | 会话 4h | 合计/说明 |
|---|---:|---:|---|
| 冻结普通主板宇宙 | 3,111 | 3,111 | 同一身份表 |
| 可用股票 | 3,021 | 2,903 | 两周期均不可用 90 只 |
| 扫描窗口 | 6,042 | 29,030 | 1h 每票 1 端点×W18/W19；4h 每票最多 5 端点×两窗 |
| YOLO 原始框 | 133 | 240 | 373 |
| 结构有效框 | 121 | 225 | 346 |
| 语义门通过框 | 26 | 26 | 52；占窗口 0.1483% |
| 去重审计事件 | 14（12 LONG / 2 SHORT） | 6（4 LONG / 2 SHORT） | 20 |
| 最终交付 | **12 LONG** | **4 LONG** | **16 LONG** |

失败没有被改写成“无信号”：1h 排除 12 只历史不足、25 只时序不匹配、36 只末端陈旧和
17 只网络失败；4h 排除 46、128、17、17 只相应类别。网络失败身份共 17，只低于冻结上限
`max(10, 1% × 3,111)=31` 后才允许继续。未放宽模型或数据门限。

## 与上一配置同表对照

| 项目 | Eastmoney 60m #9/#10 | Sina 60m #11/#12 | 变化归因 |
|---|---:|---:|---|
| 参考 1h 根数 | 127 | 1,970 | 数据源实际历史覆盖变长 |
| 完整会话 4h 根数 | 31 | 492 | 不再低于 160 日预注册门 |
| 已请求个股 | 0 | 3,111 | 参考预检通过后才扇出 |
| 模型窗口 | 0 | 35,072 | 冻结输入合同现在可满足 |
| 可交付 LONG | 未计算 | 16 | 前者是数据源失败，不是零信号 |
| 阈值/权重变化 | 无 | 无 | 不是靠调参得到更多候选 |

这张表只比较“同一冻结检测合同能否执行”，不能把 16 与“未计算”解释成模型效果提升。

## 模型与零假设对照

模型保持 Grade-A 8,000 正例 + 24,000 匹配负例、full40、native 1280；权重 SHA-256
`862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838`。冻结参数为
conf 0.25、NMS 0.70、W18/W19、core 4/5、确认 2–9、同票事件间隔 5 bar。

这是无未来 A 股结果标签的 OOD 检测交付，不是收益实验，因此下列指标按字面均**不适用**：

| 必报项 | 本轮值 | 原因 |
|---|---|---|
| val AUC | 不适用 | 没有 A 股监督标签或 train/val |
| 置换检验 p | 不适用 | 没有收益排序或标签置换统计量 |
| top-decile 毛/净收益 | 不适用 | 没有入场、TP/SL、持有期或成本回放 |
| 胜率 | 不适用 | 没有冻结未来结果标签 |
| 单特征基线 | 不适用 | 本轮没有训练 L2 或特征模型 |
| 同币×同时间块×同波动桶随机入场对照 | 不适用 | 没有声明任何方向收益结论 |

同等严格、且对检测语义有定义的零假设对照是**方向翻转复算**：346 个结构有效框中，真实方向
有 52 个框通过语义门；把每个框的 LONG/SHORT 方向反转后，仅 1 个通过。它说明这批形态门具有
方向选择性，**不说明 52 个框会赚钱，也不是 p 值**。

## 离线复验

在冻结快照上独立运行 `--verify`，结果 PASS：

- 3,021 份 K 线 SHA 与交易时序检查；
- 5,892,102 行日期因果 QFQ 算术检查；
- 346 份模型输入像素 SHA 检查；
- 346 个语义门独立重算；
- 16 个 LONG 选择规则检查；
- 16 张原图像素与 SHA 检查；
- 网络读取 0、模型推理 0，避免用“重新跑出相似结果”冒充工件复验。

复验凭据 SHA-256：`4844d1303cb4238abb520dc08b398796cbe76438270e5e65212cdd43894e0bfc`；
结果汇总 SHA-256：`cfc85c7158a33d69e5074eada12abe298b539ba1b714cc7e9597c62e4248bc2b`；
信号表 SHA-256：`02a6b478aa7b17e4d35d2c81512ce1ba8c3b31d79590b7f165ba771490a60b57`。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
git branch --show-current
git show --stat cb2700467bcdd064522e0cc7bb12fe33dfa613ae

.venv/bin/python -m pytest -q \
  tests/test_scan_ashare_yolo_1h4h_long.py \
  tests/test_filter_ashare_signals_for_standard_retail.py \
  tests/boundaries/test_layer_imports.py

# 仅在目标快照目录不存在、且 Owner 已授权对应 holdout 读取时从网络重建
.venv/bin/python scripts/scan_ashare_yolo_1h4h_long_sina.py --fetch --workers 4

# 扫描冻结快照；扫描阶段没有网络读取
.venv/bin/python scripts/scan_ashare_yolo_1h4h_long_sina.py --scan --batch-size 16

# 不联网、不推理，独立复验已有工件
.venv/bin/python scripts/scan_ashare_yolo_1h4h_long_sina.py --verify

python3 scripts/md_to_html.py \
  analysis/p1_ashare_grade_a_yolo_1h4h_long_sina_20260902.md \
  --out-dir analysis/html
```

原始上游会随时间和复权事件变化；真正逐字节复核本轮结果应使用冻结快照运行 `--verify`，
而不是在未来日期重新抓网后要求 SHA 相同。

## 风险与诚实声明

- 最大风险是 domain shift：模型在 crypto 15m 图上训练，却被用于 A 股 1h 和会话 4h；
  这 16 个只能进人工复核池，`production_eligible=false`。
- 这是完成历史窗检测。尤其 4h 形态允许按 Owner 口径保留数日前出现的事件；不得把
  08-31 的最近检测端点写成 09-02 的实时新信号。
- 新浪 QFQ 历史会随新的公司行为修订；本轮以保存的原始响应、逐 bar 因子和派生 CSV 为权威。
- 17 只网络失败和其余时序/历史不足证券被诚实排除，不能声称 3,111 只全部可推理。
- 本轮没有训练、调阈值、改权重、切换 ACTIVE/frozen、promote、部署、修改 forward、发 Telegram
  或下单。Telegram 原图未发送；可交付的是已校验的本地图包与自包含 HTML。

## 下一步选项

1. Owner 先按 6 位代码和原图人工复核 16 个候选；这是本轮唯一不需要新增 holdout 的动作。
2. 若要把结果发到 Telegram，需要另行确认具体 bot/chat 目标和发送授权；不应从历史凭据猜目标。
3. 若要回答“这些票后来赚不赚钱”，必须预注册 A 股专用结果标签、成本和匹配随机对照；那是新实验，
   不能回看本轮结果后再挑规则。
