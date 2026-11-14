import SwiftUI

/// 确认实盘订单 / 确认模拟订单 (1.x preview dialog). Shown only with 开仓二次确认 on; live needs the
/// explicit consent switch. The request id was minted at review, so a retry here is de-duplicated.
struct PreviewSheet: View {
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @Environment(\.dismiss) private var dismiss
    let pending: TicketModel.Pending
    @State private var consent = false
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        let live = account.live, order = pending.ticket
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if live { Label("真实资金 · OKX 实盘执行", systemImage: "exclamationmark.shield.fill").font(.subheadline.weight(.bold)).foregroundStyle(Color.down) }
                    HStack {
                        CoinLogo(coin: Fmt.coin(order.symbol), size: 30)
                        Text(Fmt.coin(order.symbol) + "USDT").font(.headline)
                        Spacer()
                        Pill(text: "\(order.side == "long" ? "开多" : "开空") · \(Fmt.marginMode(pending.plan?.marginMode)) \(Fmt.leverage(pending.plan?.leverage ?? order.leverage))x", color: order.side == "long" ? .up : .down)
                    }
                    if let plan = pending.plan {
                        let tick = account.state?.instruments[order.symbol]?.tick
                        VStack(spacing: 8) {
                            row("初始止损价格", Fmt.price(plan.stop, tick: tick) + " USDT")
                            if let prices = plan.takeProfitPrices {
                                ForEach(prices.indices, id: \.self) { i in
                                    row("止盈 \(i + 1) 触发价", Fmt.price(prices[i], tick: tick) + " USDT")
                                }
                            }
                            row("入场价格边界", Fmt.price(plan.entry, tick: tick) + " USDT")
                            row("名义仓位", Fmt.amount(plan.notional) + " USDT")
                            row("本单名义 / 净值", Fmt.amount(plan.notionalEquityMultiple) + " 倍")
                            row("预计保证金", Fmt.amount(plan.margin) + " USDT")
                            row("价格止损风险", Fmt.amount(plan.priceRisk) + " USDT")
                            row("成本预留", Fmt.amount(plan.costs) + " USDT")
                            row("初始计划风险 1R", Fmt.amount(plan.risk) + " USDT").fontWeight(.bold)
                        }
                        .padding(14).glassCard()
                        if plan.exposureLimited { Text("本单已按账户剩余名义仓位额度缩小。").font(.caption).foregroundStyle(.secondary) }
                    }
                    StatusNote(text: live ? (order.kind == "limit" ? "限价挂单等待成交，可在持仓页撤单" : "全部成交或取消") + "；保护异常会尝试平掉余仓，实际损失可能超出预算。" : "提交时重新校验，这不是实盘订单。",
                               systemImage: "shield", tint: live ? .amber : .up)
                    if let error { Text(error).font(.footnote.weight(.medium)).foregroundStyle(Color.down) }
                    if live { Toggle("我确认使用真实资金执行本次交易", isOn: $consent).font(.subheadline.weight(.semibold)).onChange(of: consent) { _, _ in Haptics.select() } }
                    Button {
                        Task { await confirm() }
                    } label: {
                        HStack { if busy { ProgressView().tint(.white); Text("正在提交…") } else { Text(live ? "确认实盘开仓" : "确认模拟开仓") } }
                            .font(.body.weight(.bold)).frame(maxWidth: .infinity).frame(height: 50)
                    }
                    .buttonStyle(.pill(.solid(order.side == "long" ? .up : .down), height: 50))
                    .disabled(busy || (live && !consent))
                    Button("返回修改") { Haptics.tap(); dismiss() }.frame(maxWidth: .infinity).disabled(busy)
                }
                .padding(18)
            }
            .background(Color.canvas)
            .navigationTitle(live ? "确认实盘订单" : "确认模拟订单").navigationBarTitleDisplayMode(.inline)
        }
        .interactiveDismissDisabled(busy)
    }

    private func row(_ title: String, _ value: String) -> some View {
        HStack { Text(title).foregroundStyle(.secondary); Spacer(); Text(value).monospacedDigit() }.font(.subheadline)
    }

    private func confirm() async {
        guard !busy, !(account.live && !consent) else { return }
        busy = true; error = nil; Haptics.tap()
        defer { busy = false }
        do {
            try await account.submit(pending.ticket, requestId: pending.id, policyRevision: pending.policyRevision, previewId: pending.previewId)
            ticket.manual(); ticket.targetInputs = [:]; ticket.preview = nil; ticket.fibTicket = false
            dismiss()
        } catch {
            self.error = error.localizedDescription; Haptics.warning()
        }
    }
}

/// A watchlist with stable row positions while quotes update.
struct SymbolPicker: View {
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""
    @AppStorage("trade.watchlist.filter") private var filter = 0
    @State private var sort = Trading.ContractSort.volume
    @State private var ranked: [String] = []
    @State private var favorites = UserDefaults.standard.stringArray(forKey: "spike-favorites") ?? []

    private var instruments: [String] {
        account.state?.instruments.values.filter { $0.tradable != false }.map(\.symbol).sorted() ?? []
    }

    private var recent: Set<String> {
        guard let state = account.state else { return [] }
        return Set(state.positions.map(\.symbol) + state.pending.map(\.symbol) + state.closed.map(\.symbol))
    }

    private func searchKey(_ raw: String) -> String {
        raw.uppercased().replacingOccurrences(of: ".P", with: "").filter { $0.isLetter || $0.isNumber }
    }

    var body: some View {
        let searchText = searchKey(search)
        let recentSymbols = recent
        let rows = ranked.filter { symbol in
            (filter == 0 || (filter == 1 && favorites.contains(symbol)) || (filter == 2 && recentSymbols.contains(symbol)))
                && (searchText.isEmpty || searchKey(symbol).contains(searchText))
        }
        NavigationStack {
            VStack(spacing: 0) {
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField("搜索币种或合约", text: $search)
                        .textInputAutocapitalization(.characters).autocorrectionDisabled()
                    if !search.isEmpty {
                        Button { search = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }
                            .accessibilityLabel("清除搜索")
                    }
                }.padding(.horizontal, 12).frame(minHeight: 42).background(Color.field, in: .rect(cornerRadius: 10))
                    .padding(.horizontal, 14)
                HStack(spacing: 6) {
                    ForEach(Array(["全部", "自选", "最近"].enumerated()), id: \.offset) { i, name in
                        Button {
                            filter = i; Haptics.select()
                        } label: {
                            Text(name).font(.subheadline.weight(.semibold)).padding(.horizontal, 13)
                                .frame(minHeight: 31)
                                .background(filter == i ? Color.primary : Color.field, in: Capsule())
                                .foregroundStyle(filter == i ? Color(uiColor: .systemBackground) : .primary)
                                .frame(minHeight: 44)
                        }.buttonStyle(.plain).accessibilityAddTraits(filter == i ? .isSelected : [])
                    }
                    Spacer()
                    Menu {
                        Button("24h 估算成交额") { changeSort(.volume) }
                        Button("今日涨幅") { changeSort(.gainers) }
                        Button("今日跌幅") { changeSort(.losers) }
                        Divider()
                        Button("更新排名") { rank() }
                    } label: {
                        HStack(spacing: 4) {
                            Text(sort == .volume ? "成交额" : sort == .gainers ? "涨幅" : "跌幅").font(.caption)
                            Image(systemName: "arrow.up.arrow.down").font(.caption)
                        }.frame(minWidth: 44, minHeight: 44)
                    }.tint(.primary).accessibilityLabel("行情排序").accessibilityValue(sort == .volume ? "24h 估算成交额" : sort == .gainers ? "今日涨幅" : "今日跌幅")
                }.padding(.horizontal, 14).padding(.vertical, 3)
                HStack {
                    Text("合约").padding(.leading, 4)
                    Spacer()
                    Text("最新价 / 今日涨跌")
                }.font(.caption2).foregroundStyle(.secondary).padding(.horizontal, 16).padding(.bottom, 6)
                Divider()
                List {
                    ForEach(rows, id: \.self) { symbol in row(symbol) }
                    if rows.isEmpty {
                        Text(!searchText.isEmpty ? "没有匹配的合约" : filter == 1 ? "在全部合约中点星标即可加入自选" : filter == 2 ? "交易过的合约会显示在这里" : "暂无可交易合约")
                            .font(.subheadline).foregroundStyle(.secondary).frame(maxWidth: .infinity)
                            .padding(.vertical, 28).listRowSeparator(.hidden)
                    }
                }
                .listStyle(.plain).scrollDismissesKeyboard(.interactively)
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden)
                ToolbarItem(placement: .principal) {
                    VStack(spacing: 2) {
                        Text("选择合约").font(.headline)
                        Text("USDT 永续 · 北京时间 00:00 基准").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .onChange(of: instruments, initial: true) { _, _ in rank() }
    }

    private func changeSort(_ value: Trading.ContractSort) {
        Haptics.select(); sort = value; rank()
    }

    private func rank() {
        ranked = Trading.rank(instruments, quotes: account.state?.quotes ?? [:], now: account.now, sort: sort)
    }

    private func row(_ symbol: String) -> some View {
        let state = account.state, quote = state?.quotes[symbol]
        let coin = Fmt.coin(symbol), chosen = symbol == ticket.symbol, favorite = favorites.contains(symbol)
        let tick = state?.instruments[symbol]?.tick
        let fresh = account.quoteFresh(symbol)
        let change = fresh ? quote.flatMap { Trading.dayChange($0, now: account.now) } : nil
        let delta = change.flatMap { percent -> Double? in
            guard percent > -100, let value = quote.flatMap({ Double($0.last) }), value > 0 else { return nil }
            return value * percent / (100 + percent)
        }
        return HStack(spacing: 0) {
            Button {
                if favorite { favorites.removeAll { $0 == symbol } } else { favorites.append(symbol) }
                UserDefaults.standard.set(favorites, forKey: "spike-favorites"); Haptics.select()
            } label: {
                Image(systemName: favorite ? "star.fill" : "star").font(.subheadline)
                    .foregroundStyle(favorite ? Color.amber : .secondary)
                    .frame(width: 44, height: 48).contentShape(.rect)
            }.buttonStyle(.plain).accessibilityLabel("\(favorite ? "取消自选" : "加入自选") \(coin)")
            Button {
                if symbol != ticket.symbol {
                    ticket.choose(symbol); ticket.syncLeverage(state); ticket.syncExisting(state)
                }
                Haptics.select(); dismiss()
            } label: {
                HStack(spacing: 8) {
                    CoinLogo(coin: coin, size: 30)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 3) {
                            Text(coin + "USDT").font(.subheadline.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.75)
                            if chosen { Image(systemName: "checkmark.circle.fill").font(.caption2).foregroundStyle(Color.accentColor) }
                        }
                        Text("OKX 永续").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 4)
                    VStack(alignment: .trailing, spacing: 3) {
                        Text(fresh ? Fmt.price(quote?.last, tick: tick) : "—").font(.body.weight(.semibold))
                        if let change, let delta {
                            Text(Fmt.signed(delta, Fmt.decimals(tick: tick)) + "  " + Fmt.signed(change) + "%")
                                .font(.caption).foregroundStyle(change == 0 ? Color.secondary : (change > 0 ? .up : .down))
                        } else { Text("—").font(.caption).foregroundStyle(.secondary) }
                    }.monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
                }.frame(minHeight: 54).contentShape(.rect)
            }.buttonStyle(.plain).accessibilityAddTraits(chosen ? .isSelected : [])
        }
        .listRowInsets(EdgeInsets(top: 0, leading: 2, bottom: 0, trailing: 16))
        .listRowBackground(chosen ? Color.accentColor.opacity(0.06) : Color(uiColor: .systemBackground))
    }
}

/// 1.618 快捷开单 sheet: the same ticket with the wick stop for the chart period.
struct QuickTicketSheet: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @Environment(\.dismiss) private var dismiss
    var onBlocked: (Trading.Destination) -> Void

    var body: some View {
        let state = account.state
        let block = Trading.availability(state: state, symbol: ticket.symbol, connected: session.connection != nil, online: account.online,
                                         fresh: account.quoteFresh(ticket.symbol), pending: account.pendingEntry != nil || account.closeAttempt != nil,
                                         room: Trading.allocationRoom(state, symbol: ticket.symbol), now: account.now)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let block { GuardStrip(text: block.text) { dismiss(); onBlocked(block.destination) } }
                    TicketForm(onBlocked: onBlocked).padding(16).glassCard()
                    if let entry = account.activeEntry { TradeProgressCard(entry: entry) }
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color.canvas)
            .navigationTitle(Fmt.coin(ticket.symbol)).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { ticket.manual(); dismiss() } }.sharedBackgroundVisibility(.hidden) }
        }
        .interactiveDismissDisabled(ticket.reviewing)
    }
}

struct GuardStrip: View {
    let text: String
    var action: () -> Void
    var body: some View {
        Button { Haptics.tap(); action() } label: {
            HStack(spacing: 10) {
                Image(systemName: "lock.fill").foregroundStyle(Color.amber)
                Text(text).font(.subheadline.weight(.semibold)).foregroundStyle(.primary).multilineTextAlignment(.leading)
                Spacer()
                Image(systemName: "chevron.right").font(.caption.weight(.bold)).foregroundStyle(.tertiary)
            }
            .padding(14).glassCard(tint: .amber)
        }
        .buttonStyle(CardPressStyle())
    }
}
