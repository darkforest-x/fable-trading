import SwiftUI

/// Landscape chart for the 1.618 and 合约 pages (owner 2026-11-08 item 6 “横屏看 K 线”,
/// Stitch 8a33888410d84f60b52d667bd9b0160c): the chart fills the screen edge to edge; a compact coin
/// pill top-left, a rotate-back button top-right and one period dock bottom-left float on it.
/// No tab bar, coin strip or order buttons here — it is for reading the chart.
struct LandscapeChart: View {
    let symbol: String
    @Binding var period: CandlePeriod
    var lines: [ChartLine] = []
    var showMonthlyOverlays = false

    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(FibStore.self) private var fib
    @Environment(ChartStyle.self) private var style
    @Environment(MondayHistory.self) private var history
    @Environment(TouchFeed.self) private var touches
    @Environment(MonthFirstWeekLevels.self) private var monthlyLevels
    @Environment(FibNavigation.self) private var navigation
    @Environment(\.colorScheme) private var scheme
    @State private var candles = CandleStore()
    @State private var monthlyReplay = MonthFirstWeekChartFeed()
    @State private var trendlineReplay = TrendlineChartFeed()
    @State private var trendlineLineFeed = TrendlineLineFeed()
    @State private var follow = 0
    @State private var settings = false

    var body: some View {
        GeometryReader { proxy in
            let route = navigation.monthlyTouch
            let trendlineRoute = navigation.trendlineEvent
            let chartSymbol = trendlineRoute?.symbol ?? route?.symbol ?? symbol
            let replay = replaySnapshot
            let row = fib.current?.items.first { $0.symbol == chartSymbol }
            let bars = trendlineRoute != nil ? (trendlineChartSnapshot?.candles ?? [])
                : route == nil ? candles.bars : (replay?.chartCandles ?? [])
            let currentRange = replay == nil
                ? (row.flatMap { fib.range(for: $0) } ?? history.currentRange(for: chartSymbol))
                : replay?.chartWeeklyRanges.last
            let eventHistory = replay.map { Array($0.chartWeeklyRanges.dropLast()) } ?? []
            let past = replay == nil ? (style.showHistory ? (history.ranges[chartSymbol] ?? []) : [])
                : (style.showHistory ? eventHistory : [])
            let overlayLines = replay?.chartMonthlyLines ?? (route == nil && showMonthlyOverlays
                ? FibCoinStripProjection.monthlyLines(source: .monthFirstWeek, ranges: monthlyLevels.ranges[chartSymbol] ?? [],
                                                       bars: bars, period: period) : [])
            let baseLines = route == nil ? lines + overlayLines : overlayLines
            let eventTrendline: ChartLine? = {
                guard style.showTrendline, let trendlineRoute, let first = bars.first, let last = bars.last else { return nil }
                return trendlineRoute.event.chartLine(endingAt: (last.time + period.seconds) * 1000,
                                                      startingAt: first.time * 1000)
            }()
            let currentTrendline: ChartLine? = {
                guard style.showTrendline, trendlineRoute == nil, let last = bars.last,
                      route == nil || replay != nil else { return nil }
                return trendlineLineFeed.chartLine(endingAt: (last.time + period.seconds) * 1000,
                                                   historicalTime: replay?.markerMs)
            }()
            let chartLines = baseLines + [eventTrendline, currentTrendline].compactMap { $0 }
            let chartMarkers = replay?.chartMarkers ?? (route == nil ? touches.markers(row) : [])
            let chartGeneration = generation(for: trendlineRoute, monthlyRoute: route, period: period,
                                             firstBarTime: bars.first?.time, replay: replay)
            let insets = proxy.safeAreaInsets
            ZStack {
                CandleChart(bars: bars, generation: chartGeneration, period: period, decimals: decimals(row),
                            range: currentRange, history: past,
                            innerLines: fib.showInternal, averages: fib.showAverages, cloud: fib.showCloud,
                            lines: chartLines,
                            markers: chartMarkers,
                            focusRange: replay?.chartFocusRange ?? trendlineFocusRange, timestampFocus: replay != nil || trendlineRoute != nil,
                            highlightedBarTimes: replay?.chartHighlightedBarTimes ?? [],
                            highlightRange: replay?.chartHighlightedBarTimes == nil ? replay?.chartHighlightRange : nil,
                            highlightColor: replay == nil ? nil : ChartPalette.hex(0x30D158),
                            focusBarTime: trendlineRoute != nil ? trendlineChartSnapshot.map { $0.markerMs / 1000 }
                                : replay?.locatedMarkerMs.map { $0 / 1000 }, fitReferenceLevels: replay == nil,
                            markHistory: replay == nil && trendlineRoute == nil, colors: style.colors,
                            watermark: chartSymbol.isEmpty ? nil : Fmt.coin(chartSymbol) + "USDT", followToken: follow, wallpaper: true,
                            topInset: insets.top + 54, bottomOverlay: insets.bottom + 46,
                            onNeedOlder: { if route == nil && trendlineRoute == nil { candles.loadOlder() } })
                    .padding(.trailing, insets.trailing)   // price axis clear of the trailing edge; candles run edge to edge
                if bars.isEmpty {
                    if trendlineRoute != nil {
                        if trendlineReplay.loading { ProgressView("正在读取历史 K 线").font(.footnote).foregroundStyle(Color.muted) }
                        else { Text(trendlineReplay.error ?? "趋势线突破历史 K 线暂不可用").font(.footnote).foregroundStyle(Color.muted) }
                    } else if route != nil {
                        if monthlyReplay.loading { ProgressView("正在读取触价 K 线").font(.footnote).foregroundStyle(Color.muted) }
                        else { Text(monthlyReplay.error ?? "月首周 K 线暂不可用").font(.footnote).foregroundStyle(Color.muted) }
                    } else if candles.error == nil { ProgressView() }
                }
            }
            .overlay(alignment: .topLeading) { coinPill(row, symbol: chartSymbol, bars: bars, replay: replay, route: route).padding(.leading, insets.leading + 10).padding(.top, max(insets.top, 10)) }
            .overlay(alignment: .topTrailing) {
                RoundIcon(systemName: "rectangle.portrait.rotate", size: 38) { Orientation.rotate(landscape: false) }
                    .accessibilityLabel("回到竖屏")
                    .padding(.trailing, insets.trailing + 64).padding(.top, max(insets.top, 10))
            }
            .overlay(alignment: .bottomLeading) { dock.padding(.leading, insets.leading + 10).padding(.bottom, max(insets.bottom, 8)) }
            .ignoresSafeArea()
        }
        .background((scheme == .dark ? ChartPalette.hex(0x0B0E14) : Color.white).ignoresSafeArea())
        .sheet(isPresented: $settings) { ChartSettingsSheet().presentationDetents([.large]).presentationDragIndicator(.visible) }
        .task(id: "\(symbol)|\(period.rawValue)|\(navigation.monthlyTouch?.id ?? 0)|\(navigation.trendlineEvent?.id ?? 0)|\(session.connection?.token.hashValue ?? 0)") {
            guard navigation.monthlyTouch == nil, navigation.trendlineEvent == nil else { candles.stop(); return }
            candles.show(symbol: symbol, period: period, connection: session.connection)
        }
        .task(id: historyTaskKey) {
            guard navigation.monthlyTouch == nil, navigation.trendlineEvent == nil, style.showHistory else { return }
            await history.load(symbol, from: candles.bars.first?.time, connection: session.connection)
        }
        .task(id: "\(symbol)|\(navigation.trendlineEvent?.id ?? 0)|\(session.connection?.token.hashValue ?? 0)") {
            guard navigation.monthlyTouch == nil, navigation.trendlineEvent == nil, let connection = session.connection else { return }
            await history.watchCurrent(symbol, connection: connection)
        }
        .task(id: session.connection?.token.hashValue ?? 0) {
            guard let connection = session.connection else { return }
            await touches.watch(connection: connection, session: session)
        }
        .task(id: "\(symbol)|\(showMonthlyOverlays)|\(navigation.monthlyTouch?.id ?? 0)|\(session.connection?.token.hashValue ?? 0)") {
            guard showMonthlyOverlays, navigation.monthlyTouch == nil else { return }
            await monthlyLevels.watch(symbol, connection: session.connection)
        }
        .task(id: "\(navigation.monthlyTouch?.id ?? 0)|\(period.rawValue)|\(session.connection?.token.hashValue ?? 0)") {
            guard let request = navigation.monthlyTouch, let connection = session.connection else { return }
            await monthlyReplay.load(request: .event(request.eventId), period: period, connection: connection)
        }
        .task(id: "\(navigation.trendlineEvent?.id ?? 0)|\(period.rawValue)|\(session.connection?.token.hashValue ?? 0)") {
            guard let route = navigation.trendlineEvent else { return }
            await trendlineReplay.load(route: route, period: period, connection: session.connection)
        }
        .task(id: "\(symbol)|\(period.rawValue)|\(style.showTrendline)|\(navigation.trendlineEvent?.id ?? 0)|\(session.connection?.token.hashValue ?? 0)") {
            guard style.showTrendline, navigation.trendlineEvent == nil,
                  let connection = session.connection else { return }
            await trendlineLineFeed.watch(symbol: symbol, connection: connection, session: session)
        }
        .onDisappear { candles.stopAndSave() }
    }

    private var replaySnapshot: MonthFirstWeekChartSnapshot? {
        guard let request = navigation.monthlyTouch,
              monthlyReplay.snapshot?.eventId == request.eventId,
              monthlyReplay.snapshot?.bar == period.rawValue else { return nil }
        return monthlyReplay.snapshot
    }

    private var historyTaskKey: String {
        let oldestDay = Int((candles.bars.first?.time ?? 0) / 86_400)
        let token = session.connection?.token.hashValue ?? 0
        return "\(symbol)|\(style.showHistory)|\(navigation.trendlineEvent?.id ?? 0)|\(oldestDay)|\(token)"
    }

    private var trendlineChartSnapshot: TrendlineChartSnapshot? {
        guard let route = navigation.trendlineEvent,
              trendlineReplay.snapshot?.routeId == route.id,
              trendlineReplay.snapshot?.period == period else { return nil }
        return trendlineReplay.snapshot
    }

    private var trendlineFocusRange: ClosedRange<Double>? {
        guard let route = navigation.trendlineEvent else { return nil }
        let marker = (trendlineChartSnapshot?.markerMs ?? route.event.candleMs) / 1000
        let halfWindow = max(period.seconds * 12, 43_200)
        return (marker - halfWindow)...(marker + halfWindow)
    }

    private func generation(for trendlineRoute: TrendlineEventRoute?, monthlyRoute: MonthlyTouchRoute?,
                            period: CandlePeriod, firstBarTime: Double?, replay: MonthFirstWeekChartSnapshot?) -> Int {
        let periodIndex = CandlePeriod.allCases.firstIndex(of: period) ?? 0
        if let trendlineRoute { return trendlineRoute.id * 100 + periodIndex + Int(firstBarTime ?? 0) }
        if let monthlyRoute { return monthlyRoute.id * 100 + periodIndex + (replay == nil ? 0 : 1) }
        return candles.generation
    }

    private func decimals(_ row: FibContract?) -> Int {
        if let tick = account.state?.instruments[symbol]?.tick { return Fmt.decimals(tick: tick) }
        guard let row else { return 4 }
        let places = [row.high, row.low].map { v -> Int in let t = String(v).split(separator: "."); return t.count > 1 ? t[1].count : 0 }.max() ?? 2
        return min(12, max(2, places))
    }

    private func coinPill(_ row: FibContract?, symbol: String, bars: [Candle], replay: MonthFirstWeekChartSnapshot?, route: MonthlyTouchRoute?) -> some View {
        let touch = row?.latestTouch, last = bars.last?.close
        return HStack(spacing: 8) {
            CoinLogo(coin: Fmt.coin(symbol), size: 24)
            Text(Fmt.coin(symbol)).font(.scaled(size: 15, weight: .bold)).foregroundStyle(Color.ink)
            Text("/ USDT").font(.scaled(size: 11, weight: .medium)).foregroundStyle(Color.muted)
            Text(last.map { FibChooser.price($0) } ?? "—").font(.scaled(size: 15, weight: .bold).monospacedDigit())
                .foregroundStyle(Color.ink).contentTransition(.numericText(value: last ?? 0)).animation(.snappy, value: last)
            if let route {
                let side = replay?.side ?? route.side
                let time = replay.flatMap { FibView.observationTime($0, period: period) } ?? route.occurredMs.map(FibView.touchTime)
                Text("月首周 · \(route.month) · \(side.map(MonthTouchStyle.edge) ?? "触价")\(time.map { " · " + $0 } ?? "")")
                    .font(.scaled(size: 11, weight: .semibold).monospacedDigit()).foregroundStyle(MonthTouchStyle.tint)
            } else if let touch {
                Text("\(touch.side == .upper ? "上沿" : "下沿") · \(FibView.touchTime(touch.occurredMs))")
                    .font(.scaled(size: 11, weight: .semibold).monospacedDigit())
                    .foregroundStyle(touch.side == .upper ? ChartPalette.down : ChartPalette.up)
                if let score = touches.score(symbol, touch) { ScoreBadge(score: score, small: true) }
            }
        }
        .lineLimit(1).fixedSize()
        .padding(.horizontal, 12).frame(height: 40)
        .frosted(Capsule(), veil: 0.82)
    }

    private var dock: some View {
        HStack(spacing: 4) {
            PeriodChips(selection: $period).frame(width: 300)
            Divider().frame(height: 18)
            Button { Haptics.tap(); settings = true } label: {
                Image(systemName: "slider.horizontal.3").font(.system(size: 14, weight: .semibold)).foregroundStyle(Color.ink).frame(width: 34, height: 34)
            }
            .buttonStyle(.plain).accessibilityLabel("图表设置")
            Button {
                Haptics.tap()
                if navigation.monthlyTouch != nil || navigation.trendlineEvent != nil { navigation.showLiveChart() }
                follow += 1
            } label: {
                Image(systemName: "arrow.right.to.line").font(.system(size: 14, weight: .semibold)).foregroundStyle(Color.ink).frame(width: 34, height: 34)
            }
            .buttonStyle(.plain).accessibilityLabel("回到最新")
        }
        .padding(.horizontal, 6).frame(height: 44)
        .frosted(Capsule(), veil: 0.82)
        .fixedSize(horizontal: true, vertical: false)
    }
}
