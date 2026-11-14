import SwiftUI

enum MonitorSegment: String, CaseIterable, Identifiable {
    case touches, cloud, paper, alerts
    var id: String { rawValue }
    var title: String { switch self { case .touches: "触价"; case .cloud: "12H 云带"; case .paper: "模拟盘"; case .alerts: "极值插针" } }
}

/// 监控 (owner 2026-11-06): 1.618 paper accounts, market alarms and the 12H cloud board — read-only.
/// 2.7.0 (owner 2026-11-09 “屏幕左右滑动，在监控页顶部 4 个菜单之间切换”, Stitch native-2.7.0 paper-list):
/// the title and the segment stay on top; the four menus are pages of a horizontal paging scroll view
/// that follows the finger, each page scrolling and refreshing on its own, and the segment thumb tracks
/// the swipe. It is nested in RootView's tab pager: UIKit gives the inner pager every horizontal drag it
/// can scroll, and only a drag past the first or last menu reaches the tab pager (→ 合约 / 1.618).
/// (Locking the tab pager here made those edge drags do nothing: an inner scroll view at its edge does
/// not bounce, it fails its pan so the enclosing one can take it.) No chart with its own gestures lives
/// on these pages — account charts open in their sheet.
struct MonitorView: View {
    @Environment(SessionStore.self) private var session
    @Environment(NotificationStore.self) private var notices
    @AppStorage("monitor.segment") private var segment: MonitorSegment = .paper
    @Binding var showNotifications: Bool
    @State private var refresh: [MonitorSegment: Int] = [:]
    @State private var page: MonitorSegment?
    @State private var progress: CGFloat = 0
    @State private var sheet: StrategySheet?
    @Environment(\.reselect) private var reselect

    var body: some View {
        ZStack {
            AmbientBackground()
            VStack(spacing: 0) {
                CompactPageHeader(title: "全市场监控") {
                    RoundIcon(systemName: "bell", badge: notices.unread > 0) { notices.markRead(); showNotifications = true }
                        .accessibilityLabel(notices.unread > 0 ? "通知，\(notices.unread) 条未读" : "通知")
                }
                PagerSegment(options: MonitorSegment.allCases.map { ($0, $0.title) }, progress: progress) { target in
                    withAnimation(.spring(response: 0.38, dampingFraction: 0.88)) { page = target }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(Color(uiColor: .systemBackground))
                .overlay(alignment: .bottom) { Color.primary.opacity(0.07).frame(height: 1) }
                if let connection = session.connection {
                    pager(connection)
                } else {
                    ConnectPrompt().padding(.horizontal, 14)
                    Spacer()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .environment(\.openStrategy, OpenStrategyAction { sheet = $0 })
        .sheet(item: $sheet) { item in
            if let connection = session.connection {
                StrategySheetView(item: item, connection: connection).presentationDragIndicator(.visible)
            }
        }
        .onAppear { if page == nil { page = segment; progress = CGFloat(MonitorSegment.allCases.firstIndex(of: segment) ?? 0) } }
        // A push for an alarm sets monitor.segment from outside (RootView).
        .onChange(of: segment) { _, value in if page != value { withAnimation(.snappy) { page = value } } }
        .onChange(of: page) { _, value in if let value, value != segment { Haptics.select(); segment = value } }
    }

    private func pager(_ connection: Connection) -> some View {
        ScrollView(.horizontal) {
            LazyHStack(spacing: 0) {
                ForEach(MonitorSegment.allCases) { item in
                    MonitorPage(segment: item, connection: connection, refresh: refresh[item, default: 0],
                                active: page == item, reselect: reselect) {
                        refresh[item, default: 0] += 1
                    }
                    .containerRelativeFrame(.horizontal)
                    .id(item)
                }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(.paging)
        .scrollIndicators(.hidden)
        .scrollPosition(id: $page)
        .scrollEdgeEffectHidden(true, for: .all)
        .onScrollGeometryChange(for: CGFloat.self) { geo in
            geo.contentOffset.x / max(geo.containerSize.width, 1)
        } action: { _, value in progress = value }
    }
}

/// One monitor menu: its own vertical scroll, pull-to-refresh and tab re-tap to top.
private struct MonitorPage: View {
    let segment: MonitorSegment
    let connection: Connection
    let refresh: Int
    let active: Bool
    let reselect: Int
    let onRefresh: () -> Void
    @State private var scroll = ScrollPosition(edge: .top)

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                switch segment {
                case .paper: PaperSection(connection: connection, refresh: refresh)
                case .alerts: AlertsSection(connection: connection, refresh: refresh)
                case .cloud: CloudSection(connection: connection, refresh: refresh)
                case .touches: TouchesSection(connection: connection, refresh: refresh)
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 4)
            .padding(.bottom, 24)
        }
        .scrollPosition($scroll)
        .onChange(of: reselect) { _, _ in if active { withAnimation(.snappy) { scroll.scrollTo(edge: .top) } } }
        .refreshable { onRefresh(); try? await Task.sleep(for: .milliseconds(700)); Haptics.success() }
        .contentMargins(.bottom, 96, for: .scrollContent)
        .scrollEdgeEffectHidden(true, for: [.top, .bottom])
    }
}

/// Segment whose white thumb follows the pager's scroll progress (0…n-1), with a page indicator.
struct PagerSegment<T: Hashable>: View {
    let options: [(T, String)]
    let progress: CGFloat
    let select: (T) -> Void
    private var nearest: Int { min(options.count - 1, max(0, Int(progress.rounded()))) }

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 6) {
                ForEach(Array(options.enumerated()), id: \.offset) { index, option in
                    let on = index == nearest
                    Button { guard !on else { return }; select(option.0) } label: {
                        Text(option.1)
                            .font(.scaled(size: 12, weight: on ? .semibold : .medium))
                            .foregroundStyle(on ? Color(uiColor: .systemBackground) : Color.ink)
                            .padding(.horizontal, 11)
                            .frame(height: 32)
                            .background(on ? Color.ink : Color.field, in: .capsule)
                            .contentShape(.capsule)
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            .animation(.snappy, value: nearest)
        }
        .scrollIndicators(.hidden)
        .frame(height: 34)
    }
}

/// Sheet for a paper account or a research-only booklet; booklet, backtest list and trade replay are
/// pushed inside it.
struct StrategySheetView: View {
    let item: StrategySheet
    let connection: Connection
    @State private var path: [StrategyPage] = []
    var body: some View {
        NavigationStack(path: $path) {
            Group {
                switch item {
                case .account(let account): AccountDetailView(account: account, connection: connection)
                case .research(let r): BookletView(key: r.key, label: r.label, connection: connection)
                }
            }
            .navigationDestination(for: StrategyPage.self) { page in
                switch page {
                case .booklet(let key, let label): BookletView(key: key, label: label, connection: connection)
                case .backtest(let key, let label): BacktestListView(key: key, label: label, connection: connection)
                case .trade(let key, let trade): BacktestChartView(key: key, trade: trade, connection: connection)
                }
            }
        }
    }
}

struct ConnectPrompt: View {
    var body: some View {
        HStack(spacing: 10) {
            ProgressView()
            Text("正在连接纪律服务").font(.subheadline.weight(.semibold))
        }
        .frame(maxWidth: .infinity).padding(16).glassCard(cornerRadius: 18)
    }
}

/// Polls one read-only snapshot while the view is visible; keeps the last good value on errors.
struct Polled<T: Decodable>: ViewModifier {
    let key: String
    let every: Duration
    let load: () async throws -> T
    @Binding var value: T?
    @Binding var error: String?
    @Environment(SessionStore.self) private var session
    func body(content: Content) -> some View {
        content.task(id: key) {
            while !Task.isCancelled {
                do { let next = try await load(); withAnimation(.snappy) { value = next }; error = nil }
                catch is CancellationError { return }
                catch { self.error = error.localizedDescription; session.handle(error) }
                try? await Task.sleep(for: every)
            }
        }
    }
}

struct LoadState: View {
    let loading: Bool
    let error: String?
    let stale: Bool
    var body: some View {
        if let error {
            Label(stale ? "\(error) · 显示上次数据" : error, systemImage: "exclamationmark.arrow.triangle.2.circlepath")
                .font(.footnote).foregroundStyle(Color.amber).padding(.horizontal, 4)
        } else if loading {
            HStack(spacing: 8) { ProgressView(); Text("正在读取").font(.footnote).foregroundStyle(.secondary) }.padding(.horizontal, 4)
        }
    }
}
