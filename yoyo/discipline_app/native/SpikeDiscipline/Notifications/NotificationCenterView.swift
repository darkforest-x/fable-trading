import SwiftUI

/// 通知 sheet: market alarms and this week's 1.618 touches with their push outcome. Read-only.
struct NotificationCenterView: View {
    @Environment(NotificationStore.self) private var notices
    @Environment(\.dismiss) private var dismiss
    let onOpen: (NotificationEvent) -> Void
    @State private var filter = "all"
    @State private var showUnpushed = false

    private var rows: [NotificationEvent] {
        (notices.data?.events ?? []).filter { (filter == "all" || $0.kind == filter) && (showUnpushed || $0.pushed || $0.kind == "alarm") }
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("类型", selection: $filter.animation(.snappy)) {
                        Text("全部").tag("all"); Text("报警").tag("alarm"); Text("1.618").tag("fib")
                    }.pickerStyle(.segmented).listRowBackground(Color.clear).listRowInsets(.init())
                    Toggle("显示未推送的触价", isOn: $showUnpushed.animation()).font(.subheadline)
                }
                if let error = notices.error { Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(Color.amber) } }
                ForEach(Fmt.groupByDay(rows, now: notices.data?.nowMs ?? 0) { $0.atMs }, id: \.label) { group in
                    Section(group.label) {
                        ForEach(group.rows) { e in
                            Button { onOpen(e); dismiss() } label: { NotificationRow(event: e, now: notices.data?.nowMs ?? 0) }
                                .buttonStyle(.plain)
                        }
                    }
                }
                if notices.data != nil && rows.isEmpty { Section { Text("暂无通知").foregroundStyle(.secondary) } }
            }
            .scrollContentBackground(.hidden)
            .navigationTitle("通知")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
        .presentationBackground(.thinMaterial)
    }
}

struct NotificationRow: View {
    let event: NotificationEvent
    let now: Double
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: event.kind == "alarm" ? "bell.fill" : "chart.line.uptrend.xyaxis")
                .foregroundStyle(event.kind == "alarm" ? Color.down : Color.blue)
                .frame(width: 34, height: 34)
                .background((event.kind == "alarm" ? Color.down : Color.blue).opacity(0.12), in: .circle)
            VStack(alignment: .leading, spacing: 2) {
                Text(event.kind == "alarm" ? "全市场插针 · \(Fmt.edge(event.side)) · \(event.n ?? 0) 币" : "\(Fmt.coin(event.symbol ?? "")) · \(Fmt.edge(event.side))")
                    .font(.subheadline.weight(.semibold)).lineLimit(1)
                Text(Fmt.clock(event.atMs, now: now) + (event.kind == "alarm" && event.active == true ? " · 进行中" : ""))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Pill(text: (event.pushed && event.kind == "fib" ? "Bark " : "") + Fmt.pushStatus(event), color: event.pushed ? .up : .secondary)
        }
        .contentShape(.rect)
    }
}
