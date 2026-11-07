import SwiftUI

/// SPIKE 纪律 2.x — native SwiftUI rewrite (owner 2026-11-07 “原生 SwiftUI 重写”).
/// Until the trading stages land, the 1.x app remains the only place that can place,
/// close or modify orders.
@main
struct SpikeDisciplineApp: App {
    @State private var session = SessionStore()
    @State private var notices = NotificationStore()
    @AppStorage("appearance") private var appearance = "system"
    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .environment(notices)
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
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: AppTab? = .monitor
    @State private var progress: CGFloat = CGFloat(AppTab.allCases.firstIndex(of: .monitor)!)
    @State private var showNotifications = false
    @State private var showSettings = false
    @State private var bannerDrag: CGFloat = 0

    var body: some View {
        GeometryReader { proxy in
            ScrollView(.horizontal) {
                LazyHStack(spacing: 0) {
                    ForEach(AppTab.allCases) { page in
                        content(page)
                            .containerRelativeFrame(.horizontal)
                            .id(page)
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.paging)
            .scrollPosition(id: $tab)
            .scrollIndicators(.hidden)
            .scrollBounceBehavior(.basedOnSize)
            .onScrollGeometryChange(for: CGFloat.self) { $0.contentOffset.x } action: { _, x in
                if proxy.size.width > 0 { progress = x / proxy.size.width }
            }
            .ignoresSafeArea(edges: .bottom)
        }
        .overlay(alignment: .bottom) {
            GlassTabBar(selection: $tab, progress: progress, badge: notices.unread)
                .padding(.horizontal, 14)
                .padding(.bottom, 2)
        }
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
        .sheet(isPresented: $showNotifications) { NotificationCenterView(onOpen: open) }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .task { session.restore(); applyQA() }
        .task(id: pollKey) {
            guard let connection = session.connection, scenePhase == .active else { return }
            await notices.poll(connection: connection, session: session)
        }
    }

    @ViewBuilder private func content(_ page: AppTab) -> some View {
        switch page {
        case .monitor: MonitorView(showNotifications: $showNotifications, showSettings: $showSettings)
        case .trade: StagePlaceholder(title: "合约", systemImage: page.symbol, stage: "第 4 阶段", note: "下单票据是真金链路，最后迁移并单独测试。", showSettings: $showSettings)
        case .fib: StagePlaceholder(title: "1.618", systemImage: page.symbol, stage: "第 2 阶段", note: "原生 K 线、区间线、内部线、六均线与触价列表。", showSettings: $showSettings)
        case .positions: StagePlaceholder(title: "持仓保护", systemImage: page.symbol, stage: "第 3 阶段", note: "持仓、平仓、撤单与止损保护。", showSettings: $showSettings)
        case .stats: StagePlaceholder(title: "统计", systemImage: page.symbol, stage: "第 3 阶段", note: "账户统计、交易纪律与设置。", showSettings: $showSettings)
        }
    }

    private var pollKey: String { "\(session.connection?.token.hashValue ?? 0)-\(scenePhase == .active)" }

    private func open(_ event: NotificationEvent) {
        withAnimation(.snappy) { notices.banner = nil }
        if event.kind == "alarm" { UserDefaults.standard.set(MonitorSegment.alerts.rawValue, forKey: "monitor.segment") }
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
    @Binding var selection: AppTab?
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
                            VStack(spacing: 3) {
                                Image(systemName: page.symbol)
                                    .font(.system(size: 19, weight: active ? .semibold : .regular))
                                    .symbolEffect(.bounce, value: active)
                                    .overlay(alignment: .topTrailing) {
                                        if page == .monitor && badge > 0 {
                                            Text(badge > 9 ? "9+" : "\(badge)").font(.system(size: 10, weight: .bold)).foregroundStyle(.white)
                                                .padding(.horizontal, 5).frame(minWidth: 17, minHeight: 17).background(.red, in: .capsule)
                                                .offset(x: 10, y: -7)
                                        }
                                    }
                                Text(page.title).font(.system(size: 10, weight: active ? .semibold : .medium))
                            }
                            .foregroundStyle(active ? Color.accentColor : Color.primary.opacity(0.75))
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
        .frame(height: 62)
        .glassEffect(.regular, in: .capsule)
        .shadow(color: .black.opacity(0.08), radius: 16, y: 6)
    }
}

struct StagePlaceholder: View {
    let title: String
    let systemImage: String
    let stage: String
    let note: String
    @Binding var showSettings: Bool
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    Image(systemName: systemImage).font(.system(size: 38, weight: .medium)).foregroundStyle(.blue)
                        .frame(width: 84, height: 84).glassEffect(.regular, in: .circle)
                    Text("原生版\(stage)").font(.title3.weight(.bold))
                    Text(note).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    Text("迁移完成前，交易仍在原 SPIKE 纪律交易 App 中进行，规则与保护不变。").font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity).padding(28).glassCard(cornerRadius: 28)
                .padding(16)
            }
            .contentMargins(.bottom, 96, for: .scrollContent)
            .background(AmbientBackground())
            .navigationTitle(title)
            .toolbarTitleDisplayMode(.inlineLarge)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { Haptics.tap(); showSettings = true } label: { Image(systemName: "gearshape") } } }
        }
    }
}
