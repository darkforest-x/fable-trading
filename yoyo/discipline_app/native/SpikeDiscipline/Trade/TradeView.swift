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
                    ZStack(alignment: .bottom) {
                        chart(existing: existing, top: 0, bottom: 0)
                        VStack(spacing: 8) {
                            dock
                            Button { Haptics.select(); showingChart = false } label: {
                                Label("返回交易", systemImage: "arrow.uturn.backward").font(.subheadline.weight(.bold))
                            }.buttonStyle(.pill(.frosted, height: 40))
                        }.padding(.bottom, 76)
                    }
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
                            if let block { GuardStrip(text: block.text) { route(block.destination) } }
                            let narrow = proxy.size.width < 355 || textSize >= .xxLarge
                            if narrow {
                                orderForm
                                DisclosureGroup("五档盘口") { orderBook }
                            } else {
                                HStack(alignment: .top, spacing: 12) {
                                    orderForm.frame(maxWidth: .infinity)
                                    Divider()
                                    orderBook.frame(width: (proxy.size.width - 54) * 0.38)
                                }
                            }
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
        }
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
                headerIcon(showingChart ? "slider.horizontal.3" : "chart.xyaxis.line", showingChart ? "返回交易" : "合约 K 线") {
                    showingChart.toggle()
                }
                if showingChart {
                    headerIcon("rotate.right", "横屏查看") { Orientation.rotate(landscape: true) }
                } else {
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

    private func chart(existing: Position?, top: CGFloat, bottom: CGFloat) -> some View {
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
                        topInset: top + 12, bottomOverlay: bottom + 155,
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
        .padding(5)
        .frosted(Capsule())
    }

}
