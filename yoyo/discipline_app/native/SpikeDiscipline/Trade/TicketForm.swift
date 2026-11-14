import SwiftUI

/// Order ticket (1.x orderTicket): 做多/做空, 市价/限价, 杠杆, 委托价格, 初始止损 with 1R 自动计算
/// (wick stop in the 1.618 sheet), 仓位额度, estimate, and the entry button.
/// Live with 开仓二次确认 off submits on tap (1.x behaviour); otherwise /v1/preview then confirm.
struct TicketForm: View {
    @Environment(SessionStore.self) private var session
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @AppStorage("spike-confirm-trades") private var confirmTrades = false
    var onBlocked: (Trading.Destination) -> Void = { _ in }
    /// Only one visible form presents the confirm sheet and errors (合约 page vs 1.618 sheet).
    var hosts = true
    var inline = false
    @State private var leverageSheet = false
    @State private var showTargets = false
    @FocusState private var focus: Trading.FieldIssue.Field?
    @State private var lastAllocationTick = 4

    var body: some View {
        @Bindable var ticket = ticket
        let state = account.state
        let existing = ticket.existing(state)
        let side = ticket.side(state)
        let quote = state?.quotes[ticket.symbol]
        let fresh = account.quoteFresh(ticket.symbol)
        let room = Trading.allocationRoom(state, symbol: ticket.symbol)
        let block = Trading.availability(state: state, symbol: ticket.symbol, connected: session.connection != nil, online: account.online, fresh: fresh,
                                         pending: account.pendingEntry != nil || account.closeAttempt != nil, room: room, now: account.now)
        let locked = ticket.reviewing || account.pendingEntry != nil || ticket.preview != nil
        let context = ticket.stopContext(state, connection: session.connection)
        let autoEnabled = existing == nil && account.pendingEntry == nil
            && (ticket.fibTicket ? session.connection != nil : (account.online && fresh && room > 0 && (state?.blockers.isEmpty ?? false)))
        let ready = ticket.autoReady(context, enabled: autoEnabled)
        let estimate = ready ? Trading.estimate(state, ticket.ticket(state, side: side)) : nil
        let tick = state?.instruments[ticket.symbol]?.tick

        VStack(alignment: .leading, spacing: inline ? 9 : 14) {
            if inline {
                inlineDirectionTabs(side: side, state: state, locked: locked)
            }
            if existing != nil { Text("同向加仓 · 沿用当前止损和杠杆").font(.caption).foregroundStyle(.secondary) }

            HStack(spacing: 10) {
                if inline {
                    inlineOrderKindMenu(locked: locked)
                } else {
                    SoftSegment(selection: $ticket.kind, options: [("market", "市价"), ("limit", "限价")])
                    .disabled(locked)
                    .onChange(of: ticket.kind) { _, _ in ticket.fieldIssue = nil }
                }
                if ticket.sizingMode(state) == "exposure" {
                    HStack(spacing: 5) {
                        Circle().fill(Color.muted.opacity(0.6)).frame(width: 6, height: 6)
                        Text(ticket.leverageLabel(state)).font(.caption.weight(.semibold)).foregroundStyle(.primary)
                    }
                    .padding(.horizontal, 12).frame(height: 36)
                    .background(Color.field, in: .capsule)
                    .accessibilityLabel("\(ticket.leverageLabel(state))，交易所将按持仓档位核定实际杠杆")
                } else {
                    Button {
                        Haptics.tap(); leverageSheet = true
                    } label: {
                        HStack(spacing: 4) {
                            Text(state?.sharedLeverage?[ticket.symbol] != nil ? "沿用" : "杠杆").font(.caption).foregroundStyle(.secondary)
                            Text("\(Fmt.leverage(ticket.leverage))x").font(.subheadline.weight(.bold).monospacedDigit())
                            Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
                        }
                    }
                    .buttonStyle(.pill(.soft, height: 36))
                    .disabled(locked || existing != nil || state?.sharedLeverage?[ticket.symbol] != nil)
                    .accessibilityLabel("调整杠杆，当前 \(Fmt.leverage(ticket.leverage)) 倍")
                }
            }

            if ticket.kind == "limit" {
                field(title: "委托价格", trailing: {
                    Button("最新价") {
                        if let value = Trading.priceAtTick(quote?.last, tick: tick), fresh { ticket.price = value; ticket.fieldIssue = nil; Haptics.tap() }
                    }
                    .font(.caption.weight(.semibold)).buttonStyle(.pill(.soft, height: 28)).disabled(locked || !fresh)
                }) {
                    TextField("输入委托价格", text: $ticket.price).keyboardType(.decimalPad).focused($focus, equals: .price).disabled(locked)
                        .onChange(of: ticket.price) { _, _ in ticket.fieldIssue = nil }
                }
                issue(.price)
            }

            field(title: existing != nil ? "当前止损" : "初始止损", trailing: {
                if existing != nil {
                    Text("沿用").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                } else {
                    HStack(spacing: 6) {
                        Button {
                            autoStop(side, state: state)
                        } label: {
                            HStack(spacing: 4) {
                                if ticket.autoWorking { ProgressView().controlSize(.mini); Text("计算中") }
                                else { if ticket.autoMode == side { Circle().fill(Color.up).frame(width: 6, height: 6) }; Text(inline ? "自动 1R" : "1R 自动计算") }
                            }
                            .font(.caption.weight(.semibold))
                        }
                        .buttonStyle(.pill(ticket.autoMode == side ? .tinted(.accentColor) : .soft, height: 28))
                        .disabled(locked || (!ticket.fibTicket && block != nil) || session.connection == nil)
                        .accessibilityLabel(side == "long" ? "多单 1R" : "空单 1R")
                        if !inline && ticket.autoMode != nil {
                            Button("手动") { Haptics.tap(); ticket.manual() }.font(.caption.weight(.semibold)).buttonStyle(.pill(.soft, height: 28)).disabled(locked)
                        }
                    }
                }
            }) {
                TextField(inline ? "止损价格" : "输入止损价格", text: Binding(
                    get: { ticket.stop },
                    set: { value in
                        ticket.manual()
                        ticket.stop = value
                        ticket.stopApplied = nil
                        ticket.fieldIssue = nil
                    }
                ))
                .keyboardType(.decimalPad).focused($focus, equals: .stop)
                .disabled(locked || existing != nil)
            }
            issue(.stop)
            if !inline, existing == nil, let applied = ticket.stopApplied, applied == context + "|" + ticket.stop, ticket.fieldIssue?.field != .stop {
                Label(ticket.fibTicket ? "\(ticket.fibPeriod.label)插针止损 · 按1R与剩余额度计算" : "\(side == "long" ? "多单" : "空单") 1R 止损已填入，含成本预留",
                      systemImage: "checkmark.circle.fill")
                    .font(.caption.weight(.medium)).foregroundStyle(Color.up)
            }

            if existing == nil, state?.supportsCustomProtection == true, let stages = state?.policy?.takeProfits {
                DisclosureGroup(isExpanded: $showTargets) {
                    VStack(spacing: 10) {
                        ForEach(stages.indices, id: \.self) { index in
                            let stage = stages[index]
                            ProtectionPriceRow(title: "止盈 \(index + 1)", detail: index == stages.count - 1 ? "平余仓" : "平 \(Fmt.plainPercent(stage.last))%",
                                               placeholder: "自动 · \(stage.first ?? "")R",
                                               value: Binding(get: { ticket.targetInputs[index] ?? "" }, set: {
                                                   ticket.targetInputs[index] = $0; ticket.fieldIssue = nil
                                               }))
                        }
                    }.padding(.top, 6)
                } label: {
                    HStack {
                        Text("止盈价格").font(.subheadline.weight(.semibold))
                        Spacer()
                        Text(ticket.ticket(state).takeProfits == nil ? "自动分档" : "自定义").font(.caption).foregroundStyle(.secondary)
                    }
                }.disabled(locked).tint(.accentColor)
                issue(.targets)
            }

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("仓位额度").font(.subheadline.weight(.semibold))
                    Spacer()
                    Text("\(Int(ticket.allocation))%").font(.subheadline.weight(.bold).monospacedDigit())
                }
                Text(ticket.sizingMode(state) == "exposure"
                     ? (inline ? inlineAllocationLabel(state, room: room) : allocationLabel(state))
                     : (inline ? inlineMarginAllocationLabel(state, room: room) : "保证金占净值 \(state == nil ? "—" : Fmt.amount((Double(ticket.fraction(state)) ?? 0) * 100))%"))
                        .font(.caption).foregroundStyle(.secondary)
                Slider(value: $ticket.allocation, in: 0...100, step: 1)
                    .disabled(locked || state == nil || room <= 0)
                    .onChange(of: ticket.allocation) { _, value in
                        ticket.fieldIssue = nil
                        if Int(value) / 25 != lastAllocationTick { lastAllocationTick = Int(value) / 25; Haptics.select() }
                    }
                HStack(spacing: inline ? 3 : 8) {
                    ForEach([25, 50, 75, 100], id: \.self) { value in
                        Button { withAnimation(.snappy) { ticket.allocation = Double(value) }; value == 100 ? Haptics.open() : Haptics.press() } label: {
                            Text("\(value)%").font(.caption.weight(.semibold)).lineLimit(1)
                                .foregroundStyle(Int(ticket.allocation) == value ? Color.accentColor : Color.ink)
                                .frame(maxWidth: .infinity).frame(minHeight: 30)
                                .background(Int(ticket.allocation) == value ? Color.accentColor.opacity(0.12) : Color.field, in: .rect(cornerRadius: 6))
                        }
                        .buttonStyle(.plain)
                        .disabled(locked || state == nil || room <= 0)
                    }
                }
            }
            if let issue = ticket.fieldIssue, [.sizing, .leverage].contains(issue.field) {
                Text(issue.message).font(.caption.weight(.medium)).foregroundStyle(Color.down)
            }

            if inline {
                VStack(spacing: 4) {
                    amountRow("可用资金", state.flatMap { $0.available }.flatMap(Double.init))
                    amountRow("名义金额", estimate?.notional)
                    amountRow("保证金", estimate?.margin)
                    inlineRiskRow(existing: existing, estimate: estimate, state: state)
                }
            } else {
                HStack {
                    Metric(title: existing != nil ? "本次持仓金额" : "持仓金额", value: Fmt.amount(estimate?.notional), unit: "U")
                    Metric(title: "保证金", value: Fmt.amount(estimate?.margin), unit: "U")
                    Metric(title: existing != nil ? "合并止损金额" : "预计止损", value: Fmt.amount(estimate?.risk), unit: "U")
                }
                .padding(12).background(Color.field, in: .rect(cornerRadius: 14))

                HStack {
                    Text(account.live ? "\(state?.accountName ?? "主账户") · USDT 净值" : "模拟净值").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Text(Fmt.amount(state?.equity) + " USDT").font(.subheadline.weight(.semibold).monospacedDigit())
                }

            }
            HStack(spacing: 8) {
                if !inline {
                    directionPicker(side: side, state: state, locked: locked)
                        .frame(width: 132, height: 50)
                }
                Button {
                    Task { await review(side: side, state: state) }
                } label: {
                    HStack(spacing: 8) {
                        if account.pendingEntry != nil { ProgressView().tint(.white); Text("正在核对订单") }
                        else if ticket.reviewing { ProgressView().tint(.white); Text("正在校验…") }
                        else if ticket.autoWorking { ProgressView().tint(.white); Text("计算止损中") }
                        else { Text(entryActionTitle(side: side, existing: existing)) }
                    }
                    .font(.body.weight(.bold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                    .frame(maxWidth: .infinity).frame(height: 50)
                }
                .buttonStyle(.pill(.solid(side == "long" ? .up : .down), height: 50))
                .disabled(block != nil || locked || !ready)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) { Spacer(); Button("完成") { focus = nil } }
        }
        .sheet(isPresented: $leverageSheet) { LeverageSheet().presentationDetents([.height(340)]) }
        .sheet(item: Binding(get: { hosts ? ticket.preview : nil }, set: { if !$0.isNilValue { ticket.preview = $0 } else if hosts { ticket.preview = nil } })) { pending in
            PreviewSheet(pending: pending).presentationDetents([.large])
        }
        .alert(Trading.errorCopy(ticket.error ?? "").title, isPresented: Binding(get: { hosts && ticket.error != nil }, set: { if !$0 { ticket.error = nil } })) {
            if Trading.errorCopy(ticket.error ?? "").rules { Button("查看当前规则") { ticket.error = nil; onBlocked(.discipline) } }
            Button("知道了", role: .cancel) { ticket.error = nil }
        } message: { Text(Trading.errorCopy(ticket.error ?? "").detail) }
        .task(id: "\(context)|\(ticket.autoMode ?? "")|\(ticket.autoRevision)|\(autoEnabled)|\(hosts)") {
            await runAutoStop(context: context, enabled: autoEnabled)
        }
        .onChange(of: focus) { old, _ in if let old { validate(old, state: state) } }
        .onChange(of: ticket.fieldIssue?.field) { _, field in if field == .targets { showTargets = true } }
    }

    // MARK: pieces

    private func inlineDirectionTabs(side: String, state: AccountState?, locked: Bool) -> some View {
        HStack(spacing: 10) {
            inlineDirectionTab("long", side: side, state: state, locked: locked)
            inlineDirectionTab("short", side: side, state: state, locked: locked)
        }
        .animation(.snappy, value: side)
        .accessibilityElement(children: .contain)
    }

    private func inlineDirectionTab(_ value: String, side: String, state: AccountState?, locked: Bool) -> some View {
        let selected = side == value
        let hue: Color = value == "long" ? .up : .down
        return Button {
            selectDirection(value, state: state)
        } label: {
            Text(value == "long" ? "做多" : "做空")
                .font(.scaled(size: 16, weight: .bold))
                .foregroundStyle(selected ? Color.white : Color.muted)
                .frame(maxWidth: .infinity).frame(height: 44)
                .background(selected ? hue : Color.field, in: .rect(cornerRadius: 9))
                .contentShape(.capsule)
        }
        .buttonStyle(.plain)
        .disabled(locked || ticket.existing(state) != nil)
        .accessibilityLabel("方向，\(value == "long" ? "做多" : "做空")")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func inlineOrderKindMenu(locked: Bool) -> some View {
        Menu {
            Button { setOrderKind("market", locked: locked) } label: {
                Label("市价委托", systemImage: "bolt.fill")
            }
            Button { setOrderKind("limit", locked: locked) } label: {
                Label("限价委托", systemImage: "tag.fill")
            }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: ticket.kind == "limit" ? "tag.fill" : "bolt.fill")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(ticket.kind == "limit" ? Color.accentColor : Color.amber)
                Text(ticket.kind == "limit" ? "限价" : "市价").font(.subheadline.weight(.semibold))
                Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold)).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity).frame(height: 36)
            .background(Color.field, in: .rect(cornerRadius: 8))
            .frame(minHeight: 44)
            .contentShape(.rect(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(locked)
        .accessibilityLabel("委托类型，\(ticket.kind == "limit" ? "限价" : "市价")")
    }

    private func setOrderKind(_ kind: String, locked: Bool) {
        guard !locked, ticket.kind != kind else { return }
        ticket.kind = kind
        ticket.fieldIssue = nil
        Haptics.select()
    }

    private func inlineAllocationLabel(_ state: AccountState?, room: Double) -> String {
        guard state != nil else { return "剩余名义额度 —" }
        let pool = Trading.isMajor(ticket.symbol) ? Fmt.coin(ticket.symbol) : "山寨共享"
        return "\(pool)名义剩余 \(Fmt.amount(floor(room), 0)) U"
    }

    private func inlineMarginAllocationLabel(_ state: AccountState?, room: Double) -> String {
        guard let state, let equity = Double(state.equity), equity.isFinite, equity > 0 else { return "剩余保证金额度 —" }
        return "\(Fmt.marginMode(state.policy?.marginMode)) · 保证金剩余 \(Fmt.amount(room * equity, 0)) U"
    }

    private func inlineRiskRow(existing: Position?, estimate: Trading.Estimate?, state: AccountState?) -> some View {
        let equity = state.flatMap { Double($0.equity) } ?? .nan
        let risk = estimate.flatMap { value -> Double? in
            guard value.risk.isFinite, value.risk >= 0 else { return nil }
            return value.risk
        }
        let riskPercent = risk.flatMap { value -> Double? in
            guard equity.isFinite, equity > 0 else { return nil }
            let percent = value / equity * 100
            return percent.isFinite && percent > 0 ? percent : nil
        }
        let title = existing == nil ? "预计止损" : "合并止损"
        let loss = risk.map { ($0 > 0 ? "−" : "") + Fmt.amount($0) + " U" } ?? "—"
        let accessibilityLabel = riskPercent.map {
            "\(title)，\(loss)，约占净值 \(Fmt.amount($0, 1))%"
        } ?? "\(title)，\(loss)"

        return HStack(spacing: 5) {
            Text(title).foregroundStyle(.secondary)
            Spacer(minLength: 4)
            Text(loss)
                .fontWeight(.semibold).monospacedDigit()
                .foregroundStyle((risk ?? 0) > 0 ? Color.down : Color.muted)
        }
        .font(.caption)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
    }

    private func entryActionTitle(side: String, existing: Position?) -> String {
        if confirmTrades {
            return side == "long"
                ? (existing == nil ? "预览开多" : "预览加多")
                : (existing == nil ? "预览开空" : "预览加空")
        }
        return side == "long"
            ? (existing == nil ? "买入开多" : "买入加仓")
            : (existing == nil ? "卖出开空" : "卖出加仓")
    }

    private func amountRow(_ title: String, _ value: Double?) -> some View {
        HStack(spacing: 4) {
            Text(title).foregroundStyle(.secondary)
            Spacer(minLength: 0)
            Text(Fmt.amount(value) + " U").fontWeight(.medium)
                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.8)
        }.font(.caption)
    }

    private func directionPicker(side: String, state: AccountState?, locked: Bool) -> some View {
        HStack(spacing: 2) {
            directionOption("long", side: side, state: state, locked: locked)
            directionOption("short", side: side, state: state, locked: locked)
        }
        .padding(3)
        .background(Color.field, in: .rect(cornerRadius: 11))
        .animation(.snappy, value: side)
        .accessibilityElement(children: .contain)
    }

    private func directionOption(_ value: String, side: String, state: AccountState?, locked: Bool) -> some View {
        let selected = side == value
        let hue: Color = value == "long" ? .up : .down
        return Button {
            selectDirection(value, state: state)
        } label: {
            HStack(spacing: 3) {
                Image(systemName: value == "long" ? "arrow.up" : "arrow.down")
                    .font(.system(size: 9, weight: .heavy))
                Text(value == "long" ? "做多" : "做空")
                    .font(.caption.weight(.bold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .foregroundStyle(selected ? Color.white : Color.muted)
            .frame(maxWidth: .infinity).frame(height: 42)
            .background(selected ? hue : .clear, in: .rect(cornerRadius: 8))
            .contentShape(.rect(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(locked || ticket.existing(state) != nil)
        .accessibilityLabel("方向，" + (value == "long" ? "做多" : "做空"))
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func field<Trailing: View, Content: View>(title: String, @ViewBuilder trailing: () -> Trailing, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack { Text(title).font(.subheadline.weight(.semibold)); Spacer(); trailing() }
            HStack {
                content().font(.title3.weight(.semibold).monospacedDigit())
                Text("USDT").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            }
            .padding(.horizontal, inline ? 9 : 14).frame(height: inline ? 40 : 48)
            .background(Color.field, in: .rect(cornerRadius: 14))
        }
    }

    @ViewBuilder private func issue(_ field: Trading.FieldIssue.Field) -> some View {
        if let issue = ticket.fieldIssue, issue.field == field {
            Text(issue.message).font(.caption.weight(.medium)).foregroundStyle(Color.down).transition(.opacity)
        }
    }

    private func validate(_ field: Trading.FieldIssue.Field, state: AccountState?) {
        if let issue = Trading.fieldIssue(ticket.ticket(state), state: state), issue.field == field { ticket.fieldIssue = issue }
    }

    private func allocationLabel(_ state: AccountState?) -> String {
        guard state != nil else { return "额度剩余 —" }
        let remaining = Fmt.amount(floor(Trading.exposureRoom(state, symbol: ticket.symbol)), 0)
        return Trading.isMajor(ticket.symbol) ? "\(Fmt.coin(ticket.symbol)) 剩余 \(remaining) U" : "山寨共享剩余 \(remaining) U"
    }

    private func selectDirection(_ value: String, state: AccountState?) {
        guard ticket.existing(state) == nil, !ticket.reviewing, account.pendingEntry == nil, value != ticket.side(state) else { return }
        let recalculate = ticket.autoMode != nil
        ticket.manual(); ticket.direction = value; ticket.stop = ""; ticket.stopApplied = nil; ticket.fieldIssue = nil
        if recalculate { ticket.selectAuto(value) }
        Haptics.select()
    }

    private func autoStop(_ side: String, state: AccountState?) {
        if !ticket.fibTicket, let issue = Trading.fieldIssue(ticket.ticket(state, side: side, stop: ""), state: state, skipStop: true) { ticket.fieldIssue = issue; return }
        ticket.fieldIssue = nil
        if ticket.fibTicket { ticket.stop = ""; ticket.stopApplied = nil }
        ticket.selectAuto(side); Haptics.tap()
    }

    private func runAutoStop(context: String, enabled: Bool) async {
        guard hosts else { return }
        guard let mode = ticket.autoMode, enabled, session.connection != nil else { ticket.autoWorking = false; return }
        if ticket.fibTicket && ticket.fibPeriod == .h12 {
            // The service computes wick stops only for OKX's own bars (1m…4H, 1D); 12H is display-only.
            ticket.autoWorking = false; ticket.autoMode = nil; ticket.stop = ""
            ticket.fieldIssue = Trading.FieldIssue(field: .stop, message: "12H 暂无插针止损，请切到 4H 或手动填写")
            return
        }
        ticket.autoWorking = true
        try? await Task.sleep(for: .milliseconds(350))
        guard !Task.isCancelled else { return }
        do {
            let state = account.state
            let value = try await withThrowingTaskGroup(of: String.self) { group in
                group.addTask { @MainActor in
                    ticket.fibTicket ? try await account.wickStop(symbol: ticket.symbol, period: ticket.fibPeriod.rawValue, side: mode)
                        : try await account.autoStop(ticket.ticket(state, side: mode, stop: ""))
                }
                group.addTask { try await Task.sleep(for: .seconds(12)); throw APIError(status: 0, message: "止损读取超时，请重试或手动填写") }
                let first = try await group.next()!
                group.cancelAll()
                return first
            }
            guard !Task.isCancelled, ticket.autoMode == mode else { return }
            guard let number = Double(value), number.isFinite, number > 0 else { throw APIError(status: 0, message: "插针止损暂不可用，请重试或手动填写") }
            ticket.stop = value
            ticket.stopApplied = context + "|" + value
            ticket.autoCalculated = context + ":" + mode
            ticket.fieldIssue = Trading.fieldIssue(ticket.ticket(account.state, side: mode, stop: value), state: account.state)
            Haptics.success()
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            ticket.autoCalculated = ""
            if ticket.fibTicket { ticket.stop = "" }
            let message = error.localizedDescription
            ticket.fieldIssue = Trading.issue(forMessage: message) ?? Trading.FieldIssue(field: .stop, message: message)
        }
        ticket.autoWorking = false
    }

    private func review(side: String, state: AccountState?) async {
        guard let owner = session.connection, account.connection == owner,
              !ticket.reviewing, account.pendingEntry == nil, let state else { return }
        ticket.reviewing = true; ticket.error = nil; Haptics.tap()
        defer { ticket.reviewing = false }
        let order = ticket.ticket(state, side: side)
        if let issue = Trading.fieldIssue(order, state: state) { ticket.fieldIssue = issue; focus = issue.field; Haptics.warning(); return }
        ticket.fieldIssue = nil; focus = nil
        let requestId = Trading.newRequestId()
        do {
            if account.live && !confirmTrades {
                try await account.submit(order, requestId: requestId, policyRevision: state.policyRevision, previewId: nil)
                finish()
                return
            }
            let result = try await account.preview(order)
            guard session.connection == owner, account.connection == owner else { return }
            let pending = TicketModel.Pending(id: requestId, ticket: order, plan: result.plan, policyRevision: result.policyRevision, previewId: result.previewId)
            if confirmTrades { ticket.preview = pending } else {
                try await account.submit(order, requestId: requestId, policyRevision: result.policyRevision, previewId: result.previewId)
                finish()
            }
        } catch {
            let message = error.localizedDescription
            if let issue = Trading.issue(forMessage: message) { ticket.fieldIssue = issue } else { ticket.error = message }
            Haptics.warning()
        }
    }

    private func finish() {
        ticket.manual()
        ticket.targetInputs = [:]
        if ticket.fibTicket { ticket.fibTicket = false }
    }
}

private extension Optional {
    var isNilValue: Bool { if case .none = self { return true }; return false }
}

struct LeverageSheet: View {
    @Environment(AccountStore.self) private var account
    @Environment(TicketModel.self) private var ticket
    @Environment(\.dismiss) private var dismiss
    @State private var draft: Double = 1

    var body: some View {
        let cap = ticket.cap(account.state)
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("\(Fmt.coin(ticket.symbol))USDT · 当前\(Fmt.marginMode(account.state?.policy?.marginMode))规则").font(.subheadline).foregroundStyle(.secondary)
                HStack(alignment: .firstTextBaseline) {
                    Text("调整倍数").font(.subheadline.weight(.semibold))
                    Spacer()
                    Text("\(Int(draft))").font(.scaled(size: 34, weight: .bold, design: .rounded).monospacedDigit()).contentTransition(.numericText(value: draft))
                    Text("x").font(.headline).foregroundStyle(.secondary)
                }
                HStack(spacing: 12) {
                    Button { draft = max(1, draft - 1); Haptics.select() } label: { Image(systemName: "minus").frame(width: 30, height: 30) }
                        .buttonStyle(.pill(.soft)).disabled(draft <= 1).accessibilityLabel("杠杆减一")
                    Slider(value: $draft, in: 1...Double(max(cap, 2)), step: 1).onChange(of: draft) { _, _ in Haptics.select() }
                    Button { draft = min(Double(cap), draft + 1); Haptics.select() } label: { Image(systemName: "plus").frame(width: 30, height: 30) }
                        .buttonStyle(.pill(.soft)).disabled(Int(draft) >= cap).accessibilityLabel("杠杆加一")
                }
                Text("杠杆影响保证金，仓位仍由止损风险与当前规则核定。" + (account.state?.policy?.marginMode == "isolated" ? "逐仓模式下，较远的止损可能需要降低杠杆。" : ""))
                    .font(.caption).foregroundStyle(.secondary)
                Button {
                    ticket.leverage = min(Double(cap), Double(Int(draft))); Haptics.tap(); dismiss()
                } label: { Text("确认 \(min(cap, Int(draft)))x").font(.body.weight(.bold)).frame(maxWidth: .infinity).frame(height: 46) }
                .buttonStyle(.pill(.solid(.accentColor), height: 46))
            }
            .padding(18)
            .navigationTitle("杠杆").navigationBarTitleDisplayMode(.inline)
        }
        .onAppear { draft = min(ticket.leverage, Double(cap)) }
    }
}
