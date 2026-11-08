import SwiftUI

struct PaperSection: View {
    @Environment(\.openChart) private var openChart
    @Environment(\.openAccount) private var openAccount
    let connection: Connection
    let refresh: Int
    @State private var data: PaperSnapshot?
    @State private var error: String?

    var body: some View {
        let live = data?.accounts.filter { $0.group == "live" } ?? []
        let replica = data?.accounts.filter { $0.group != "live" } ?? []
        VStack(alignment: .leading, spacing: 8) {
            LoadState(loading: data == nil && error == nil, error: error, stale: data != nil)
            if !live.isEmpty {
                SectionHeader(title: "live- 前向模拟", count: live.count)
                Group {
                    let pairs = stride(from: 0, to: live.count - live.count % 2, by: 2).map { (live[$0], live[$0 + 1]) }
                    VStack(spacing: 10) {
                        ForEach(pairs, id: \.0.key) { pair in
                            HStack(spacing: 10) { AccountTile(account: pair.0); AccountTile(account: pair.1) }
                        }
                        if live.count % 2 == 1, let last = live.last { AccountTile(account: last, wide: true) }
                    }
                }
            }
            if !replica.isEmpty {
                SectionHeader(title: "回测复刻", count: replica.count)
                VStack(spacing: 0) {
                    ForEach(Array(replica.enumerated()), id: \.element.key) { index, account in
                        Button { openAccount(account) } label: {
                            HStack(spacing: 10) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(account.label).font(.subheadline.weight(.semibold))
                                    Text("已平 \(Int(account.stats.closed ?? 0)) · 胜 \(Int(account.stats.wins ?? 0))").font(.caption2).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Text(Fmt.money(account.equity)).font(.subheadline.weight(.semibold)).monospacedDigit()
                                    .contentTransition(.numericText(value: account.equity ?? 0))
                                ChangeCapsule(value: account.returnPct)
                                Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
                            }
                            .padding(.horizontal, 12).frame(minHeight: 48).contentShape(.rect)
                        }
                        .buttonStyle(RowPressStyle())
                        if index < replica.count - 1 { Divider().padding(.leading, 14) }
                    }
                }
                .glassCard()
                .scrollReveal()
            }
        }
        .modifier(Polled(key: "paper-\(refresh)", every: .seconds(10), load: { try await API.get("/v1/monitor/paper", query: [URLQueryItem(name: "track", value: "live-wick6x3")], connection: connection) }, value: $data, error: $error))
    }
}

/// Green/red return capsule used on tiles and rows.
struct ChangeCapsule: View {
    let value: Double?
    var body: some View {
        Text(Fmt.signedPercent(value)).font(.caption.weight(.bold)).monospacedDigit()
            .foregroundStyle(Fmt.tone(value).color)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(Fmt.tone(value).color.opacity(0.12), in: .capsule)
            .contentTransition(.numericText(value: value ?? 0))
    }
}

struct AccountTile: View {
    @Environment(\.openAccount) private var openAccount
    let account: PaperAccount
    var wide = false
    var body: some View {
        Button { openAccount(account) } label: {
            Group {
                if wide {
                    HStack(alignment: .center) {
                        VStack(alignment: .leading, spacing: 6) { name; foot }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 6) { equity; ChangeCapsule(value: account.returnPct) }
                    }
                } else {
                    VStack(alignment: .leading, spacing: 6) { name; equity.padding(.top, 4); ChangeCapsule(value: account.returnPct); foot.padding(.top, 2) }
                }
            }
            .padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(CardPressStyle())
        .glassCard(cornerRadius: 18)
        .scrollReveal()
    }
    private var name: some View { Text(account.label).font(.system(.caption, design: .monospaced).weight(.semibold)).foregroundStyle(.secondary).lineLimit(1) }
    private var equity: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(Fmt.money(account.equity)).font(.scaled(size: 23, weight: .semibold, design: .rounded)).monospacedDigit()
                .contentTransition(.numericText(value: account.equity ?? 0))
            Text("U").font(.caption).foregroundStyle(.secondary)
        }
    }
    private var foot: some View { Text("已平 \(Int(account.stats.closed ?? 0)) · 胜 \(Int(account.stats.wins ?? 0))").font(.caption2).foregroundStyle(.secondary) }
}

struct RowPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.background(configuration.isPressed ? Color.primary.opacity(0.07) : .clear)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.8), value: configuration.isPressed)
    }
}

/// Account detail, presented as a sheet from the tapped tile/row.
struct AccountDetailView: View {
    @Environment(\.openChart) private var openChart
    @Environment(\.dismiss) private var dismiss
    let account: PaperAccount
    let connection: Connection
    @State private var data: PaperSnapshot?
    @State private var error: String?

    var body: some View {
        let detail = data?.detail.key == account.key ? data?.detail : nil
        let a = data?.accounts.first { $0.key == account.key } ?? account
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(Fmt.money(a.equity)).font(.scaled(size: 44, weight: .semibold, design: .rounded)).monospacedDigit()
                            .contentTransition(.numericText(value: a.equity ?? 0))
                        Text("U").font(.title3).foregroundStyle(.secondary)
                        Spacer()
                        ChangeCapsule(value: a.returnPct)
                    }
                    HStack(spacing: 10) {
                        stat("起始", "\(Fmt.money(a.startEquity)) U")
                        stat("已平", "\(Int(a.stats.closed ?? 0)) 笔")
                        stat("胜", "\(Int(a.stats.wins ?? 0)) 笔")
                        stat("平均", a.stats.meanNetBp.map { String(format: "%.1fbp", $0) } ?? "—")
                    }
                }
                .padding(18).glassCard(cornerRadius: 26)
                LoadState(loading: detail == nil && error == nil, error: error, stale: detail != nil)
                if let detail {
                    SectionHeader(title: "持仓", count: detail.open.count)
                    positions(detail.open, closed: false, empty: "当前没有模拟持仓")
                    SectionHeader(title: "今日平仓", count: detail.closed.count)
                    positions(detail.closed, closed: true, empty: "今天没有平仓")
                }
            }
            .padding(.horizontal, 14).padding(.bottom, 24)
        }
        .scrollEdgeEffectHidden(true, for: .top)
        .containerBackground(for: .navigation) { AmbientBackground() }
        .navigationTitle(account.label)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
        .modifier(Polled(key: "paper-\(account.key)", every: .seconds(10), load: { try await API.get("/v1/monitor/paper", query: [URLQueryItem(name: "track", value: account.key)], connection: connection) }, value: $data, error: $error))
    }

    private func stat(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.footnote.weight(.semibold)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.8)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func positions(_ rows: [PaperPosition], closed: Bool, empty: String) -> some View {
        VStack(spacing: 0) {
            if rows.isEmpty { Text(empty).font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(16) }
            ForEach(Array(rows.enumerated()), id: \.offset) { index, p in
                Button { Haptics.open(); openChart(p.symbol) } label: {
                    PositionRow(position: p, now: data?.nowMs ?? 0, closed: closed).padding(.horizontal, 12).frame(minHeight: 52).contentShape(.rect)
                }
                .buttonStyle(RowPressStyle())
                if index < rows.count - 1 { Divider().padding(.leading, 56) }
            }
        }
        .glassCard()
        .scrollReveal()
    }
}

struct PositionRow: View {
    let position: PaperPosition
    let now: Double
    let closed: Bool
    var body: some View {
        HStack(spacing: 11) {
            CoinLogo(coin: Fmt.coin(position.symbol), size: 30)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) { Text(Fmt.coin(position.symbol)).font(.subheadline.weight(.semibold)); SidePill(side: position.side) }
                Text(closed ? "\(Fmt.reason(position.reason)) · \(Fmt.clock(position.exitMs, now: now))" : "\(Fmt.stage(position.stage)) · 入场 \(Fmt.price(position.entry)) · 止损 \(Fmt.price(position.stop))")
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                if closed {
                    Text(position.pnl.map { ($0 > 0 ? "+" : "") + Fmt.money($0) } ?? "—").font(.subheadline.weight(.semibold)).foregroundStyle(Fmt.tone(position.pnl).color)
                    Text(position.netBp.map { String(format: "%+.0fbp", $0) } ?? "").font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text(position.unrealizedBp.map { String(format: "%+.0fbp", $0) } ?? "—").font(.subheadline.weight(.semibold)).foregroundStyle(Fmt.tone(position.unrealizedBp).color)
                    Text("目标 \(Fmt.price(position.target))").font(.caption2).foregroundStyle(.secondary)
                }
            }.monospacedDigit()
        }
    }
}
