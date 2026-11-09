import SwiftUI

/// 全市场插针报警 (Stitch monitor-alerts 7e2f6b91…). Test alarms are filtered server-side.
struct AlertsSection: View {
    let connection: Connection
    let refresh: Int
    @State private var data: AlertSnapshot?
    @State private var error: String?
    @State private var expanded: Double?

    var body: some View {
        let all = (data?.alerts ?? []).uniqued { $0.rowId }
        let live = all.filter(\.active)
        let history = all.filter { !$0.active }
        VStack(alignment: .leading, spacing: 8) {
            if let data, !data.workerAlive {
                StatusNote(text: "报警服务未运行", systemImage: "exclamationmark.triangle", tint: .amber)
            }
            LoadState(loading: data == nil && error == nil, error: error, stale: data != nil)
            ForEach(live, id: \.rowId) { alarm in AlarmHero(alarm: alarm, windowMinutes: data?.windowMinutes ?? 5, now: data?.nowMs ?? 0) }
            if data != nil && live.isEmpty {
                Label("当前没有进行中的报警", systemImage: "checkmark.circle").font(.subheadline).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity).padding(12).glassCard(cornerRadius: 18)
            }
            if !history.isEmpty {
                SectionHeader(title: "历史报警", count: history.count)
                ForEach(Fmt.groupByDay(history, now: data?.nowMs ?? 0) { $0.firstMs }, id: \.label) { group in
                    Text(group.label).font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.horizontal, 6).padding(.top, 4)
                    ForEach(group.rows, id: \.rowId) { alarm in
                        VStack(alignment: .leading, spacing: 10) {
                            Button { Haptics.tap(); withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) { expanded = expanded == alarm.rowId ? nil : alarm.rowId } } label: {
                                HStack(spacing: 12) {
                                    // Owner 2026-11-09 (“上下箭头的样式好丑”, Stitch waived this time): the complete coin
                                    // count in the side color replaces the pastel arrow circle.
                                    let tint: Color = alarm.side == .lower ? .down : .up
                                    VStack(spacing: -1) {
                                        Text("\(alarm.n)").font(.system(size: 22, weight: .bold, design: .rounded)).monospacedDigit()
                                            .foregroundStyle(tint).contentTransition(.numericText()).minimumScaleFactor(0.7).lineLimit(1)
                                        Text("个币").font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                                    }
                                    .frame(width: 46)
                                    VStack(alignment: .leading, spacing: 2) {
                                        (Text(alarm.side == .lower ? "急跌" : "急涨").foregroundStyle(tint) + Text(" · \(Fmt.edge(alarm.side))"))
                                            .font(.subheadline.weight(.semibold))
                                        Text(range(alarm)).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                                    }
                                    .accessibilityElement(children: .ignore)
                                    .accessibilityLabel("\(alarm.side == .lower ? "急跌" : "急涨") \(Fmt.edge(alarm.side))，\(alarm.n) 个币，\(range(alarm))")
                                    Spacer()
                                    if !alarm.acknowledged { Circle().fill(Color.blue).frame(width: 7, height: 7).accessibilityLabel("未确认") }
                                    Image(systemName: "chevron.down").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                                        .rotationEffect(.degrees(expanded == alarm.rowId ? 180 : 0))
                                }
                                .contentShape(.rect)
                            }
                            .buttonStyle(.plain)
                            if expanded == alarm.rowId { CoinChips(symbols: alarm.symbols).transition(.opacity.combined(with: .scale(scale: 0.98, anchor: .top))) }
                        }
                        .padding(.horizontal, 12).padding(.vertical, 9)
                        .glassCard(cornerRadius: 18)
                        .scrollReveal()
                    }
                }
            }
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
            HStack(spacing: 8) {
                Circle().fill(tint).frame(width: 9, height: 9).scaleEffect(pulse ? 1.35 : 1).opacity(pulse ? 0.6 : 1)
                Text("\(Fmt.edge(alarm.side)) · \(alarm.n) 币").font(.title3.weight(.bold)).fontDesign(.rounded)
                    .contentTransition(.numericText())
                Spacer()
                Text("\(Fmt.clock(alarm.firstMs, now: now)) – \(Fmt.clock(alarm.lastMs, now: now).split(separator: " ").last.map(String.init) ?? "")")
                    .font(.caption.weight(.semibold)).monospacedDigit().foregroundStyle(tint)
                    .padding(.horizontal, 9).padding(.vertical, 4).background(tint.opacity(0.10), in: .capsule)
            }
            CoinChips(symbols: alarm.symbols)
        }
        .padding(12)
        .glassCard(cornerRadius: 20, tint: tint)
        .onAppear { withAnimation(.easeInOut(duration: 0.9).repeatForever()) { pulse = true } }
    }
}

/// Alarm coins; each chip opens that coin's chart. Owner 2026-11-09 (“有多少显示多少”): every coin of the
/// complete alarm is shown — no 12-coin fold.
struct CoinChips: View {
    let symbols: [String]
    @Environment(\.openChart) private var openChart
    var body: some View {
        FlowLayout {
            ForEach(symbols, id: \.self) { s in
                Button { Haptics.open(); openChart(s) } label: {
                    HStack(spacing: 5) { CoinLogo(coin: Fmt.coin(s), size: 18); Text(Fmt.coin(s)).font(.footnote.weight(.semibold)).foregroundStyle(.primary) }
                        .padding(.leading, 4).padding(.trailing, 9).padding(.vertical, 4)
                        .background(Color.white.opacity(0.9), in: .capsule)
                        .overlay(Capsule().strokeBorder(Color.black.opacity(0.05)))
                }
                .buttonStyle(CardPressStyle())
                .accessibilityLabel("查看 \(Fmt.coin(s)) K线")
            }
        }
    }
}
