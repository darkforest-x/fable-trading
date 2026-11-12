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

/// 选择合约 (ContractOptions.tsx): search, 全部/自选/最近交易, 成交量/涨幅/跌幅; the ranking is a snapshot
/// that only changes on 更新排名 or a new sort, so rows do not jump under the finger.
struct SymbolPicker: View {
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""
    @State private var filter = 0
    @State private var sort = Trading.ContractSort.volume
    @State private var ranked: [String] = []
    @State private var favorites: [String] = (UserDefaults.standard.stringArray(forKey: "spike-favorites") ?? [])

    var body: some View {
        let state = account.state
        let query = search.uppercased().replacingOccurrences(of: "/", with: "").replacingOccurrences(of: "-", with: "").trimmingCharacters(in: .whitespaces)
        let recent = recentSymbols(state)
        let rows = ranked.filter { s in
            (filter == 0 || (filter == 1 && favorites.contains(s)) || (filter == 2 && recent.contains(s)))
                && (query.isEmpty || s.replacingOccurrences(of: "-", with: "").contains(query))
        }
        NavigationStack {
            List {
                Section {
                    SoftSegment(selection: $filter, options: [(0, "全部"), (1, "自选"), (2, "最近交易")]).listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 0, trailing: 0))
                    HStack(spacing: 8) {
                        Text(sort == .volume ? "24h · USDT" : "今日 · 北京时间").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        ForEach([(Trading.ContractSort.volume, "成交量 ↓"), (.gainers, "涨幅 ↓"), (.losers, "跌幅 ↑")], id: \.1) { item in
                            Button(item.1) { Haptics.select(); sort = item.0 }
                                .font(.caption.weight(.semibold)).buttonStyle(.pill(sort == item.0 ? .tinted(.accentColor) : .soft, height: 28))
                        }
                    }
                    .listRowBackground(Color.clear).listRowInsets(EdgeInsets(top: 6, leading: 0, bottom: 4, trailing: 0))
                }
                Section {
                    ForEach(rows, id: \.self) { symbol in row(symbol, state: state) }
                    if rows.isEmpty {
                        Text(!search.isEmpty ? "没有匹配的合约" : filter == 1 ? "点亮星标，添加常用合约" : filter == 2 ? "交易过的合约会显示在这里" : "暂无可交易合约")
                            .font(.subheadline).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(.vertical, 16)
                    }
                } footer: { Text("切换合约会清空委托价格和止损。") }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.canvas)
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always), prompt: "搜索币种，例如 BTC、DOGE")
            .textInputAutocapitalization(.characters)
            .navigationTitle("选择合约").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("更新排名") { Haptics.tap(); rank() }.buttonStyle(.pill(.soft, height: 32)) }.sharedBackgroundVisibility(.hidden)
                ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden)
            }
        }
        .onAppear { rank() }
        .onChange(of: sort) { _, _ in rank() }
    }

    private func rank() {
        guard let state = account.state else { return }
        let symbols = state.instruments.values.filter { $0.tradable != false }.map(\.symbol)
        ranked = Trading.rank(symbols, quotes: state.quotes, now: state.serverTime, sort: sort)
    }

    private func recentSymbols(_ state: AccountState?) -> [String] {
        guard let state else { return [] }
        let rows = state.positions.map { ($0.symbol, $0.opened ?? 0) } + state.pending.map { ($0.symbol, $0.created ?? 0) } + state.closed.map { ($0.symbol, $0.opened ?? 0) }
        return rows.sorted { $0.1 > $1.1 }.map(\.0)
    }

    private func row(_ symbol: String, state: AccountState?) -> some View {
        let coin = Fmt.coin(symbol), quote = state?.quotes[symbol], chosen = symbol == ticket.symbol
        let change = quote.flatMap { Trading.dayChange($0, now: state?.serverTime ?? 0) }
        let favorite = favorites.contains(symbol)
        return HStack(spacing: 12) {
            Button {
                Haptics.select(); if symbol != ticket.symbol { ticket.choose(symbol); ticket.syncLeverage(account.state) }; dismiss()
            } label: {
                HStack(spacing: 12) {
                    CoinLogo(coin: coin, size: 32)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(coin).font(.body.weight(.semibold))
                        Text(sort == .volume ? Trading.turnoverLabel(Trading.turnover(quote)) : "永续").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(quote.map { Fmt.price($0.last, tick: state?.instruments[symbol]?.tick) } ?? "—").font(.subheadline.weight(.semibold).monospacedDigit())
                        Text(change.map { ($0 > 0 ? "+" : "") + String(format: "%.2f%%", $0) } ?? "—").font(.caption.monospacedDigit())
                            .foregroundStyle((change ?? 0) < 0 ? Color.down : Color.up)
                    }
                    if chosen { Image(systemName: "checkmark").fontWeight(.bold).foregroundStyle(Color.accentColor) }
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(chosen ? .isSelected : [])
            Button {
                if favorite { favorites.removeAll { $0 == symbol } } else { favorites.append(symbol) }
                UserDefaults.standard.set(favorites, forKey: "spike-favorites"); Haptics.tap()
            } label: {
                Image(systemName: favorite ? "star.fill" : "star").foregroundStyle(favorite ? Color.amber : .secondary).frame(width: 30, height: 30)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(favorite ? "取消自选" : "加入自选") \(coin)")
        }
        .listRowBackground(chosen ? Color.accentColor.opacity(0.10) : Color.white.opacity(0.55))
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
