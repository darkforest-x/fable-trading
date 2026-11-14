import SwiftUI

/// 持仓 (1.x 持仓 tab): order progress, close recovery, positions with 止损到开仓价 / 平仓,
/// working orders with 撤单, recent completions and read-only external activity.
/// Every write goes through AccountStore (persisted identities, no resend on timeout).
struct PositionsView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    var onOpenSymbol: (String) -> Void
    @State private var closeTarget: CloseSelection?
    @State private var protectionTarget: ProtectionSelection?
    @State private var scroll = ScrollPosition(edge: .top)
    @Environment(\.reselect) private var reselect
    @State private var action: String?
    @State private var error: String?
    @State private var selection: AccountSectionTab = .positions

    var body: some View {
            ZStack {
                AmbientBackground()
                ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    PageHeader(title: "持仓保护")
                    if let entry = account.activeEntry { TradeProgressCard(entry: entry) }
                    if let attempt = account.closeAttempt { PositionCloseRecoveryCard(attempt: attempt) }
                    if session.connection == nil {
                        AccountDataMessage(title: "未连接交易账户", detail: "连接后可查看持仓与当前委托。", systemImage: "link")
                    } else if let state = account.state {
                        let positions = state.positions.uniqued(by: \.id)
                        AccountSnapshotNote(online: account.online, receivedAt: account.receivedAt)
                        AccountSectionTabs(selection: $selection,
                                           positionCount: positions.count,
                                           orderCount: state.pending.uniqued(by: \.id).count)
                        if selection == .positions {
                            if positions.isEmpty {
                                EmptyCard(systemImage: "briefcase", title: "暂无持仓", note: account.online ? "开仓后，止损、保本与分批止盈都由服务端持续保护。" : "上次快照中暂无持仓，连接恢复后会自动更新。")
                            } else {
                                SectionHeader(title: "当前持仓", count: positions.count)
                            }
                            ForEach(positions) { position in
                                PositionCardView(position: position, state: state, online: account.online,
                                                 busy: action != nil || account.closeAttempt != nil,
                                                 entryStopBusy: action == "stop:" + position.id,
                                                 onEntryStop: { Task { await entryStop(position) } },
                                                 onEdit: {
                                                     guard let owner = account.connection else { return }
                                                     Haptics.tap(); protectionTarget = ProtectionSelection(position: position, owner: owner)
                                                 },
                                                 onClose: { requestClose(position) },
                                                 onSymbol: { onOpenSymbol(position.symbol) })
                                .scrollReveal()
                            }
                        } else if state.pending.isEmpty {
                            EmptyCard(systemImage: "tray", title: "暂无当前委托", note: account.online ? "未成交或部分成交的委托会显示在这里。" : "上次快照中暂无委托，连接恢复后会自动更新。")
                        } else {
                            pendingOrders(state)
                        }
                        recent(state)
                        if let activity = state.externalActivity, !(activity.current.isEmpty && activity.recent.isEmpty) {
                            ExternalActivityCard(activity: activity, fresh: account.online && state.privateError == nil && state.serverTime - activity.updatedAt <= 15)
                        }
                    } else if account.cached != nil {
                        AccountDataMessage(title: "等待实时账户快照",
                                           detail: account.lastError ?? "启动缓存仅供参考，收到实时数据前不显示为当前账户状态。",
                                           loading: account.lastError == nil,
                                           systemImage: "exclamationmark.arrow.triangle.2.circlepath")
                    } else {
                        LoadState(loading: account.lastError == nil, error: account.lastError, stale: false)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 24)
            }
            .scrollPosition($scroll)
            .onChange(of: reselect) { _, _ in withAnimation(.snappy) { scroll.scrollTo(edge: .top) } }
            .refreshable { await account.refresh(session: session); Haptics.success() }
            .contentMargins(.bottom, 96, for: .scrollContent)
            .scrollEdgeEffectHidden(true, for: [.top, .bottom])
            }
        .sheet(item: $closeTarget) { selection in
            ClosePositionSheet(selection: selection).presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
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
        guard action == nil else { return }
        action = "stop:" + position.id; Haptics.tap()
        defer { action = nil }
        do { try await account.entryStop(position) } catch { self.error = error.localizedDescription; Haptics.warning() }
    }

    private func cancel(_ order: PendingOrder) async {
        guard action == nil, account.online else { return }
        action = "cancel:" + order.id; Haptics.tap()
        defer { action = nil }
        do { try await account.cancel(orderId: order.id) } catch { self.error = error.localizedDescription; Haptics.warning() }
    }

    private func pendingOrders(_ state: AccountState) -> some View {
        let orders = state.pending.uniqued(by: \.id)
        return VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "待成交委托", count: orders.count, trailing: "北京时间")
            ForEach(orders) { order in
                pendingOrderCard(order, state: state)
            }
        }
    }

    private func pendingOrderCard(_ order: PendingOrder, state: AccountState) -> some View {
        PendingOrderCardView(order: order,
                             tick: state.instruments[order.symbol]?.tick,
                             online: account.online,
                             busy: action != nil,
                             cancelling: action == "cancel:" + order.id,
                             onCancel: { Task { await cancel(order) } })
    }

    @ViewBuilder private func recent(_ state: AccountState) -> some View {
        let rows = state.closed.sorted { ($0.closed ?? 0) > ($1.closed ?? 0) }.prefix(5)
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                SectionHeader(title: "最近完成", trailing: "北京时间")
                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, position in
                        CompletedRow(position: position, tick: state.instruments[position.symbol]?.tick)
                        if index < rows.count - 1 { Divider().padding(.leading, 16) }
                    }
                }
                .glassCard()
            }
        }
    }
}

struct Metric: View {
    let title: String
    let value: String
    var unit: String? = nil
    var tone: Color = .primary
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value).font(.subheadline.weight(.semibold).monospacedDigit()).foregroundStyle(tone)
                if let unit { Text(unit).font(.caption2).foregroundStyle(.secondary) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct EmptyCard: View {
    let systemImage: String
    let title: String
    let note: String
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: systemImage).font(.system(size: 30, weight: .medium)).foregroundStyle(.blue)
            Text(title).font(.headline)
            Text(note).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity).padding(26).glassCard()
    }
}

/// PositionCard.tsx: PnL/ROI use remaining size, before fees.
struct PositionCardView: View {
    let position: Position
    let state: AccountState
    let online: Bool
    let busy: Bool
    let entryStopBusy: Bool
    var compact = false
    var onEntryStop: () -> Void
    var onEdit: () -> Void
    var onClose: () -> Void
    var onSymbol: () -> Void
    @State private var details = false

    var body: some View {
        let q = state.quotes[position.symbol], entry = Double(position.entry) ?? 0, remaining = Double(position.remaining) ?? 0
        let direction: Double = position.side == "long" ? 1 : -1
        let fresh = online && q.map { state.serverTime - $0.time >= 0 && state.serverTime - $0.time <= (state.policy?.quoteMaxAgeSeconds ?? 15) } == true
        let last = q.flatMap { Double($0.last) }
        let pnl = last.map { ($0 - entry) * remaining * direction }
        let margin = entry * remaining / max(position.leverage, 1)
        let roi = pnl.flatMap { margin > 0 ? $0 / margin * 100 : nil }
        let protectedEntry = ((Double(position.stop) ?? 0) - entry) * direction >= 0
        let allowed = fresh && last.map { ($0 - entry) * direction > 0 } == true && !protectedEntry && (state.mode != "okx_live" || position.protection == "exchange-verified")
        let tick = state.instruments[position.symbol]?.tick, coin = Fmt.coin(position.symbol)
        let tone: Color = (pnl ?? 0) < 0 ? .down : .up

        VStack(alignment: .leading, spacing: compact ? 11 : 14) {
            if compact {
                compactContent(state: state, coin: coin, direction: direction, pnl: pnl, roi: roi,
                               tone: tone, protectedEntry: protectedEntry,
                               allowed: allowed, fresh: fresh, tick: tick)
            } else {
                fullContent(state: state, coin: coin, direction: direction, pnl: pnl, roi: roi,
                            tone: tone, remaining: remaining, entry: entry, protectedEntry: protectedEntry,
                            allowed: allowed, fresh: fresh, last: last, tick: tick)
            }
        }
        .padding(16)
        .glassCard()
    }

    private func compactContent(state: AccountState, coin: String, direction: Double, pnl: Double?, roi: Double?,
                                tone: Color, protectedEntry: Bool,
                                allowed: Bool, fresh: Bool, tick: String?) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                symbolButton(coin: coin)
                Spacer(minLength: 4)
                Pill(text: "\(direction == 1 ? "多" : "空") · \(Fmt.leverage(position.leverage))x",
                     color: direction == 1 ? .up : .down)
            }
            pnlRow(pnl: pnl, roi: roi, tone: tone, fresh: fresh, compact: true)
            HStack(spacing: 10) {
                Metric(title: "剩余数量", value: Fmt.coinQuantity(position.remaining), unit: coin)
                Metric(title: "入场", value: Fmt.price(position.entry, tick: tick), unit: "USDT")
                Metric(title: "止损", value: Fmt.price(position.stop, tick: tick), unit: "USDT")
            }
            HStack(spacing: 6) {
                entryStopButton(protectedEntry: protectedEntry, allowed: allowed, compact: true)
                if state.supportsCustomProtection == true {
                    protectionButton(compact: true, fresh: fresh)
                }
                closeButton(compact: true)
            }
        }
    }

    private func fullContent(state: AccountState, coin: String, direction: Double, pnl: Double?, roi: Double?,
                             tone: Color, remaining: Double, entry: Double, protectedEntry: Bool,
                             allowed: Bool, fresh: Bool, last: Double?, tick: String?) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                symbolButton(coin: coin)
                Spacer()
                Pill(text: "\(direction == 1 ? "多" : "空") · \(Fmt.leverage(position.leverage))x",
                     color: direction == 1 ? .up : .down)
                if state.supportsCustomProtection == true {
                    protectionButton(compact: false, fresh: fresh)
                }
            }
            pnlRow(pnl: pnl, roi: roi, tone: tone, fresh: fresh, compact: false)
            HStack {
                Metric(title: "持仓金额 · U", value: Fmt.amount((last ?? entry) * remaining))
                Metric(title: "开仓均价", value: Fmt.price(position.entry, tick: tick))
                Metric(title: "当前止损", value: Fmt.price(position.stop, tick: tick))
            }
            HStack(spacing: 10) {
                entryStopButton(protectedEntry: protectedEntry, allowed: allowed, compact: false)
                closeButton(compact: false)
            }
            DisclosureGroup(isExpanded: $details.animation(.snappy)) {
                VStack(alignment: .leading, spacing: 6) {
                    let p = position.policy
                    Text("\(p.breakevenR)R 成本保本")
                    ForEach(p.takeProfits.indices, id: \.self) { i in
                        let stage = p.takeProfits[i]
                        let prices = position.takeProfitPrices ?? []
                        let price = prices.indices.contains(i) ? prices[i] : nil
                        let trigger = price.map { Fmt.price($0, tick: tick) + " USDT" } ?? "\(stage.first ?? "")R"
                        let portion = i == p.takeProfits.count - 1 ? "平余仓" : "平 \(Fmt.plainPercent(stage.last))%"
                        Text("止盈 \(i + 1) · \(trigger) · \(portion)")
                    }
                    Text("初始风险 1R = \(Fmt.amount(position.risk)) USDT · 当前 \(Fmt.amount(position.currentR)) R")
                    Text((state.mode == "okx_live" ? (position.protection == "exchange-verified" ? "交易所保护已核对" : "交易所保护待核对") : "服务端模拟保护") + " · " + (position.breakeven ? "已移成本保本" : "自动保本待触发"))
                    if state.mode == "okx_live" {
                        Text("在 OKX 撤销止损会触发应急平仓。需要改价时，请在 App 调整原保护单。")
                    }
                    Text("浮盈按最新价和剩余仓位估算，未扣费用；收益率＝浮盈 ÷ 剩余仓位开仓保证金。移到开仓价不含手续费。").foregroundStyle(.secondary)
                }
                .font(.footnote).frame(maxWidth: .infinity, alignment: .leading).padding(.top, 6)
            } label: {
                Text("自动保护与计算口径").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
            }
            .tint(.secondary)
            .onChange(of: details) { _, _ in Haptics.select() }
        }
    }

    private func symbolButton(coin: String) -> some View {
        Button(action: { Haptics.tap(); onSymbol() }) {
            HStack(spacing: 8) {
                CoinLogo(coin: coin, size: compact ? 26 : 30)
                Text(coin).font(compact ? .subheadline.weight(.semibold) : .headline)
                Image(systemName: "chevron.right").font(.caption.weight(.bold)).foregroundStyle(.tertiary)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("查看 \(coin)/USDT 合约")
    }

    private func pnlRow(pnl: Double?, roi: Double?, tone: Color, fresh: Bool, compact: Bool) -> some View {
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text(Fmt.signed(pnl))
                        .font(.scaled(size: compact ? 24 : 28, weight: .bold, design: .rounded).monospacedDigit())
                        .foregroundStyle(tone)
                        .contentTransition(.numericText(value: pnl ?? 0))
                        .animation(.snappy, value: pnl)
                    Text("USDT").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                }
                Text("浮动盈亏" + (fresh ? "" : " · 待更新"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                Text(Fmt.signed(roi) + "%")
                    .font((compact ? Font.headline : Font.title3).weight(.bold).monospacedDigit())
                    .foregroundStyle(tone)
                Text("收益率").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func entryStopButton(protectedEntry: Bool, allowed: Bool, compact: Bool) -> some View {
        Button(action: onEntryStop) {
            HStack(spacing: 5) {
                if entryStopBusy { ProgressView().controlSize(.mini) }
                else { Image(systemName: protectedEntry ? "checkmark.shield.fill" : "shield.lefthalf.filled") }
                Text(entryStopBusy ? "设置中" : compact ? (protectedEntry ? "已保本" : "保本") : (protectedEntry ? "已保护开仓价" : "止损到开仓价"))
            }
            .font((compact ? Font.caption : Font.subheadline).weight(.semibold))
            .frame(maxWidth: .infinity).frame(height: compact ? 34 : 36)
        }
        .buttonStyle(.pill(.soft, height: compact ? 36 : 38))
        .disabled(busy || !allowed)
        .accessibilityLabel(protectedEntry ? "已保护开仓价" : "止损到开仓价")
    }

    private func protectionButton(compact: Bool, fresh: Bool) -> some View {
        Button(action: onEdit) {
            HStack(spacing: 4) {
                Image(systemName: "slider.horizontal.3")
                Text(compact ? "止盈止损" : "调整止盈止损")
            }
            .font(.caption.weight(.semibold))
            .frame(maxWidth: compact ? .infinity : nil).frame(height: compact ? 34 : 30)
        }
        .buttonStyle(.pill(.tinted(.accentColor), height: compact ? 36 : 32))
        .disabled(busy || !fresh || position.protectionEditable != true)
        .accessibilityLabel("调整止盈止损")
    }

    private func closeButton(compact: Bool) -> some View {
        Button(action: onClose) {
            Text("平仓")
                .font((compact ? Font.caption : Font.subheadline).weight(.bold))
                .frame(maxWidth: .infinity).frame(height: compact ? 34 : 36)
        }
        .buttonStyle(.pill(.solid(.down), height: compact ? 36 : 38))
        .disabled(busy || !online)
        .accessibilityLabel("平仓")
    }
}

struct CompletedRow: View {
    let position: Position
    let tick: String?
    @State private var open = false
    var body: some View {
        let pnl = Double(position.pnl) ?? 0
        VStack(alignment: .leading, spacing: 8) {
            Button { Haptics.select(); withAnimation(.snappy) { open.toggle() } } label: {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(Fmt.coin(position.symbol)).font(.subheadline.weight(.semibold))
                        Pill(text: position.side == "long" ? "多" : "空", color: position.side == "long" ? .up : .down)
                        Spacer()
                        Text(Fmt.signed(pnl) + " USDT").font(.subheadline.weight(.bold).monospacedDigit()).foregroundStyle(pnl >= 0 ? Color.up : Color.down)
                        Image(systemName: "chevron.down").font(.caption.weight(.bold)).foregroundStyle(.tertiary).rotationEffect(.degrees(open ? 180 : 0))
                    }
                    Text(Fmt.beijing(position.closed)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            if open {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Metric(title: "开仓价", value: Fmt.price(position.entry, tick: tick), unit: "USDT")
                        Metric(title: "最后止损价", value: Fmt.price(position.stop, tick: tick), unit: "USDT")
                    }
                    LabeledContent("开仓时间", value: Fmt.beijing(position.opened))
                    LabeledContent("平仓时间", value: Fmt.beijing(position.closed))
                    LabeledContent(position.totalFee != nil ? "累计交易手续费" : "开仓手续费", value: Fmt.coinQuantity(position.totalFee ?? position.entryFee) + " USDT")
                    LabeledContent("平仓原因", value: position.emergencyCloseSubmitted == true
                                   ? "App 已提交应急市价平仓" : Fmt.closeReason(position.closeReason))
                }
                .font(.caption.monospacedDigit())
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
    }
}

/// Inline order progress (TradeProgress.tsx).
struct TradeProgressCard: View {
    @Environment(AccountStore.self) private var account
    let entry: AccountStore.EntryProgress

    var body: some View {
        let state = account.state, intent = entry.intent
        let position = state?.positions.first { $0.id == intent.id || (entry.status == "filled" && $0.symbol == intent.symbol) }
        let order = state?.pending.first { $0.id == intent.id }
        let closed = state?.closed.contains { $0.id == intent.id } == true
        let status = order.map { (Double($0.filledQuantity ?? "0") ?? 0) > 0 ? "partially_filled" : "pending" } ?? (position != nil ? "filled" : closed ? "closed" : entry.status)
        let verified = position?.protection == "exchange-verified"
        let checking = !["pending", "partially_filled", "filled", "closed", "cancelled", "rejected", "interrupted"].contains(status)
        let ended = ["closed", "cancelled", "rejected", "interrupted"].contains(status)
        let text: String = switch status {
        case "sending": "正在发送委托"
        case "processing": "已受理 · 正在处理委托"
        case _ where checking: "正在核对原订单，请勿重复提交"
        case "pending": "挂单中 · 等待成交"
        case "partially_filled": "部分成交 · 余量等待"
        case "filled": verified ? "已成交 · 止损已生效" : "已成交 · 正在核对保护"
        case "closed": "交易已完成"
        case "cancelled": "委托已取消"
        case "interrupted": "请求已中断 · 未生成委托"
        default: entry.message ?? "委托未成交"
        }
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                if checking { ProgressView().controlSize(.small) } else { Image(systemName: verified ? "checkmark.shield.fill" : "clock").foregroundStyle(verified ? Color.up : .secondary) }
                Text(Fmt.coin(intent.symbol)).font(.headline)
                Text((intent.kind == "limit" ? "限价" : "市价") + (intent.side == "short" ? "开空" : "开多")).font(.caption).foregroundStyle(.secondary)
                Spacer()
                if !checking && account.pendingEntry == nil {
                    Button { Haptics.tap(); withAnimation(.snappy) { account.entryProgress = nil } } label: { Image(systemName: "xmark").font(.caption.weight(.bold)) }
                        .buttonStyle(.pill(.soft, height: 28)).accessibilityLabel("收起订单进度")
                }
            }
            Text(text).font(.subheadline.weight(.medium)).foregroundStyle(status == "rejected" ? Color.down : .primary)
            if !ended {
                HStack(spacing: 6) {
                    ForEach([("受理", status == "processing" || !checking), ("成交", status == "filled"), ("保护", verified)], id: \.0) { step in
                        HStack(spacing: 4) {
                            Image(systemName: step.1 ? "checkmark.circle.fill" : "circle").foregroundStyle(step.1 ? Color.up : .secondary)
                            Text(step.0).font(.caption.weight(.semibold))
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
            }
        }
        .padding(16)
        .glassCard(tint: checking ? .blue : nil)
        .animation(.snappy, value: status)
    }
}

struct ExternalActivityCard: View {
    let activity: ExternalActivity
    let fresh: Bool
    @State private var history = false
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("外部交易").font(.title3.weight(.bold)).fontDesign(.rounded)
                Pill(text: "仅记录")
                Spacer()
            }
            Text("不计入 App 纪律" + (fresh ? "" : " · 等待同步")).font(.caption).foregroundStyle(.secondary)
            ForEach(activity.current) { row($0, history: false) }
            if !activity.recent.isEmpty {
                DisclosureGroup("历史观测 \(activity.recent.count)", isExpanded: $history.animation(.snappy)) {
                    Text("仅保留观测记录，最终成交与盈亏请在 OKX 查看。").font(.caption).foregroundStyle(.secondary)
                    ForEach(activity.recent.prefix(5)) { row($0, history: true) }
                }
                .font(.subheadline.weight(.semibold))
            }
        }
        .padding(16).glassCard()
    }

    private func row(_ r: ExternalRecord, history: Bool) -> some View {
        let short = r.side == "short" || r.side == "sell"
        let status = history ? "已离开当前列表" : r.mixed ? "归属待核对" : r.kind == "position" ? "外部持仓" : r.kind == "algo" ? "策略委托" : "外部挂单"
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(Fmt.coin(r.symbol)).font(.subheadline.weight(.semibold))
                Pill(text: ["long": "多", "short": "空", "buy": "买", "sell": "卖"][r.side] ?? r.side, color: short ? .down : .up)
                Text(status).font(.caption).foregroundStyle(.secondary)
            }
            if !history {
                HStack {
                    Metric(title: (r.mixed ? "交易所总持仓" : r.kind == "position" ? "持仓金额" : "委托金额") + " · " + (r.valueCurrency == "USD" ? "USD" : "U"), value: Fmt.amount(r.notional))
                    Metric(title: r.kind == "position" ? "开仓均价" : "委托价", value: r.price.flatMap(Double.init).map { $0.formatted(.number.precision(.fractionLength(0...8))) } ?? "—")
                    if r.kind == "position" { Metric(title: "浮动盈亏 · U", value: Fmt.amount(r.pnl), tone: (Double(r.pnl ?? "0") ?? 0) < 0 ? .down : .up) }
                }
            }
            Text((history ? "最后观测 " : "委托 / 开仓时间 ") + Fmt.beijing(history ? r.lastSeen : r.created)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}
