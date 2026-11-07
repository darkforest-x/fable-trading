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

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Picker("监控模块", selection: $segment.animation(.snappy)) {
                        ForEach(MonitorSegment.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    if let connection = session.connection {
                        Group {
                            switch segment {
                            case .paper: PaperSection(connection: connection)
                            case .alerts: AlertsSection(connection: connection)
                            case .cloud: CloudSection(connection: connection)
                            }
                        }
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    } else {
                        ConnectPrompt(showSettings: $showSettings)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .background(AmbientBackground())
            .navigationTitle("监控")
            .navigationSubtitle("模拟盘、全市场报警与云带，只读")
            .toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button { notices.markRead(); showNotifications = true } label: {
                        Image(systemName: notices.unread > 0 ? "bell.badge" : "bell")
                    }
                    .badge(notices.unread)
                    .accessibilityLabel(notices.unread > 0 ? "通知，\(notices.unread) 条未读" : "通知")
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("设置")
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
            Text("连接后查看").font(.headline)
            Text("数据经纪律服务只读转发，令牌只存在本机。").font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("连接纪律服务") { showSettings = true }.buttonStyle(.glassProminent)
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
