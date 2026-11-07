import SwiftUI

/// 12H EMA20/50 云带 — observation only; the score was rejected on every timeframe (2026-11-05).
struct CloudSection: View {
    let connection: Connection
    let refresh: Int
    @State private var data: CloudSnapshot?
    @State private var error: String?
    @State private var side = "all"
    @State private var scope = "live"

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                SoftSegment(selection: $side, options: [("all", "全部"), ("long", "做多"), ("short", "做空")], height: 34).frame(maxWidth: 220)
                Spacer()
                Menu {
                    Picker("范围", selection: $scope) { Text("本周").tag("live"); Text("含历史").tag("all") }
                        .onChange(of: scope) { _, _ in Haptics.select() }
                } label: { Label(scope == "live" ? "本周" : "含历史", systemImage: "line.3.horizontal.decrease").font(.footnote.weight(.semibold)) }
                .buttonStyle(.pill(.soft, height: 32))
            }
            if let data, !data.status.workerAlive {
                StatusNote(text: "云带扫描未运行", systemImage: "exclamationmark.triangle", tint: .amber)
            }
            LoadState(loading: data == nil && error == nil, error: error, stale: data != nil)
            if let data, data.items.isEmpty {
                Text(scope == "live" ? "本周暂无云带信号" : "暂无信号").font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(18).glassCard()
            }
            ForEach(Array((data?.items ?? []).enumerated()), id: \.offset) { _, s in CloudCard(signal: s, now: data?.nowMs ?? 0) }
        }
        .modifier(Polled(key: "cloud-\(side)-\(scope)-\(refresh)", every: .seconds(60), load: { try await API.get("/v1/monitor/cloud", query: [URLQueryItem(name: "side", value: side), URLQueryItem(name: "scope", value: scope)], connection: connection) }, value: $data, error: $error))
    }
}

struct CloudCard: View {
    let signal: CloudSignal
    let now: Double
    @Environment(\.openChart) private var openChart
    var body: some View {
        Button { Haptics.open(); openChart(signal.symbol) } label: { card }.buttonStyle(CardPressStyle())
    }
    private var card: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                CoinLogo(coin: Fmt.coin(signal.symbol))
                Text(Fmt.coin(signal.symbol)).font(.headline).foregroundStyle(.primary)
                SidePill(side: signal.side)
                if !signal.historical { Circle().fill(Color.blue).frame(width: 7, height: 7).accessibilityLabel("最新") }
                Spacer()
                Text(Fmt.clock(signal.confirmedMs, now: now)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
            }
            HStack {
                figure("入场", Fmt.price(signal.entry), .primary)
                figure("止损", Fmt.price(signal.stop), .down, note: signal.stopPct.map { String(format: "−%.1f%%", abs($0)) })
                figure("目标", Fmt.price(signal.target), .up)
                figure("R:R", signal.plannedRr.map { String(format: "%.1f", $0) } ?? "—", .primary)
            }
        }
        .padding(12)
        .glassCard(cornerRadius: 18)
        .scrollReveal()
    }
    private func figure(_ label: String, _ value: String, _ color: Color, note: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.subheadline.weight(.semibold)).foregroundStyle(color).monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
            if let note { Text(note).font(.caption2).foregroundStyle(Color.down) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
