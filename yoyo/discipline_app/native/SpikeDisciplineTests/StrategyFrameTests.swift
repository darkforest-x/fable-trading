import XCTest
@testable import SpikeDiscipline

/// 2.7.0 strategy frame: payloads exactly as monitor_feed.py / strategy_book.py project them
/// (placeholder numbers, nulls and integers included).
final class StrategyFrameTests: XCTestCase {
    func testDecodesBreadthAccountRowsAndSignals() throws {
        let json = #"{"now_ms":1794000000000,"paper_only":true,"equity_basis":"realized","research":[{"key":"breadth-ema20","label":"全市场下轨反弹","title":"研究小册"}],"accounts":[{"key":"live-breadth","label":"live-breadth","title":"全市场宽度信号 · SOL 计资金","group":"live","kind":"breadth","equity":1032.4,"start_equity":1000.0,"pnl":32.4,"refilled":false,"return_pct":3.24,"started_ms":1793900000000,"open_count":1,"stats":{"closed":1,"wins":1,"mean_net_bp":325.0,"win_pct":100.0},"breadth_rule":{"count_min":31,"count_max":62,"bar_minutes":5,"observe_minutes":30,"hold_days":6,"funded":"SOL-USDT-SWAP","shadows":["BTC-USDT-SWAP","ETH-USDT-SWAP"],"leverage":1.0,"taker_bp":5.0},"hold":{"symbol":"SOL-USDT-SWAP","fill_ms":1793800000000,"due_ms":1794318400000}}],"detail":{"key":"live-breadth","open":[{"id":9260000101,"symbol":"BTC-USDT-SWAP","side":"long","entry":76020.0,"target":null,"stage":"holding","funded":false,"notional":1000.0,"fill_ms":1793800000000,"score":null,"shadow":true,"due_ms":1794318400000,"mark":76861.0,"unrealized_bp":110.6,"remaining_notional":null,"stop":null}],"closed":[],"touches":[],"breadth_signals":[{"id":9260000200,"symbol":"SOL-USDT-SWAP","status":"followers","candle_ms":1793790000000,"observed_ms":1,"decision_ms":1793791800000,"n":40,"new_n":3},{"id":9260000201,"symbol":null,"status":"watching","candle_ms":1793999000000,"observed_ms":null,"decision_ms":1794000720000,"n":37,"new_n":null}]},"signals":[]}"#
        let p = try API.decoder.decode(PaperSnapshot.self, from: Data(json.utf8))
        let a = try XCTUnwrap(p.accounts.first)
        XCTAssertTrue(a.isBreadth)
        XCTAssertEqual(a.breadthRule?.countMax, 62)
        XCTAssertEqual(a.breadthRule?.shadows.count, 2)
        XCTAssertEqual(a.hold?.dueMs, 1_794_318_400_000)
        XCTAssertEqual(a.stats.winPct, 100)
        XCTAssertEqual(p.research?.first?.key, "breadth-ema20")
        XCTAssertEqual(p.detail.open.first?.shadow, true)
        XCTAssertEqual(p.detail.breadthSignals?.count, 2)
        let signals = try XCTUnwrap(p.detail.breadthSignals)
        XCTAssertEqual(StrategyFmt.breadthStatus(signals[0], now: p.nowMs), "观察期有新币 · 取消 +3")
        XCTAssertEqual(StrategyFmt.breadthStatus(signals[1], now: p.nowMs), "观察中 · 剩 12分")
        XCTAssertEqual(StrategyFmt.breadthTone("busy"), .warn)
    }

    /// A 2.6 service (no strategy-frame fields) still decodes.
    func testOldServicePayloadStillDecodes() throws {
        let json = #"{"now_ms":1,"accounts":[{"key":"spike","label":"spike","group":"replica","equity":1.0,"start_equity":1.0,"return_pct":0.0,"started_ms":null,"stats":{"closed":0,"wins":0,"mean_net_bp":null}}],"detail":{"key":"spike","open":[],"closed":[]},"signals":[]}"#
        let p = try API.decoder.decode(PaperSnapshot.self, from: Data(json.utf8))
        XCTAssertFalse(p.accounts[0].isBreadth)
        XCTAssertNil(p.research)
        XCTAssertNil(p.detail.breadthSignals)
    }

    func testDecodesBookletBacktestAndCandles() throws {
        let booklet = #"{"key":"live-wick6x3","label":"live-wick6x3","title":"插针收盘 · 6份3倍","kind":"fib","paper":true,"facts":[{"label":"入场分门槛","value":"≥ 0.5","unit":""}],"flow":[{"title":"划区间","text":"…"}],"history":{"start_ms":1577836800000,"end_ms":1793000000000,"note":"占位","refilled":false,"unit":"%","summary":{"start":1000.0,"end":16438.4,"pnl":15438.4,"return_pct":1543.8,"max_drawdown_pct":19.3,"win_pct":66.0,"trades":400,"profit_factor":1.69,"no_losses":false,"cash_added":0.0,"avg_win":143.2,"avg_loss":-164.4},"drawdown_basis":"realized","points":[[1577836800000,1000.0,0.0],[1793000000000,16438.4,-2.1]],"yearly":[["2020",12.5],["2021",null]],"monthly":[["2020-01",1.2]],"sides":{"headers":["方向","成交笔数","胜率 %","净盈亏 USDT"],"rows":[["做多",210,66.2,8000.1]]}},"history_error":null,"research":{"status":"模拟运行","note":"n","detail":null,"table":{"headers":["区间","样本数"],"rows":[["2025",10],[null,"x"]]},"capital":null,"controls":{"note":"c","headers":["a"],"rows":[[1]]},"sections":[{"title":"分节","note":null,"headers":["a","b"],"rows":[[1,2.5]]}],"methodology":"m","period_note":null},"backtest":true,"notes":["历史为币安回测"]}"#
        let b = try API.decoder.decode(Booklet.self, from: Data(booklet.utf8))
        XCTAssertEqual(b.history?.points.count, 2)
        XCTAssertEqual(b.history?.yearly.map(\.period), ["2020", "2021"])
        XCTAssertNil(b.history?.yearly[1].value)
        XCTAssertEqual(b.research?.table?.rows[1].first, .empty)
        XCTAssertEqual(b.research?.table?.rows[0][1].display, "10")
        XCTAssertEqual(b.research?.sections.first?.rows[0][1].display, "2.50")

        let page = #"{"track":"live-breadth","total":13,"offset":0,"has_more":false,"sort":"time","direction":"desc","meta":{"trades":13,"pnl":2373.47,"start":"2023-01-01","end":"2026-09-29T14:45:00+00:00","source":"Binance","note":"n","basis":"b","limitation":"l"},"rows":[{"id":13,"symbol":"SOLUSDT","coin":"SOL","side":"long","entry_ms":1790174700000,"exit_ms":1790693100000,"pnl":168.36,"return_pct":5.25,"exit_reason":"time_6d","score":null,"count":53,"new_coins":0,"kind":"breadth","entry_price":114.46,"exit_price":120.59,"entry_start_ms":1790174700000,"entry_end_ms":1790174700000,"exit_start_ms":1790693100000,"exit_end_ms":1790693100000}]}"#
        let bt = try API.decoder.decode(BacktestPage.self, from: Data(page.utf8))
        XCTAssertEqual(bt.rows.first?.coin, "SOL")
        XCTAssertEqual(bt.rows.first?.count, 53)
        XCTAssertEqual(StrategyFmt.utcTime(bt.rows.first?.entryMs), "2026-09-23 14:45")

        let chart = #"{"track":"live-breadth","trade":{"id":13,"symbol":"SOLUSDT","coin":"SOL","side":"long","entry_ms":1,"exit_ms":2,"pnl":1.0,"return_pct":1.0,"exit_reason":null,"score":null,"count":null,"new_coins":null,"kind":null,"entry_price":1.0,"exit_price":1.1,"entry_start_ms":null,"entry_end_ms":null,"exit_start_ms":null,"exit_end_ms":null},"bar":"1H","source":"Binance","note":"n","candles":[{"time":1790000000000,"open":1.0,"high":1.2,"low":0.9,"close":1.1,"volume":5.0}],"weekly_ranges":[{"week":"2026-09-21","start_ms":1,"end_ms":2,"expires_ms":3,"high":1.1,"low":0.9,"upper":1.2,"lower":0.8}]}"#
        let c = try API.decoder.decode(BacktestChartData.self, from: Data(chart.utf8))
        XCTAssertEqual(c.candles.count, 1)
        XCTAssertEqual(c.weeklyRanges.first?.upper, 1.2)
    }

    func testCloudAccountKeepsLegacyRealizedFieldsAndUsesMarkedValuesWhenAvailable() throws {
        let json = #"{"now_ms":1794000000000,"paper_only":true,"equity_basis":"realized","accounts":[{"key":"live-cloud-v17","label":"live-cloud-v17","title":"云带 v1.7 · 12H 逆向","group":"live","kind":"cloud","equity":1005.0,"start_equity":1000.0,"pnl":5.0,"return_pct":0.5,"marked_equity":1025.0,"marked_pnl":25.0,"marked_return_pct":2.5,"unrealized_pnl":20.0,"refilled":false,"open_count":1,"slot_count":9,"phase":"running","heartbeat_ms":1794000000000,"stats":{"closed":null,"wins":null,"mean_net_bp":null,"win_pct":null}}],"detail":{"key":"live-cloud-v17","open":[],"closed":[],"touches":[],"breadth_signals":[],"cloud_events":[{"id":1794000000000,"symbol":"AAA-USDT-SWAP","side":"long","status":"opened","reason":"accepted","time_ms":1794000000000,"entry_price":10.0}]},"signals":[],"research":[]}"#
        let p = try API.decoder.decode(PaperSnapshot.self, from: Data(json.utf8))
        let account = try XCTUnwrap(p.accounts.first)
        XCTAssertTrue(account.isCloud)
        XCTAssertEqual(account.equity, 1005)
        XCTAssertEqual(account.pnl, 5)
        XCTAssertEqual(account.displayEquity, 1025)
        XCTAssertEqual(account.displayPnl, 25)
        XCTAssertEqual(account.displayReturnPct, 2.5)
        XCTAssertEqual(account.unrealizedPnl, 20)
        XCTAssertEqual(p.detail.cloudEvents?.first?.status, "opened")

        // Old server payloads have no additive marked fields and still decode through the 2.7.x model.
        let old = #"{"key":"live-cloud-v17","label":"live-cloud-v17","group":"live","equity":1005.0,"start_equity":1000.0,"pnl":5.0,"return_pct":0.5,"kind":"cloud","stats":{"closed":null,"wins":null,"mean_net_bp":null}}"#
        let legacyAccount = try API.decoder.decode(PaperAccount.self, from: Data(old.utf8))
        XCTAssertEqual(legacyAccount.displayEquity, 1005)
        XCTAssertEqual(legacyAccount.displayPnl, 5)
    }

    func testCloudBookletAndChartDecodeHourlyBasisAndOriginal12HCandles() throws {
        let booklet = #"{"key":"live-cloud-v17","label":"live-cloud-v17","title":"云带 v1.7 · 12H 逆向","kind":"cloud","paper":true,"facts":[{"label":"金额盈亏比","value":"5.35","unit":"倍"}],"flow":[{"title":"等收盘","text":"按 12H 收盘确认。"}],"history":{"start_ms":1579824000000,"end_ms":1794139200000,"note":"OKX · 小时收盘净值含浮盈亏。","refilled":false,"unit":"%","summary":{"start":1000.0,"end":147590.5,"pnl":146590.5,"return_pct":14659.05,"max_drawdown_pct":57.8293,"max_drawdown_bound_pct":58.54,"win_pct":21.0,"trades":1252,"profit_factor":1.4235,"no_losses":false,"cash_added":0.0},"drawdown_basis":"hourly_marked","points":[[1579824000000,1000.0,0.0],[1794139200000,147590.5,-57.829]],"yearly":[["2026",51.1]],"monthly":[],"sides":{"headers":["方向","成交笔数"],"rows":[["做多",618]]}},"history_error":null,"research":{"status":"OKX 12H","note":"先检查云带与轨道。","detail":null,"table":{"headers":["检查","做多","做空"],"rows":[["EMA","EMA20 > EMA50","EMA20 < EMA50"]]},"capital":null,"controls":null,"sections":[],"methodology":null,"period_note":null,"cases":[{"title":"止损案例","text":"完整案例文字。"}],"risk":["回撤上界 58.54%。"],"sources":["OKX 12H 原始K线。"]},"backtest":true,"notes":["实时账户从 1,000U 起步。"]}"#
        let b = try API.decoder.decode(Booklet.self, from: Data(booklet.utf8))
        XCTAssertEqual(b.history?.drawdownBasis, "hourly_marked")
        XCTAssertEqual(b.history?.summary.profitFactor, 1.4235)
        XCTAssertEqual(b.history?.summary.maxDrawdownBoundPct, 58.54)
        XCTAssertEqual(b.facts.first?.value, "5.35")
        XCTAssertEqual(b.research?.cases?.first?.title, "止损案例")
        XCTAssertEqual(b.research?.risk?.first, "回撤上界 58.54%。")

        let chart = #"{"track":"live-cloud-v17","trade":{"id":1082,"symbol":"BEATUSDT","coin":"BEAT","side":"long","entry_ms":1790000000000,"exit_ms":1791000000000,"pnl":55392.05,"return_pct":37.8,"exit_reason":"ema_flip","exit_reason_label":"云层翻转平仓","entry_price":0.5434,"exit_price":1.73172},"bar":"12H","source":"OKX","note":"缺失时段留空","candles":[{"time":1790000000000,"open":0.54,"high":0.6,"low":0.5,"close":0.58,"ema20":0.55,"ema50":0.53,"ema_ready":true}],"weekly_ranges":[],"references":[{"price":0.5434,"title":"入场","color":"up"},{"price":0.5,"title":"固定止损","color":"down"}],"expected_bars":80,"available_bars":78,"missing_bars":2}"#
        let c = try API.decoder.decode(BacktestChartData.self, from: Data(chart.utf8))
        XCTAssertEqual(c.bar, "12H")
        XCTAssertNil(c.candles.first?.volume)
        XCTAssertEqual(c.candles.first?.ema20, 0.55)
        XCTAssertEqual(c.references?.count, 2)
        XCTAssertEqual(c.trade.exitReasonLabel, "云层翻转平仓")

        let executions = #"{"track":"live-cloud-v17","trade":{"id":1,"symbol":"AAAUSDT","coin":"AAA","side":"long"},"candles":[],"weekly_ranges":[],"execution_legs":[{"time_ms":1790000000000,"start_ms":1790000000000,"end_ms":1790000000000,"price":10.0,"label":"开仓","action":"entry","quantity":2.0,"share":1.0,"phase":"open"},{"time_ms":1790003600000,"start_ms":1790003600000,"end_ms":1790007200000,"price":12.0,"label":"止盈","action":"take_profit","quantity":1.0,"share":0.5,"phase":"intrabar"}]}"#
        let replay = try API.decoder.decode(BacktestChartData.self, from: Data(executions.utf8))
        XCTAssertEqual(replay.executionLegs?.count, 2)
        XCTAssertEqual(replay.executionLegs?.last?.action, "take_profit")
        XCTAssertEqual(replay.executionLegs?.last?.share, 0.5)
    }

    func testSpanAndDecimals() {
        XCTAssertEqual(StrategyFmt.span(2 * 86_400_000 + 4 * 3_600_000), "2天4小时")
        XCTAssertEqual(StrategyFmt.span(12 * 60_000), "12分")
        XCTAssertEqual(StrategyFmt.signedMoney(-74.141), "−74.14")
        XCTAssertEqual(StrategyFmt.decimals(114.46), 2)
        XCTAssertEqual(StrategyFmt.decimals(0.0123), 6)
    }

    func testCloudReplayViewportFitsTradeInFinalPhoneWidthAndLongHold() throws {
        let period = 12 * 60 * 60.0
        let exit = 1_794_096_000.0 + 11 * 60 * 60 // 2026-11-08 11:00 UTC, inside the 00:00 candle.
        let entry = exit - (3 * 24 * 60 * 60 + 23 * 60 * 60)
        let lastBar = exit - 11 * 60 * 60
        let firstBar = lastBar - 71 * period
        let width: CGFloat = 275
        let fit = try XCTUnwrap(ChartReplayViewportFit.make(plotWidth: width, periodSeconds: period,
                                                            firstBarTime: firstBar, lastBarTime: lastBar,
                                                            focus: entry...exit, rightPad: 5))
        XCTAssertGreaterThanOrEqual(fit.visiblePeriods, 25)
        XCTAssertLessThanOrEqual(fit.visiblePeriods, 42)

        let shortAxis = ChartTimeAxis(times: (0..<72).map { firstBar + Double($0) * period }, periodSeconds: period)
        let shortRight = max(shortAxis.position(atTime: lastBar), shortAxis.position(atTime: exit)) + 5 - fit.offset
        let shortEntryX = width - (shortRight - shortAxis.position(atTime: entry)) * fit.spacing
        let shortExitX = width - (shortRight - shortAxis.position(atTime: exit)) * fit.spacing
        XCTAssertTrue((0...width).contains(shortEntryX))
        XCTAssertTrue((0...width).contains(shortExitX))

        let longEntry = firstBar + 10 * period
        let longExit = longEntry + 100 * period
        let longFit = try XCTUnwrap(ChartReplayViewportFit.make(plotWidth: width, periodSeconds: period,
                                                                firstBarTime: firstBar, lastBarTime: longExit,
                                                                focus: longEntry...longExit, rightPad: 5))
        let longAxis = ChartTimeAxis(times: [firstBar, longExit], periodSeconds: period)
        let longRight = longAxis.position(atTime: longExit) + 5 - longFit.offset
        let longEntryX = width - (longRight - longAxis.position(atTime: longEntry)) * longFit.spacing
        let longExitX = width - (longRight - longAxis.position(atTime: longExit)) * longFit.spacing
        XCTAssertTrue((0...width).contains(longEntryX))
        XCTAssertTrue((0...width).contains(longExitX))
        XCTAssertGreaterThan(longFit.visiblePeriods, 42)
    }

    func testSparseChartTimeAxisPreservesMissingPeriodsWithoutSnapping() {
        let period = 12 * 60 * 60.0
        let start = 1_794_000_000.0
        let axis = ChartTimeAxis(times: [start, start + period, start + 3 * period], periodSeconds: period)
        XCTAssertEqual(axis.times.count, 3)
        XCTAssertEqual(Double(axis.position(forFractionalBarIndex: 2) - axis.position(forFractionalBarIndex: 1)), 2, accuracy: 1e-6)

        let missingTime = start + 2 * period
        let sourceIndex = axis.barIndex(atTime: missingTime)
        let mappedPosition = axis.position(forFractionalBarIndex: sourceIndex)
        XCTAssertEqual(Double(mappedPosition), Double(axis.position(atTime: missingTime)), accuracy: 1e-6)
        XCTAssertEqual(axis.nearestBarIndex(atPosition: mappedPosition), 1) // the gap is not synthesized as a candle.
    }
}
