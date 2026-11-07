import SwiftUI

/// SPIKE 纪律 2.x — native SwiftUI rewrite (owner 2026-11-07 “原生 SwiftUI 重写”, 2.1.0 “剩下的全部重构”).
/// Every order, close, cancel and protection write goes through AccountStore, which persists request
/// identities before sending and never resends on timeout.
@main
struct SpikeDisciplineApp: App {
    @State private var session = SessionStore()
    @State private var notices = NotificationStore()
    @State private var account = AccountStore()
    @State private var fib = FibStore()
    @State private var ticket = TicketModel()
    @AppStorage("appearance") private var appearance = "system"
    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .environment(notices)
                .environment(account)
                .environment(fib)
                .environment(ticket)
                .preferredColorScheme(appearance == "light" ? .light : appearance == "dark" ? .dark : nil)
        }
    }
}

enum AppTab: String, Hashable, CaseIterable, Identifiable {
    case trade, monitor, fib, positions, stats
    var id: String { rawValue }
    var title: String { switch self { case .trade: "合约"; case .monitor: "监控"; case .fib: "1.618"; case .positions: "持仓"; case .stats: "统计" } }
    var symbol: String {
        switch self {
        case .trade: "arrow.left.arrow.right"; case .monitor: "bell"; case .fib: "chart.line.uptrend.xyaxis"
        case .positions: "briefcase"; case .stats: "chart.bar"
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
    @State private var tab: AppTab = .monitor
    @State private var progress: CGFloat = CGFloat(AppTab.allCases.firstIndex(of: .monitor)!)
    @State private var showNotifications = false
    @State private var showSettings = false
    @State private var showReplay = false
    @State private var bannerDrag: CGFloat = 0

    var body: some View {
        // System page TabView: native finger-following swipes and correct safe areas for each
        // page's NavigationStack. Each page reports its x offset so the tab-bar lens can follow.
        GeometryReader { proxy in
            TabView(selection: $tab) {
                ForEach(AppTab.allCases) { page in
                    content(page)
                        .tag(page)
                        .background {
                            GeometryReader { g in
                                Color.clear.preference(key: PageOffsetKey.self, value: [AppTab.allCases.firstIndex(of: page)!: g.frame(in: .global).minX])
                            }
                        }
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            // The 1.618 page is a full-screen chart: horizontal drags pan the chart, not the pages.
            .scrollDisabled(tab == .fib)
            .onPreferenceChange(PageOffsetKey.self) { offsets in
                let width = max(proxy.size.width, 1)
                if let (index, x) = offsets.first(where: { abs($0.value) < width }) { progress = CGFloat(index) - x / width }
            }
        }
        .ignoresSafeArea()
        .background(AmbientBackground())
        .overlay(alignment: .bottom) {
            GlassTabBar(selection: $tab, progress: progress, badge: notices.unread)
                .padding(.horizontal, 22)
                .padding(.bottom, 2)
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
        .onChange(of: tab) { _, _ in Haptics.select() }
        .environment(\.openChart, OpenChartAction { symbol in
            Haptics.open(); fib.focus(symbol)
            withAnimation(.snappy) { tab = .fib }
        })
        .sheet(isPresented: $showNotifications) { NotificationCenterView(onOpen: open) }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .fullScreenCover(isPresented: $showReplay) { ReplayView() }
        .onChange(of: showReplay) { _, open in account.paused = open }
        .sheet(isPresented: Binding(get: { ticket.fibTicket }, set: { if !$0 { ticket.manual(); ticket.fibTicket = false } })) {
            QuickTicketSheet(onBlocked: route).presentationDetents([.large]).presentationDragIndicator(.visible)
        }
        .task { session.restore(); applyQA() }
        .task(id: pollKey) {
            guard let connection = session.connection, scenePhase == .active else { return }
            await notices.poll(connection: connection, session: session)
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
        case .trade: TradeView(active: tab == .trade && scenePhase == .active, onDestination: route)
        case .fib: FibView(active: tab == .fib && scenePhase == .active, onQuick: { row, period in
            guard account.pendingEntry == nil else { return }
            ticket.openQuick(row, period: period, state: account.state)
        }, onReplay: { showReplay = true })
        case .positions: PositionsView(onOpenSymbol: { symbol in
            if symbol != ticket.symbol { ticket.choose(symbol); ticket.syncLeverage(account.state); ticket.syncExisting(account.state) }
            withAnimation(.snappy) { tab = .trade }
        })
        case .stats: StatsView(showSettings: $showSettings, active: tab == .stats && scenePhase == .active)
        }
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
        if event.kind == "alarm" { UserDefaults.standard.set(MonitorSegment.alerts.rawValue, forKey: "monitor.segment") }
        if event.kind != "alarm", let symbol = event.symbol { fib.symbol = symbol }
        withAnimation(.snappy) { tab = event.kind == "alarm" ? .monitor : .fib }
    }

    private func applyQA() {
        #if DEBUG
        if let value = AppConfig.qaTab, let qa = AppTab(rawValue: value) { tab = qa; progress = CGFloat(AppTab.allCases.firstIndex(of: qa) ?? 1) }
        if let value = AppConfig.qaSegment { UserDefaults.standard.set(value, forKey: "monitor.segment") }
        #endif
    }
}

/// Floating Liquid Glass tab bar; the lens slides with the page scroll and morphs on taps.
struct GlassTabBar: View {
    @Binding var selection: AppTab
    let progress: CGFloat
    let badge: Int
    @Namespace private var glass

    var body: some View {
        GeometryReader { proxy in
            let count = CGFloat(AppTab.allCases.count), inset: CGFloat = 5
            let item = (proxy.size.width - inset * 2) / count
            let x = inset + item * min(max(progress, 0), count - 1)
            ZStack(alignment: .leading) {
                Capsule().fill(.clear)
                    .glassEffect(.regular.tint(Color.accentColor.opacity(0.10)).interactive(), in: .capsule)
                    .frame(width: item, height: proxy.size.height - inset * 2)
                    .offset(x: x)
                HStack(spacing: 0) {
                    ForEach(AppTab.allCases) { page in
                        let active = selection == page
                        Button {
                            Haptics.tap()
                            withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { selection = page }
                        } label: {
                            TabGlyph(tab: page, active: active)
                                .overlay(alignment: .topTrailing) {
                                    if page == .monitor && badge > 0 {
                                        Text(badge > 9 ? "9+" : "\(badge)").font(.system(size: 10, weight: .bold)).foregroundStyle(.white)
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
        .frame(height: 56)
        .glassEffect(.regular, in: .capsule)
        .shadow(color: .black.opacity(0.08), radius: 16, y: 6)
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
        .glassEffect(.regular, in: .capsule)
        .padding(.horizontal, 24)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
    }
}

private struct PageOffsetKey: PreferenceKey {
    static var defaultValue: [Int: CGFloat] = [:]
    static func reduce(value: inout [Int: CGFloat], nextValue: () -> [Int: CGFloat]) { value.merge(nextValue()) { $1 } }
}
