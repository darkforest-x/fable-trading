import SwiftUI

struct PaperSection: View {
    let connection: Connection
    @State private var data: PaperSnapshot?
    @State private var error: String?
    @State private var detail: PaperAccount?

    var body: some View {
        let live = data?.accounts.filter { $0.group == "live" } ?? []
        let replica = data?.accounts.filter { $0.group != "live" } ?? []
        VStack(alignment: .leading, spacing: 12) {
            StatusNote(text: "模拟账户 · 只读 · 不涉及真实资金 · 净值不含浮盈")
            LoadState(loading: data == nil && error == nil, error: error, stale: data != nil)
            if !live.isEmpty {
                SectionHeader(title: "live- 前向模拟", count: live.count)
                GlassEffectContainer(spacing: 10) {
                    let pairs = stride(from: 0, to: live.count - live.count % 2, by: 2).map { (live[$0], live[$0 + 1]) }
                    VStack(spacing: 10) {
                        ForEach(pairs, id: \.0.key) { pair in
                            HStack(spacing: 10) { AccountTile(account: pair.0) { detail = pair.0 }; AccountTile(account: pair.1) { detail = pair.1 } }
                        }
                        if live.count % 2 == 1, let last = live.last { AccountTile(account: last, wide: true) { detail = last } }
                    }
                }
            }
            if !replica.isEmpty {
                SectionHeader(title: "回测复刻", count: replica.count)
                VStack(spacing: 0) {
                    ForEach(Array(replica.enumerated()), id: \.element.key) { index, account in
                        Button { detail = account } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(account.label).font(.subheadline.weight(.semibold))
                                    Text("已平 \(Int(account.stats.closed ?? 0)) · 胜 \(Int(account.stats.wins ?? 0))").font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                VStack(alignment: .trailing, spacing: 2) {
                                    Text(Fmt.money(account.equity)).font(.subheadline.weight(.semibold)).monospacedDigit()
                                    Text(Fmt.signedPercent(account.returnPct)).font(.caption).foregroundStyle(Fmt.tone(account.returnPct).color).monospacedDigit()
                                }
                                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                            }
                            .padding(.horizontal, 16).frame(minHeight: 58).contentShape(.rect)
                        }
                        .buttonStyle(RowPressStyle())
                        if index < replica.count - 1 { Divider().padding(.leading, 16) }
                    }
                }
                .glassCard()
            }
            if let data {
                SectionHeader(title: "今日触价", count: data.signals.count)
                if data.signals.isEmpty {
                    Text("今天还没有触价").font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(18).glassCard()
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(data.signals.enumerated()), id: \.element.rowId) { index, signal in
                            HStack(spacing: 12) {
                                CoinLogo(coin: Fmt.coin(signal.symbol))
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack(spacing: 6) { Text(Fmt.coin(signal.symbol)).font(.subheadline.weight(.semibold)); SidePill(side: signal.side) }
                                    Text("\(Fmt.signalStatus(signal)) · 1.618 \(Fmt.price(signal.level))").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                }
                                Spacer()
                                VStack(alignment: .trailing, spacing: 2) {
                                    Text(signal.score.map { String(format: "%.2f", $0) } ?? "—").font(.subheadline.weight(.semibold)).monospacedDigit()
                                    Text(Fmt.clock(signal.observedMs, now: data.nowMs)).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            .padding(.horizontal, 16).frame(minHeight: 58)
                            if index < data.signals.count - 1 { Divider().padding(.leading, 60) }
                        }
                    }
                    .glassCard()
                }
            }
        }
        .modifier(Polled(key: "paper", every: .seconds(10), load: { try await API.get("/v1/monitor/paper", query: [URLQueryItem(name: "track", value: "live-wick6x3")], connection: connection) }, value: $data, error: $error))
        .sheet(item: $detail) { account in AccountSheet(account: account, connection: connection) }
    }
}

struct AccountTile: View {
    let account: PaperAccount
    var wide = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Group {
                if wide {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) { name; foot }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 4) { equity; change }
                    }
                } else {
                    VStack(alignment: .leading, spacing: 4) { name; equity.padding(.top, 6); change; Spacer(minLength: 2); foot }
                }
            }
            .padding(14).frame(maxWidth: .infinity, minHeight: wide ? 0 : 124, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .glassEffect(.regular.interactive(), in: .rect(cornerRadius: 22))
    }
    private var name: some View { Text(account.label).font(.system(.footnote, design: .monospaced).weight(.semibold)).foregroundStyle(.secondary).lineLimit(1) }
    private var equity: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(Fmt.money(account.equity)).font(.system(.title2, design: .rounded).weight(.semibold)).monospacedDigit()
            Text("U").font(.caption).foregroundStyle(.secondary)
        }
    }
    private var change: some View { Text(Fmt.signedPercent(account.returnPct)).font(.subheadline.weight(.semibold)).foregroundStyle(Fmt.tone(account.returnPct).color).monospacedDigit() }
    private var foot: some View { Text("已平 \(Int(account.stats.closed ?? 0)) · 胜 \(Int(account.stats.wins ?? 0))").font(.caption2).foregroundStyle(.secondary) }
}

struct RowPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.background(configuration.isPressed ? Color.primary.opacity(0.06) : .clear)
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}

struct AccountSheet: View {
    let account: PaperAccount
    let connection: Connection
    @State private var data: PaperSnapshot?
    @State private var error: String?

    var body: some View {
        let detail = data?.detail.key == account.key ? data?.detail : nil
        let a = data?.accounts.first { $0.key == account.key } ?? account
        NavigationStack {
            List {
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(Fmt.money(a.equity)).font(.system(.largeTitle, design: .rounded).weight(.semibold)).monospacedDigit()
                            Text("U").foregroundStyle(.secondary)
                            Text(Fmt.signedPercent(a.returnPct)).font(.headline).foregroundStyle(Fmt.tone(a.returnPct).color)
                        }
                        Text("起始 \(Fmt.money(a.startEquity)) U · 已平 \(Int(a.stats.closed ?? 0)) 笔 · 平均 \(a.stats.meanNetBp.map { String(format: "%.1f", $0) } ?? "—")bp")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    .listRowBackground(Color.clear)
                }
                if let error { Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(Color.amber) } }
                if let detail {
                    Section("持仓 · \(detail.open.count)") {
                        if detail.open.isEmpty { Text("当前没有模拟持仓").foregroundStyle(.secondary) }
                        ForEach(detail.open, id: \.self) { p in PositionRow(position: p, now: data?.nowMs ?? 0, closed: false) }
                    }
                    Section("今日平仓 · \(detail.closed.count)") {
                        if detail.closed.isEmpty { Text("今天没有平仓").foregroundStyle(.secondary) }
                        ForEach(detail.closed, id: \.self) { p in PositionRow(position: p, now: data?.nowMs ?? 0, closed: true) }
                    }
                } else if error == nil {
                    Section { ProgressView() }
                }
                Section { Text("监控工作台模拟账本的前向复刻，含回测口径评分；不是可下单的实盘策略。").font(.footnote).foregroundStyle(.secondary) }
            }
            .scrollContentBackground(.hidden)
            .navigationTitle(account.label)
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
        .presentationBackground(.thinMaterial)
        .modifier(Polled(key: "paper-\(account.key)", every: .seconds(10), load: { try await API.get("/v1/monitor/paper", query: [URLQueryItem(name: "track", value: account.key)], connection: connection) }, value: $data, error: $error))
    }
}

struct PositionRow: View {
    let position: PaperPosition
    let now: Double
    let closed: Bool
    var body: some View {
        HStack(spacing: 12) {
            CoinLogo(coin: Fmt.coin(position.symbol), size: 30)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) { Text(Fmt.coin(position.symbol)).font(.subheadline.weight(.semibold)); SidePill(side: position.side) }
                Text(closed ? "\(Fmt.reason(position.reason)) · \(Fmt.clock(position.exitMs, now: now))" : "\(Fmt.stage(position.stage)) · 入场 \(Fmt.price(position.entry)) · 止损 \(Fmt.price(position.stop))")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                if closed {
                    Text(position.pnl.map { ($0 > 0 ? "+" : "") + Fmt.money($0) } ?? "—").font(.subheadline.weight(.semibold)).foregroundStyle(Fmt.tone(position.pnl).color)
                    Text(position.netBp.map { String(format: "%+.0fbp", $0) } ?? "").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(position.unrealizedBp.map { String(format: "%+.0fbp", $0) } ?? "—").font(.subheadline.weight(.semibold)).foregroundStyle(Fmt.tone(position.unrealizedBp).color)
                    Text("目标 \(Fmt.price(position.target))").font(.caption).foregroundStyle(.secondary)
                }
            }.monospacedDigit()
        }
    }
}
