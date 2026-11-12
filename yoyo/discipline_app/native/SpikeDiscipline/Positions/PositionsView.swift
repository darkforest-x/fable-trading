import SwiftUI

/// 持仓 (1.x 持仓 tab): order progress, close recovery, positions with 止损到开仓价 / 平仓,
/// working orders with 撤单, recent completions and read-only external activity.
/// Every write goes through AccountStore (persisted identities, no resend on timeout).
struct PositionsView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    var onOpenSymbol: (String) -> Void
    @State private var closeTarget: Position?
    @State private var protectionTarget: ProtectionSelection?
    @State private var scroll = ScrollPosition(edge: .top)
    @Environment(\.reselect) private var reselect
    @State private var action: String?
    @State private var error: String?

    var body: some View {
            ZStack {
                AmbientBackground()
                ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    PageHeader(title: "持仓保护")
                    if let entry = account.activeEntry { TradeProgressCard(entry: entry) }
                    if let attempt = account.closeAttempt { closeRecovery(attempt) }
                    if session.connection == nil {
                        ConnectPrompt()
                    } else if let state = account.state {
                        if state.positions.isEmpty && state.pending.isEmpty {
                            EmptyCard(systemImage: "briefcase", title: "暂无持仓", note: "开仓后，止损、保本与分批止盈都由服务端持续保护。")
                        }
                        if !state.positions.isEmpty {
                            SectionHeader(title: "当前持仓", count: state.positions.count)
                            ForEach(state.positions) { position in
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
                        }
                        if !state.pending.isEmpty { pendingOrders(state) }
                        recent(state)
                        if let activity = state.externalActivity, !(activity.current.isEmpty && activity.recent.isEmpty) {
                            ExternalActivityCard(activity: activity, fresh: account.online && state.privateError == nil && state.serverTime - activity.updatedAt <= 15)
                        }
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
        .sheet(item: $closeTarget) { position in
            ClosePositionSheet(position: position).presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
        }
        .sheet(item: $protectionTarget) { selection in
            ProtectionSheet(selection: selection)
                .presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
        }
        .onChange(of: session.connection) { _, _ in protectionTarget = nil }
        .alert("本次操作未完成", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("好", role: .cancel) {}
        } message: { Text(error ?? "") }
    }

    private func requestClose(_ position: Position) {
        guard action == nil else { return }
        if account.closeAttempt != nil { error = "请先等待原平仓请求完成核对"; return }
        Haptics.tap(); closeTarget = position
    }

    private func entryStop(_ position: Position) async {
        guard action == nil else { return }
        action = "stop:" + position.id; Haptics.tap()
        defer { action = nil }
        do { try await account.entryStop(position) } catch { self.error = error.localizedDescription; Haptics.warning() }
    }

    private func cancel(_ order: PendingOrder) async {
        guard action == nil else { return }
        action = "cancel:" + order.id; Haptics.tap()
        defer { action = nil }
        do { try await account.cancel(orderId: order.id) } catch { self.error = error.localizedDescription; Haptics.warning() }
    }

    private func closeRecovery(_ attempt: AccountStore.CloseAttempt) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("\(Fmt.coin(attempt.symbol)) · 平仓 \(attempt.percent)%").font(.headline)
            }
            Text(account.closeStatus == "ready" ? "原请求尚未发送，可继续提交" : "正在核对原平仓请求")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack(spacing: 10) {
                Button("刷新核对") { Haptics.tap(); account.checkClose() }.buttonStyle(.pill(.soft))
                if account.closeStatus == "ready" {
                    Button("以原请求重试") {
                        Haptics.tap()
                        Task { do { try await account.close(nil, percent: attempt.percent, recovered: true) } catch { self.error = error.localizedDescription } }
                    }
                    .buttonStyle(.pill(.solid(.accentColor)))
                }
            }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .glassCard(tint: .amber)
    }

    private func pendingOrders(_ state: AccountState) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "待成交委托", count: state.pending.count, trailing: "北京时间")
            ForEach(state.pending) { order in
                let tick = state.instruments[order.symbol]?.tick
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        CoinLogo(coin: Fmt.coin(order.symbol), size: 26)
                        Text(Fmt.coin(order.symbol)).font(.headline)
                        Pill(text: order.side == "long" ? "开多" : "开空", color: order.side == "long" ? .up : .down)
                        Spacer()
                        Text((Double(order.filledQuantity ?? "0") ?? 0) > 0 ? "部分成交" : "挂单中").font(.caption.weight(.semibold)).foregroundStyle(Color.amber)
                    }
                    HStack {
                        Metric(title: "委托价", value: Fmt.price(order.price, tick: tick), unit: "USDT")
                        Metric(title: "委托持仓金额", value: Fmt.amount(order.plan.notional), unit: "USDT")
                    }
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("委托时间").font(.caption).foregroundStyle(.secondary)
                            Text(Fmt.beijing(order.created)).font(.caption.monospacedDigit())
                        }
                        Spacer()
                        Button {
                            Task { await cancel(order) }
                        } label: {
                            if action == "cancel:" + order.id { HStack(spacing: 6) { ProgressView().controlSize(.mini); Text("撤单中") } } else { Text("撤单") }
                        }
                        .buttonStyle(.pill(.tinted(.down)))
                        .disabled(action != nil)
                        .accessibilityLabel("撤销 \(Fmt.coin(order.symbol)) 委托")
                    }
                }
                .padding(16).glassCard()
            }
        }
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

        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Button(action: { Haptics.tap(); onSymbol() }) {
                    HStack(spacing: 8) {
                        CoinLogo(coin: coin, size: 30)
                        Text(coin).font(.headline)
                        Image(systemName: "chevron.right").font(.caption.weight(.bold)).foregroundStyle(.tertiary)
                    }
                }
                .buttonStyle(.plain)
                .accessibilityLabel("查看 \(coin)/USDT 合约")
                Spacer()
                Pill(text: "\(direction == 1 ? "多" : "空") · \(Fmt.leverage(position.leverage))x", color: direction == 1 ? .up : .down)
                if state.supportsCustomProtection == true {
                    Button("调整止盈止损", action: onEdit)
                        .font(.caption.weight(.semibold)).buttonStyle(.pill(.tinted(.accentColor), height: 32))
                        .disabled(busy || !fresh || position.protectionEditable != true)
                }
            }
            HStack(alignment: .bottom) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 3) {
                        Text(Fmt.signed(pnl)).font(.scaled(size: 28, weight: .bold, design: .rounded).monospacedDigit()).foregroundStyle(tone)
                            .contentTransition(.numericText(value: pnl ?? 0)).animation(.snappy, value: pnl)
                        Text("USDT").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    }
                    Text("浮动盈亏" + (fresh ? "" : " · 待更新")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(Fmt.signed(roi) + "%").font(.title3.weight(.bold).monospacedDigit()).foregroundStyle(tone)
                    Text("收益率").font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack {
                Metric(title: "持仓金额 · U", value: Fmt.amount((last ?? entry) * remaining))
                Metric(title: "开仓均价", value: Fmt.price(position.entry, tick: tick))
                Metric(title: "当前止损", value: Fmt.price(position.stop, tick: tick))
            }
            HStack(spacing: 10) {
                Button(action: onEntryStop) {
                    HStack(spacing: 6) {
                        if entryStopBusy { ProgressView().controlSize(.mini); Text("设置中…") } else {
                            Image(systemName: protectedEntry ? "checkmark.shield.fill" : "shield.lefthalf.filled")
                            Text(protectedEntry ? "已保护开仓价" : "止损到开仓价")
                        }
                    }
                    .font(.subheadline.weight(.semibold)).frame(maxWidth: .infinity).frame(height: 36)
                }
                .buttonStyle(.pill(.soft, height: 38))
                .disabled(busy || !allowed)
                Button(action: onClose) {
                    Text("平仓").font(.subheadline.weight(.bold)).frame(maxWidth: .infinity).frame(height: 36)
                }
                .buttonStyle(.pill(.solid(.down), height: 38))
                .disabled(busy || !online)
            }
            if !compact {
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
        .padding(16)
        .glassCard()
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
