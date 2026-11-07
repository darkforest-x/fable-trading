import SwiftUI

/// SPIKE 纪律 2.0 — native SwiftUI rewrite (owner 2026-11-07 “原生 SwiftUI 重写”).
/// Stage 1 ships 监控 and in-app alarms; trading tabs follow in later stages and, until then,
/// the 1.x app remains the only place that can place, close or modify orders.
@main
struct SpikeDisciplineApp: App {
    @State private var session = SessionStore()
    @State private var notices = NotificationStore()
    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .environment(notices)
        }
    }
}

enum AppTab: String, Hashable { case trade, monitor, fib, positions, stats }

struct RootView: View {
    @Environment(SessionStore.self) private var session
    @Environment(NotificationStore.self) private var notices
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: AppTab = .monitor
    @State private var showNotifications = false
    @State private var showSettings = false
    @State private var bannerDrag: CGFloat = 0

    var body: some View {
        TabView(selection: $tab) {
            Tab("合约", systemImage: "arrow.left.arrow.right", value: AppTab.trade) {
                StagePlaceholder(title: "合约", systemImage: "arrow.left.arrow.right", stage: "第 4 阶段", note: "下单票据是真金链路，最后迁移并单独测试。", showSettings: $showSettings)
            }
            Tab("监控", systemImage: "bell", value: AppTab.monitor) {
                MonitorView(showNotifications: $showNotifications, showSettings: $showSettings)
            }
            .badge(notices.unread)
            Tab("1.618", systemImage: "chart.line.uptrend.xyaxis", value: AppTab.fib) {
                StagePlaceholder(title: "1.618", systemImage: "chart.line.uptrend.xyaxis", stage: "第 2 阶段", note: "原生 K 线、区间线、内部线、六均线与触价列表。", showSettings: $showSettings)
            }
            Tab("持仓", systemImage: "briefcase", value: AppTab.positions) {
                StagePlaceholder(title: "持仓保护", systemImage: "briefcase", stage: "第 3 阶段", note: "持仓、平仓、撤单与止损保护。", showSettings: $showSettings)
            }
            Tab("统计", systemImage: "chart.bar", value: AppTab.stats) {
                StagePlaceholder(title: "统计", systemImage: "chart.bar", stage: "第 3 阶段", note: "账户统计、交易纪律与设置。", showSettings: $showSettings)
            }
        }
        .tabBarMinimizeBehavior(.onScrollDown)
        .overlay(alignment: .top) {
            if let banner = notices.banner {
                AlarmBanner(event: banner, now: notices.data?.nowMs ?? 0, onView: { open(banner) }, onDismiss: {
                    withAnimation(.spring(response: 0.35, dampingFraction: 0.9)) { notices.banner = nil }
                })
                .offset(y: min(0, bannerDrag))
                .gesture(DragGesture().onChanged { bannerDrag = $0.translation.height }.onEnded { value in
                    if value.translation.height < -30 { withAnimation(.spring(response: 0.3, dampingFraction: 0.9)) { notices.banner = nil } }
                    withAnimation(.spring) { bannerDrag = 0 }
                })
                .transition(.move(edge: .top).combined(with: .opacity))
                .padding(.top, 4)
            }
        }
        .sheet(isPresented: $showNotifications) { NotificationCenterView(onOpen: open) }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .task { session.restore(); applyQA() }
        .task(id: pollKey) {
            guard let connection = session.connection, scenePhase == .active else { return }
            await notices.poll(connection: connection, session: session)
        }
    }

    private var pollKey: String { "\(session.connection?.token.hashValue ?? 0)-\(scenePhase == .active)" }

    private func open(_ event: NotificationEvent) {
        withAnimation(.snappy) { notices.banner = nil }
        if event.kind == "alarm" { UserDefaults.standard.set(MonitorSegment.alerts.rawValue, forKey: "monitor.segment") }
        tab = event.kind == "alarm" ? .monitor : .fib
    }

    private func applyQA() {
        #if DEBUG
        if let value = AppConfig.qaTab, let qa = AppTab(rawValue: value) { tab = qa }
        if let value = AppConfig.qaSegment { UserDefaults.standard.set(value, forKey: "monitor.segment") }
        #endif
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
            .background(AmbientBackground())
            .navigationTitle(title)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { showSettings = true } label: { Image(systemName: "gearshape") } } }
        }
    }
}
