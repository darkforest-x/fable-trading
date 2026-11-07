import SwiftUI

enum MonitorSegment: String, CaseIterable, Identifiable {
    case paper, alerts, cloud
    var id: String { rawValue }
    var title: String { switch self { case .paper: "模拟盘"; case .alerts: "报警"; case .cloud: "云带" } }
}

/// 监控 (owner 2026-11-06): 1.618 paper accounts, market alarms and the 12H cloud board — read-only.
/// Stitch monitor-paper-edit d163871f… / monitor-alerts 7e2f6b91… are the layout reference.
struct MonitorView: View {
    @Environment(SessionStore.self) private var session
    @Environment(NotificationStore.self) private var notices
    @AppStorage("monitor.segment") private var segment: MonitorSegment = .paper
    @Binding var showNotifications: Bool
    @Binding var showSettings: Bool
    @Namespace private var zoom
    @State private var refresh = 0

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Picker("监控模块", selection: $segment.animation(.snappy)) {
                        ForEach(MonitorSegment.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: segment) { _, _ in Haptics.select() }
                    if let connection = session.connection {
                        Group {
                            switch segment {
                            case .paper: PaperSection(connection: connection, zoom: zoom, refresh: refresh)
                            case .alerts: AlertsSection(connection: connection, refresh: refresh)
                            case .cloud: CloudSection(connection: connection, refresh: refresh)
                            }
                        }
                        .id(segment)
                        .transition(.asymmetric(insertion: .opacity.combined(with: .offset(y: 18)), removal: .opacity))
                    } else {
                        ConnectPrompt(showSettings: $showSettings)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 24)
            }
            .refreshable { refresh += 1; try? await Task.sleep(for: .milliseconds(700)); Haptics.success() }
            .contentMargins(.bottom, 96, for: .scrollContent)
            .navigationDestination(for: PaperAccount.self) { account in
                if let connection = session.connection {
                    AccountDetailView(account: account, connection: connection)
                        .navigationTransition(.zoom(sourceID: account.key, in: zoom))
                }
            }
            .background(AmbientBackground())
            .navigationTitle("监控")
            .toolbarTitleDisplayMode(.inlineLarge)
            .toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button { Haptics.open(); notices.markRead(); showNotifications = true } label: {
                        Image(systemName: notices.unread > 0 ? "bell.badge" : "bell")
                            .symbolEffect(.bounce, value: notices.unread)
                    }
                    .badge(notices.unread)
                    .accessibilityLabel(notices.unread > 0 ? "通知，\(notices.unread) 条未读" : "通知")
                    Button { Haptics.tap(); showSettings = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("设置")
                }
            }
        }
    }
}

struct ConnectPrompt: View {
    @Binding var showSettings: Bool
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "lock.shield").font(.system(size: 34)).foregroundStyle(.blue)
            Text("正在连接纪律服务").font(.headline)
            Text("本机构建已内置授权，打开即自动连接。").font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("查看连接状态") { Haptics.tap(); showSettings = true }.buttonStyle(.glassProminent)
        }
        .frame(maxWidth: .infinity).padding(28).glassCard()
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
