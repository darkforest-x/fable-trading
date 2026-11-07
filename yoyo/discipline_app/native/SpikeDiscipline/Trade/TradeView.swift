import SwiftUI

/// 合约 (1.x 合约 tab): quote hero with the contract picker, compact chart with entry/stop lines,
/// remaining discipline budget, guard strip, the order ticket, order progress and current positions.
struct TradeView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    let active: Bool
    @Binding var showSettings: Bool
    var onDestination: (Trading.Destination) -> Void
    @State private var picker = false
    @State private var discipline = false
    @State private var closeTarget: Position?
    @State private var candles = CandleStore()
    @AppStorage("trade.period") private var period: CandlePeriod = .m15
    @State private var follow = 0
    @State private var action: String?
    @State private var error: String?

    var body: some View {
        let state = account.state
        let quote = state?.quotes[ticket.symbol], fresh = account.quoteFresh(ticket.symbol)
        let tick = state?.instruments[ticket.symbol]?.tick
        let existing = ticket.existing(state)
        let room = Trading.marginRoom(state, symbol: ticket.symbol)
        let block = Trading.availability(state: state, symbol: ticket.symbol, connected: session.connection != nil, online: account.online, fresh: fresh,
                                         pending: account.pendingEntry != nil || account.closeAttempt != nil, room: room, now: account.now)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("\(account.live ? "实盘" : "模拟") · 纪律风控托管，1R 自动保护").font(.subheadline).foregroundStyle(.secondary).padding(.horizontal, 4)
                    if session.connection == nil { ConnectPrompt(showSettings: $showSettings) }
                    hero(quote: quote, fresh: fresh, tick: tick, state: state)
                    chart(existing: existing, tick: tick)
                    Button { Haptics.tap(); discipline = true } label: { DisciplineBudgetRow(state: state, online: account.online) }.buttonStyle(CardPressStyle())
                    if session.connection != nil, let block { GuardStrip(text: block.text) { route(block.destination) } }
                    TicketForm(onBlocked: route, hosts: !ticket.fibTicket).padding(16).glassCard(tint: ticket.side(state) == "long" ? .up : .down)
                    if let entry = account.activeEntry { TradeProgressCard(entry: entry) }
                    if let positions = state?.positions, !positions.isEmpty, let state {
                        SectionHeader(title: "当前持仓", count: positions.count)
                        ForEach(positions) { position in
                            PositionCardView(position: position, state: state, online: account.online, busy: action != nil || account.closeAttempt != nil,
                                             entryStopBusy: action == position.id, compact: true,
                                             onEntryStop: { Task { await entryStop(position) } },
                                             onClose: { if account.closeAttempt == nil { Haptics.tap(); closeTarget = position } },
                                             onSymbol: { if position.symbol != ticket.symbol { ticket.choose(position.symbol); ticket.syncLeverage(state); ticket.syncExisting(state) } })
                        }
                    }
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)
            .refreshable { await account.refresh(session: session); Haptics.success() }
            .contentMargins(.bottom, 96, for: .scrollContent)
            .scrollEdgeEffectHidden(true, for: .top)
            .toolbarBackground(.hidden, for: .navigationBar)
            .containerBackground(for: .navigation) { AmbientBackground() }
            .navigationTitle("合约")
            .toolbarTitleDisplayMode(.inlineLarge)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Haptics.tap(); showSettings = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("设置")
                }
            }
        }
        .sheet(isPresented: $picker) { SymbolPicker().presentationDragIndicator(.visible) }
        .sheet(isPresented: $discipline) { NavigationStack { DisciplineView().background(AmbientBackground()) } .presentationDragIndicator(.visible) }
        .sheet(item: $closeTarget) { ClosePositionSheet(position: $0).presentationDetents([.medium, .large]) }
        .alert("本次操作未完成", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) { Button("好", role: .cancel) {} } message: { Text(error ?? "") }
        .onChange(of: "\(ticket.symbol)|\(period.rawValue)|\(active)|\(session.connection?.token.hashValue ?? 0)") { _, _ in showCandles() }
        .onAppear { showCandles() }
        .onChange(of: "\(ticket.symbol)|\(state?.policyRevision ?? -1)|\(ticket.cap(state))|\(state?.sharedLeverage?[ticket.symbol] ?? -1)") { _, _ in ticket.syncLeverage(state) }
        .onChange(of: "\(existing?.id ?? "")|\(existing?.stop ?? "")|\(existing?.leverage ?? 0)") { _, _ in ticket.syncExisting(state) }
    }

    private func showCandles() {
        guard active, !ticket.symbol.isEmpty else { candles.stop(); return }
        candles.show(symbol: ticket.symbol, period: period, connection: session.connection)
    }

    private func route(_ destination: Trading.Destination) {
        switch destination {
        case .settings: showSettings = true
        case .discipline: discipline = true
        case .positions: onDestination(.positions)
        }
    }

    private func entryStop(_ position: Position) async {
        guard action == nil else { return }
        action = position.id; Haptics.tap()
        defer { action = nil }
        do { try await account.entryStop(position) } catch { self.error = error.localizedDescription }
    }

    private func hero(quote: Quote?, fresh: Bool, tick: String?, state: AccountState?) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Button { Haptics.open(); picker = true } label: {
                    HStack(spacing: 8) {
                        CoinLogo(coin: Fmt.coin(ticket.symbol), size: 32)
                        Text(Fmt.coin(ticket.symbol)).font(.title3.weight(.bold)) + Text("/USDT").font(.subheadline).foregroundStyle(.secondary)
                        Image(systemName: "chevron.down").font(.caption.weight(.bold)).foregroundStyle(.secondary)
                    }
                }
                .buttonStyle(.plain)
                .disabled(ticket.reviewing || account.pendingEntry != nil)
                .accessibilityLabel("选择合约")
                Pill(text: "永续")
                Pill(text: Fmt.marginMode(state?.policy?.marginMode))
                Spacer()
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(Fmt.price(quote?.last, tick: tick)).font(.system(size: 34, weight: .bold, design: .rounded).monospacedDigit())
                    .foregroundStyle(fresh ? .primary : .secondary)
                    .contentTransition(.numericText(value: Double(quote?.last ?? "0") ?? 0)).animation(.snappy, value: quote?.last)
                Text("USDT").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                Spacer()
                HStack(spacing: 5) {
                    Circle().fill(fresh ? Color.up : Color.secondary.opacity(0.5)).frame(width: 7, height: 7)
                    Text(fresh ? "实时" : quote != nil ? "待更新" : "待连接").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                }
            }
        }
        .padding(16).glassCard(cornerRadius: 24)
    }

    private func chart(existing: Position?, tick: String?) -> some View {
        VStack(spacing: 6) {
            HStack(spacing: 6) {
                ForEach([CandlePeriod.m1, .m5, .m15, .h1, .h4, .d1]) { p in
                    Button { Haptics.select(); period = p } label: {
                        Text(p.label).font(.caption.weight(.semibold)).padding(.horizontal, 9).frame(height: 26)
                            .foregroundStyle(period == p ? Color.accentColor : .secondary)
                            .background(period == p ? Color.accentColor.opacity(0.12) : .clear, in: .capsule)
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
                Button { Haptics.tap(); follow += 1 } label: { Image(systemName: "arrow.right.to.line").font(.caption.weight(.bold)) }
                    .buttonStyle(.plain).foregroundStyle(.secondary).accessibilityLabel("跟随最新")
            }
            .padding(.horizontal, 10)
            ZStack {
                CandleChart(bars: candles.bars, generation: candles.generation, period: period, decimals: Fmt.decimals(tick: tick),
                            lines: existing.map { e in [ChartLine(price: Double(e.entry) ?? 0, title: "开仓", color: ChartPalette.accent),
                                                        ChartLine(price: Double(e.stop) ?? 0, title: "止损", color: ChartPalette.down, dashed: true)] } ?? [],
                            watermark: Fmt.coin(ticket.symbol) + "USDT", followToken: follow, onNeedOlder: { candles.loadOlder() })
                if candles.bars.isEmpty {
                    if candles.error == nil { ProgressView() } else { Text(candles.error ?? "").font(.footnote).foregroundStyle(.secondary) }
                }
            }
            .frame(height: 230)
        }
        .padding(.vertical, 10).padding(.leading, 2)
        .glassCard(cornerRadius: 22)
    }
}
