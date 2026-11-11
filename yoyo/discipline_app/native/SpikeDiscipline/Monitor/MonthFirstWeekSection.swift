import SwiftUI

enum MonthTouchStyle {
    static let kind = "month_first_week_touch"
    static let icon = "calendar"
    static let tint = ChartPalette.hex(0x8754B8)

    static func edge(_ side: Edge) -> String { side == .upper ? "上轨" : "下轨" }

    static func price(_ value: Double) -> String {
        value.formatted(.number.precision(.significantDigits(1...8)).grouping(.never))
    }

    static func state(_ snapshot: MonthFirstWeekSnapshot) -> String {
        if !snapshot.window.complete { return "等待首周结束" }
        if !snapshot.worker.alive || !snapshot.stream.connected { return "行情连接中断" }
        if snapshot.coverage.readySymbols == 0 { return "正在核对区间" }
        return "监控中"
    }
}

/// Monthly touches open as event replays in the existing 1.618 chart.
struct MonthFirstWeekSection: View {
    let connection: Connection
    let refresh: Int
    @Environment(SessionStore.self) private var session
    @Environment(\.openChart) private var openChart
    @State private var feed = MonthFirstWeekFeed()
    @State private var side = "all"

    private var filtered: [MonthFirstWeekTouch] {
        feed.events.filter { side == "all" || $0.side.rawValue == side }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LoadState(loading: !feed.loaded && feed.error == nil, error: feed.error, stale: feed.loaded)
            if let snapshot = feed.snapshot {
                VStack(alignment: .leading, spacing: 9) {
                    HStack {
                        Label("月首周 1.618", systemImage: MonthTouchStyle.icon)
                            .font(.headline).foregroundStyle(MonthTouchStyle.tint)
                        Spacer()
                        Text(MonthTouchStyle.state(snapshot)).font(.caption.weight(.medium))
                    }
                    HStack {
                        Text("纽约 · " + snapshot.month)
                        Spacer()
                        Text("已就绪 \(snapshot.coverage.readySymbols) / \(snapshot.coverage.universeSize)")
                    }
                    .font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                }
                .padding(14).glassCard(cornerRadius: 18)

                SoftSegment(selection: $side, options: [("all", "全部"), ("upper", "上轨"), ("lower", "下轨")], height: 34)
                SectionHeader(title: "本月首次触价", count: filtered.count, trailing: "最近优先")
                let rows = filtered
                if rows.isEmpty {
                    Text(snapshot.window.complete ? "尚无符合条件的触价" : "首个完整周结束后开始监控")
                        .font(.footnote).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity).padding(16).glassCard(cornerRadius: 18)
                } else {
                    LazyVStack(spacing: 0) {
                        ForEach(rows) { event in
                            eventRow(event, now: snapshot.nowMs)
                                .id(event.eventId)
                            if event.id != rows.last?.id { Divider().padding(.leading, 54) }
                        }
                    }
                    .glassCard(cornerRadius: 18)
                }
            }
        }
        .task(id: refresh) { await feed.watch(connection: connection, session: session) }
    }

    private func eventRow(_ event: MonthFirstWeekTouch, now: Double) -> some View {
        Button {
            Haptics.open()
            openChart.monthlyTouch(event)
        } label: {
            HStack(spacing: 10) {
                CoinLogo(coin: Fmt.coin(event.symbol), size: 32).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(Fmt.coin(event.symbol)).font(.headline)
                    Text(event.source == "history" ? "历史回补" : "实时触及").font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 4)
                VStack(alignment: .trailing, spacing: 4) {
                    Label(MonthTouchStyle.edge(event.side) + "  " + MonthTouchStyle.price(event.level),
                          systemImage: event.side == .upper ? "arrow.up.right" : "arrow.down.right")
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                    Text(Fmt.clock(event.source == "history" ? event.candleMs : event.occurredMs, now: now))
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            .foregroundStyle(Color.ink).padding(.horizontal, 12).padding(.vertical, 9)
            .contentShape(.rect)
        }
        .buttonStyle(RowPressStyle())
    }
}
