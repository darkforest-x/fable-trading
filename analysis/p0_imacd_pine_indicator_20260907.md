# IMACD 零轴密集启动 · 多周期趋势 V1

已按本轮系统定义编写为Pine v6指标，保留IMACD副图，并把六均线和启动/结束标记画到主图。

[打开完整Pine源码](/Users/zhangzc/fable-trading/yoyo/evaluation/pine/imacd_dense_mtf_v1.pine)。复制全部内容，在TradingView的Pine编辑器新建空白指标，粘贴后添加到图表。
若账户指标名额不足，可以用此指标替换原IMACD；它已经自带六均线显示，不必额外叠加同样的均线。

## 三种模式

| 模式 | 入场提示条件 | 用途 |
|---|---|---|
| 密集启动（默认） | 前12根六MA宽度/ATR均值≤3、交织≥2，本根md首次离零 | 对应研究P02的核心条件，不启用多周期过滤 |
| 密集＋共振 | 最近34根出现密集，本根首次离零且close离开六MA带，高周期许可 | 当根确认，条件不足则跳过 |
| 密集＋共振等待 | 启动时锁定密集资格，最多再等9根首次共振 | 期间md回零/反向取消，不能事后补密集资格 |

六均线固定为close的SMA20/60/120及EMA20/60/120；形成窗口不包含启动当根。IMACD默认34/9。
高周期许可可选“主线同向”“主线或动量同向”“同向或零轴”。默认许可方式允许已知md=0。
可再打开低周期主线同向，缺少低周期数据时不发新启动。

## 怎么看

- 黄色小圆点：md在零轴，且满足当前六MA密集。
- 主图绿色向上三角“多启动”：多头启动在该根收盘确认。
- 主图红色向下三角“空启动”：空头启动在该根收盘确认。
- 副图浅绿/浅红背景：指标正在跟踪一段多头/空头趋势。
- “多结束/空结束”叉号：md回零或反向；不会因为一次md/sb反向交叉就提示结束。
- 面板显示模式、趋势、高低周期状态、形成宽度和交织次数。“持有”是指标状态，不是读取了你的实际账户仓位。

启动/结束标记画在确认K线上，意味着收盘后才知道；本版不是策略回测，没有把标记价格冒充下一根实际成交价。
在TradingView创建警报时，选本指标的多启动、空启动、趋势结束或等待共振，并选择“每根K线收盘一次”。本轮没有代你建立警报或下单。

## 周期与初始设置

可先在OKX:ETHUSDT.P或OKX:BTCUSDT.P的普通4小时蜡烛图查看，保持默认“密集启动”。
要观察共振，在设置中切到“密集＋共振等待”，保留“同向或零轴”；这只是第二种观察模式，不代表已证明比默认更赚钱。
4小时自动对应日线背景、1小时低周期；1小时对应4小时/15分钟；15分钟对应1小时/5分钟。
分钟及秒级图若启用低周期，数据可用性还受交易所与TradingView套餐限制；1秒图没有更低自动周期。
非普通时间图表（如Heikin Ashi、Renko、tick）会提示换图，以免合成价格被当成原始OHLC。

预热至少340根，每个所用周期独立计算；调整IMACD长度后取max(340,10×长度)。图上历史不足时会显示预热/数据不足。
“启动前至少连续零轴根数”默认1，调高会改变研究定义和信号集合；没有强制“横盘越久越好”。

## 收盘确认与研究差异

高周期采用官方建议的expression[1]＋lookahead_on，所有入场/退出/等待状态仅在当前图表收盘更新。
高周期与当前周期恰好同时收盘时，新高周期状态会在下一根当前周期K线才参与判断，因此可能比Python研究晚一根图表bar。
低周期用security_lower_tf已闭合子K线数组；缺数据不假装共振。原IMACD柱线仍会在当前未收盘K线上变化，启动与结束警报不会在盘中据此触发。
[TradingView官方时钟说明](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)。

这份脚本没有资金费率、TP/SL、保证金、自动下单或收益表。本轮只移植和验证显示/编译，不重新回测、不沿用Python统计冒充Pine逐笔收益。

## 验证记录

- 源SHA256：`90639febb6afedb087e80c0992efdf2ece487e60a4ec4121c3797fab68428387`。
- [编译及运行记录](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-ma-mtf-20260907-v3/pine_indicator/compiler_receipt.json)。
- 官方编译：`True`；逐笔TradingView/研究账本对齐：未运行。
- 使用独立只读审查核对形成窗口、等待锁定、零轴计数、图表类型、高低周期时钟；修复了预热零轴计数和小周期自动映射边界。
- 本轮不是收益实验：AUC、收益、胜率、置换p与随机入场对照均不适用。对照是原始公式/冻结状态契约、官方编译器与实际图表分支检查，不能据此推广盈利结论。
- 所有源码与记录training_eligible=false、production_eligible=false；本轮未切换任何仓库模型或生产配置。

## 复现交付

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.evaluation.imacd_pine_delivery
```

官方编译步骤：普通OKX蜡烛图 → Pine → 空白指标 → 粘贴下方完整代码 → 添加到图表；依次检查默认模式、共振模式与等待＋低周期模式。

## 完整代码

```pine
//@version=6
// IMACD formula credited to LazyBear; formation follows the repository V3 study.
// Research display only: no orders, strategy fills, stops or performance claims.
// Confirmed HTF values use expression[1] + lookahead_on. At an HTF boundary,
// close-only decisions see the new state one chart bar later than the Python study.
indicator("IMACD 零轴密集启动 · 多周期趋势 V1", shorttitle="IMACD MTF", overlay=false, max_bars_back=1500)

string gSignal = "01 · 信号模式"
string mode = input.string("密集启动", "模式", options=["密集启动", "密集＋共振", "密集＋共振等待"], group=gSignal, tooltip="密集启动：当前形成，默认对应研究P02。共振模式：最近34根出现密集，价格离开均线带，高周期许可。等待：启动后最多再等指定根数。")
int lengthMA = input.int(34, "IMACD 长度", minval=2, maxval=200, group=gSignal)
int lengthSignal = input.int(9, "信号线长度", minval=1, maxval=100, group=gSignal)
int minZero = input.int(1, "启动前至少连续零轴根数", minval=1, maxval=200, group=gSignal, tooltip="默认1，不强制长横盘。调高会减少信号，也可能错过大趋势。")
int waitBars = input.int(9, "最多等待根数", minval=0, maxval=50, group=gSignal)

string gDense = "02 · 六均线密集"
int denseWindow = input.int(12, "形成窗口（不含启动当根）", minval=2, maxval=100, group=gDense)
float maxWidth = input.float(3.0, "平均均线带宽 / ATR 上限", minval=0.1, step=0.1, group=gDense)
int minCrosses = input.int(2, "窗口内至少交织次数", minval=0, maxval=100, group=gDense)
int denseMemory = input.int(34, "共振模式：密集记忆根数", minval=1, maxval=200, group=gDense)

string gMtf = "03 · 多周期（仅共振模式参与过滤）"
bool autoTf = input.bool(true, "自动匹配高 / 低周期", group=gMtf)
string manualHtf = input.timeframe("1D", "手动高周期", group=gMtf)
string manualLtf = input.timeframe("60", "手动低周期", group=gMtf)
string htfMode = input.string("同向或零轴", "高周期许可", options=["主线同向", "主线或动量同向", "同向或零轴"], group=gMtf, tooltip="同向或零轴：高周期md或sh同向，或者md精确为0。仅使用已确认高周期，不使用正在形成的大K线。")
bool useLower = input.bool(false, "再要求低周期主线同向", group=gMtf, tooltip="启用时，缺少低周期数据就不发新信号；不会把缺数据当共振。")

string gView = "04 · 显示"
bool showMa = input.bool(true, "主图显示 SMA / EMA 20、60、120", group=gView)
bool showMarks = input.bool(true, "主图显示启动 / 退出", group=gView)
bool showDense = input.bool(true, "副图显示零轴密集点", group=gView)
bool showState = input.bool(true, "副图显示趋势持有背景", group=gView)
bool showPanel = input.bool(true, "显示状态面板", group=gView)
bool colorBars = input.bool(false, "按 IMACD 给 K 线染色", group=gView)

// Every call has its own history; the SMA seed is evaluated on every bar.
f_smma(float source, int length) =>
    float seed = ta.sma(source, length)
    var float value = na
    value := na(value[1]) ? seed : (value[1] * (length - 1) + source) / length
    value

f_imacd() =>
    float hi = f_smma(high, lengthMA)
    float lo = f_smma(low, lengthMA)
    float e1 = ta.ema(hlc3, lengthMA)
    float e2 = ta.ema(e1, lengthMA)
    float mi = 2.0 * e1 - e2
    float md = mi > hi ? mi - hi : mi < lo ? mi - lo : 0.0
    float sb = ta.sma(md, lengthSignal)
    [md, sb, md - sb, mi, hi, lo]

f_higher() =>
    [x, y, z, mi, hi, lo] = f_imacd()
    [x[1], z[1], time_close[1], bar_index[1]]

f_lower() =>
    [x, y, z, mi, hi, lo] = f_imacd()
    [x, time_close, bar_index]

f_flip(float a, float b) =>
    float d = a - b
    (d > 0 and d[1] <= 0) or (d < 0 and d[1] >= 0) ? 1.0 : 0.0

f_zone(float value) =>
    na(value) ? "数据不足" : value > 0 ? "多" : value < 0 ? "空" : "零轴"

[md, sb, sh, mi, hi, lo] = f_imacd()
float s20 = ta.sma(close, 20)
float e20 = ta.ema(close, 20)
float s60 = ta.sma(close, 60)
float e60 = ta.ema(close, 60)
float s120 = ta.sma(close, 120)
float e120 = ta.ema(close, 120)
float ropeHigh = math.max(s20, e20, s60, e60, s120, e120)
float ropeLow = math.min(s20, e20, s60, e60, s120, e120)
float atr = f_smma(ta.tr(true), 14)
float width = atr > 0 ? (ropeHigh - ropeLow) / atr : na
float flips = (f_flip(s20, e20) + f_flip(s20, s60) + f_flip(s20, e60) + f_flip(s20, s120) + f_flip(s20, e120) +
    f_flip(e20, s60) + f_flip(e20, e60) + f_flip(e20, s120) + f_flip(e20, e120) +
    f_flip(s60, e60) + f_flip(s60, s120) + f_flip(s60, e120) + f_flip(e60, s120) + f_flip(e60, e120) + f_flip(s120, e120))
// shift(1) is essential: the breakout candle cannot rewrite its formation.
float priorWidth = ta.sma(width[1], denseWindow)
float priorCrosses = math.sum(flips[1], denseWindow)
bool fullWindow = math.sum(not na(width[1]) ? 1.0 : 0.0, denseWindow) == denseWindow
bool denseNow = fullWindow and not na(priorWidth) and priorWidth <= maxWidth and priorCrosses >= minCrosses
bool denseRecent = ta.highest(denseNow ? 1.0 : 0.0, denseMemory) == 1.0
int warmup = math.max(340, lengthMA * 10)
bool ready = bar_index >= warmup and not na(priorWidth) and not na(sb)

float seconds = timeframe.isticks ? na : timeframe.in_seconds()
string autoHtf = seconds <= 900 ? "60" : seconds <= 1800 ? "120" : seconds <= 3600 ? "240" : seconds <= 7200 ? "360" : seconds <= 43200 ? "1D" : seconds <= 86400 ? "1W" : seconds <= 604800 ? "1M" : "12M"
string autoLtf = seconds <= 15 ? "1S" : seconds <= 60 ? "15S" : seconds <= 300 ? "1" : seconds <= 900 ? "5" : seconds <= 3600 ? "15" : seconds <= 7200 ? "30" : seconds <= 21600 ? "60" : seconds <= 43200 ? "240" : seconds <= 86400 ? "360" : "1D"
string htf = autoTf ? autoHtf : manualHtf
string ltf = autoTf ? autoLtf : manualLtf
bool confluence = mode != "密集启动"
bool waiting = mode == "密集＋共振等待"
if barstate.isfirst
    if na(seconds) or not chart.is_standard
        runtime.error("请使用普通时间 K 线。")
    if confluence and timeframe.in_seconds(htf) <= seconds
        runtime.error("高周期必须大于当前图表周期，请调整多周期设置。")
    if confluence and useLower and timeframe.in_seconds(ltf) >= seconds
        runtime.error("低周期必须小于当前图表周期，请调整多周期设置。")

[hMdRaw, hShRaw, hClose, hIndex] = request.security(syminfo.tickerid, htf, f_higher(), gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_on)
bool hKnown = hIndex >= warmup and not na(hMdRaw) and not na(hShRaw) and hClose <= time
float hMd = hKnown ? hMdRaw : na
float hSh = hKnown ? hShRaw : na
bool hLong = hKnown and (htfMode == "主线同向" ? hMd > 0 : hMd > 0 or hSh > 0 or (htfMode == "同向或零轴" and hMd == 0))
bool hShort = hKnown and (htfMode == "主线同向" ? hMd < 0 : hMd < 0 or hSh < 0 or (htfMode == "同向或零轴" and hMd == 0))

float lMd = na
if confluence and useLower
    [values, closes, indexes] = request.security_lower_tf(syminfo.tickerid, ltf, f_lower())
    int count = array.size(values)
    if count > 0
        int last = count - 1
        if array.get(closes, last) <= time_close and array.get(indexes, last) >= warmup
            lMd := array.get(values, last)
bool lowerLong = not useLower or (not na(lMd) and lMd > 0)
bool lowerShort = not useLower or (not na(lMd) and lMd < 0)
bool releaseLong = close > ropeHigh
bool releaseShort = close < ropeLow

// State means indicator-side trend tracking, not a broker position.
var int zeroRun = 0
var int trendSide = 0
var int pendingSide = 0
var int anchorBar = na
bool longSignal = false
bool shortSignal = false
bool exitLong = false
bool exitShort = false
bool armed = false
if barstate.isconfirmed
    int anchorSide = ready and md[1] == 0 and zeroRun >= minZero ? (md > 0 ? 1 : md < 0 ? -1 : 0) : 0
    // Exit is independent of all formation and timeframe gates.
    if trendSide != 0 and md * trendSide <= 0
        exitLong := trendSide == 1
        exitShort := trendSide == -1
        trendSide := 0
    if pendingSide != 0 and (md * pendingSide <= 0 or bar_index - anchorBar > waitBars)
        pendingSide := 0
        anchorBar := na
    if trendSide == 0 and anchorSide != 0
        pendingSide := 0
        anchorBar := na
        bool formed = confluence ? denseRecent : denseNow
        if formed
            pendingSide := anchorSide
            anchorBar := bar_index
            armed := true
    if trendSide == 0 and pendingSide != 0
        bool allowed = not confluence or (pendingSide == 1 ? releaseLong and hLong and lowerLong : releaseShort and hShort and lowerShort)
        if allowed
            longSignal := pendingSide == 1
            shortSignal := pendingSide == -1
            trendSide := pendingSide
            pendingSide := 0
            anchorBar := na
        else if not waiting or bar_index - anchorBar >= waitBars
            pendingSide := 0
            anchorBar := na
    zeroRun := not na(hi) and not na(lo) and md == 0 ? zeroRun + 1 : 0

color mdColor = hlc3 > mi ? (hlc3 > hi ? color.lime : color.green) : (hlc3 < lo ? color.red : color.orange)
hline(0, "零轴", color=color.new(color.gray, 55))
plot(md, "Impulse MACD", color=mdColor, style=plot.style_histogram, linewidth=2)
plot(sh, "动量差 sh", color=color.new(color.blue, 55), style=plot.style_histogram, linewidth=2)
plot(sb, "信号线 sb", color=color.maroon, linewidth=2)
plotshape(showDense and ready and barstate.isconfirmed and md == 0 and denseNow, title="零轴密集", style=shape.circle, location=location.bottom, color=color.yellow, size=size.tiny)
bgcolor(showState ? (trendSide == 1 ? color.new(color.green, 91) : trendSide == -1 ? color.new(color.red, 91) : na) : na, title="趋势持有状态")
barcolor(colorBars ? mdColor : na)
plot(showMa ? s20 : na, "SMA20", color=color.orange, force_overlay=true)
plot(showMa ? e20 : na, "EMA20", color=color.new(color.orange, 55), force_overlay=true)
plot(showMa ? s60 : na, "SMA60", color=color.blue, force_overlay=true)
plot(showMa ? e60 : na, "EMA60", color=color.new(color.blue, 55), force_overlay=true)
plot(showMa ? s120 : na, "SMA120", color=color.purple, force_overlay=true)
plot(showMa ? e120 : na, "EMA120", color=color.new(color.purple, 55), force_overlay=true)
plotshape(showMarks and longSignal, title="多头启动", text="多启动", style=shape.triangleup, location=location.belowbar, color=color.lime, textcolor=color.lime, size=size.small, force_overlay=true)
plotshape(showMarks and shortSignal, title="空头启动", text="空启动", style=shape.triangledown, location=location.abovebar, color=color.red, textcolor=color.red, size=size.small, force_overlay=true)
plotshape(showMarks and exitLong, title="多头结束", text="多结束", style=shape.xcross, location=location.abovebar, color=color.orange, textcolor=color.orange, size=size.tiny, force_overlay=true)
plotshape(showMarks and exitShort, title="空头结束", text="空结束", style=shape.xcross, location=location.belowbar, color=color.aqua, textcolor=color.aqua, size=size.tiny, force_overlay=true)
plot(priorWidth, "形成带宽/ATR", display=display.data_window)
plot(priorCrosses, "形成交织次数", display=display.data_window)
plot(trendSide, "持有方向 1多 -1空 0等待", display=display.data_window)

var table panel = table.new(position.top_right, 2, 6, border_width=1)
if barstate.islast and showPanel
    color cell = color.new(color.black, 20)
    table.cell(panel, 0, 0, "IMACD 密集启动", bgcolor=cell, text_color=color.white)
    table.cell(panel, 1, 0, mode, bgcolor=cell, text_color=color.white)
    table.cell(panel, 0, 1, "趋势状态", bgcolor=cell, text_color=color.white)
    table.cell(panel, 1, 1, not ready ? "预热中" : trendSide == 1 ? "多头持有" : trendSide == -1 ? "空头持有" : pendingSide != 0 ? "等待共振" : "等待启动", bgcolor=cell, text_color=color.white)
    table.cell(panel, 0, 2, "高周期 " + htf, bgcolor=cell, text_color=color.white)
    table.cell(panel, 1, 2, confluence ? "md " + f_zone(hMd) + " / sh " + f_zone(hSh) : "未启用过滤", bgcolor=cell, text_color=color.white)
    table.cell(panel, 0, 3, "低周期 " + ltf, bgcolor=cell, text_color=color.white)
    table.cell(panel, 1, 3, confluence and useLower ? f_zone(lMd) : "未启用过滤", bgcolor=cell, text_color=color.white)
    table.cell(panel, 0, 4, "形成宽度 / 交织", bgcolor=cell, text_color=color.white)
    table.cell(panel, 1, 4, str.tostring(priorWidth, "#.00") + " / " + str.tostring(priorCrosses, "#"), bgcolor=cell, text_color=color.white)
    table.cell(panel, 0, 5, "信号确认", bgcolor=cell, text_color=color.white)
    table.cell(panel, 1, 5, "本周期收盘", bgcolor=cell, text_color=color.white)

alertcondition(longSignal, "IMACD 多头启动", "{{exchange}}:{{ticker}} {{interval}} IMACD密集多头启动；本K线已收盘。")
alertcondition(shortSignal, "IMACD 空头启动", "{{exchange}}:{{ticker}} {{interval}} IMACD密集空头启动；本K线已收盘。")
alertcondition(exitLong, "IMACD 多头趋势结束", "{{exchange}}:{{ticker}} {{interval}} IMACD主线回零或转负，多头趋势结束。")
alertcondition(exitShort, "IMACD 空头趋势结束", "{{exchange}}:{{ticker}} {{interval}} IMACD主线回零或转正，空头趋势结束。")
alertcondition(armed and pendingSide != 0, "IMACD 等待共振", "{{exchange}}:{{ticker}} {{interval}} 密集零轴启动已出现，等待周期确认。")
```
