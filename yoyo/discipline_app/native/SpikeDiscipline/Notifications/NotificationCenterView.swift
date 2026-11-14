import SwiftUI

/// Read-only inbox. Market search is an adjacent destination and never precedes the first alert.
struct NotificationCenterView: View {
    @Environment(NotificationStore.self) private var notices
    @Environment(\.dismiss) private var dismiss
    let onOpen: (NotificationEvent) -> Void
    let onOpenMarket: (String) -> Void
    @State private var market = false
    @State private var filter = "all"
    @State private var showUnpushed = false
    @State private var marketSearch = ""
    private let categories = [("all", "全部"), ("alarm", "报警"), ("fib", "周一"), ("month", "月首周")]

    private var rows: [NotificationEvent] {
        (notices.data?.events ?? []).uniqued { $0.id }.filter {
            (filter == "all" || (filter == "month" ? $0.isMonthlyNotice : $0.kind == filter))
                && (showUnpushed || $0.pushed || $0.isLiveMonthTouch || $0.isLiveReclaim || $0.kind == "alarm")
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 20) {
                pageTab("通知", isMarket: false)
                pageTab("市场", isMarket: true)
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Color.ink).frame(width: 32, height: 32).background(Color.field, in: Circle())
                        .frame(width: 44, height: 44).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityLabel("关闭通知中心")
            }
            .padding(.leading, 20).padding(.trailing, 12).padding(.top, 17)
            if market {
                MarketWatchlist(search: $marketSearch, layout: .full, selectedSymbol: nil) { symbol in
                    Haptics.open(); dismiss(); onOpenMarket(symbol)
                }
            } else {
                filters
                Divider()
                feed
            }
        }
        .background(Color(uiColor: .systemBackground))
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Color(uiColor: .systemBackground))
    }

    private func pageTab(_ title: String, isMarket: Bool) -> some View {
        let selected = market == isMarket
        return Button {
            guard !selected else { return }
            Haptics.select(); market = isMarket
        } label: {
            Text(title).font(.scaled(size: 24, weight: .bold))
                .foregroundStyle(selected ? Color.ink : Color.muted)
                .frame(minHeight: 46).contentShape(Rectangle())
                .overlay(alignment: .bottom) {
                    if selected { Capsule().fill(Color.ink).frame(width: 20, height: 3) }
                }
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var filters: some View {
        HStack(spacing: 4) {
            ViewThatFits(in: .horizontal) {
                categoryButtons
                ScrollView(.horizontal) { categoryButtons }.scrollIndicators(.hidden)
            }
            Spacer(minLength: 2)
            Menu {
                Toggle("显示未推送的触价", isOn: $showUnpushed)
            } label: {
                Image(systemName: showUnpushed ? "line.3.horizontal.decrease.circle.fill" : "slider.horizontal.3")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(showUnpushed ? Color.accentColor : Color.muted)
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("通知显示选项")
            .accessibilityValue(showUnpushed ? "包含未推送触价" : "默认")
        }
        .padding(.leading, 18).padding(.trailing, 12).padding(.top, 5).padding(.bottom, 2)
    }

    private var categoryButtons: some View {
        HStack(spacing: 6) {
            ForEach(categories, id: \.0) { option in
                let selected = filter == option.0
                Button {
                    guard !selected else { return }
                    Haptics.select(); filter = option.0
                } label: {
                    Text(option.1).font(.scaled(size: 13, weight: .semibold))
                        .foregroundStyle(selected ? Color(uiColor: .systemBackground) : Color.ink)
                        .fixedSize().padding(.horizontal, 12).padding(.vertical, 5)
                        .background(selected ? Color.ink : Color.field, in: Capsule())
                        .frame(minWidth: 44, minHeight: 44).contentShape(Rectangle())
                }
                .buttonStyle(.plain).accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
    }

    @ViewBuilder private var feed: some View {
        let items = rows
        if notices.data == nil && notices.error == nil {
            ProgressView("正在读取通知").frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if notices.error != nil {
                        Label("通知暂未更新", systemImage: "exclamationmark.triangle")
                            .font(.footnote).foregroundStyle(Color.amber).padding(.vertical, 8)
                    }
                    ForEach(Fmt.groupByDay(items, now: notices.data?.nowMs ?? 0) { $0.atMs }, id: \.label) { group in
                        Text(group.label).font(.scaled(size: 13, weight: .semibold)).foregroundStyle(Color.muted)
                            .padding(.top, 14).padding(.bottom, 4).accessibilityAddTraits(.isHeader)
                        ForEach(group.rows) { event in
                            Button { Haptics.open(); dismiss(); onOpen(event) } label: { NotificationRow(event: event) }
                                .buttonStyle(.plain)
                            Divider().padding(.leading, 44)
                        }
                    }
                    if items.isEmpty && notices.data != nil {
                        ContentUnavailableView("暂无" + (filter == "all" ? "通知" : (categories.first { $0.0 == filter }?.1 ?? "") + "通知"),
                                               systemImage: "bell")
                            .frame(maxWidth: .infinity).padding(.top, 44)
                    }
                }
                .padding(.horizontal, 20).padding(.bottom, 20)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .id(filter)
        }
    }
}

private struct NotificationRow: View {
    let event: NotificationEvent
    private var alarm: Bool { event.kind == "alarm" }
    private var tint: Color { alarm ? .amber : event.isMonthlyNotice ? MonthTouchStyle.tint : .blue }
    private var title: String {
        if alarm { return (event.test == true ? "测试报警" : "全市场插针") + " · " + (event.n.map { "\($0) 币" } ?? Fmt.edge(event.side)) }
        return Fmt.coin(event.symbol ?? "") + (event.isMonthReclaim ? "  1H 收回" : "  触及") + MonthTouchStyle.edge(event.side)
    }
    private var detail: String {
        if alarm {
            return [event.side == .upper ? "急涨" : "急跌", event.active == true ? "进行中" : event.acknowledged == true ? "已确认" : nil]
                .compactMap { $0 }.joined(separator: " · ")
        }
        let origin = event.isMonthlyNotice ? "月首周 1.618" : "周一 1.618"
        let state = event.isMonthReclaim ? event.close.map { "收盘 " + MonthTouchStyle.price($0) } ?? "收盘确认"
                   : event.isMonthlyNotice ? (event.source == "history" ? "历史回补" : event.isLiveMonthTouch ? "实时触及" : "首次触价") : Fmt.pushStatus(event)
        if let time = event.confirmedTouchHour?.closeMs {
            return origin + " · 1H 已收回 " + Fmt.hourMinute(time) + " · " + state
        }
        return origin + " · " + state
    }
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: alarm ? "bell.badge" : event.isMonthReclaim ? "arrow.uturn.backward" : event.isMonthlyNotice ? "calendar" : "point.3.connected.trianglepath.dotted")
                .font(.system(size: 15, weight: .semibold)).foregroundStyle(tint)
                .frame(width: 32, height: 32).background(tint.opacity(0.09), in: Circle()).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.scaled(size: 16, weight: .semibold)).foregroundStyle(Color.ink)
                Text(detail).font(.scaled(size: 12)).foregroundStyle(Color.muted)
            }.layoutPriority(1)
            Spacer(minLength: 3)
            Text(Fmt.hourMinute(event.atMs)).font(.scaled(size: 13, weight: .medium).monospacedDigit()).foregroundStyle(Color.muted)
                .fixedSize()
        }
        .padding(.vertical, 6).frame(minHeight: 58).contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
