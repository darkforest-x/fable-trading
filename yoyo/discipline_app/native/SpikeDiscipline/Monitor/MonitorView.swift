import SwiftUI

enum MonitorSegment: String, CaseIterable, Identifiable {
    case paper, alerts, cloud, touches
    var id: String { rawValue }
    var title: String { switch self { case .paper: "模拟盘"; case .alerts: "报警"; case .cloud: "云带"; case .touches: "触价" } }
}

/// 监控 (owner 2026-11-06): 1.618 paper accounts, market alarms and the 12H cloud board — read-only.
/// Stitch monitor-paper-edit d163871f… / monitor-alerts 7e2f6b91… are the layout reference.
struct MonitorView: View {
    @Environment(SessionStore.self) private var session
    @Environment(NotificationStore.self) private var notices
    @AppStorage("monitor.segment") private var segment: MonitorSegment = .paper
    @Binding var showNotifications: Bool
    @State private var refresh = 0
    @State private var scroll = ScrollPosition(edge: .top)
    @Environment(\.reselect) private var reselect

    @State private var detail: PaperAccount?

    var body: some View {
        ZStack {
            AmbientBackground()
            ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                PageHeader(title: "监控") {
                    RoundIcon(systemName: "bell", badge: notices.unread > 0) { notices.markRead(); showNotifications = true }
                        .accessibilityLabel(notices.unread > 0 ? "通知，\(notices.unread) 条未读" : "通知")
                }
                SoftSegment(selection: $segment, options: MonitorSegment.allCases.map { ($0, $0.title) })
                if let connection = session.connection {
                    Group {
                        switch segment {
                        case .paper: PaperSection(connection: connection, refresh: refresh)
                        case .alerts: AlertsSection(connection: connection, refresh: refresh)
                        case .cloud: CloudSection(connection: connection, refresh: refresh)
                        case .touches: TouchesSection(connection: connection, refresh: refresh)
                        }
                    }
                    .id(segment)
                    .transition(.asymmetric(insertion: .opacity.combined(with: .offset(y: 18)), removal: .opacity))
                } else {
                    ConnectPrompt()
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 24)
        }
        .scrollPosition($scroll)
        .onChange(of: reselect) { _, _ in withAnimation(.snappy) { scroll.scrollTo(edge: .top) } }
        .refreshable { refresh += 1; try? await Task.sleep(for: .milliseconds(700)); Haptics.success() }
        .contentMargins(.bottom, 96, for: .scrollContent)
        .scrollEdgeEffectHidden(true, for: [.top, .bottom])
        }
        .environment(\.openAccount, OpenAccountAction { Haptics.open(); detail = $0 })
        .sheet(item: $detail) { account in
            if let connection = session.connection {
                NavigationStack { AccountDetailView(account: account, connection: connection) }
                    .presentationDragIndicator(.visible)
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
