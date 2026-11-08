import SwiftUI

/// 监控 · 模拟盘 (2.7.0, Stitch native-2.7.0 paper-list 59777299…): every account the monitor reports,
/// in workbench order — live- forward accounts as tiles, backtest replicas as rows, and research-only
/// strategies (breadth-ema20) with their booklet. The list is driven by the service, so a new account
/// appears without an app release. Simulation only: nothing here places orders.
struct PaperSection: View {
    @Environment(\.openStrategy) private var openStrategy
    let connection: Connection
    let refresh: Int
    @State private var data: PaperSnapshot?
    @State private var error: String?

    var body: some View {
        let live = data?.accounts.filter { $0.group == "live" } ?? []
        let replica = data?.accounts.filter { $0.group != "live" } ?? []
        let research = data?.research ?? []
        VStack(alignment: .leading, spacing: 8) {
            LoadState(loading: data == nil && error == nil, error: error, stale: data != nil)
            if let data {
                HStack(spacing: 6) {
                    Circle().fill(Color.up).frame(width: 6, height: 6)
                    Text("\(data.accounts.count) 个模拟账户 · 只模拟，不下单 · 已实现净值")
                        .font(.caption).foregroundStyle(Color.muted).lineLimit(1).minimumScaleFactor(0.85)
                }
                .padding(.horizontal, 4)
            }
            if !live.isEmpty {
                SectionHeader(title: "live- 前向模拟", count: live.count)
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                    ForEach(live) { account in AccountTile(account: account, now: data?.nowMs ?? 0) }
                }
            }
            if !replica.isEmpty {
                SectionHeader(title: "回测复刻", count: replica.count)
                VStack(spacing: 0) {
                    ForEach(Array(replica.enumerated()), id: \.element.key) { index, account in
                        Button { Haptics.open(); openStrategy(.account(account)) } label: { ReplicaRow(account: account) }
                            .buttonStyle(RowPressStyle())
                        if index < replica.count - 1 { Divider().padding(.leading, 14) }
                    }
                }
                .glassCard()
                .scrollReveal()
            }
            if !research.isEmpty {
                SectionHeader(title: "研究小册", count: research.count)
                VStack(spacing: 0) {
                    ForEach(research) { item in
                        Button { Haptics.open(); openStrategy(.research(item)) } label: {
                            HStack(spacing: 12) {
                                Image(systemName: "book.closed").font(.system(size: 17, weight: .semibold)).foregroundStyle(Color.accentColor)
                                    .frame(width: 36, height: 36).background(Color.accentColor.opacity(0.1), in: .circle)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.label).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                                    Text("\(item.key) · \(item.title)").font(.caption2).foregroundStyle(Color.muted).lineLimit(2)
                                }
                                Spacer(minLength: 6)
                                Text("看小册").font(.caption.weight(.semibold)).foregroundStyle(Color.accentColor)
                                    .padding(.horizontal, 11).padding(.vertical, 6).background(Color.accentColor.opacity(0.12), in: .capsule)
                            }
                            .padding(.horizontal, 12).padding(.vertical, 10).contentShape(.rect)
                        }
                        .buttonStyle(RowPressStyle())
                    }
                }
                .glassCard()
                .scrollReveal()
            }
        }
        .modifier(Polled(key: "paper-\(refresh)", every: .seconds(10), load: {
            try await API.get("/v1/monitor/paper", query: [URLQueryItem(name: "track", value: "live-breadth")], connection: connection)
        }, value: $data, error: $error))
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

/// Blue "宽度·SOL" tag of the breadth account.
struct BreadthTag: View {
    let account: PaperAccount
    var body: some View {
        Text("宽度·" + Fmt.coin(account.breadthRule?.funded ?? "SOL"))
            .font(.system(size: 10, weight: .bold)).foregroundStyle(.white)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(Color.accentColor, in: .capsule)
    }
}

/// Thin six-day holding bar (monday-reclaim-paper.js ``rp-hold-progress``).
struct HoldBar: View {
    let fillMs: Double?
    let dueMs: Double?
    let now: Double
    var height: CGFloat = 4
    var fraction: Double {
        guard let fillMs, let dueMs, dueMs > fillMs else { return 0 }
        return min(1, max(0, (now - fillMs) / (dueMs - fillMs)))
    }
    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.08))
                Capsule().fill(Color.accentColor).frame(width: max(height, proxy.size.width * fraction))
            }
        }
        .frame(height: height)
        .accessibilityLabel("持有进度 \(Int(fraction * 100))%")
    }
}

struct AccountTile: View {
    @Environment(\.openStrategy) private var openStrategy
    let account: PaperAccount
    let now: Double
    var body: some View {
        Button { Haptics.open(); openStrategy(.account(account)) } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 5) {
                    Text(account.label).font(.system(.caption, design: .monospaced).weight(.semibold)).foregroundStyle(Color.ink)
                        .lineLimit(1).minimumScaleFactor(0.7)
                    Spacer(minLength: 2)
                    if account.isBreadth { BreadthTag(account: account) }
                    else { Circle().fill(Fmt.tone(account.pnl).color).frame(width: 6, height: 6) }
                }
                Text(account.subtitle.isEmpty ? " " : account.subtitle).font(.caption2).foregroundStyle(Color.muted).lineLimit(1)
                if account.isBreadth { breadthState } else { fibState }
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text(Fmt.money(account.equity)).font(.scaled(size: 21, weight: .semibold, design: .rounded)).monospacedDigit()
                        .foregroundStyle(Color.ink).lineLimit(1).minimumScaleFactor(0.7)
                        .contentTransition(.numericText(value: account.equity ?? 0))
                    Text("U").font(.caption2).foregroundStyle(Color.muted)
                    Spacer(minLength: 2)
                    Text(Fmt.signedPercent(account.returnPct)).font(.caption2.weight(.bold)).monospacedDigit()
                        .foregroundStyle(Fmt.tone(account.returnPct).color).lineLimit(1)
                }
                .padding(.top, 2)
                Text(foot).font(.caption2).foregroundStyle(Color.muted).monospacedDigit().lineLimit(1)
            }
            .padding(12).frame(maxWidth: .infinity, minHeight: 128, alignment: .topLeading)
            .contentShape(.rect)
        }
        .buttonStyle(CardPressStyle())
        .glassCard(cornerRadius: 20)
        .scrollReveal()
        .accessibilityLabel("\(account.label)，净值 \(Fmt.money(account.equity)) U，\(Fmt.signedPercent(account.returnPct))")
    }

    @ViewBuilder private var breadthState: some View {
        if let hold = account.hold, let fill = hold.fillMs, let due = hold.dueMs {
            let days = account.breadthRule?.holdDays ?? 6
            let day = min(Int(days), max(1, Int(((now - fill) / 86_400_000).rounded(.down)) + 1))
            VStack(alignment: .leading, spacing: 3) {
                Text("持有 \(Fmt.coin(hold.symbol ?? "")) 第\(day)/\(Int(days))天").font(.caption2.weight(.medium)).foregroundStyle(Color.accentColor)
                HoldBar(fillMs: fill, dueMs: due, now: now, height: 3)
            }
        } else {
            Text("空仓 · 等待信号").font(.caption2.weight(.medium)).foregroundStyle(Color.muted)
        }
    }

    private var fibState: some View {
        let open = account.openCount ?? 0
        return Text(open > 0 ? "持仓 \(open) 笔" + (account.stats.meanNetBp.map { String(format: " · 均 %+.0fbp", $0) } ?? "") : "空仓" + (account.stats.meanNetBp.map { String(format: " · 均 %+.0fbp", $0) } ?? ""))
            .font(.caption2.weight(.medium)).foregroundStyle(open > 0 ? Color.accentColor : Color.muted).lineLimit(1)
    }

    private var foot: String {
        let win = account.stats.winPct.map { "胜率 \(Int($0.rounded()))%" } ?? "胜率 —"
        return "\(win) · 已平 \(Int(account.stats.closed ?? 0))"
    }
}

struct ReplicaRow: View {
    let account: PaperAccount
    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(account.label).font(.subheadline.weight(.semibold)).foregroundStyle(Color.ink)
                Text(account.subtitle.isEmpty ? "已平 \(Int(account.stats.closed ?? 0))" : account.subtitle)
                    .font(.caption2).foregroundStyle(Color.muted).lineLimit(1)
            }
            Spacer(minLength: 6)
            Text(Fmt.money(account.equity) + " U").font(.subheadline.weight(.semibold)).monospacedDigit().foregroundStyle(Color.ink)
                .contentTransition(.numericText(value: account.equity ?? 0))
            ChangeCapsule(value: account.returnPct)
            Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 12).frame(minHeight: 52).contentShape(.rect)
    }
}

struct RowPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.background(configuration.isPressed ? Color.primary.opacity(0.07) : .clear)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.8), value: configuration.isPressed)
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

/// What a monitor list opens in the strategy sheet.
enum StrategySheet: Identifiable, Hashable {
    case account(PaperAccount)
    case research(ResearchStrategy)
    var id: String { switch self { case .account(let a): "a:" + a.key; case .research(let r): "r:" + r.key } }
}

struct OpenStrategyAction { var run: (StrategySheet) -> Void = { _ in }; func callAsFunction(_ s: StrategySheet) { run(s) } }
private struct OpenStrategyKey: EnvironmentKey { static let defaultValue = OpenStrategyAction() }
extension EnvironmentValues {
    var openStrategy: OpenStrategyAction { get { self[OpenStrategyKey.self] } set { self[OpenStrategyKey.self] = newValue } }
}
