import SwiftUI

/// 合约 (owner 2026-11-08 “合约页面也是一样，K 线和 1.618 保持统一全屏，其他都是悬浮组件”;
/// Stitch screen 2e91dcebc5c34a41bd0855aa2cabe437): the chart is the page wallpaper with the same layers,
/// colours and Monday ranges as 1.618; the contract capsule, discipline budget, position / guard chips,
/// period bar and the 做多 / 做空 buttons float on top. The ticket opens as a sheet.
struct TradeView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @Environment(FibStore.self) private var fib
    @Environment(ChartStyle.self) private var style
    @Environment(MondayHistory.self) private var history
    let active: Bool
    var onDestination: (Trading.Destination) -> Void
    @State private var showSettings = false
    @State private var picker = false
    @State private var discipline = false
    @State private var ticketSheet = false
    @State private var chartSettings = false
    @State private var candles = CandleStore()
    @AppStorage("trade.period") private var period: CandlePeriod = .m15
    @State private var follow = 0

    var body: some View {
        let state = account.state
        let existing = ticket.existing(state)
        let block = Trading.availability(state: state, symbol: ticket.symbol, connected: session.connection != nil, online: account.online,
                                         fresh: account.quoteFresh(ticket.symbol), pending: account.pendingEntry != nil || account.closeAttempt != nil,
                                         room: Trading.marginRoom(state, symbol: ticket.symbol), now: account.now)
        GeometryReader { proxy in
            let top = proxy.safeAreaInsets.top, bottom = proxy.safeAreaInsets.bottom
            ZStack(alignment: .top) {
                chart(existing: existing, top: top, bottom: bottom).ignoresSafeArea()
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .top, spacing: 8) {
                        coinCapsule
                        Spacer(minLength: 4)
                        budgetChip
                    }
                    if session.connection == nil {
                        chip(icon: nil, text: "正在连接纪律服务", tint: Color.muted, spinning: true) {}
                    } else if let existing {
                        chip(icon: "circle.fill", text: "\(existing.side == "short" ? "空头" : "多头")持仓中 · \(existing.leverage)x · \(Fmt.signed(Double(existing.pnl))) U",
                             tint: existing.side == "short" ? ChartPalette.down : ChartPalette.up) { onDestination(.positions) }
                    } else if let count = state?.positions.count, count > 0 {
                        chip(icon: "circle.fill", text: "其他持仓 \(count) 笔", tint: Color.accentColor) { onDestination(.positions) }
                    }
                    if session.connection != nil, let block {
                        chip(icon: "lock.fill", text: block.text, tint: Color.amber) { route(block.destination) }
                    }
                    Spacer(minLength: 0)
                    if let entry = account.activeEntry { TradeProgressCard(entry: entry) }
                    dock
                    actions(existing: existing)
                }
                .padding(.horizontal, 12)
                .padding(.top, 4)
                .padding(.bottom, 78)
            }
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $picker) { SymbolPicker().presentationDragIndicator(.visible) }
        .sheet(isPresented: $discipline) { NavigationStack { DisciplineView() }.presentationDragIndicator(.visible) }
        .sheet(isPresented: $chartSettings) { ChartSettingsSheet().presentationDetents([.medium, .large]).presentationDragIndicator(.visible) }
        .sheet(isPresented: $ticketSheet) {
            TradeTicketSheet(onBlocked: { destination in ticketSheet = false; route(destination) })
                .presentationDetents([.large]).presentationDragIndicator(.visible)
        }
        .task(id: "\(ticket.symbol)|\(style.showHistory)|\(active)") {
            guard active, style.showHistory else { return }
            await history.load(ticket.symbol, connection: session.connection)
        }
        .task(id: active) {
            guard active else { candles.stopAndSave(); return }
            if fib.current == nil { try? await fib.refresh() }
        }
        .onChange(of: "\(ticket.symbol)|\(period.rawValue)|\(active)|\(session.connection?.token.hashValue ?? 0)") { _, _ in showCandles() }
        .onAppear { showCandles() }
        .onChange(of: "\(ticket.symbol)|\(state?.policyRevision ?? -1)|\(ticket.cap(state))|\(state?.sharedLeverage?[ticket.symbol] ?? -1)") { _, _ in ticket.syncLeverage(state) }
        .onChange(of: "\(existing?.id ?? "")|\(existing?.stop ?? "")|\(existing?.leverage ?? 0)") { _, _ in ticket.syncExisting(state) }
    }

    private func showCandles() {
        guard active, !ticket.symbol.isEmpty else { candles.stopAndSave(); return }
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
        var lines: [ChartLine] = []
        if let e = existing {
            lines = [ChartLine(price: Double(e.entry) ?? 0, title: "开仓", color: ChartPalette.accent),
                     ChartLine(price: Double(e.stop) ?? 0, title: "止损", color: ChartPalette.down, dashed: true)]
        } else if let stop = Double(ticket.stop), stop > 0 {
            lines = [ChartLine(price: stop, title: "止损", color: ChartPalette.down, dashed: true)]
        }
        return ZStack {
            CandleChart(bars: candles.bars, generation: candles.generation, period: period, decimals: Fmt.decimals(tick: tick),
                        range: row.flatMap { fib.range(for: $0) }, history: style.showHistory ? (history.ranges[symbol] ?? []) : [],
                        innerLines: fib.showInternal, averages: fib.showAverages, cloud: fib.showCloud,
                        lines: lines, colors: style.colors, watermark: Fmt.coin(symbol) + "USDT", followToken: follow, wallpaper: true,
                        topInset: top + 58, bottomOverlay: bottom + 78 + 112,
                        onNeedOlder: { candles.loadOlder() })
            if candles.bars.isEmpty {
                if candles.error == nil { ProgressView() } else { Text(candles.error ?? "").font(.footnote).foregroundStyle(Color.muted) }
            }
        }
    }

    // MARK: floating pieces

    /// Contract capsule: logo, coin and live price (tap → 选择合约).
    private var coinCapsule: some View {
        let quote = account.state?.quotes[ticket.symbol], fresh = account.quoteFresh(ticket.symbol)
        let last = candles.bars.last
        return Button { Haptics.open(); picker = true } label: {
            HStack(spacing: 8) {
                CoinLogo(coin: Fmt.coin(ticket.symbol), size: 28)
                HStack(spacing: 3) {
                    Text(Fmt.coin(ticket.symbol)).font(.system(size: 17, weight: .bold)).foregroundStyle(Color.ink).lineLimit(1).fixedSize()
                    Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold)).foregroundStyle(Color.muted)
                }
                Rectangle().fill(Color.muted.opacity(0.25)).frame(width: 1, height: 18)
                Text(Fmt.price(quote?.last ?? last.map { String($0.close) }, tick: tick))
                    .font(.system(size: 16, weight: .bold).monospacedDigit()).lineLimit(1).fixedSize()
                    .foregroundStyle((last.map { $0.close >= $0.open } ?? true) ? ChartPalette.up : ChartPalette.down)
                    .contentTransition(.numericText(value: Double(quote?.last ?? "0") ?? 0)).animation(.snappy, value: quote?.last)
                Circle().fill(fresh ? Color.up : Color.muted.opacity(0.45)).frame(width: 7, height: 7)
                    .accessibilityLabel(fresh ? "实时" : "待更新")
            }
            .padding(.leading, 7).padding(.trailing, 14).frame(height: 46)
            .frosted(Capsule())
        }
        .buttonStyle(CardPressStyle())
        .layoutPriority(1)
        .disabled(ticket.reviewing || account.pendingEntry != nil)
        .accessibilityLabel("选择合约，当前 \(Fmt.coin(ticket.symbol))")
    }

    /// Remaining discipline budget in one chip (tap → 纪律).
    private var budgetChip: some View {
        let state = account.state, d = state?.discipline
        let halted = state?.daily?.lockedReason != nil || (state.map { $0.cooldownUntil > $0.serverTime } ?? false)
        let loss = d.map { (Double($0.dailyLossRemaining) ?? 0).formatted(.number.precision(.fractionLength(0...2))) } ?? "—"
        return Button { Haptics.open(); discipline = true } label: {
            HStack(spacing: 5) {
                Image(systemName: halted ? "pause.circle.fill" : "shield.lefthalf.filled").font(.system(size: 12, weight: .bold))
                    .foregroundStyle(halted ? ChartPalette.down : Color.accentColor)
                Text(d.map { "\($0.entriesRemaining) 次 · 余 \(loss)U" } ?? "纪律")
                    .font(.system(size: 12, weight: .semibold).monospacedDigit()).foregroundStyle(Color.ink).lineLimit(1).minimumScaleFactor(0.8)
            }
            .padding(.horizontal, 12).frame(height: 46)
            .frosted(Capsule())
        }
        .buttonStyle(CardPressStyle())
        .accessibilityLabel(d.map { "今日还可开 \($0.entriesRemaining) 次，日亏剩余 \(loss) U" } ?? "纪律额度")
    }

    private func chip(icon: String?, text: String, tint: Color, spinning: Bool = false, action: @escaping () -> Void) -> some View {
        Button { Haptics.tap(); action() } label: {
            HStack(spacing: 6) {
                if spinning { ProgressView().controlSize(.mini) }
                if let icon { Image(systemName: icon).font(.system(size: icon == "circle.fill" ? 6 : 11, weight: .bold)).foregroundStyle(tint) }
                Text(text).font(.system(size: 12, weight: .semibold).monospacedDigit()).foregroundStyle(Color.ink).lineLimit(1)
            }
            .padding(.horizontal, 12).frame(height: 30)
            .frosted(Capsule(), veil: 0.75)
        }
        .buttonStyle(.plain)
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

    /// 做多 / 做空 open the ticket with that direction; with a position on this contract they open it as-is.
    private func actions(existing: Position?) -> some View {
        let locked = session.connection == nil || ticket.reviewing || account.pendingEntry != nil
        return HStack(spacing: 10) {
            ForEach(["long", "short"], id: \.self) { side in
                let hue = side == "long" ? Color.up : Color.down
                let held = existing.map { ($0.side == "short" ? "short" : "long") == side } ?? false
                Button { open(side) } label: {
                    HStack(spacing: 6) {
                        Image(systemName: side == "long" ? "arrow.up.right" : "arrow.down.right").font(.system(size: 15, weight: .heavy))
                        Text(side == "long" ? "做多" : "做空").font(.system(size: 18, weight: .bold))
                        if held { Text("持仓").font(.system(size: 11, weight: .bold)).padding(.horizontal, 6).padding(.vertical, 2).background(.white.opacity(0.25), in: .capsule) }
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.pill(.solid(hue), height: 52))
                .disabled(locked || (existing != nil && !held))
                .accessibilityLabel(side == "long" ? "做多" : "做空")
            }
        }
    }

    private func open(_ side: String) {
        let state = account.state
        if ticket.existing(state) == nil, ticket.direction != side {
            ticket.manual(); ticket.direction = side; ticket.stop = ""; ticket.stopApplied = nil; ticket.fieldIssue = nil
        }
        Haptics.open(); ticketSheet = true
    }
}

/// The 合约 ticket as a sheet: guard, the form and the order progress.
struct TradeTicketSheet: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @Environment(\.dismiss) private var dismiss
    var onBlocked: (Trading.Destination) -> Void

    var body: some View {
        let state = account.state
        let block = Trading.availability(state: state, symbol: ticket.symbol, connected: session.connection != nil, online: account.online,
                                         fresh: account.quoteFresh(ticket.symbol), pending: account.pendingEntry != nil || account.closeAttempt != nil,
                                         room: Trading.marginRoom(state, symbol: ticket.symbol), now: account.now)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let block { GuardStrip(text: block.text) { onBlocked(block.destination) } }
                    TicketForm(onBlocked: onBlocked, hosts: !ticket.fibTicket).padding(16).glassCard()
                    if let entry = account.activeEntry { TradeProgressCard(entry: entry) }
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color.canvas)
            .navigationTitle(Fmt.coin(ticket.symbol) + " 永续").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
        }
        .interactiveDismissDisabled(ticket.reviewing)
    }
}
