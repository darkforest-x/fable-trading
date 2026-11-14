import SwiftUI

enum AccountSectionTab: String, CaseIterable, Identifiable {
    case positions
    case orders

    var id: String { rawValue }
    var title: String { self == .positions ? "持仓" : "当前委托" }
}

/// Reused by the contract inline section and the full Positions page.
struct AccountSectionTabs: View {
    @Binding var selection: AccountSectionTab
    let positionCount: Int?
    let orderCount: Int?

    var body: some View {
        HStack(alignment: .bottom, spacing: 18) {
            ForEach(AccountSectionTab.allCases) { tab in
                let selected = selection == tab
                Button {
                    guard selection != tab else { return }
                    Haptics.select()
                    withAnimation(.snappy) { selection = tab }
                } label: {
                    VStack(spacing: 8) {
                        HStack(spacing: 4) {
                            Text(tab.title)
                                .font(.subheadline.weight(selected ? .bold : .medium))
                            Text(count(for: tab).map { String($0) } ?? "—")
                                .font(.caption.weight(.semibold).monospacedDigit())
                                .foregroundStyle(selected ? Color.ink.opacity(0.6) : Color.muted)
                        }
                        .foregroundStyle(selected ? Color.ink : Color.muted)
                        Capsule()
                            .fill(selected ? Color.accentColor : .clear)
                            .frame(height: 2)
                    }
                    .frame(maxWidth: .infinity)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        .padding(.horizontal, 4)
        .accessibilityElement(children: .contain)
    }

    private func count(for tab: AccountSectionTab) -> Int? {
        tab == .positions ? positionCount : orderCount
    }
}

struct AccountDataMessage: View {
    let title: String
    var detail: String? = nil
    var loading = false
    var systemImage = "info.circle"

    var body: some View {
        HStack(spacing: 10) {
            if loading { ProgressView().controlSize(.small) }
            else { Image(systemName: systemImage).foregroundStyle(Color.amber) }
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.subheadline.weight(.semibold))
                if let detail {
                    Text(detail).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .glassCard(tint: loading ? .blue : .amber)
    }
}

struct AccountSnapshotNote: View {
    let online: Bool
    let receivedAt: Date

    var body: some View {
        if !online {
            Label("连接中断 · 显示上次账户快照（\(Fmt.beijing(receivedAt.timeIntervalSince1970))）", systemImage: "wifi.slash")
                .font(.caption.weight(.medium))
                .foregroundStyle(Color.amber)
                .padding(.horizontal, 4)
                .accessibilityElement(children: .combine)
        }
    }
}

/// Live positions and open orders for a selected contract. This view is intentionally not scrollable
/// so it can live inside the contract page's existing ScrollView.
struct TradeAccountSection: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    let symbol: String
    var onOpenSymbol: (String) -> Void

    @State private var selection: AccountSectionTab = .positions
    @State private var currentOnly = true
    @State private var closeTarget: CloseSelection?
    @State private var protectionTarget: ProtectionSelection?
    @State private var action: String?
    @State private var error: String?

    var body: some View {
        let state = account.state
        let allPositions = state?.positions.uniqued(by: \.id) ?? []
        let allOrders = state?.pending.uniqued(by: \.id) ?? []
        let positions = currentOnly ? allPositions.filter { $0.symbol == symbol } : allPositions
        let orders = currentOnly ? allOrders.filter { $0.symbol == symbol } : allOrders

        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .bottom, spacing: 10) {
                AccountSectionTabs(selection: $selection,
                                   positionCount: state == nil ? nil : positions.count,
                                   orderCount: state == nil ? nil : orders.count)
                    .frame(maxWidth: .infinity)
                Button {
                    Haptics.select()
                    withAnimation(.snappy) { currentOnly.toggle() }
                } label: {
                    Label("仅当前合约", systemImage: currentOnly ? "checkmark.square.fill" : "square")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(currentOnly ? Color.accentColor : Color.muted)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(currentOnly ? "仅显示当前合约，点击显示全部" : "显示全部合约，点击仅显示当前合约")
            }
            .padding(.horizontal, 4)

            if let attempt = account.closeAttempt {
                PositionCloseRecoveryCard(attempt: attempt)
            }

            if let state {
                AccountSnapshotNote(online: account.online, receivedAt: account.receivedAt)
                if selection == .positions {
                    if positions.isEmpty {
                        emptySelection(title: currentOnly ? "当前合约暂无持仓" : "暂无持仓",
                                       detail: account.online ? "持仓和盈亏会在收到新的账户快照后显示。" : "上次快照中没有匹配的持仓；连接恢复后会自动更新。")
                    } else {
                        VStack(spacing: 10) {
                            ForEach(positions) { position in
                                PositionCardView(position: position, state: state, online: account.online,
                                                 busy: action != nil || account.closeAttempt != nil,
                                                 entryStopBusy: action == "stop:" + position.id,
                                                 compact: true,
                                                 onEntryStop: { Task { await entryStop(position) } },
                                                 onEdit: {
                                                     guard let owner = account.connection else { return }
                                                     Haptics.tap(); protectionTarget = ProtectionSelection(position: position, owner: owner)
                                                 },
                                                 onClose: { requestClose(position) },
                                                 onSymbol: { onOpenSymbol(position.symbol) })
                            }
                        }
                    }
                } else if orders.isEmpty {
                    emptySelection(title: currentOnly ? "当前合约暂无委托" : "暂无当前委托",
                                   detail: account.online ? "未成交或部分成交的委托会显示在这里。" : "上次快照中没有匹配的委托；连接恢复后会自动更新。")
                } else {
                    VStack(spacing: 10) {
                        ForEach(orders) { order in
                            pendingOrderCard(order, state: state)
                        }
                    }
                }
            } else if session.connection == nil {
                AccountDataMessage(title: "未连接交易账户", detail: "连接后可查看持仓与当前委托。", systemImage: "link")
            } else {
                AccountDataMessage(title: account.cached == nil ? "正在读取账户快照" : "等待实时账户快照",
                                   detail: account.lastError ?? (account.cached == nil ? nil : "启动缓存仅用于参考，收到实时数据前不显示为当前账户状态。"),
                                   loading: account.lastError == nil,
                                   systemImage: "exclamationmark.arrow.triangle.2.circlepath")
            }
        }
        .sheet(item: $closeTarget) { selection in
            ClosePositionSheet(selection: selection)
                .presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
        }
        .sheet(item: $protectionTarget) { selection in
            ProtectionSheet(selection: selection)
                .presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
        }
        .onChange(of: session.connection) { _, _ in
            closeTarget = nil
            protectionTarget = nil
        }
        .alert("本次操作未完成", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("好", role: .cancel) {}
        } message: { Text(error ?? "") }
    }

    private func emptySelection(title: String, detail: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "tray")
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(Color.muted)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .glassCard()
    }

    private func requestClose(_ position: Position) {
        guard action == nil else { return }
        if account.closeAttempt != nil { error = "请先等待原平仓请求完成核对"; return }
        guard let owner = account.connection, account.online,
              account.state?.positions.contains(where: { $0.id == position.id }) == true else {
            error = "当前持仓状态已变化，请刷新后重试"; return
        }
        Haptics.tap(); closeTarget = CloseSelection(position: position, owner: owner)
    }

    private func entryStop(_ position: Position) async {
        guard action == nil, account.online else { return }
        action = "stop:" + position.id; Haptics.tap()
        defer { action = nil }
        do { try await account.entryStop(position) }
        catch { self.error = error.localizedDescription; Haptics.warning() }
    }

    private func cancel(_ order: PendingOrder) async {
        guard action == nil, account.online else { return }
        action = "cancel:" + order.id; Haptics.tap()
        defer { action = nil }
        do { try await account.cancel(orderId: order.id) }
        catch { self.error = error.localizedDescription; Haptics.warning() }
    }

    private func pendingOrderCard(_ order: PendingOrder, state: AccountState) -> some View {
        PendingOrderCardView(order: order,
                             tick: state.instruments[order.symbol]?.tick,
                             compact: true,
                             online: account.online,
                             busy: action != nil,
                             cancelling: action == "cancel:" + order.id,
                             onCancel: { Task { await cancel(order) } })
    }
}

struct PositionCloseRecoveryCard: View {
    @Environment(AccountStore.self) private var account
    let attempt: AccountStore.CloseAttempt
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("\(Fmt.coin(attempt.symbol)) · 平仓 \(attempt.percent)%").font(.headline)
            }
            Text(account.closeStatus == "ready" ? "原请求尚未发送，可继续提交" : "正在核对原平仓请求")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack(spacing: 10) {
                Button("刷新核对") { Haptics.tap(); account.checkClose() }
                    .buttonStyle(.pill(.soft))
                    .disabled(!account.online)
                if account.closeStatus == "ready" {
                    Button("以原请求重试") {
                        Haptics.tap()
                        Task {
                            do { try await account.close(nil, percent: attempt.percent, recovered: true) }
                            catch { self.error = error.localizedDescription }
                        }
                    }
                    .buttonStyle(.pill(.solid(.accentColor)))
                    .disabled(!account.online)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .glassCard(tint: .amber)
        .alert("本次操作未完成", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("好", role: .cancel) {}
        } message: { Text(error ?? "") }
    }
}

struct PendingOrderCardView: View {
    let order: PendingOrder
    let tick: String?
    let compact: Bool
    let online: Bool
    let busy: Bool
    let cancelling: Bool
    let onCancel: () -> Void

    init(order: PendingOrder, tick: String?, compact: Bool = false, online: Bool,
         busy: Bool, cancelling: Bool = false, onCancel: @escaping () -> Void) {
        self.order = order
        self.tick = tick
        self.compact = compact
        self.online = online
        self.busy = busy
        self.cancelling = cancelling
        self.onCancel = onCancel
    }

    var body: some View {
        let sideColor: Color = order.side == "long" ? .up : .down
        let partiallyFilled = (Double(order.filledQuantity ?? "0") ?? 0) > 0
        let kind = order.kind == "limit" ? "限价委托" : order.kind == "market" ? "市价委托" : order.kind

        VStack(alignment: .leading, spacing: compact ? 8 : 10) {
            HStack(spacing: 8) {
                CoinLogo(coin: Fmt.coin(order.symbol), size: compact ? 24 : 26)
                Text(Fmt.coin(order.symbol)).font(compact ? .subheadline.weight(.semibold) : .headline)
                Pill(text: order.side == "long" ? "开多" : "开空", color: sideColor)
                Spacer(minLength: 4)
                Text(partiallyFilled ? "部分成交" : "挂单中")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.amber)
            }

            HStack(spacing: 12) {
                Metric(title: "委托价", value: Fmt.price(order.price, tick: tick), unit: "USDT")
                Metric(title: "委托金额", value: Fmt.amount(order.plan.notional), unit: "USDT")
            }

            HStack(spacing: 8) {
                if compact {
                    Text(kind + (partiallyFilled ? " · 余量等待" : ""))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("委托时间").font(.caption).foregroundStyle(.secondary)
                        Text(Fmt.beijing(order.created)).font(.caption.monospacedDigit())
                    }
                }
                Spacer(minLength: 4)
                Button(action: onCancel) {
                    if cancelling {
                        HStack(spacing: 6) { ProgressView().controlSize(.mini); Text("撤单中") }
                    } else {
                        Text("撤单")
                    }
                }
                .buttonStyle(.pill(.tinted(.down), height: compact ? 32 : 34))
                .disabled(busy || !online)
                .accessibilityLabel("撤销 \(Fmt.coin(order.symbol)) 委托")
            }
        }
        .padding(compact ? 12 : 16)
        .glassCard()
    }
}
