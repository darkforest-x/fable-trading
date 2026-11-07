import SwiftUI

/// 模拟盘回放 desk (ReplayDesk.tsx). Simulated cash only; never touches the live account.
struct ReplayView: View {
    @Environment(SessionStore.self) private var session
    @Environment(FibStore.self) private var fib
    @Environment(\.dismiss) private var dismiss
    @State private var store = ReplayStore()

    var body: some View {
        NavigationStack {
            Group {
                if store.snapshot != nil { ReplayDesk(store: store) } else { ReplaySetup(store: store, defaultSymbol: fib.chartSymbol.isEmpty ? "BTC-USDT-SWAP" : fib.chartSymbol) }
            }
            .containerBackground(for: .navigation) { Color.canvas }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if store.snapshot != nil {
                        Button { Haptics.tap(); store.close(); Task { await store.load() } } label: { Label("回放列表", systemImage: "chevron.left") }
                    }
                }
                ToolbarItem(placement: .topBarTrailing) { Button(role: .close) { Haptics.tap(); store.playing = false; dismiss() } }
            }
        }
        .task { store.attach(session.connection); await store.load() }
    }
}

private struct ReplaySetup: View {
    @Bindable var store: ReplayStore
    let defaultSymbol: String
    @State private var symbol = ""
    @State private var period = "15m"
    @State private var source = "okx_online"
    @State private var start = Date().addingTimeInterval(-7 * 86400)
    @State private var cash = "1000"

    var body: some View {
        Form {
            if !store.sessions.isEmpty {
                Section("继续回放") {
                    ForEach(store.sessions) { row in
                        Button { Haptics.tap(); Task { await store.open(row.id) } } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("\(Fmt.coin(row.symbol)) · \(row.period)").font(.body.weight(.semibold)).foregroundStyle(.primary)
                                    Text("进度 \(ReplayFormat.time(row.clock)) · 本金 \(ReplayFormat.value(row.initialCash)) U").font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "chevron.right").font(.caption.weight(.bold)).foregroundStyle(.tertiary)
                            }
                        }
                    }
                }
            }
            Section {
                TextField("合约，例如 BTC", text: $symbol).textInputAutocapitalization(.characters).autocorrectionDisabled()
                Picker("周期", selection: $period) { ForEach(store.catalog?.periods ?? ["1m", "5m", "15m", "1H", "4H"], id: \.self) { Text($0).tag($0) } }
                Picker("数据来源", selection: $source) { ForEach(store.catalog?.sources ?? [], id: \.id) { Text($0.label).tag($0.id) } }
                DatePicker("开始（北京时间）", selection: $start, in: ...Date(), displayedComponents: [.date, .hourAndMinute])
                    .environment(\.timeZone, TimeZone(identifier: "Asia/Shanghai")!)
                HStack { Text("模拟本金"); TextField("1000", text: $cash).keyboardType(.decimalPad).multilineTextAlignment(.trailing); Text("USDT").foregroundStyle(.secondary) }
                Button {
                    Task { await create() }
                } label: { HStack { if store.busy { ProgressView() }; Text(store.busy ? "正在准备历史 K 线…" : "开始回放").fontWeight(.semibold) } }
                .disabled(store.busy)
            } header: { Text("新建回放") } footer: {
                Text((store.catalog?.warning.map { $0 + " " } ?? "") + "模拟成交只用历史 K 线，不连接真实账户。")
            }
            if let error = store.error { Section { Text(error).foregroundStyle(Color.down) } }
        }
        .scrollContentBackground(.hidden)
        .navigationTitle("模拟盘回放")
        .onAppear { if symbol.isEmpty { symbol = Fmt.coin(defaultSymbol) }; if let first = store.catalog?.sources.first, !(store.catalog?.sources.contains { $0.id == source } ?? false) { source = first.id } }
        .onChange(of: store.catalog?.sources) { _, sources in if let first = sources?.first, !(sources?.contains { $0.id == source } ?? false) { source = first.id } }
    }

    private func create() async {
        do {
            let normalized = try ReplayFormat.symbol(symbol)
            guard let amount = Double(cash.trimmingCharacters(in: .whitespaces)), amount > 0 else { throw APIError(status: 0, message: "模拟本金必须大于 0") }
            _ = amount
            Haptics.tap()
            await store.create(symbol: normalized, period: period, source: source, start: start, cash: cash.trimmingCharacters(in: .whitespaces))
        } catch { store.error = error.localizedDescription; Haptics.warning() }
    }
}

private struct ReplayDesk: View {
    @Bindable var store: ReplayStore
    @State private var side = "long"
    @State private var kind = "market"
    @State private var notional = "100"
    @State private var price = ""
    @State private var stop = ""
    @State private var target = ""
    @State private var mondayNotional = "100"
    @State private var takeProfit = "none"
    @State private var follow = 0
    @State private var localError: String?

    var body: some View {
        let s = store.snapshot!
        let period = CandlePeriod(rawValue: s.period) ?? .m15
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header(s)
                CandleChart(bars: store.candles, generation: s.id.hashValue, period: period,
                            decimals: min(6, max(2, 5 - Int(floor(log10(max(s.price.number, 1e-9)))))), range: store.range,
                            lines: s.positions.flatMap { p in
                                [ChartLine(price: p.entry.number, title: "开仓", color: ChartPalette.accent)]
                                    + (p.stop?.value.map { [ChartLine(price: $0, title: "止损", color: ChartPalette.down, dashed: true)] } ?? [])
                                    + (p.target?.value.map { [ChartLine(price: $0, title: "止盈", color: ChartPalette.up, dashed: true)] } ?? [])
                            },
                            watermark: Fmt.coin(s.symbol) + "USDT", followToken: follow)
                    .frame(height: 300).padding(.vertical, 8).glassCard(cornerRadius: 22)
                controls
                if let notice = store.notice { StatusNote(text: notice, systemImage: "info.circle", tint: .blue) }
                if let error = localError ?? store.error {
                    VStack(alignment: .leading, spacing: 8) {
                        StatusNote(text: error, systemImage: "exclamationmark.triangle", tint: .amber)
                        if store.pending != nil { Button("继续原操作") { Haptics.tap(); Task { await store.retry() } }.buttonStyle(.glassProminent).disabled(store.busy) }
                    }
                }
                orderPanel(s)
                mondayPanel
                positions(s)
                orders(s)
                stats(s)
                closed(s)
            }
            .padding(.horizontal, 14).padding(.bottom, 30)
        }
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle("\(Fmt.coin(s.symbol)) · \(s.period) 回放")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: "\(store.playing)|\(store.speed)") {
            while store.playing && !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard store.playing, !store.busy, store.pending == nil else { continue }
                await store.command("advance", ["count": store.speed], automated: true)
            }
        }
    }

    private func header(_ s: ReplaySnapshot) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(ReplayFormat.time(s.clock)).font(.subheadline.weight(.semibold).monospacedDigit())
                Text("北京时间").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(ReplayFormat.price(s.price)).font(.title3.weight(.bold).monospacedDigit())
            }
            HStack {
                Metric(title: "模拟净值", value: ReplayFormat.value(s.equity), unit: "U")
                Metric(title: "可用", value: ReplayFormat.value(s.available), unit: "U")
                Metric(title: "累计手续费", value: ReplayFormat.value(s.fees), unit: "U")
            }
        }
        .padding(14).glassCard()
    }

    private var controls: some View {
        HStack(spacing: 8) {
            Button { Haptics.tap(); Task { await store.command("advance", ["count": 1]) } } label: { Label("前进 1 根", systemImage: "forward.frame").frame(height: 36) }
                .buttonStyle(.glass).disabled(store.busy || store.pending != nil)
            Picker("速度", selection: $store.speed) { Text("×1").tag(1); Text("×5").tag(5); Text("×20").tag(20) }.pickerStyle(.segmented).frame(width: 130)
            Button { Haptics.tap(); store.playing.toggle() } label: { Image(systemName: store.playing ? "pause.fill" : "play.fill").frame(width: 30, height: 36) }
                .buttonStyle(.glassProminent).disabled(store.pending != nil)
                .accessibilityLabel(store.playing ? "暂停" : "自动播放")
            Button { Haptics.tap(); follow += 1 } label: { Image(systemName: "arrow.right.to.line").frame(height: 36) }.buttonStyle(.glass).accessibilityLabel("跟随最新")
        }
    }

    private func orderPanel(_ s: ReplaySnapshot) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("模拟下单").font(.headline)
            Picker("方向", selection: $side) { Text("做多").tag("long"); Text("做空").tag("short") }.pickerStyle(.segmented)
            Picker("类型", selection: $kind) { Text("市价").tag("market"); Text("限价").tag("limit") }.pickerStyle(.segmented)
            numberField("名义金额 · USDT", $notional)
            if kind == "limit" { numberField("限价", $price) }
            HStack { numberField("止损（可选）", $stop); numberField("止盈（可选）", $target) }
            Button { submitOrder(s) } label: {
                Text(side == "long" ? "模拟买入开多" : "模拟卖出开空").font(.body.weight(.bold)).frame(maxWidth: .infinity).frame(height: 44)
            }
            .buttonStyle(.glassProminent).tint(side == "long" ? .up : .down).disabled(store.busy || store.pending != nil)
        }
        .padding(14).glassCard()
    }

    private var mondayPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("一键 1.618 挂单").font(.headline)
            Text("在本周上下 1.618 各挂一笔被动限价单；已挂过或已越过的方向会跳过。").font(.caption).foregroundStyle(.secondary)
            numberField("每笔名义金额 · USDT", $mondayNotional)
            Picker("止盈", selection: $takeProfit) { Text("不设止盈").tag("none"); Text("近侧周一边界").tag("near"); Text("远侧周一边界").tag("far") }.pickerStyle(.segmented)
            Button {
                guard let n = Double(mondayNotional), n > 0 else { localError = "名义金额必须是大于 0 的 USDT 数值"; return }
                localError = nil; Haptics.tap()
                Task { await store.command("monday", ["notional": mondayNotional.trimmingCharacters(in: .whitespaces), "take_profit": takeProfit]) }
            } label: { Text("挂出 1.618 委托").fontWeight(.semibold).frame(maxWidth: .infinity).frame(height: 40) }
            .buttonStyle(.glass).disabled(store.busy || store.pending != nil || store.range == nil)
        }
        .padding(14).glassCard()
    }

    @ViewBuilder private func positions(_ s: ReplaySnapshot) -> some View {
        if !s.positions.isEmpty {
            SectionHeader(title: "模拟持仓", count: s.positions.count)
            ForEach(s.positions) { p in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Pill(text: p.side == "long" ? "多" : "空", color: p.side == "long" ? .up : .down)
                        Text("开仓 \(ReplayFormat.price(p.entry))").font(.subheadline.monospacedDigit())
                        Spacer()
                        Text(Fmt.signed(p.unrealized.value) + " U").font(.subheadline.weight(.bold).monospacedDigit()).foregroundStyle(p.unrealized.number < 0 ? Color.down : Color.up)
                    }
                    HStack {
                        Metric(title: "名义 · U", value: ReplayFormat.value(p.notional))
                        Metric(title: "止损", value: ReplayFormat.price(p.stop))
                        Metric(title: "止盈", value: ReplayFormat.price(p.target))
                    }
                    HStack {
                        Button("平一半") { Haptics.tap(); Task { await store.command("close", ["position_id": p.id, "fraction": 0.5]) } }.buttonStyle(.glass)
                        Button("全部平仓") { Haptics.tap(); Task { await store.command("close", ["position_id": p.id, "fraction": 1]) } }.buttonStyle(.glassProminent).tint(.down)
                    }
                    .disabled(store.busy || store.pending != nil)
                }
                .padding(14).glassCard()
            }
        }
    }

    @ViewBuilder private func orders(_ s: ReplaySnapshot) -> some View {
        let open = s.orders.filter { ["open", "pending", "submitted"].contains($0.status) }
        if !open.isEmpty {
            SectionHeader(title: "模拟委托", count: open.count)
            ForEach(open) { o in
                HStack {
                    Pill(text: o.side == "long" ? "多" : "空", color: o.side == "long" ? .up : .down)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(ReplayFormat.price(o.price)) · \(ReplayFormat.value(o.notional)) U").font(.subheadline.monospacedDigit())
                        Text(ReplayFormat.status(o.status) + (o.tag == "monday1618" ? " · 1.618" : "") + (o.expires.map { " · 到期 \(ReplayFormat.time($0))" } ?? "")).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if o.status == "open" {
                        Button("撤单") { Haptics.tap(); Task { await store.command("cancel", ["order_id": o.id]) } }.buttonStyle(.glass).disabled(store.busy || store.pending != nil)
                    }
                }
                .padding(12).glassCard()
            }
        }
    }

    private func stats(_ s: ReplaySnapshot) -> some View {
        let st = s.statistics
        return VStack(alignment: .leading, spacing: 10) {
            Text("回放统计").font(.headline)
            HStack {
                Metric(title: "交易", value: "\(st.trades) 笔")
                Metric(title: "胜率", value: st.winRate.map { Fmt.amount($0 * 100, 1) + "%" } ?? "—")
                Metric(title: "盈亏比", value: st.payoff.map { Fmt.amount($0) } ?? "—")
            }
            HStack {
                Metric(title: "净盈亏 · U", value: ReplayFormat.value(st.netPnl), tone: st.netPnl.number < 0 ? .down : .up)
                Metric(title: "收益率", value: ReplayFormat.value(st.returnPct) + "%")
                Metric(title: "最大回撤", value: ReplayFormat.value(st.maxDrawdownPct) + "%")
            }
            Text("最长连亏 \(st.longestLossStreak) 笔 · 利润因子 \(st.profitFactor.map { Fmt.amount($0) } ?? "—")").font(.caption).foregroundStyle(.secondary)
        }
        .padding(14).glassCard()
    }

    @ViewBuilder private func closed(_ s: ReplaySnapshot) -> some View {
        if !s.closed.isEmpty {
            SectionHeader(title: "已平仓", count: s.closed.count)
            VStack(spacing: 0) {
                ForEach(s.closed.reversed()) { t in
                    HStack {
                        Pill(text: t.side == "long" ? "多" : "空", color: t.side == "long" ? .up : .down)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(ReplayFormat.price(t.entry)) → \(ReplayFormat.price(t.exit))").font(.subheadline.monospacedDigit())
                            Text("\(ReplayFormat.time(t.opened)) – \(ReplayFormat.time(t.closed))").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(Fmt.signed(t.netPnl.value) + " U").font(.subheadline.weight(.bold).monospacedDigit()).foregroundStyle(t.netPnl.number < 0 ? Color.down : Color.up)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 10)
                }
            }
            .glassCard()
        }
    }

    private func numberField(_ title: String, _ value: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            TextField(title, text: value).keyboardType(.decimalPad).font(.body.monospacedDigit())
                .padding(.horizontal, 12).frame(height: 40).background(Color.field, in: .rect(cornerRadius: 12))
        }
    }

    /// Local guidance mirrors ReplayDesk onOrder; the simulator re-validates.
    private func submitOrder(_ s: ReplaySnapshot) {
        func value(_ t: String) -> Double? { Double(t.trimmingCharacters(in: .whitespaces)).flatMap { $0 > 0 && $0.isFinite ? $0 : nil } }
        guard value(notional) != nil else { localError = "名义金额必须是大于 0 的 USDT 数值"; return }
        if kind == "limit" && value(price) == nil { localError = "限价委托必须填写有效价格"; return }
        for (name, text) in [("止损", stop), ("止盈", target)] where !text.trimmingCharacters(in: .whitespaces).isEmpty && value(text) == nil {
            localError = "\(name)价格必须是大于 0 的数值"; return
        }
        let latest = s.price.number, expected = kind == "limit" ? value(price)! : latest
        if kind == "limit" && ((side == "long" && expected >= latest) || (side == "short" && expected <= latest)) { localError = "限价须保持被动：做多低于最新收盘价，做空高于最新收盘价"; return }
        if let v = value(stop) { if side == "long" ? v >= expected : v <= expected { localError = side == "long" ? "做多止损价必须低于预计成交价" : "做空止损价必须高于预计成交价"; return } }
        if let v = value(target) { if side == "long" ? v <= expected : v >= expected { localError = side == "long" ? "做多止盈价必须高于预计成交价" : "做空止盈价必须低于预计成交价"; return } }
        localError = nil; Haptics.tap()
        var params: [String: Any] = ["side": side, "kind": kind, "notional": notional.trimmingCharacters(in: .whitespaces)]
        if kind == "limit" { params["price"] = price.trimmingCharacters(in: .whitespaces) }
        if !stop.trimmingCharacters(in: .whitespaces).isEmpty { params["stop"] = stop.trimmingCharacters(in: .whitespaces) }
        if !target.trimmingCharacters(in: .whitespaces).isEmpty { params["target"] = target.trimmingCharacters(in: .whitespaces) }
        Task { await store.command("order", params) }
    }
}
