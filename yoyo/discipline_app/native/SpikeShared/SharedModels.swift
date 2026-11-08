import ActivityKit
import Foundation

/// Compiled into the app and the SpikeWidgets extension (2.6.0).

/// Lock-screen / Dynamic Island Live Activity for one open position (owner 2026-11-08 item 4,
/// Stitch widgets-live-activity). Read-only: it shows the position; it never trades.
/// `ContentState` keys match `activity_state` in yoyo/discipline_app/push.py, which pushes updates
/// while the app is in the background once the owner has added an APNs key.
struct PositionActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var mark: Double
        var pnl: Double
        var roe: Double?
        var stop: Double
        var updated: Double          // epoch seconds
    }
    var positionId: String
    var symbol: String
    var side: String                 // long / short
    var leverage: Int
    var entry: Double
    var decimals: Int
}

/// What the home-screen / lock-screen widgets show (owner 2026-11-08 item 5): today's scored 1.618
/// touches and the open positions. The app writes it into the App Group whenever it refreshes; the
/// widget refreshes it itself on device builds. Simulated scores, display only.
struct WidgetSnapshot: Codable, Equatable {
    struct Touch: Codable, Hashable { let symbol: String; let side: String; let score: Double?; let at: Double }
    struct Holding: Codable, Hashable { let symbol: String; let side: String; let leverage: Int; let pnl: Double }
    var updated: Double              // epoch seconds
    var touches: [Touch]             // today (Beijing day), newest first
    var positions: [Holding]
    var threshold: Double            // 设置 · 触价评分 highlight
    var account: String? = nil       // account the app shows (X-Spike-Account)

    static let group = "group.com.spike.discipline.native"
    static var url: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)?.appendingPathComponent("widget-snapshot.json")
    }
    static func load() -> WidgetSnapshot? {
        guard let url, let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(WidgetSnapshot.self, from: data)
    }
    func save() {
        guard let url = Self.url, let data = try? JSONEncoder().encode(self) else { return }
        try? data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    var highTouches: [Touch] { touches.filter { ($0.score ?? -1) >= threshold }.sorted { ($0.score ?? 0) > ($1.score ?? 0) } }

    static func coin(_ symbol: String) -> String { symbol.components(separatedBy: "-").first ?? symbol }

    static func beijingDay(_ date: Date) -> String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "Asia/Shanghai")
        f.dateFormat = "yyyy-MM-dd"; return f.string(from: date)
    }
    static func clock(_ seconds: Double) -> String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "Asia/Shanghai")
        f.dateFormat = "HH:mm"; return f.string(from: Date(timeIntervalSince1970: seconds))
    }
}
