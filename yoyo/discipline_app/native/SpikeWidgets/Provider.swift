import Foundation
import WidgetKit

/// Timeline for the 天才交易员 widgets (owner 2026-11-08 item 5). Starts from the App Group snapshot
/// the app writes; on device builds (the service token is embedded in the containing app bundle) it
/// also refreshes today's scored touches and open positions itself, read-only, every 15 minutes.
struct SnapshotEntry: TimelineEntry {
    let date: Date
    let snapshot: WidgetSnapshot
    static let placeholder = SnapshotEntry(date: .now, snapshot: WidgetSnapshot(
        updated: Date().timeIntervalSince1970,
        touches: [.init(symbol: "TIA-USDT-SWAP", side: "short", score: 0.88, at: Date().timeIntervalSince1970 * 1000),
                  .init(symbol: "NEAR-USDT-SWAP", side: "long", score: 0.83, at: Date().timeIntervalSince1970 * 1000 - 600_000),
                  .init(symbol: "APT-USDT-SWAP", side: "short", score: 0.79, at: Date().timeIntervalSince1970 * 1000 - 1_200_000)],
        positions: [.init(symbol: "ETH-USDT-SWAP", side: "long", leverage: 3, pnl: 18.6)], threshold: 0.5))
}

struct SnapshotProvider: TimelineProvider {
    func placeholder(in context: Context) -> SnapshotEntry { .placeholder }

    func getSnapshot(in context: Context, completion: @escaping (SnapshotEntry) -> Void) {
        completion(context.isPreview ? .placeholder : SnapshotEntry(date: .now, snapshot: WidgetSnapshot.load() ?? SnapshotEntry.placeholder.snapshot))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<SnapshotEntry>) -> Void) {
        Task {
            var snapshot = WidgetSnapshot.load() ?? WidgetSnapshot(updated: 0, touches: [], positions: [], threshold: 0.5)
            if let fresh = await ServiceFeed.refresh(snapshot) { snapshot = fresh; snapshot.save() }
            let next = Date().addingTimeInterval(15 * 60)
            completion(Timeline(entries: [SnapshotEntry(date: .now, snapshot: snapshot)], policy: .after(next)))
        }
    }
}

/// Read-only service reads for the widget (GET /v1/monitor/paper and /v1/state).
enum ServiceFeed {
    static let base = URL(string: "https://discipline.spikeok.com")!

    /// The containing app's embedded token (device builds only; never in simulator builds or git).
    static var token: String? {
        let app = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
        guard let text = try? String(contentsOf: app.appendingPathComponent("service-token"), encoding: .utf8) else { return nil }
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.count >= 32 ? value : nil
    }

    private struct Paper: Decodable {
        struct Signal: Decodable { let symbol: String; let side: String; let score: Double?; let status: String; let observedMs: Double? }
        let signals: [Signal]
    }
    private struct State: Decodable {
        struct Position: Decodable { let symbol: String; let side: String; let leverage: Double; let pnl: String }
        let positions: [Position]
    }

    private static func get<T: Decodable>(_ path: String, query: String = "", token: String, account: String?) async throws -> T {
        var request = URLRequest(url: URL(string: base.absoluteString + path + query)!, timeoutInterval: 12)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let account { request.setValue(account, forHTTPHeaderField: "X-Spike-Account") }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(T.self, from: data)
    }

    static func refresh(_ old: WidgetSnapshot) async -> WidgetSnapshot? {
        guard let token else { return nil }
        var next = old
        let today = WidgetSnapshot.beijingDay(Date())
        if let paper: Paper = try? await get("/v1/monitor/paper", query: "?track=live-wick6x3", token: token, account: old.account) {
            next.touches = paper.signals.compactMap { s in
                guard s.status == "scored", let at = s.observedMs, WidgetSnapshot.beijingDay(Date(timeIntervalSince1970: at / 1000)) == today else { return nil }
                return .init(symbol: s.symbol, side: s.side, score: s.score, at: at)
            }.sorted { $0.at > $1.at }
        }
        if let state: State = try? await get("/v1/state", token: token, account: old.account) {
            next.positions = state.positions.map { .init(symbol: $0.symbol, side: $0.side, leverage: $0.leverage, pnl: Double($0.pnl) ?? 0) }
        }
        guard next != old else { return nil }
        next.updated = Date().timeIntervalSince1970
        return next
    }
}
