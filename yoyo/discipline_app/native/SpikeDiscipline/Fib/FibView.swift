import SwiftUI

/// 1.618 (owner 2026-11-08, replicating the web mobile workbench): a full-screen chart with floating
/// controls only — the coin strip (today's touches, BTC / ETH pinned), the coin card (tap to open
/// 最新触价), and one bottom bar with periods, chart settings, follow and a small 开单 pill.
/// Previous weeks' ranges and custom candle colours come from ChartStyle / MondayHistory.
struct FibView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(FibStore.self) private var fib
    @Environment(ChartStyle.self) private var style
    @Environment(MondayHistory.self) private var history
    @Environment(TouchFeed.self) private var touches
    let active: Bool
    var onQuick: (FibContract, CandlePeriod) -> Void
    @State private var candles = CandleStore()
    @State private var chooser = false
    @State private var settings = false
    @State private var follow = 0
    @Environment(\.reselect) private var reselect

    static let choices: [CandlePeriod] = [.m1, .m5, .m15, .h1, .h4, .h12, .d1]
    static let pinned = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]

    var body: some View {
        GeometryReader { proxy in
            let top = proxy.safeAreaInsets.top, bottom = proxy.safeAreaInsets.bottom
            ZStack(alignment: .top) {
                chart(top: top, bottom: bottom).ignoresSafeArea()
                VStack(alignment: .leading, spacing: 8) {
                    coinStrip
                    HStack(alignment: .top, spacing: 8) {
                        coinCard
                        Spacer(minLength: 8)
                        rotateButton
                    }
                    Spacer(minLength: 0)
                    if fib.current == nil && fib.error != nil && candles.bars.isEmpty { offlineNote }
                    dock
                }
                .padding(.horizontal, 12)
                .padding(.top, 4)
                .padding(.bottom, 78)
            }
        }
        .sheet(isPresented: $chooser) { FibChooser().presentationDetents([.large]).presentationDragIndicator(.visible) }
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
        .task(id: "\(fib.chartSymbol)|\(style.showHistory)|\(active)") {
            guard active, style.showHistory else { return }
            await history.load(fib.chartSymbol, connection: session.connection)
        }
        .onChange(of: chartKey) { _, _ in showCandles() }
        .onChange(of: active) { _, now in if now { showCandles() } }
        .onAppear { if active { showCandles() } }
    }

    private var chartKey: String { "\(fib.chartSymbol)|\(fib.period.rawValue)|\(session.connection?.token.hashValue ?? 0)" }

    private func showCandles() {
        let symbol = fib.chartSymbol
        guard active, !symbol.isEmpty else { candles.stop(); return }
        candles.show(symbol: symbol, period: fib.period, connection: session.connection)
    }

    private func decimals(_ symbol: String) -> Int {
        if let tick = account.state?.instruments[symbol]?.tick { return Fmt.decimals(tick: tick) }
        guard let row = fib.current?.items.first(where: { $0.symbol == symbol }) else { return 4 }
        let places = [row.high, row.low].map { v -> Int in let t = String(v).split(separator: "."); return t.count > 1 ? t[1].count : 0 }.max() ?? 2
        return min(12, max(2, places))
    }

    private func price(_ symbol: String) -> Double? {
        if symbol == fib.chartSymbol, let last = candles.bars.last?.close { return last }
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
        let row = fib.selected, symbol = fib.chartSymbol
        let past = style.showHistory ? (history.ranges[symbol] ?? []) : []
        return ZStack {
            CandleChart(bars: candles.bars, generation: candles.generation, period: fib.period, decimals: decimals(symbol),
                        range: row.flatMap { fib.range(for: $0) }, history: past, innerLines: fib.showInternal, averages: fib.showAverages,
                        cloud: fib.showCloud, markers: touches.markers(row), markHistory: true, colors: style.colors,
                        watermark: symbol.isEmpty ? nil : Fmt.coin(symbol) + "USDT", followToken: follow, wallpaper: true,
                        topInset: top + 58, bottomOverlay: bottom + 78 + 50,
                        onNeedOlder: { candles.loadOlder() })
            if candles.bars.isEmpty && !symbol.isEmpty {
                if candles.error == nil { ProgressView() } else { Text(candles.error ?? "").font(.footnote).foregroundStyle(Color.muted) }
            }
        }
    }

    /// Web-mobile floating coin strip: today's touched coins scroll; BTC and ETH stay pinned at the right.
    private var coinStrip: some View {
        HStack(spacing: 0) {
            ScrollView(.horizontal) {
                let others = todayRows.filter { !Self.pinned.contains($0.symbol) }
                HStack(spacing: 4) {
                    ForEach(others) { row in stripChip(row.symbol, edge: row.latestTouch?.side) }
                    if others.isEmpty { Text("今日暂无其他触价").font(.scaled(size: 12, weight: .medium)).foregroundStyle(Color.muted).padding(.horizontal, 10) }
                }
                .padding(.horizontal, 4)
            }
            .scrollIndicators(.hidden)
            Divider().frame(height: 26).padding(.horizontal, 2)
            HStack(spacing: 2) { ForEach(Self.pinned, id: \.self) { stripChip($0, edge: nil, compact: true) } }
                .padding(.trailing, 4)
        }
        .frame(height: 48)
        .frosted(RoundedRectangle(cornerRadius: 24, style: .continuous))
    }

    private func stripChip(_ symbol: String, edge: Edge?, compact: Bool = false) -> some View {
        let on = symbol == fib.chartSymbol, coin = Fmt.coin(symbol)
        return Button { Haptics.select(); withAnimation(.snappy) { fib.focus(symbol) } } label: {
            HStack(spacing: 5) {
                CoinLogo(coin: coin, size: 20)
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 4) {
                        Text(coin).font(.scaled(size: 12, weight: .bold)).foregroundStyle(Color.ink).lineLimit(1)
                        if !compact, let score = touches.score(symbol, fib.rows.first { $0.symbol == symbol }?.latestTouch) {
                            ScoreBadge(score: score, small: true)
                        }
                    }
                    if !compact {
                        Text(price(symbol).map { $0.formatted(.number.precision(.significantDigits(1...5)).grouping(.never)) } ?? "—")
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
    }

    /// Turns the chart to landscape (owner 2026-11-08 item 6); also works with the rotation lock on.
    private var rotateButton: some View {
        RoundIcon(systemName: "rotate.right", size: 36) { Orientation.rotate(landscape: true) }.accessibilityLabel("横屏查看")
    }

    /// Floating coin card (tap → 最新触价 list): logo, coin, live price and this week's touch.
    private var coinCard: some View {
        let symbol = fib.chartSymbol, touch = fib.selected?.latestTouch
        return Button { Haptics.open(); chooser = true } label: {
            HStack(spacing: 9) {
                if !symbol.isEmpty { CoinLogo(coin: Fmt.coin(symbol), size: 30) }
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 3) {
                        Text(symbol.isEmpty ? "选择币种" : Fmt.coin(symbol)).font(.scaled(size: 17, weight: .bold)).foregroundStyle(Color.ink)
                        Text("/ USDT").font(.scaled(size: 11, weight: .medium)).foregroundStyle(Color.muted)
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold)).foregroundStyle(Color.muted)
                    }
                    if let touch {
                        Text("\(touch.side == .upper ? "上沿" : "下沿") · \(FibView.touchTime(touch.occurredMs))")
                            .font(.scaled(size: 11, weight: .medium)).foregroundStyle(touch.side == .upper ? ChartPalette.down : ChartPalette.up)
                    } else {
                        Text("本周未触价").font(.scaled(size: 11, weight: .medium)).foregroundStyle(Color.muted)
                    }
                }
                if let p = price(symbol) {
                    Text(p.formatted(.number.precision(.fractionLength(decimals(symbol))).grouping(.never)))
                        .font(.scaled(size: 16, weight: .bold).monospacedDigit())
                        .foregroundStyle((candles.bars.last.map { $0.close >= $0.open } ?? true) ? ChartPalette.up : ChartPalette.down)
                        .contentTransition(.numericText(value: p)).animation(.snappy, value: p)
                        .padding(.leading, 4)
                }
            }
            .padding(.leading, 8).padding(.trailing, 14).padding(.vertical, 8)
            .frosted(RoundedRectangle(cornerRadius: 20, style: .continuous))
        }
        .buttonStyle(CardPressStyle())
        .accessibilityLabel("最新触价列表，当前 \(Fmt.coin(symbol))")
    }

    /// One slim bar: period chips, chart settings, follow, and a small 开单 pill.
    private var dock: some View {
        @Bindable var fib = fib
        let locked = account.pendingEntry != nil || account.closeAttempt != nil || session.connection == nil
        let row = fib.selected
        return HStack(spacing: 2) {
            PeriodChips(selection: $fib.period)
            Button { Haptics.open(); settings = true } label: {
                Image(systemName: "slider.horizontal.3").font(.system(size: 14, weight: .bold)).foregroundStyle(Color.ink).frame(width: 32, height: 34).contentShape(.rect)
            }
            .buttonStyle(.plain).accessibilityLabel("图表设置")
            Button { Haptics.tap(); follow += 1 } label: {
                Image(systemName: "arrow.right.to.line").font(.system(size: 13, weight: .bold)).foregroundStyle(Color.ink).frame(width: 30, height: 34).contentShape(.rect)
            }
            .buttonStyle(.plain).accessibilityLabel("跟随最新")
            Button { if let row { Haptics.open(); onQuick(row, fib.period) } } label: {
                HStack(spacing: 3) { Image(systemName: "bolt.fill"); Text("开单") }.font(.system(size: 13, weight: .bold)).lineLimit(1).fixedSize()
            }
            .buttonStyle(.pill(.solid(.accentColor), height: 34))
            .disabled(locked || row == nil || !fib.fresh || !fib.live)
            .accessibilityLabel("快捷开单 1R")
        }
        .padding(5)
        .frosted(Capsule())
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
    @Environment(FibStore.self) private var fib
    @Environment(ChartStyle.self) private var style
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        @Bindable var fib = fib
        @Bindable var style = style
        NavigationStack {
            Form {
                Section("图层") {
                    Toggle("云带", isOn: $fib.showCloud)
                    Toggle("六均线", isOn: $fib.showAverages)
                    Toggle("内部线 0.705 · 0.618 · 0.5", isOn: $fib.showInternal)
                    Toggle("历史周区间", isOn: $style.showHistory)
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
