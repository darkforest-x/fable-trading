import SwiftUI

/// 通知 sheet: market alarms and this week's 1.618 touches with their push outcome. Read-only.
struct NotificationCenterView: View {
    @Environment(NotificationStore.self) private var notices
    @Environment(\.dismiss) private var dismiss
    let onOpen: (NotificationEvent) -> Void
    @State private var filter = "all"
    @State private var showUnpushed = false

    private var rows: [NotificationEvent] {
        (notices.data?.events ?? []).uniqued { $0.id }.filter { (filter == "all" || $0.kind == filter) && (showUnpushed || $0.pushed || $0.isLiveMonthTouch || $0.kind == "alarm") }
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    SoftSegment(selection: $filter, options: [("all", "全部"), ("alarm", "报警"), ("fib", "周一"), (MonthTouchStyle.kind, "月首周")]).listRowBackground(Color.clear).listRowInsets(.init())
                    Toggle("显示未推送的触价", isOn: $showUnpushed.animation()).font(.subheadline)
                }
                if let error = notices.error { Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(Color.amber) } }
                ForEach(Fmt.groupByDay(rows, now: notices.data?.nowMs ?? 0) { $0.atMs }, id: \.label) { group in
                    Section(group.label) {
                        ForEach(group.rows) { e in
                            Button { Haptics.open(); onOpen(e); dismiss() } label: { NotificationRow(event: e, now: notices.data?.nowMs ?? 0) }
                                .buttonStyle(.plain)
                        }
                    }
                }
                if notices.data != nil && rows.isEmpty { Section { Text("暂无通知").foregroundStyle(.secondary) } }
            }
            .scrollContentBackground(.hidden)
            .navigationTitle("通知")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }.sharedBackgroundVisibility(.hidden) }
        }
        .presentationDetents([.medium, .large])
        .presentationBackground(.thinMaterial)
    }
}

struct NotificationRow: View {
    let event: NotificationEvent
    let now: Double
    private var monthly: Bool { event.kind == MonthTouchStyle.kind }
    private var tint: Color { monthly ? MonthTouchStyle.tint : event.kind == "alarm" ? .down : .blue }
    private var title: String {
        if event.kind == "alarm" { return "全市场插针 · \(Fmt.edge(event.side)) · \(event.n ?? 0) 币" }
        return Fmt.coin(event.symbol ?? "") + " · " + MonthTouchStyle.edge(event.side)
    }
    private var source: String {
        if monthly { return "月首周 1.618 · " }
        return event.kind == "fib" ? "周一 1.618 · " : ""
    }
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: monthly ? MonthTouchStyle.icon : event.kind == "alarm" ? "bell.fill" : "chart.line.uptrend.xyaxis")
                .foregroundStyle(tint)
                .frame(width: 34, height: 34)
                .background(tint.opacity(0.12), in: .circle)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold)).lineLimit(1)
                Text(source + Fmt.clock(event.atMs, now: now) + (event.kind == "alarm" && event.active == true ? " · 进行中" : ""))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if monthly {
                Pill(text: event.source == "history" ? "历史回补" : "实时触及", color: MonthTouchStyle.tint)
            } else {
                Pill(text: (event.pushed && event.kind == "fib" ? "Bark " : "") + Fmt.pushStatus(event), color: event.pushed ? .up : .secondary)
            }
        }
        .contentShape(.rect)
    }
}
