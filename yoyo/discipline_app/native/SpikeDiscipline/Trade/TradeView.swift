import SwiftUI

/// Inline order ticket and order book. The chart uses the same contract and draft.
struct TradeView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @Environment(FibStore.self) private var fib
    @Environment(ChartStyle.self) private var style
    @Environment(MondayHistory.self) private var history
    @Environment(TouchFeed.self) private var touches
    let active: Bool
    @Binding var showingChart: Bool
    var onDestination: (Trading.Destination) -> Void
    @State private var showSettings = false
    @State private var showMarketDepth = false
    @State private var quoteDetailsExpanded = false
    @State private var picker = false
    @State private var discipline = false
    @Environment(\.dynamicTypeSize) private var textSize
    @State private var chartSettings = false
    @State private var candles = CandleStore()
    @AppStorage("trade.period") private var period: CandlePeriod = .m15
    @State private var follow = 0
    @Environment(\.reselect) private var reselect

    var body: some View {
        let state = account.state
        let existing = ticket.existing(state)
        let block = Trading.availability(state: state, symbol: ticket.symbol, connected: session.connection != nil, online: account.online,
                                         fresh: account.quoteFresh(ticket.symbol), pending: account.pendingEntry != nil || account.closeAttempt != nil,
                                         room: Trading.allocationRoom(state, symbol: ticket.symbol), now: account.now)
        GeometryReader { proxy in
            VStack(spacing: 0) {
                terminalHeader
                Divider()
                if showingChart {
                    VStack(spacing: 0) {
                        chart(existing: existing)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                        Divider()
                        HStack(spacing: 6) {
                            dock
                            Button { Haptics.select(); showingChart = false } label: {
                                Text("返回交易").font(.scaled(size: 13, weight: .semibold)).fixedSize()
                            }
                            .buttonStyle(.pill(.soft, height: 34))
                        }
                        .padding(.horizontal, 8)
                        .frame(minHeight: 46)
                    }
                    .padding(.bottom, 68)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
                            if let block { GuardStrip(text: block.text) { route(block.destination) } }
                            let narrow = proxy.size.width < 355 || textSize >= .xxLarge
                            quoteSummary(narrow: narrow)
                            orderForm
                            if let entry = account.activeEntry { TradeProgressCard(entry: entry) }
                            Divider()
                            TradeAccountSection(symbol: ticket.symbol, onOpenSymbol: { symbol in
                                guard !ticket.reviewing, account.pendingEntry == nil else { return }
                                if symbol != ticket.symbol {
                                    ticket.choose(symbol); ticket.syncLeverage(account.state); ticket.syncExisting(account.state)
                                }
                                Haptics.open(); showingChart = true
                            })
                        }
                        .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 82)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .scrollIndicators(.hidden)
                    .refreshable { await account.refresh(session: session) }
                }
            }
            .background(Color(uiColor: .systemBackground))
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $picker) { SymbolPicker().presentationDragIndicator(.visible) }
        .sheet(isPresented: $discipline) { NavigationStack { DisciplineView() }.presentationDragIndicator(.visible) }
        .sheet(isPresented: $chartSettings) { ChartSettingsSheet().presentationDetents([.medium, .large]).presentationDragIndicator(.visible) }
        .sheet(isPresented: $showMarketDepth) {
            orderBook
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
        .task(id: "\(showingChart)|\(ticket.symbol)|\(style.showHistory)|\(active)|\(Int((candles.bars.first?.time ?? 0) / 86_400))|\(session.connection?.token.hashValue ?? 0)") {
            guard active, showingChart, style.showHistory else { return }
            await history.load(ticket.symbol, from: candles.bars.first?.time, connection: session.connection)
        }
        .task(id: "\(showingChart)|\(ticket.symbol)|\(active)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, showingChart, let connection = session.connection else { return }
            await history.watchCurrent(ticket.symbol, connection: connection)
        }
        .onChange(of: reselect) { _, _ in follow += 1 }   // tab tapped again → back to the latest bar
        .task(id: active) {
            guard active else { candles.stopAndSave(); return }
            if fib.current == nil { _ = try? await fib.refresh() }
        }
        .task(id: "\(showingChart)|\(active)|\(session.connection?.token.hashValue ?? 0)") {
            guard active, showingChart, let connection = session.connection else { return }
            await touches.watch(connection: connection, session: session)   // touch-marker scores
        }
        .onChange(of: "\(ticket.symbol)|\(period.rawValue)|\(active)|\(session.connection?.token.hashValue ?? 0)") { _, _ in showCandles() }
        .onChange(of: showingChart) { _, _ in showCandles() }
        .onChange(of: active) { _, isActive in if !isActive { showMarketDepth = false } }
        .onChange(of: ticket.symbol) { _, _ in quoteDetailsExpanded = false }
        .onAppear { showCandles(); ticket.syncLeverage(state) }
        .onChange(of: "\(ticket.symbol)|\(state?.policyRevision ?? -1)|\(ticket.cap(state))|\(state?.sharedLeverage?[ticket.symbol] ?? -1)") { _, _ in ticket.syncLeverage(state) }
        .onChange(of: "\(existing?.id ?? "")|\(existing?.stop ?? "")|\(existing?.leverage ?? 0)") { _, _ in ticket.syncExisting(state) }
    }

    private var orderForm: some View {
        TicketForm(onBlocked: route, hosts: active && !ticket.fibTicket, inline: true)
    }

    private var orderBook: some View {
        let symbol = ticket.symbol, quote = account.state?.quotes[symbol]
        return MarketDepthView(symbol: symbol, latestPrice: quote?.last,
                               todayChangePct: quote.flatMap { Trading.dayChange($0, now: account.now) },
                               tick: tick, quoteFresh: account.quoteFresh(symbol), active: active && !showingChart && !ticket.fibTicket,
                               canSelectPrice: active && !ticket.reviewing && ticket.preview == nil && account.pendingEntry == nil && account.closeAttempt == nil && !ticket.fibTicket) { price in
            guard symbol == ticket.symbol, active, !ticket.reviewing, ticket.preview == nil, account.pendingEntry == nil, account.closeAttempt == nil, !ticket.fibTicket else { return }
            ticket.kind = "limit"; ticket.price = price; ticket.fieldIssue = nil
            Haptics.select()
            showMarketDepth = false
        }
    }

    private func quoteSummary(narrow: Bool) -> some View {
        let symbol = ticket.symbol
        let quote = account.state?.quotes[symbol]
        let fresh = account.quoteFresh(symbol)
        let tick = account.state?.instruments[symbol]?.tick
        let change = quote.flatMap { Trading.dayChange($0, now: account.now) }
        let bid = fresh ? quoteNumber(quote?.bid) : nil
        let ask = fresh ? quoteNumber(quote?.ask) : nil
        let spread: (Double, Double)? = {
            guard let bid, let ask, ask >= bid else { return nil }
            let distance = ask - bid, midpoint = (ask + bid) / 2
            guard midpoint > 0 else { return nil }
            return (distance, distance / midpoint * 100)
        }()

        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(Fmt.price(quote?.last, tick: tick))
                        .font(.system(size: 23, weight: .bold, design: .rounded).monospacedDigit())
                        .foregroundStyle(fresh ? Fmt.tone(change).color : Color.muted)
                        .lineLimit(1)
                        .minimumScaleFactor(0.72)
                    HStack(spacing: 5) {
                        Text(quoteCaption(quote: quote, fresh: fresh))
                            .foregroundStyle(.secondary)
                        if fresh, let change {
                            Text(Fmt.signedPercent(change))
                                .fontWeight(.semibold)
                                .foregroundStyle(Fmt.tone(change).color)
                        }
                    }
                    .font(.caption2.monospacedDigit())
                }
                Spacer(minLength: 4)
                Button { Haptics.open(); showMarketDepth = true } label: {
                    HStack(spacing: 5) {
                        Text("盘口深度").font(.caption.weight(.semibold))
                        Image(systemName: "chevron.right").font(.system(size: 9, weight: .bold))
                    }
                    .padding(.horizontal, 10).frame(height: 32)
                    .background(Color.field, in: .rect(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .disabled(!active || showingChart || ticket.fibTicket)
                .accessibilityLabel("查看五档盘口")
            }

            if narrow {
                DisclosureGroup(isExpanded: $quoteDetailsExpanded) {
                    quoteRows(tick: tick, bid: bid, ask: ask, spread: spread)
                        .padding(.top, 8)
                } label: {
                    Text("买卖点位与价差")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                }
                .tint(Color.muted)
            } else {
                quoteRows(tick: tick, bid: bid, ask: ask, spread: spread)
            }
        }
        .padding(12)
        .background(Color.field.opacity(0.45), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Color.muted.opacity(0.2), lineWidth: 0.6))
        .accessibilityElement(children: .contain)
    }

    private func quoteRows(tick: String?, bid: Double?, ask: Double?, spread: (Double, Double)?) -> some View {
        HStack(alignment: .top, spacing: 6) {
            quoteValue(title: "买一", value: bid.map { Fmt.price(String($0), tick: tick) }, color: .up, alignment: .leading)
            quoteValue(title: "价差", value: spread.map { "\(Fmt.price(String($0.0), tick: tick)) (\(Fmt.amount($0.1, 4))%)" }, color: .ink, alignment: .center)
            quoteValue(title: "卖一", value: ask.map { Fmt.price(String($0), tick: tick) }, color: .down, alignment: .trailing)
        }
        .font(.caption2.monospacedDigit())
        .padding(.top, 8)
        .overlay(alignment: .top) { Rectangle().fill(Color.muted.opacity(0.22)).frame(height: 0.5) }
    }

    private func quoteValue(title: String, value: String?, color: Color, alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 3) {
            Text(title).font(.system(size: 10, weight: .medium)).foregroundStyle(Color.muted)
            Text(value ?? "—")
                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                .foregroundStyle(value == nil ? Color.muted : color)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: Alignment(horizontal: alignment, vertical: .center))
    }

    private func quoteNumber(_ text: String?) -> Double? {
        guard let value = text.flatMap(Double.init), value.isFinite, value > 0 else { return nil }
        return value
    }

    private func quoteCaption(quote: Quote?, fresh: Bool) -> String {
        guard session.connection != nil else { return "未连接" }
        guard account.online else { return "行情断开" }
        guard quote != nil else { return "等待行情" }
        return fresh ? "最新价" : "上次报价 · 已过期"
    }

    private var terminalHeader: some View {
        VStack(spacing: 2) {
            HStack(spacing: 5) {
                Button { Haptics.open(); picker = true } label: {
                    HStack(spacing: 5) {
                        Text(Fmt.coin(ticket.symbol) + "USDT").font(.scaled(size: 22, weight: .bold))
                            .lineLimit(1).minimumScaleFactor(0.7)
                        Text("永续").font(.caption2.weight(.semibold)).foregroundStyle(Color.amber)
                            .padding(.horizontal, 5).padding(.vertical, 2).background(Color.amber.opacity(0.12), in: .rect(cornerRadius: 4))
                        Image(systemName: "chevron.down").font(.caption2.weight(.bold))
                    }.frame(minHeight: 44)
                }.buttonStyle(.plain).disabled(ticket.reviewing || account.pendingEntry != nil)
                    .accessibilityLabel("选择合约，当前 " + Fmt.coin(ticket.symbol))
                Spacer(minLength: 2)
                if showingChart {
                    headerIcon("rotate.right", "横屏查看") { Orientation.rotate(landscape: true) }
                } else {
                    headerIcon("chart.xyaxis.line", "合约 K 线") { showingChart = true }
                    headerIcon("ellipsis.circle", "交易设置") { showSettings = true }
                }
            }
            HStack(spacing: 5) {
                Circle().fill(account.online ? Color.up : Color.muted).frame(width: 6, height: 6)
                Text(session.connection == nil ? "未连接" : (account.live ? "实盘" : "模拟") + (account.online ? "" : " · 待同步"))
                    .font(.caption.weight(.semibold))
                if let name = account.state?.accountName { Text(name).font(.caption).lineLimit(1) }
                Spacer()
                Button { discipline = true } label: {
                    Label(account.state?.discipline.map { "剩余 \($0.entriesRemaining) 次 · 日亏额度 \(Fmt.amount($0.dailyLossRemaining)) U" } ?? "交易纪律", systemImage: "shield")
                        .font(.caption.monospacedDigit()).padding(.vertical, 6)
                }.buttonStyle(.plain)
            }.foregroundStyle(.secondary)
        }.padding(.horizontal, 14)
    }

    private func headerIcon(_ icon: String, _ label: String, action: @escaping () -> Void) -> some View {
        Button { Haptics.select(); action() } label: {
            Image(systemName: icon).font(.system(size: 18, weight: .medium))
                .frame(width: 44, height: 44).contentShape(.rect)
        }.buttonStyle(.plain).accessibilityLabel(label)
    }

    private func showCandles() {
        guard active, showingChart, !ticket.symbol.isEmpty else { candles.stopAndSave(); return }
        candles.show(symbol: ticket.symbol, period: period, connection: session.connection)
    }

    private func route(_ destination: Trading.Destination) {
        switch destination {
        case .settings: showSettings = true
        case .discipline: discipline = true
        case .positions: onDestination(.positions)
        }
    }

    private var tick: String? { account.state?.instruments[ticket.symbol]?.tick }

    // MARK: chart

    private func chart(existing: Position?) -> some View {
        let symbol = ticket.symbol
        let row = fib.current?.items.first { $0.symbol == symbol }
        let currentRange = row.flatMap { fib.range(for: $0) } ?? history.currentRange(for: symbol)
        var lines: [ChartLine] = []
        if let e = existing {
            lines = [ChartLine(price: Double(e.entry) ?? 0, title: "开仓", color: ChartPalette.accent),
                     ChartLine(price: Double(e.stop) ?? 0, title: "止损", color: ChartPalette.down, dashed: true)]
        } else if let stop = Double(ticket.stop), stop > 0 {
            lines = [ChartLine(price: stop, title: "止损", color: ChartPalette.down, dashed: true)]
        }
        return ZStack {
            CandleChart(bars: candles.bars, generation: candles.generation, period: period, decimals: Fmt.decimals(tick: tick),
                        range: currentRange, history: style.showHistory ? (history.ranges[symbol] ?? []) : [],
                        innerLines: fib.showInternal, averages: fib.showAverages, cloud: fib.showCloud,
                        lines: lines, markers: touches.markers(row), markHistory: true,
                        colors: style.colors, watermark: Fmt.coin(symbol) + "USDT", followToken: follow, wallpaper: true,
                        topInset: 0, bottomOverlay: 0,
                        onNeedOlder: { candles.loadOlder() })
            if candles.bars.isEmpty {
                if candles.error == nil { ProgressView() } else { Text(candles.error ?? "").font(.footnote).foregroundStyle(Color.muted) }
            }
        }
    }

    /// Period chips, chart settings and follow — the same bar as 1.618.
    private var dock: some View {
        HStack(spacing: 2) {
            PeriodChips(selection: $period)
            Button { Haptics.open(); chartSettings = true } label: {
                Image(systemName: "slider.horizontal.3").font(.system(size: 14, weight: .bold)).foregroundStyle(Color.ink).frame(width: 34, height: 34).contentShape(.rect)
            }
            .buttonStyle(.plain).accessibilityLabel("图表设置")
            Button { Haptics.tap(); follow += 1 } label: {
                Image(systemName: "arrow.right.to.line").font(.system(size: 13, weight: .bold)).foregroundStyle(Color.ink).frame(width: 32, height: 34).contentShape(.rect)
            }
            .buttonStyle(.plain).accessibilityLabel("跟随最新")
        }
    }

}
