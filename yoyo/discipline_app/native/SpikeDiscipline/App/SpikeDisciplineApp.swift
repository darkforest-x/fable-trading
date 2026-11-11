import SwiftUI

/// SPIKE 纪律 2.x — native SwiftUI rewrite (owner 2026-11-07 “原生 SwiftUI 重写”, 2.1.0 “剩下的全部重构”).
/// Every order, close, cancel and protection write goes through AccountStore, which persists request
/// identities before sending and never resends on timeout.
@main
struct SpikeDisciplineApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var session = SessionStore()
    @State private var notices = NotificationStore()
    @State private var account = AccountStore()
    @State private var fib = FibStore()
    @State private var ticket = TicketModel()
    @State private var chartStyle = ChartStyle()
    @State private var mondayHistory = MondayHistory()
    @State private var touchFeed = TouchFeed()
    @AppStorage("appearance") private var appearance = "system"
    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .environment(notices)
                .environment(account)
                .environment(fib)
                .environment(ticket)
                .environment(chartStyle)
                .environment(mondayHistory)
                .environment(touchFeed)
                .preferredColorScheme(appearance == "light" ? .light : appearance == "dark" ? .dark : nil)
                // Text follows the system size up to xxxLarge; larger sizes would break the floating docks.
                .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
        }
    }
}

enum AppTab: String, Hashable, CaseIterable, Identifiable {
    case trade, monitor, fib, positions, stats
    var id: String { rawValue }
    var title: String { switch self { case .trade: "合约"; case .monitor: "监控"; case .fib: "1.618"; case .positions: "持仓"; case .stats: "统计" } }
    /// Minimal SF Symbols (owner 2026-11-07 “简约”): outline when idle, filled when active.
    func symbol(active: Bool) -> String {
        // Stitch native-2.3.0: one stroke weight, no fills — the active tab only changes colour.
        switch self {
        case .trade: "arrow.up.arrow.down"
        case .monitor: "bell"
        case .fib: "chart.line.uptrend.xyaxis"
        case .positions: "checkmark.shield"
        case .stats: "chart.bar"
        }
    }
}

/// Pages swipe left/right and follow the finger, like the 1.x app (owner 2026-11-07
/// “菜单左右支持滑动切换”); the glass tab bar's lens tracks the scroll position.
struct RootView: View {
    @Environment(SessionStore.self) private var session
    @Environment(NotificationStore.self) private var notices
    @Environment(AccountStore.self) private var account
    @Environment(FibStore.self) private var fib
    @Environment(TicketModel.self) private var ticket
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: AppTab = .fib       // owner 2026-11-07: open on 1.618
    @State private var progress: CGFloat = CGFloat(AppTab.allCases.firstIndex(of: .fib)!)
    @State private var showNotifications = false
    @State private var showSettings = false
    @State private var bannerDrag: CGFloat = 0
    /// Landscape is allowed only on the 1.618 / 合约 charts (owner 2026-11-08 item 6).
    @State private var landscape = false
    @State private var reselect: [AppTab: Int] = [:]
    @AppStorage("trade.period") private var tradePeriod: CandlePeriod = .m15
    private var chartTab: Bool { tab == .fib || tab == .trade }
    private var landscapeChart: Bool { landscape && chartTab }

    @State private var scrolled: AppTab? = .fib
    /// Page a tab tap is scrolling to: the pages it passes must not overwrite the selection, and
    /// paging stays enabled until it lands (1.618 disables paging so drags go to the chart).
    @State private var target: AppTab?

    var body: some View {
        // Pure SwiftUI paging (no UIKit page container): pages follow the finger, draw edge to edge,
        // and no UINavigationBar lives inside a swiping container (device crash logs 2026-11-07).
        GeometryReader { proxy in
        ScrollViewReader { reader in
        ScrollView(.horizontal) {
            HStack(spacing: 0) {
                ForEach(AppTab.allCases) { page in
                    content(page)
                        .environment(\.reselect, reselect[page] ?? 0)
                        // Backgrounds run edge to edge; content keeps the real safe area.
                        .safeAreaPadding(.top, proxy.safeAreaInsets.top)
                        .safeAreaPadding(.bottom, proxy.safeAreaInsets.bottom)
                        .containerRelativeFrame(.horizontal)
                        .id(page)
                }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(.paging)
        .scrollIndicators(.hidden)
        .scrollEdgeEffectHidden(true, for: .all)
        .scrollPosition(id: $scrolled)
        // The 1.618 page is a full-screen chart: horizontal drags pan the chart, not the pages. 监控 nests its
        // own menu pager (owner 2026-11-09); UIKit gives it drags first and hands edge drags back here.
        .scrollDisabled(tab == .fib && target == nil)
        .onScrollGeometryChange(for: CGFloat.self) { geo in
            geo.contentOffset.x / max(geo.containerSize.width, 1)
        } action: { _, value in progress = value }
        .onChange(of: scrolled) { _, value in
            guard let value else { return }
            if let target {
                if value == target { self.target = nil }
                return
            }
            if value != tab { tab = value }
        }
        .onChange(of: tab) { _, value in
            guard scrolled != value else { return }
            target = value
            withAnimation(.spring(response: 0.42, dampingFraction: 0.86)) { reader.scrollTo(value, anchor: .leading) }
            // Never leave paging locked if the scroll position report is missed.
            Task { try? await Task.sleep(for: .seconds(1.2)); if target == value { target = nil; scrolled = value } }
        }
        .onAppear { reader.scrollTo(tab, anchor: .leading); scrolled = tab }
        .ignoresSafeArea()
        }
        }
        .background(AmbientBackground())
        .onGeometryChange(for: Bool.self) { $0.size.width > $0.size.height } action: { landscape = $0 }
        .overlay {
            if landscapeChart {
                LandscapeChart(symbol: tab == .fib ? fib.chartSymbol : ticket.symbol,
                               period: tab == .fib ? Bindable(fib).period : $tradePeriod,
                               lines: tab == .trade ? tradeLines : [])
                    .transition(.opacity)
            }
        }
        .overlay(alignment: .bottom) {
            if !landscape {
                GlassTabBar(selection: $tab, onReselect: { reselect[$0, default: 0] += 1 }, progress: progress, badge: notices.unread)
                    .padding(.horizontal, 28)
                    .padding(.bottom, 2)
            }
        }
        .overlay(alignment: .top) {
            if !NetworkWatch.shared.online {
                OfflineCapsule().padding(.top, 2).transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .overlay(alignment: .bottom) {
            if let notice = account.notice {
                NoticeToast(notice: notice)
                    .padding(.bottom, 82)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: notice.id) {
                        try? await Task.sleep(for: .seconds(3.2))
                        withAnimation(.snappy) { if account.notice?.id == notice.id { account.notice = nil } }
                    }
                    .onTapGesture { withAnimation(.snappy) { account.notice = nil } }
            }
        }
        .animation(.spring(response: 0.38, dampingFraction: 0.86), value: account.notice)
        .overlay(alignment: .top) {
            if let banner = notices.banner {
                AlarmBanner(event: banner, now: notices.data?.nowMs ?? 0, onView: { Haptics.open(); open(banner) }, onDismiss: {
                    Haptics.tap()
                    withAnimation(.spring(response: 0.35, dampingFraction: 0.9)) { notices.banner = nil }
                })
                .offset(y: min(0, bannerDrag))
                .gesture(DragGesture().onChanged { bannerDrag = $0.translation.height }.onEnded { value in
                    if value.translation.height < -30 { Haptics.tap(); withAnimation(.spring(response: 0.3, dampingFraction: 0.9)) { notices.banner = nil } }
                    withAnimation(.spring) { bannerDrag = 0 }
                })
                .transition(.move(edge: .top).combined(with: .opacity))
                .padding(.top, 4)
            }
        }
        .onChange(of: tab) { _, now in Haptics.select(); Orientation.allowLandscape(now == .fib || now == .trade) }
        .onAppear { Orientation.allowLandscape(chartTab) }
        // Widget taps (spike://fib, spike://trade) open their page.
        .onOpenURL { url in
            guard url.scheme == "spike", let page = AppTab(rawValue: url.host() ?? "") else { return }
            withAnimation(.snappy) { tab = page }
        }
        .onChange(of: PushRegistry.shared.openRequestKey) { _, _ in
            guard let request = PushRegistry.shared.openRequest else { return }
            PushRegistry.shared.openRequest = nil
            if request.kind == "month_first_week_touch" {
                focusMonthFirstWeekTouch(eventId: request.eventId, month: request.month)
                withAnimation(.snappy) { tab = .monitor }
                return
            }
            if request.kind == "alarm" { UserDefaults.standard.set(MonitorSegment.alerts.rawValue, forKey: "monitor.segment") }
            if request.kind != "alarm", let symbol = request.symbol { fib.focus(symbol) }
            withAnimation(.snappy) { tab = request.kind == "alarm" ? .monitor : .fib }
        }
        .environment(\.openChart, OpenChartAction { symbol in
            Haptics.open(); fib.focus(symbol)
            withAnimation(.snappy) { tab = .fib }
        })
        #if DEBUG
        .sheet(isPresented: .constant(AppConfig.qaWidgets)) { WidgetGallery() }
        #endif
        .sheet(isPresented: $showNotifications) { NotificationCenterView(onOpen: open) }
        .sheet(isPresented: $showSettings) { SettingsView() }

        .sheet(isPresented: Binding(get: { ticket.fibTicket }, set: { if !$0 { ticket.manual(); ticket.fibTicket = false } })) {
            QuickTicketSheet(onBlocked: route).presentationDetents([.large]).presentationDragIndicator(.visible)
        }
        .task { session.restore(); applyQA() }
        .task(id: pollKey) {
            guard let connection = session.connection, scenePhase == .active else { return }
            await notices.poll(connection: connection, session: session)
        }
        .task(id: session.connection?.token.hashValue ?? 0) {
            guard session.connection != nil else { return }
            await PushRegistry.shared.start(connection: session.connection, fib: notices.prefs.fib,
                                            alarm: notices.prefs.popup,
                                            monthFirstWeekTouch: notices.prefs.monthFirstWeekTouch)
        }
        .task(id: pollKey) {
            account.attach(session.connection)
            guard session.connection != nil, scenePhase == .active else { return }
            await account.run(session: session)
        }
    }

    @ViewBuilder private func content(_ page: AppTab) -> some View {
        switch page {
        case .monitor: MonitorView(showNotifications: $showNotifications)
        case .trade: TradeView(active: tab == .trade && scenePhase == .active && !landscape, onDestination: route)
        case .fib: FibView(active: tab == .fib && scenePhase == .active && !landscape, onQuick: { row, period in
            guard account.pendingEntry == nil else { return }
            ticket.openQuick(row, period: period, state: account.state)
        })
        case .positions: PositionsView(onOpenSymbol: { symbol in
            if symbol != ticket.symbol { ticket.choose(symbol); ticket.syncLeverage(account.state); ticket.syncExisting(account.state) }
            withAnimation(.snappy) { tab = .trade }
        })
        case .stats: StatsView(showSettings: $showSettings, active: tab == .stats && scenePhase == .active)
        }
    }

    /// Entry / stop of the 合约 coin's open position for the landscape chart (same as TradeView).
    private var tradeLines: [ChartLine] {
        guard let e = ticket.existing(account.state) else { return [] }
        return [ChartLine(price: Double(e.entry) ?? 0, title: "开仓", color: ChartPalette.accent),
                ChartLine(price: Double(e.stop) ?? 0, title: "止损", color: ChartPalette.down, dashed: true)]
    }

    private var pollKey: String { "\(session.connection?.token.hashValue ?? 0)-\(session.connection?.accountId ?? "")-\(scenePhase == .active)" }

    private func route(_ destination: Trading.Destination) {
        switch destination {
        case .settings: showSettings = true
        case .positions: withAnimation(.snappy) { tab = .positions }
        case .discipline: withAnimation(.snappy) { tab = .stats }
        }
    }

    private func open(_ event: NotificationEvent) {
        withAnimation(.snappy) { notices.banner = nil }
        if event.kind == "month_first_week_touch" {
            focusMonthFirstWeekTouch(eventId: event.eventId, month: event.month)
            withAnimation(.snappy) { tab = .monitor }
            return
        }
        if event.kind == "alarm" { UserDefaults.standard.set(MonitorSegment.alerts.rawValue, forKey: "monitor.segment") }
        if event.kind != "alarm", let symbol = event.symbol { fib.symbol = symbol }
        withAnimation(.snappy) { tab = event.kind == "alarm" ? .monitor : .fib }
    }

    private func focusMonthFirstWeekTouch(eventId: String?, month: String?) {
        UserDefaults.standard.set("month", forKey: "touch.source")
        UserDefaults.standard.set(MonitorSegment.touches.rawValue, forKey: "monitor.segment")
        if let eventId {
            UserDefaults.standard.set(eventId, forKey: "touch.month.focus")
            UserDefaults.standard.set(eventId, forKey: "touch.month.chartEventId")
        }
        if let month { UserDefaults.standard.set(month, forKey: "touch.month") }
    }

    private func applyQA() {
        #if DEBUG
        if let value = AppConfig.qaTab, let qa = AppTab(rawValue: value) { tab = qa; progress = CGFloat(AppTab.allCases.firstIndex(of: qa) ?? 1) }
        if let value = AppConfig.qaSegment { UserDefaults.standard.set(value, forKey: "monitor.segment") }
        #endif
    }
}

/// Floating frosted tab bar (icons only); the accent lens slides with the page scroll.
struct GlassTabBar: View {
    @Binding var selection: AppTab
    var onReselect: (AppTab) -> Void = { _ in }
    let progress: CGFloat
    let badge: Int
    @Namespace private var glass

    var body: some View {
        GeometryReader { proxy in
            let count = CGFloat(AppTab.allCases.count), inset: CGFloat = 5
            let item = (proxy.size.width - inset * 2) / count
            let x = inset + item * min(max(progress, 0), count - 1)
            ZStack(alignment: .leading) {
                Capsule().fill(Color.accentColor.opacity(0.10))
                    .frame(width: item, height: proxy.size.height - inset * 2)
                    .offset(x: x)
                HStack(spacing: 0) {
                    ForEach(AppTab.allCases) { page in
                        let active = selection == page
                        Button {
                            Haptics.tap()
                            if selection == page { Haptics.tap(); onReselect(page) } else { withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { selection = page } }
                        } label: {
                            Image(systemName: page.symbol(active: active))
                                .font(.scaled(size: 19, weight: active ? .semibold : .regular))
                                .foregroundStyle(active ? Color.accentColor : Color.muted)
                                .symbolEffect(.bounce, value: active)
                                .contentTransition(.symbolEffect(.replace))
                                .overlay(alignment: .topTrailing) {
                                    if page == .monitor && badge > 0 {
                                        Text(badge > 9 ? "9+" : "\(badge)").font(.scaled(size: 10, weight: .bold)).foregroundStyle(.white)
                                            .padding(.horizontal, 5).frame(minWidth: 17, minHeight: 17).background(.red, in: .capsule)
                                            .offset(x: 10, y: -8)
                                    }
                                }
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                                .contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(page.title)
                        .accessibilityAddTraits(active ? .isSelected : [])
                    }
                }
                .padding(.horizontal, inset)
            }
        }
        .frame(height: 54)
        .frosted(Capsule(), veil: 0.7)
    }
}

/// Result of an order, close, cancel or protection request (1.x notify toast).
struct NoticeToast: View {
    let notice: AccountStore.Notice
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: notice.success ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .foregroundStyle(notice.success ? Color.up : Color.amber)
            Text(notice.text).font(.subheadline.weight(.semibold)).lineLimit(2)
        }
        .padding(.horizontal, 16).padding(.vertical, 11)
        .frosted(Capsule(), veil: 0.85)
        .padding(.horizontal, 24)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
    }
}

