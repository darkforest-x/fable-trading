import SwiftUI

/// 通知 sheet: market alarms and this week's 1.618 touches with their push outcome. Read-only.
struct NotificationCenterView: View {
    @Environment(NotificationStore.self) private var notices
    @Environment(\.dismiss) private var dismiss
    let onOpen: (NotificationEvent) -> Void
    let onOpenMarket: (String) -> Void
    @State private var page: NotificationCenterPage = .notifications
    @State private var detent: PresentationDetent = .medium
    @State private var filter = "all"
    @State private var showUnpushed = false
    @State private var marketSearch = ""

    private enum NotificationCenterPage: Hashable {
        case notifications, market
    }

    private var rows: [NotificationEvent] {
        (notices.data?.events ?? []).uniqued { $0.id }.filter { (filter == "all" || $0.kind == filter) && (showUnpushed || $0.pushed || $0.isLiveMonthTouch || $0.kind == "alarm") }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                centerTabs
                if page == .notifications {
                    notificationFeed
                } else {
                    MarketWatchlist(search: $marketSearch, layout: .full, selectedSymbol: nil, onSelect: openMarket)
                }
            }
            .background(Color(uiColor: .systemBackground))
            .navigationTitle("通知中心")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    HStack(spacing: 5) {
                        Text("通知中心").font(.headline)
                        if notices.unread > 0 { Circle().fill(Color.accentColor).frame(width: 7, height: 7) }
                    }
                    .accessibilityElement(children: .combine)
                }
                ToolbarItem(placement: .topBarTrailing) { CloseButton { dismiss() } }
            }
        }
        .presentationDetents([.medium, .large], selection: $detent)
        .onChange(of: page) { _, destination in
            if destination == .market { detent = .large }
        }
        .presentationBackground(Color(uiColor: .systemBackground))
    }

    private var centerTabs: some View {
        HStack(spacing: 2) {
            tabButton(.notifications, title: notices.unread > 0 ? "通知（\(notices.unread) 未读）" : "通知")
            tabButton(.market, title: "市场")
        }
        .padding(3)
        .background(Color.field, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 4)
        .background(Color(uiColor: .systemBackground))
    }

    private func tabButton(_ target: NotificationCenterPage, title: String) -> some View {
        let selected = page == target
        return Button {
            guard !selected else { return }
            Haptics.select()
            withAnimation(.snappy) { page = target }
        } label: {
            Text(title)
                .font(.scaled(size: 13, weight: selected ? .semibold : .medium))
                .foregroundStyle(selected ? Color.ink : Color.muted)
                .frame(maxWidth: .infinity)
                .frame(height: 32)
                .background {
                    if selected {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color(uiColor: .systemBackground))
                            .shadow(color: .black.opacity(0.08), radius: 4, y: 1)
                    }
                }
                .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var notificationFeed: some View {
        List {
            Section {
                SoftSegment(selection: $filter, options: [("all", "全部"), ("alarm", "报警"), ("fib", "周一"), (MonthTouchStyle.kind, "月首周")], height: 34)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 2, leading: 14, bottom: 2, trailing: 14))
                    .listRowSeparator(.hidden)
                Toggle("显示未推送的触价", isOn: $showUnpushed.animation())
                    .font(.subheadline)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 2, trailing: 16))
                    .listRowSeparator(.hidden)
            }
            Section {
                MarketWatchlist(search: $marketSearch, layout: .compact, selectedSymbol: nil, onSelect: openMarket)
                    .listRowBackground(Color(uiColor: .secondarySystemBackground).opacity(0.55))
                    .listRowInsets(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12))
                    .listRowSeparator(.hidden)
            }
            if let error = notices.error {
                Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(Color.amber) }
            }
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
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Color(uiColor: .systemBackground))
        .environment(\.defaultMinListRowHeight, 1)
        .scrollDismissesKeyboard(.interactively)
    }

    private func openMarket(_ symbol: String) {
        Haptics.open()
        dismiss()
        onOpenMarket(symbol)
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
