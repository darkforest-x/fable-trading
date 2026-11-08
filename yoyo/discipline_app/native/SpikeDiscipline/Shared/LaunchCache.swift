import CryptoKit
import Foundation
import Network
import Observation
import SwiftUI

/// Display-only launch cache (owner 2026-11-08 “优化很多细节 提高用户体验”, Stitch c8bea04c…
/// “启动即显示上次数据”). The last account snapshot and statistics are kept in Caches, keyed by a hash
/// of the connection scope (service + token + account), and shown — marked 上次 HH:mm — until the
/// first live read. Order gates never read it: they need `AccountStore.state` and `online`.
enum LaunchCache {
    private static func url(_ name: String, scope: String) -> URL? {
        guard !scope.isEmpty, let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else { return nil }
        let key = SHA256.hash(data: Data((scope + "|" + name).utf8)).prefix(12).map { String(format: "%02x", $0) }.joined()
        return base.appendingPathComponent("launch", isDirectory: true).appendingPathComponent(key + ".json")
    }

    static func read<T: Decodable>(_ type: T.Type, _ name: String, scope: String) -> T? {
        guard let url = url(name, scope: scope), let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    static func write<T: Encodable & Sendable>(_ value: T, _ name: String, scope: String) {
        guard let url = url(name, scope: scope) else { return }
        Task.detached(priority: .utility) {
            guard let data = try? JSONEncoder().encode(value) else { return }
            try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        }
    }
}

/// Network reachability for the offline capsule (Stitch c8bea04c… “断网提示”).
@MainActor @Observable
final class NetworkWatch {
    static let shared = NetworkWatch()
    private(set) var online = true
    private let monitor = NWPathMonitor()

    private init() {
        monitor.pathUpdateHandler = { path in
            let ok = path.status == .satisfied
            Task { @MainActor in if NetworkWatch.shared.online != ok { withAnimation(.snappy) { NetworkWatch.shared.online = ok } } }
        }
        monitor.start(queue: DispatchQueue(label: "spike.network"))
    }
}

/// Tapping the tab of the page already shown: lists scroll to the top, charts jump to the latest bar.
private struct ReselectKey: EnvironmentKey { static let defaultValue = 0 }
extension EnvironmentValues {
    var reselect: Int { get { self[ReselectKey.self] } set { self[ReselectKey.self] = newValue } }
}

/// Slim capsule under the status bar while the phone has no network.
struct OfflineCapsule: View {
    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(Color.amber).frame(width: 7, height: 7)
            Image(systemName: "wifi.slash").font(.system(size: 12, weight: .semibold)).foregroundStyle(Color.amber)
            Text("网络已断开 · 显示上次数据").font(.scaled(size: 13, weight: .semibold)).foregroundStyle(Color.ink)
        }
        .padding(.horizontal, 14).frame(height: 34)
        .frosted(Capsule(), veil: 0.85)
        .accessibilityElement(children: .combine)
    }
}
