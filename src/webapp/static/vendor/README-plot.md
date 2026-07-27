# Observable Plot — 分析图层(与 LWC 分工)

**分工是硬约束,不要混:**

| 层 | 用什么 | 为什么 |
|---|---|---|
| 价格 K 线图 | `lightweight-charts` | TradingView 官方,专做金融时序,45KB,已接入 |
| 分析/诊断图 | `Observable Plot` | LWC 画不了散点/热力图/直方图/误差棒 |
| **训练渲染** | **`src/detection/render.py` (cv2)** | **绝对不动** — 检测器绑定在那些确切像素上,换渲染器等于换输入分布,历史数字全部作废 |

Observable Plot 是 d3 作者写的高层封装,底层就是 d3。选它而不是裸 d3:
这个项目要的是散点、直方图、带 CI 的分位曲线、热力图这些**标准统计图**,
Plot 一行一张,裸 d3 每张几十行样板。真需要定制时可以直接下钻到 d3。

## 为什么现在需要它

今天在同一个会话里,我三次从小样本读出了不存在的模式(16 笔交易读出"边塌了"、
1 张图读出"止损太近"、20 张图读出"涨幅榜必死"),每次都被全量数据推翻。
这些误读的共同点是**离散度不可见** —— 命令行只给一个中位数,看不到区间有多宽。

CPCV 的 15 个切分正是为此存在,但它现在的输出是一堆数字。做成热力图后,
"判断层在早期组好、后期组塌" 这种结构一眼可见,不需要先猜再验。

## 下载

```bash
curl -L https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/dist/plot.umd.min.js \
  -o src/webapp/static/vendor/plot.umd.min.js
```

离线优先:和 `lightweight-charts.standalone.production.js` / `tabulator.min.js`
一样落到 vendor/,不走 CDN(看板要在 VPS 上无外网可用)。
