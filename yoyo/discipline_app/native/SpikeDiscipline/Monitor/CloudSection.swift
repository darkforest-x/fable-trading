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
        VStack(alignment: .leading, spacing: 12) {
            StatusNote(text: "12H EMA20/50 云带 · 只供观察，不作开单依据", systemImage: "eye", tint: .amber)
            HStack {
                Picker("方向", selection: $side.animation(.snappy)) { Text("全部").tag("all"); Text("做多").tag("long"); Text("做空").tag("short") }
                    .pickerStyle(.segmented).frame(maxWidth: 220)
                Spacer()
                Menu {
                    Picker("范围", selection: $scope) { Text("本周").tag("live"); Text("含历史").tag("all") }
                } label: { Label(scope == "live" ? "本周" : "含历史", systemImage: "line.3.horizontal.decrease").font(.footnote.weight(.semibold)) }
                .buttonStyle(.glass)
            }
            if let data {
                Text("下次 12H 收盘 \(Fmt.clock(data.nextCloseMs, now: data.nowMs)) · 已扫描 \(data.status.completed ?? 0)/\(data.status.total ?? 0)\(data.status.workerAlive ? "" : " · 扫描未运行")")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit().padding(.horizontal, 4)
            }
            LoadState(loading: data == nil && error == nil, error: error, stale: data != nil)
            if let data, data.items.isEmpty {
                Text(scope == "live" ? "本周暂无云带信号" : "暂无信号").font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(18).glassCard()
            }
            ForEach(data?.items ?? []) { s in CloudCard(signal: s, now: data?.nowMs ?? 0) }
            Text("云带评分 2026-11-05 已在全部周期被否决，这里仅作盘面观察。").font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(.top, 4)
        }
        .modifier(Polled(key: "cloud-\(side)-\(scope)-\(refresh)", every: .seconds(60), load: { try await API.get("/v1/monitor/cloud", query: [URLQueryItem(name: "side", value: side), URLQueryItem(name: "scope", value: scope)], connection: connection) }, value: $data, error: $error))
    }
}

struct CloudCard: View {
    let signal: CloudSignal
    let now: Double
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                CoinLogo(coin: Fmt.coin(signal.symbol))
                HStack(alignment: .firstTextBaseline, spacing: 3) { Text(Fmt.coin(signal.symbol)).font(.headline); Text("/ USDT").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                SidePill(side: signal.side)
                Pill(text: signal.timeframe)
                Pill(text: signal.historical ? "历史" : "最新", color: signal.historical ? .secondary : .blue)
            }
            HStack {
                figure("入场", Fmt.price(signal.entry), .primary)
                figure("止损", Fmt.price(signal.stop), .down, note: signal.stopPct.map { String(format: "−%.1f%%", abs($0)) })
                figure("目标", Fmt.price(signal.target), .up)
            }
            .padding(12).background(.white.opacity(0.18), in: .rect(cornerRadius: 16))
            HStack {
                Text("R:R \(signal.plannedRr.map { String(format: "%.1f", $0) } ?? "—")")
                Spacer()
                Text("确认 \(Fmt.clock(signal.confirmedMs, now: now))")
            }
            .font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
        .padding(16)
        .glassCard(cornerRadius: 22)
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
