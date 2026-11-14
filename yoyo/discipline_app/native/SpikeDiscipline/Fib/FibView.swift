import SwiftUI

/// Live chart and touch replays share the same contract selector and period controls.
struct FibView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(FibStore.self) private var fib
    @Environment(ChartStyle.self) private var style
    @Environment(MondayHistory.self) private var history
    @Environment(TouchFeed.self) private var touches
    @Environment(MonthFirstWeekLevels.self) private var monthlyLevels
    @Environment(FibNavigation.self) private var navigation
    let active: Bool
    var onQuick: (FibQuickEntry, CandlePeriod) -> Void
    var onTrendlineBack: () -> Void = {}
    @State private var candles = CandleStore()
    @State private var monthlyReplay = MonthFirstWeekChartFeed()
    @State private var trendlineReplay = TrendlineChartFeed()
    @State private var trendlineLineFeed = TrendlineLineFeed()
    @State private var monthlyFeed = MonthFirstWeekFeed()
    @State private var chooser = false
    @State private var settings = false
    @State private var follow = 0
    @AppStorage("fib.strip.signalSource") private var stripSourceKey = FibCoinStripSource.monday.rawValue
    @Environment(\.reselect) private var reselect

    static let choices: [CandlePeriod] = [.m1, .m5, .m15, .h1, .h4, .h12, .d1]
    static let pinned = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]

    var body: some View {
        GeometryReader { proxy in
            if let route = navigation.trendlineEvent {
                let bottom = proxy.safeAreaInsets.bottom
                ZStack(alignment: .top) {
                    chart(top: proxy.safeAreaInsets.top + 58, bottom: bottom + 116)
                        .environment(\.colorScheme, .dark)
                        .ignoresSafeArea()
                    VStack(spacing: 8) {
                        trendlineEventHeader(route)
                            .padding(.horizontal, 12)
                            .padding(.top, 4)
                        Spacer(minLength: 8)
                        trendlineEventDock.padding(.horizontal, 12)
                        Text(trendlineIndicatorText)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(trendlineReplay.error == nil ? ChartPalette.up : Color.muted)
                            .lineLimit(1)
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(ChartPalette.hex(0x11151B).opacity(0.9), in: Capsule())
                            .padding(.bottom, bottom + 76)
                    }
                }
                .background(ChartPalette.hex(0x0B0E13))
            } else {
                liveChartWorkspace
            }
        }
        .sheet(isPresented: $chooser) { FibChooser(source: stripSource, monthFeed: monthlyFeed).presentationDetents([.large]).presentationDragIndicator(.visible) }
        .sheet(isPresented: $settings) { ChartSettingsSheet().presentationDetents([.medium, .large]).presentationDragIndicator(.visible) }
        .onChange(of: reselect) { _, _ in follow += 1 }   // tab tapped again → back to the latest bar
        .task(id: active) {
            guard active else { candles.stopAndSave(); return }
            await fib.poll()
        }
        .task(id: "\(active)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, let connection = session.connection else { return }
            await touches.watch(connection: connection, session: session)
        }
        .task(id: "\(active)|\(stripSourceKey)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, stripSource == .monthFirstWeek, let connection = session.connection else { return }
            await monthlyFeed.watch(connection: connection, session: session)
        }
        .task(id: "\(chartSymbol)|\(active)|\(navigation.monthlyTouch?.id ?? 0)|\(navigation.trendlineEvent?.id ?? 0)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, navigation.monthlyTouch == nil, navigation.trendlineEvent == nil,
                  let connection = session.connection else { return }
            await history.watchCurrent(chartSymbol, connection: connection)
        }
        // Full history (owner 2026-11-09): weeks back to the oldest loaded bar; older bars → older weeks.
        .task(id: "\(chartSymbol)|\(style.showHistory)|\(active)|\(navigation.monthlyTouch?.id ?? 0)|\(navigation.trendlineEvent?.id ?? 0)|\(Int((candles.bars.first?.time ?? 0) / 86_400))|\(session.connection?.token.hashValue ?? 0)") {
            guard active, navigation.monthlyTouch == nil, navigation.trendlineEvent == nil, style.showHistory else { return }
            await history.load(chartSymbol, from: candles.bars.first?.time, connection: session.connection)
        }
        .task(id: "\(active)|\(stripSourceKey)|\(chartSymbol)|\(navigation.monthlyTouch?.id ?? 0)|\(navigation.trendlineEvent?.id ?? 0)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, stripSource == .monthFirstWeek, navigation.monthlyTouch == nil, navigation.trendlineEvent == nil else { return }
            await monthlyLevels.watch(chartSymbol, connection: session.connection)
        }
        .task(id: "\(active)|\(navigation.monthlyTouch?.id ?? 0)|\(fib.period.rawValue)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, navigation.trendlineEvent == nil, let request = navigation.monthlyTouch,
                  let connection = session.connection else { return }
            await monthlyReplay.load(request: .event(request.eventId), period: fib.period, connection: connection)
        }
        .task(id: "\(active)|\(navigation.trendlineEvent?.id ?? 0)|\(fib.period.rawValue)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, let route = navigation.trendlineEvent else { return }
            await trendlineReplay.load(route: route, period: fib.period, connection: session.connection)
        }
        .task(id: "\(active)|\(chartSymbol)|\(style.showTrendline)|\(navigation.trendlineEvent?.id ?? 0)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, style.showTrendline, navigation.trendlineEvent == nil,
                  let connection = session.connection else { return }
            await trendlineLineFeed.watch(symbol: chartSymbol, connection: connection, session: session)
        }
        .onChange(of: chartKey) { _, _ in showCandles() }
        .onChange(of: active) { _, now in if now { showCandles() } }
        .onAppear { if active { showCandles() } }
    }

    private var liveChartWorkspace: some View {
        VStack(spacing: 0) {
            coinStrip.padding(.horizontal, 8)
            Divider()
            HStack(spacing: 8) {
                coinCard
                Spacer(minLength: 0)
                rotateButton
            }
            .padding(.horizontal, 12)
            Divider()
            chart(top: 0, bottom: 0)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if navigation.monthlyTouch == nil, navigation.trendlineEvent == nil, fib.current == nil, fib.error != nil, candles.bars.isEmpty {
                offlineNote
            }
            Divider()
            dock
        }
        .padding(.bottom, 68)
        .background(Color(uiColor: .systemBackground))
    }

    private var chartSymbol: String { navigation.trendlineEvent?.symbol ?? navigation.monthlyTouch?.symbol ?? fib.chartSymbol }
    private var stripSource: FibCoinStripSource { FibCoinStripSource(rawValue: stripSourceKey) ?? .monday }
    private var chartKey: String {
        "\(chartSymbol)|\(fib.period.rawValue)|\(session.connection?.token.hashValue ?? 0)|\(navigation.monthlyTouch?.id ?? 0)|\(navigation.trendlineEvent?.id ?? 0)"
    }

    private var replaySnapshot: MonthFirstWeekChartSnapshot? {
        guard let request = navigation.monthlyTouch,
              monthlyReplay.snapshot?.eventId == request.eventId,
              monthlyReplay.snapshot?.bar == fib.period.rawValue else { return nil }
        return monthlyReplay.snapshot
    }

    private var chartBars: [Candle] {
        if navigation.trendlineEvent != nil { return trendlineChartSnapshot?.candles ?? [] }
        return navigation.monthlyTouch == nil ? candles.bars : (replaySnapshot?.chartCandles ?? [])
    }

    private var trendlineChartSnapshot: TrendlineChartSnapshot? {
        guard let route = navigation.trendlineEvent,
              trendlineReplay.snapshot?.routeId == route.id,
              trendlineReplay.snapshot?.period == fib.period else { return nil }
        return trendlineReplay.snapshot
    }

    private var trendlineFocusRange: ClosedRange<Double>? {
        guard let route = navigation.trendlineEvent else { return nil }
        let marker = (trendlineChartSnapshot?.markerMs ?? route.event.candleMs) / 1000
        let halfWindow = max(fib.period.seconds * 12, 43_200)
        return (marker - halfWindow)...(marker + halfWindow)
    }

    private var trendlineIndicatorText: String {
        guard style.showTrendline else { return "4H 趋势线已关闭" }
        if navigation.trendlineEvent != nil {
            if trendlineReplay.error != nil { return "历史 1H K 线暂不可用" }
            return trendlineChartSnapshot == nil ? "正在读取历史 1H K 线" : "1H 收盘确认有效 · 4H 下降线"
        }
        return trendlineLineFeed.availabilityLabel
    }

    private func showCandles() {
        guard navigation.monthlyTouch == nil, navigation.trendlineEvent == nil else { candles.stop(); return }
        let symbol = chartSymbol
        guard active, !symbol.isEmpty else { candles.stop(); return }
        candles.show(symbol: symbol, period: fib.period, connection: session.connection)
    }

    private func decimals(_ symbol: String) -> Int {
        if let tick = account.state?.instruments[symbol]?.tick { return Fmt.decimals(tick: tick) }
        if symbol == chartSymbol, let replay = replaySnapshot {
            let prices = [replay.anchor.high, replay.anchor.low].compactMap { $0 }.filter { $0.isFinite && $0 > 0 }
            if let smallest = prices.min() { return min(12, max(2, 4 - Int(floor(log10(smallest))))) }
        }
        guard let row = fib.current?.items.first(where: { $0.symbol == symbol }) else { return 4 }
        let places = [row.high, row.low].map { v -> Int in let t = String(v).split(separator: "."); return t.count > 1 ? t[1].count : 0 }.max() ?? 2
        return min(12, max(2, places))
    }

    private func price(_ symbol: String) -> Double? {
        if symbol == chartSymbol {
            if navigation.monthlyTouch != nil { return chartBars.last?.close }
            if let last = chartBars.last?.close { return last }
        }
        if let q = account.state?.quotes[symbol], let v = Double(q.last) { return v }
        return fib.current?.items.first { $0.symbol == symbol }?.price
    }

    /// Touches whose latest touch happened today (Beijing day).
    private var todayRows: [FibContract] {
        let today = Fmt.beijingDay(Date())
        return fib.rows.filter { row in row.latestTouch.map { Fmt.beijingDay(Date(timeIntervalSince1970: $0.occurredMs / 1000)) == today } ?? false }
    }

    // MARK: pieces

    private func chart(top: CGFloat, bottom: CGFloat) -> some View {
        let row = fib.selected, symbol = chartSymbol, replay = replaySnapshot
        let trendlineRoute = navigation.trendlineEvent
        let currentRange = replay == nil
            ? (row.flatMap { fib.range(for: $0) } ?? history.currentRange(for: symbol))
            : replay?.chartWeeklyRanges.last
        let eventHistory = replay.map { Array($0.chartWeeklyRanges.dropLast()) } ?? []
        let past = replay == nil ? (style.showHistory ? (history.ranges[symbol] ?? []) : [])
            : (style.showHistory ? eventHistory : [])
        var lines = replay?.chartMonthlyLines ?? (navigation.monthlyTouch == nil
            ? FibCoinStripProjection.monthlyLines(source: stripSource, ranges: monthlyLevels.ranges[symbol] ?? [],
                                                   bars: chartBars, period: fib.period) : [])
        if style.showTrendline, let trendlineRoute, let first = chartBars.first, let last = chartBars.last,
           let line = trendlineRoute.event.chartLine(endingAt: (last.time + fib.period.seconds) * 1000,
                                                      startingAt: first.time * 1000) {
            lines.append(line)
        } else if style.showTrendline, trendlineRoute == nil, let last = chartBars.last,
                  navigation.monthlyTouch == nil,
                  let line = trendlineLineFeed.chartLine(endingAt: (last.time + fib.period.seconds) * 1000) {
            lines.append(line)
        } else if style.showTrendline, trendlineRoute == nil, navigation.monthlyTouch != nil,
                  let markerMs = replay?.markerMs, let last = chartBars.last,
                  let line = trendlineLineFeed.chartLine(endingAt: (last.time + fib.period.seconds) * 1000,
                                                         historicalTime: markerMs) {
            lines.append(line)
        }
        let markers = replay?.chartMarkers ?? (navigation.monthlyTouch == nil ? touches.markers(row) : [])
        let periodIndex = CandlePeriod.allCases.firstIndex(of: fib.period) ?? 0
        let firstBarTime = Int(chartBars.first?.time ?? 0)
        let generation: Int
        if let trendlineRoute { generation = trendlineRoute.id * 100 + periodIndex + firstBarTime }
        else if let monthlyRoute = navigation.monthlyTouch { generation = monthlyRoute.id * 100 + periodIndex + (replay == nil ? 0 : 1) }
        else { generation = candles.generation }
        let focusBarTime = trendlineRoute == nil ? replay?.locatedMarkerMs.map { $0 / 1000 }
            : trendlineChartSnapshot.map { $0.markerMs / 1000 }
        return ZStack {
            CandleChart(bars: chartBars, generation: generation, period: fib.period, decimals: decimals(symbol),
                        range: currentRange, history: past, innerLines: fib.showInternal, averages: fib.showAverages,
                        cloud: fib.showCloud, lines: lines, markers: markers,
                        focusRange: replay?.chartFocusRange ?? trendlineFocusRange,
                        timestampFocus: replay != nil || trendlineRoute != nil,
                        highlightedBarTimes: replay?.chartHighlightedBarTimes ?? [],
                        highlightRange: replay?.chartHighlightedBarTimes == nil ? replay?.chartHighlightRange : nil,
                        highlightColor: replay == nil ? nil : ChartPalette.hex(0x30D158),
                        focusBarTime: focusBarTime, fitReferenceLevels: replay == nil,
                        markHistory: replay == nil && trendlineRoute == nil, colors: style.colors,
                        watermark: symbol.isEmpty ? nil : Fmt.coin(symbol) + "USDT", followToken: follow, wallpaper: true,
                        topInset: top, bottomOverlay: bottom,
                        onNeedOlder: { if replay == nil && trendlineRoute == nil { candles.loadOlder() } })
            if chartBars.isEmpty && !symbol.isEmpty {
                if trendlineRoute != nil {
                    if trendlineReplay.loading { ProgressView("正在读取历史 1H K 线").font(.footnote).foregroundStyle(Color.muted) }
                    else { Text(trendlineReplay.error ?? "趋势线突破历史 K 线暂不可用").font(.footnote).foregroundStyle(Color.muted) }
                } else if navigation.monthlyTouch != nil {
                    if monthlyReplay.loading { ProgressView("正在读取触价 K 线").font(.footnote).foregroundStyle(Color.muted) }
                    else { Text(monthlyReplay.error ?? "月首周 K 线暂不可用").font(.footnote).foregroundStyle(Color.muted) }
                } else if candles.error == nil { ProgressView() }
                else { Text(candles.error ?? "").font(.footnote).foregroundStyle(Color.muted) }
            }
        }
        .overlay(alignment: .topLeading) {
            if style.showTrendline && trendlineRoute == nil {
                Text(trendlineIndicatorText)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(trendlineLineFeed.error == nil && trendlineLineFeed.snapshot?.status != "unavailable"
                                     ? ChartPalette.down : Color.muted)
                    .lineLimit(1).padding(.horizontal, 8).padding(.vertical, 5)
                    .background(Color(uiColor: .systemBackground).opacity(0.9), in: Capsule())
                    .padding(.top, top + 6).padding(.leading, 10)
            }
        }
    }

    private func trendlineEventHeader(_ route: TrendlineEventRoute) -> some View {
        HStack(spacing: 9) {
            Button { Haptics.tap(); onTrendlineBack() } label: {
                Image(systemName: "chevron.left").font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.white).frame(width: 34, height: 34).contentShape(.circle)
            }
            .buttonStyle(.plain).accessibilityLabel("返回趋势线列表")
            CoinLogo(coin: Fmt.coin(route.symbol), size: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(Fmt.coin(route.symbol)) · 1H 上破 4H 趋势线")
                    .font(.system(size: 13, weight: .bold)).foregroundStyle(.white)
                    .lineLimit(1).minimumScaleFactor(0.8)
                Text("\(Fmt.clock(route.event.confirmedMs)) · 线价 \(Fmt.price(route.event.level)) · 收盘 \(Fmt.price(route.event.close))")
                    .font(.system(size: 10, weight: .medium, design: .monospaced)).foregroundStyle(ChartPalette.hex(0xA7B0BC))
                    .lineLimit(1).minimumScaleFactor(0.8)
            }
            Spacer(minLength: 0)
            Text("有效上破")
                .font(.system(size: 10, weight: .bold)).foregroundStyle(ChartPalette.up)
                .padding(.horizontal, 8).padding(.vertical, 5)
                .background(ChartPalette.up.opacity(0.13), in: Capsule())
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(ChartPalette.hex(0x11151B).opacity(0.96), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.white.opacity(0.1), lineWidth: 1))
    }

    private var trendlineEventDock: some View {
        @Bindable var fib = fib
        return HStack(spacing: 3) {
            PeriodChips(selection: $fib.period).layoutPriority(1)
            Button { Haptics.tap(); settings = true } label: {
                Image(systemName: "slider.horizontal.3").font(.system(size: 13, weight: .bold)).foregroundStyle(Color.ink)
                    .frame(width: 30, height: 34).contentShape(.rect)
            }
            .buttonStyle(.plain).accessibilityLabel("图表设置")
            Button { Haptics.tap(); navigation.showLiveChart(); follow += 1 } label: {
                Text("最新").font(.system(size: 12, weight: .bold)).foregroundStyle(ChartPalette.up)
                    .padding(.horizontal, 9).frame(height: 32).background(ChartPalette.up.opacity(0.12), in: Capsule())
            }
            .buttonStyle(.plain).accessibilityLabel("返回实时图表")
        }
        .padding(4)
        .background(ChartPalette.hex(0x11151B).opacity(0.96), in: Capsule())
        .overlay(Capsule().strokeBorder(Color.white.opacity(0.1), lineWidth: 1))
    }

    /// The source switch reuses the existing strip; moving chips change source while BTC / ETH stay pinned.
    private var coinStrip: some View {
        HStack(spacing: 0) {
            stripSourcePicker
            ScrollView(.horizontal) {
                HStack(spacing: 4) {
                    if stripSource == .monday {
                        let others = todayRows.filter { !Self.pinned.contains($0.symbol) }
                        ForEach(others) { row in stripChip(row.symbol, edge: row.latestTouch?.side, scoreTouch: row.latestTouch) }
                        if others.isEmpty { stripEmptyState }
                    } else {
                        let others = monthlyStripTouches
                        ForEach(others) { event in stripChip(event.symbol, edge: event.side, touchLevel: event.level) }
                        if others.isEmpty { stripEmptyState }
                    }
                }
                .padding(.horizontal, 4)
            }
            .scrollIndicators(.hidden)
            Divider().frame(height: 26).padding(.horizontal, 2)
            HStack(spacing: 2) { ForEach(Self.pinned, id: \.self) { stripChip($0, edge: nil, compact: true) } }
                .padding(.trailing, 4)
        }
        .frame(height: 44)
    }

    private var stripSourcePicker: some View {
        Button {
            Haptics.select()
            withAnimation(.snappy) { stripSourceKey = stripSource.next.rawValue }
        } label: {
            HStack(spacing: 4) {
                Text(stripSource.title).font(.system(size: 11, weight: .semibold)).lineLimit(1)
                Image(systemName: "arrow.left.arrow.right").font(.system(size: 9, weight: .semibold))
            }
            .foregroundStyle(Color.ink)
            .padding(.horizontal, 8).frame(height: 36)
            .contentShape(.capsule)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("信号来源，当前\(stripSource.title)")
        .accessibilityHint("切换到\(stripSource.next.title)")
    }

    private var monthlyStripTouches: [MonthFirstWeekTouch] {
        FibCoinStripProjection.monthlyTouches(monthlyFeed.snapshot?.touches ?? [], month: monthlyFeed.snapshot?.month,
                                              excluding: Set(Self.pinned))
    }

    private var quickEntry: FibQuickEntry? {
        guard navigation.monthlyTouch == nil else { return nil }
        if stripSource == .monthFirstWeek {
            return FibCoinStripProjection.monthlyQuickEntry(events: monthlyFeed.events,
                                                            month: monthlyFeed.snapshot?.month,
                                                            symbol: chartSymbol,
                                                            nowMs: monthlyFeed.snapshot?.nowMs ?? 0)
        }
        return FibCoinStripProjection.mondayQuickEntry(row: fib.selected, week: fib.current?.week, live: fib.live)
    }

    private var stripEmptyState: some View {
        let title: String
        if stripSource == .monday {
            title = "今日暂无其他触价"
        } else if !monthlyFeed.loaded && monthlyFeed.error == nil {
            title = "加载月首周信号…"
        } else if monthlyFeed.snapshot == nil {
            title = "月首周数据暂不可用"
        } else {
            title = "暂无月首周触价"
        }
        return Text(title).font(.scaled(size: 12, weight: .medium)).foregroundStyle(Color.muted).padding(.horizontal, 10)
    }

    private func stripChip(_ symbol: String, edge: Edge?, compact: Bool = false, scoreTouch: FibTouch? = nil, touchLevel: Double? = nil) -> some View {
        let on = symbol == chartSymbol, coin = Fmt.coin(symbol)
        return Button {
            Haptics.select()
            navigation.showLiveChart()
            withAnimation(.snappy) { fib.focus(symbol) }
        } label: {
            HStack(spacing: 5) {
                CoinLogo(coin: coin, size: 20)
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 4) {
                        Text(coin).font(.scaled(size: 12, weight: .bold)).foregroundStyle(Color.ink).lineLimit(1)
                        if !compact, let scoreTouch, let score = touches.score(symbol, scoreTouch) {
                            ScoreBadge(score: score, small: true)
                        }
                    }
                    if !compact {
                        Text((touchLevel ?? price(symbol)).map { $0.formatted(.number.precision(.significantDigits(1...5)).grouping(.never)) } ?? "—")
                            .font(.scaled(size: 10, weight: .medium).monospacedDigit()).foregroundStyle(edge == .upper ? ChartPalette.down : ChartPalette.up)
                    }
                }
            }
            .padding(.horizontal, 7).frame(height: 40)
            .background(on ? Color.accentColor.opacity(0.12) : .clear, in: .rect(cornerRadius: 16))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("查看 \(coin)")
        .accessibilityValue(touchLevel.map { "月首周触价 \($0.formatted(.number.precision(.significantDigits(1...8)).grouping(.never)))" } ?? "")
    }

    /// Turns the chart to landscape (owner 2026-11-08 item 6); also works with the rotation lock on.
    private var rotateButton: some View {
        Button { Haptics.tap(); Orientation.rotate(landscape: true) } label: {
            Image(systemName: "rotate.right").frame(width: 44, height: 44).contentShape(.rect)
        }
        .buttonStyle(.plain).accessibilityLabel("横屏查看")
    }

    /// Contract selector (tap → 最新触价 list): logo, coin, live price and this week's touch.
    private var coinCard: some View {
        let symbol = chartSymbol, replay = replaySnapshot
        let monthTouch = stripSource == .monthFirstWeek
            ? FibCoinStripProjection.latestTouch(monthlyFeed.events, month: monthlyFeed.snapshot?.month, symbol: symbol) : nil
        let touch = stripSource == .monthFirstWeek ? monthTouch.map { (side: $0.side, time: $0.occurredMs) }
            : fib.selected?.latestTouch.map { (side: $0.side, time: $0.occurredMs) }
        return Button { Haptics.open(); navigation.showLiveChart(); chooser = true } label: {
            HStack(spacing: 9) {
                if !symbol.isEmpty { CoinLogo(coin: Fmt.coin(symbol), size: 30) }
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 3) {
                        Text(symbol.isEmpty ? "选择币种" : Fmt.coin(symbol)).font(.scaled(size: 17, weight: .bold)).foregroundStyle(Color.ink)
                            .lineLimit(1).minimumScaleFactor(0.75).layoutPriority(1)
                        Text("/ USDT").font(.scaled(size: 11, weight: .medium)).foregroundStyle(Color.muted).fixedSize()
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold)).foregroundStyle(Color.muted)
                    }
                    .fixedSize(horizontal: true, vertical: false)
                    if let route = navigation.monthlyTouch {
                        let side = replay?.side ?? route.side
                        let time = replay.flatMap { Self.observationTime($0, period: fib.period) } ?? route.occurredMs.map(FibView.touchTime)
                        Text("月首周 · \(route.month) · \(side.map(MonthTouchStyle.edge) ?? "触价")\(time.map { " · " + $0 } ?? "")")
                            .font(.scaled(size: 11, weight: .medium)).foregroundStyle(MonthTouchStyle.tint)
                            .lineLimit(1).minimumScaleFactor(0.72)
                    } else if let touch {
                        Text("\(touch.side == .upper ? "上沿" : "下沿") · \(FibView.touchTime(touch.time))")
                            .font(.scaled(size: 11, weight: .medium)).foregroundStyle(touch.side == .upper ? ChartPalette.down : ChartPalette.up)
                            .lineLimit(1).minimumScaleFactor(0.78)
                    } else {
                        Text(stripSource == .monday ? "本周未触价" : monthlyFeed.snapshot == nil ? "月首周信号待读取" : "本月未触价")
                            .font(.scaled(size: 11, weight: .medium)).foregroundStyle(Color.muted)
                    }
                }
                if let p = price(symbol) {
                    Text(p.formatted(.number.precision(.fractionLength(decimals(symbol))).grouping(.never)))
                        .font(.scaled(size: 16, weight: .bold).monospacedDigit())
                        .foregroundStyle((chartBars.last.map { $0.close >= $0.open } ?? true) ? ChartPalette.up : ChartPalette.down)
                        .padding(.leading, 4)
                }
            }
            .frame(minHeight: 46, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(CardPressStyle())
        .accessibilityLabel("选择合约，当前 \(Fmt.coin(symbol))")
    }

    /// One slim bar: period chips, chart settings, follow, and a small 开单 pill.
    private var dock: some View {
        @Bindable var fib = fib
        let locked = account.pendingEntry != nil || account.closeAttempt != nil || session.connection == nil
        let entry = quickEntry
        return HStack(spacing: 2) {
            PeriodChips(selection: $fib.period)
            Button { Haptics.open(); settings = true } label: {
                Image(systemName: "slider.horizontal.3").font(.system(size: 14, weight: .bold)).foregroundStyle(Color.ink).frame(width: 32, height: 34).contentShape(.rect)
            }
            .buttonStyle(.plain).accessibilityLabel("图表设置")
            Button {
                Haptics.tap()
                if navigation.monthlyTouch != nil { navigation.showLiveChart() }
                follow += 1
            } label: {
                Image(systemName: "arrow.right.to.line").font(.system(size: 13, weight: .bold)).foregroundStyle(Color.ink).frame(width: 30, height: 34).contentShape(.rect)
            }
            .buttonStyle(.plain).accessibilityLabel("跟随最新")
            Button { if let entry { Haptics.open(); onQuick(entry, fib.period) } } label: {
                HStack(spacing: 3) { Image(systemName: "bolt.fill"); Text("开单") }.font(.system(size: 13, weight: .bold)).lineLimit(1).fixedSize()
            }
            .buttonStyle(.pill(.solid(.accentColor), height: 34))
            .disabled(locked || entry == nil)
            .accessibilityLabel("快捷开单 1R")
        }
        .padding(.horizontal, 8)
        .frame(minHeight: 46)
    }

    private var offlineNote: some View {
        Button { Haptics.tap(); Task { try? await fib.refresh() } } label: {
            Label("触价监控暂未连接 · 点此重试", systemImage: "wifi.exclamationmark").font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                .padding(.horizontal, 14).padding(.vertical, 10).frosted(Capsule())
        }
        .buttonStyle(.plain)
    }

    static func touchTime(_ ms: Double) -> String {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "MM/dd HH:mm"
        return f.string(from: Date(timeIntervalSince1970: ms / 1000))
    }

    static func observationTime(_ snapshot: MonthFirstWeekChartSnapshot, period: CandlePeriod) -> String? {
        if period == .m1, let marker = snapshot.locatedMarkerMs {
            return "\(touchTime(marker)) 1分定位"
        }
        guard let start = snapshot.observationStartMs, let end = snapshot.observationEndMs else {
            return snapshot.markerMs.map(touchTime)
        }
        let duration = end - start
        guard duration > 0 else { return touchTime(start) }
        let inclusiveEnd = end - 60_000   // observationEndMs is exclusive
        let span: String
        if duration >= 3_600_000 {
            span = "\(touchTime(start))–\(touchTime(inclusiveEnd))"
        } else if duration >= 60_000 {
            span = duration == 60_000 ? touchTime(start) : "\(touchTime(start))–\(touchTime(inclusiveEnd))"
        } else {
            span = touchTime(start)
        }
        return snapshot.source == "history" && duration >= 3_600_000 ? "\(span) 小时窗" : span
    }
}

/// Horizontally scrolling period chips that keep the selected period in view (1.618 and 合约 docks).
struct PeriodChips: View {
    @Binding var selection: CandlePeriod
    var body: some View {
        ScrollViewReader { reader in
            ScrollView(.horizontal) {
                HStack(spacing: 1) {
                    ForEach(FibView.choices) { p in
                        Button { Haptics.select(); selection = p } label: {
                            Text(p.rawValue).font(.scaled(size: 13, weight: .bold).monospacedDigit())
                                .foregroundStyle(selection == p ? Color.white : Color.ink)
                                .padding(.horizontal, 8).frame(height: 34)
                                .background(selection == p ? Color.accentColor : .clear, in: .capsule)
                                .contentShape(.capsule)
                        }
                        .buttonStyle(.plain)
                        .id(p)
                        .accessibilityAddTraits(selection == p ? .isSelected : [])
                    }
                }
            }
            .scrollIndicators(.hidden)
            .onAppear { reader.scrollTo(selection, anchor: .center) }
            .onChange(of: selection) { _, now in withAnimation(.snappy) { reader.scrollTo(now, anchor: .center) } }
        }
    }
}

struct ToggleChip: View {
    let title: String
    @Binding var on: Bool
    var body: some View {
        Button { Haptics.select(); withAnimation(.snappy) { on.toggle() } } label: {
            Text(title).font(.scaled(size: 13, weight: .semibold)).lineLimit(1).fixedSize()
                .foregroundStyle(on ? Color.accentColor : Color.ink)
                .padding(.horizontal, 9).frame(height: 34)
                .background(on ? Color.accentColor.opacity(0.13) : .clear, in: .capsule)
                .contentShape(.capsule)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

struct TrailingIcon: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 3) { configuration.title; configuration.icon }
    }
}

/// 图表设置: layers and candle colours (presets or custom), shared by 1.618 and 合约.
struct ChartSettingsSheet: View {
    @AppStorage("chart.fitReferencePrices") private var showOffscreenPrices = true
    @Environment(FibStore.self) private var fib
    @Environment(ChartStyle.self) private var style
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        @Bindable var fib = fib
        @Bindable var style = style
        NavigationStack {
            Form {
                Section {
                    Toggle("提示屏外参考价", isOn: $showOffscreenPrices)
                } header: {
                    Text("参考价")
                } footer: {
                    Text("图内参考线按真实价格绘制。点图表边缘的箭头可查看屏外价位，K 线比例不变。")
                }
                Section("图层") {
                    Toggle("云带", isOn: $fib.showCloud)
                    Toggle("六均线", isOn: $fib.showAverages)
                    Toggle("内部线 0.705 · 0.618 · 0.5", isOn: $fib.showInternal)
                    Toggle("历史周区间", isOn: $style.showHistory)
                    Toggle("4H 趋势线", isOn: $style.showTrendline)
                }
                Section("K 线配色") {
                    SoftSegment(selection: $style.palette, options: ChartStyle.Palette.allCases.map { ($0, $0.title) })
                        .listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                    if style.palette == .custom {
                        ColorPicker("上涨", selection: $style.customUp, supportsOpacity: false)
                        ColorPicker("下跌", selection: $style.customDown, supportsOpacity: false)
                    }
                    Toggle("周一 K 线高亮", isOn: $style.mondayHighlight)
                    if style.mondayHighlight { ColorPicker("周一颜色", selection: $style.mondayColor, supportsOpacity: false) }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Color.canvas)
            .navigationTitle("图表设置").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
            .onChange(of: style.palette) { _, _ in Haptics.select() }
        }
    }
}
