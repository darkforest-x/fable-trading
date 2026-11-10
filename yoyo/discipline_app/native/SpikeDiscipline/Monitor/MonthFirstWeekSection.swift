import SwiftUI

enum MonthTouchStyle {
    static let kind = "month_first_week_touch"
    static let icon = "calendar"
    static let tint = Color.purple

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

/// Month and Monday share navigation. A monthly touch opens its own frozen range.
struct MonthFirstWeekSection: View {
    let connection: Connection
    let refresh: Int
    @Environment(SessionStore.self) private var session
    @State private var feed = MonthFirstWeekFeed()
    @AppStorage("touch.month.focus") private var focus = ""
    @State private var side = "all"
    @State private var selected: MonthFirstWeekTouch?

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
                    ScrollViewReader { reader in
                        LazyVStack(spacing: 0) {
                            ForEach(rows) { event in
                                eventRow(event, now: snapshot.nowMs)
                                    .id(event.eventId)
                                if event.id != rows.last?.id { Divider().padding(.leading, 54) }
                            }
                        }
                        .glassCard(cornerRadius: 18)
                        .task(id: focus) {
                            guard !focus.isEmpty else { return }
                            side = "all"
                            await Task.yield()
                            reader.scrollTo(focus, anchor: .center)
                        }
                    }
                }
            }
        }
        .sheet(item: $selected) { event in
            NavigationStack {
                List {
                    Section {
                        LabeledContent("合约", value: Fmt.coin(event.symbol))
                        LabeledContent("纽约月份", value: event.month)
                        LabeledContent(MonthTouchStyle.edge(event.side) + " 1.618", value: MonthTouchStyle.price(event.level))
                        LabeledContent(event.source == "history" ? "回补 K 线起点" : "实时首次观测",
                                       value: Fmt.clock(event.source == "history" ? event.candleMs : event.occurredMs) + " 北京时间")
                    }
                    if let item = feed.snapshot?.items.first(where: { $0.symbol == event.symbol }),
                       let high = item.high, let low = item.low {
                        Section("首个完整周") {
                            LabeledContent("首周高", value: MonthTouchStyle.price(high))
                            LabeledContent("首周低", value: MonthTouchStyle.price(low))
                            if let upper = item.upper { LabeledContent("上轨 1.618", value: MonthTouchStyle.price(upper)) }
                            if let lower = item.lower { LabeledContent("下轨 1.618", value: MonthTouchStyle.price(lower)) }
                        }
                    }
                }
                .navigationTitle("月首周触价").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .topBarTrailing) { CloseButton { selected = nil } } }
            }
            .presentationDetents([.medium, .large])
        }
        .task(id: refresh) { await feed.watch(connection: connection, session: session) }
    }

    private func eventRow(_ event: MonthFirstWeekTouch, now: Double) -> some View {
        Button { Haptics.open(); selected = event } label: {
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
            .background(event.eventId == focus ? MonthTouchStyle.tint.opacity(0.11) : Color.clear)
            .contentShape(.rect)
        }
        .buttonStyle(RowPressStyle())
    }
}
