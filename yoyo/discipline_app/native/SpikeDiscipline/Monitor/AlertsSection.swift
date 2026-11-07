import SwiftUI

/// 全市场插针报警 (Stitch monitor-alerts 7e2f6b91…). Test alarms are filtered server-side.
struct AlertsSection: View {
    let connection: Connection
    let refresh: Int
    @State private var data: AlertSnapshot?
    @State private var error: String?
    @State private var expanded: Double?

    var body: some View {
        let live = data?.alerts.filter(\.active) ?? []
        let history = data?.alerts.filter { !$0.active } ?? []
        VStack(alignment: .leading, spacing: 12) {
            if let data {
                StatusNote(text: data.workerAlive ? "报警服务运行中 · \(data.windowMinutes ?? 5) 分钟内 ≥\(data.threshold ?? 25) 币触及 1.618" : "报警服务未运行",
                           systemImage: data.workerAlive ? "dot.radiowaves.left.and.right" : "exclamationmark.triangle",
                           tint: data.workerAlive ? .up : .amber)
            }
            LoadState(loading: data == nil && error == nil, error: error, stale: data != nil)
            ForEach(live, id: \.rowId) { alarm in AlarmHero(alarm: alarm, windowMinutes: data?.windowMinutes ?? 5, now: data?.nowMs ?? 0) }
            if data != nil && live.isEmpty {
                Label("当前没有进行中的报警", systemImage: "checkmark.circle").font(.subheadline).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity).padding(18).glassCard()
            }
            if !history.isEmpty {
                SectionHeader(title: "历史报警", count: history.count)
                ForEach(Fmt.groupByDay(history, now: data?.nowMs ?? 0) { $0.firstMs }, id: \.label) { group in
                    Text(group.label).font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.horizontal, 6).padding(.top, 4)
                    ForEach(group.rows, id: \.rowId) { alarm in
                        VStack(alignment: .leading, spacing: 10) {
                            Button { Haptics.tap(); withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) { expanded = expanded == alarm.rowId ? nil : alarm.rowId } } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: alarm.side == .lower ? "arrow.down" : "arrow.up").font(.headline)
                                        .foregroundStyle(alarm.side == .lower ? Color.down : Color.up)
                                        .frame(width: 40, height: 40).background((alarm.side == .lower ? Color.down : Color.up).opacity(0.12), in: .circle)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text("\(Fmt.edge(alarm.side)) · \(alarm.n) 币").font(.subheadline.weight(.semibold))
                                        Text(range(alarm)).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                                    }
                                    Spacer()
                                    Pill(text: alarm.acknowledged ? "已确认" : "未确认", color: alarm.acknowledged ? .secondary : .blue)
                                    Image(systemName: "chevron.down").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                                        .rotationEffect(.degrees(expanded == alarm.rowId ? 180 : 0))
                                }
                                .contentShape(.rect)
                            }
                            .buttonStyle(.plain)
                            if expanded == alarm.rowId { CoinChips(symbols: alarm.symbols).transition(.opacity.combined(with: .scale(scale: 0.98, anchor: .top))) }
                        }
                        .padding(14)
                        .glassCard(cornerRadius: 22)
                        .scrollReveal()
                    }
                }
            }
            Text("只读显示。确认报警、测试推送与通知设置在监控工作台操作。").font(.caption).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity).padding(.top, 6)
        }
        .modifier(Polled(key: "alerts-\(refresh)", every: .seconds(8), load: { try await API.get("/v1/monitor/alerts", connection: connection) }, value: $data, error: $error))
    }

    private func range(_ a: Alarm) -> String {
        "\(Fmt.clock(a.firstMs, now: data?.nowMs ?? 0)) – \(Fmt.clock(a.lastMs, now: data?.nowMs ?? 0).split(separator: " ").last.map(String.init) ?? "")"
    }
}

struct AlarmHero: View {
    let alarm: Alarm
    let windowMinutes: Int
    let now: Double
    @State private var pulse = false
    var body: some View {
        let tint: Color = alarm.side == .lower ? .down : .up
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                HStack(spacing: 7) {
                    Circle().fill(tint).frame(width: 9, height: 9).scaleEffect(pulse ? 1.35 : 1).opacity(pulse ? 0.6 : 1)
                    Text("进行中").font(.subheadline.weight(.semibold)).foregroundStyle(tint)
                }
                Spacer()
                Text("\(Fmt.clock(alarm.firstMs, now: now)) – \(Fmt.clock(alarm.lastMs, now: now))").font(.caption.weight(.semibold)).monospacedDigit()
                    .padding(.horizontal, 10).padding(.vertical, 4).frostedCapsule()
            }
            Label("全市场插针 · \(Fmt.edge(alarm.side))", systemImage: alarm.side == .lower ? "arrow.down.circle.fill" : "arrow.up.circle.fill")
                .font(.title3.weight(.bold)).fontDesign(.rounded).labelStyle(.titleAndIcon)
                .symbolEffect(.pulse, options: .repeating)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("\(alarm.n)").font(.system(size: 52, weight: .bold, design: .rounded)).monospacedDigit().contentTransition(.numericText())
                Text("个币 \(windowMinutes) 分钟内触及").font(.subheadline).foregroundStyle(.secondary)
            }
            CoinChips(symbols: alarm.symbols)
        }
        .padding(18)
        .glassCard(cornerRadius: 26, tint: tint)
        .onAppear { withAnimation(.easeInOut(duration: 0.9).repeatForever()) { pulse = true } }
    }
}

struct CoinChips: View {
    let symbols: [String]
    @State private var all = false
    var body: some View {
        FlowLayout {
            ForEach(all ? symbols : Array(symbols.prefix(8)), id: \.self) { s in
                HStack(spacing: 5) { CoinLogo(coin: Fmt.coin(s), size: 18); Text(Fmt.coin(s)).font(.footnote.weight(.semibold)) }
                    .padding(.leading, 5).padding(.trailing, 10).padding(.vertical, 5)
                    .frostedCapsule()
            }
            if !all && symbols.count > 8 {
                Button("+\(symbols.count - 8)") { Haptics.tap(); withAnimation(.snappy) { all = true } }
                    .font(.footnote.weight(.semibold)).buttonStyle(.glass).controlSize(.small)
            }
        }
    }
}
