import Foundation
import Observation

/// Public 1.618 monitor (/v1/market/monday-fib every 5 s while the page is visible), plus the local
/// list preferences (favorites; hiding expires at the NY week boundary). Mirrors FibHome.tsx,
/// fibData.ts and fibPreferences.ts. Never an order input: quick orders re-read the wick stop server-side.
@MainActor @Observable
final class FibStore {
    struct Preferences: Codable, Equatable {
        var favorites: [String] = []
        var hiddenWeek = ""
        var hidden: [String] = []
    }

    private(set) var snapshot: FibSnapshot?
    private(set) var fresh = false
    private(set) var error: String?
    private(set) var clock = Date()
    var symbol: String { didSet { save() } }
    var period: CandlePeriod { didSet { save() } }
    var showInternal: Bool { didSet { save() } }
    var showAverages: Bool { didSet { save() } }
    var showCloud: Bool { didSet { save() } }
    private(set) var preferences: Preferences

    private static let key = "fib.native.v1"
    private struct Saved: Codable {
        var symbol = ""; var period = "15m"; var inner = false; var averages = false; var cloud: Bool? = true
        var preferences = Preferences(); var snapshot: Data?
    }

    init() {
        let saved = UserDefaults.standard.data(forKey: Self.key).flatMap { try? JSONDecoder().decode(Saved.self, from: $0) } ?? Saved()
        symbol = saved.symbol
        period = CandlePeriod(rawValue: saved.period) ?? .m15
        showInternal = saved.inner
        showAverages = saved.averages
        showCloud = saved.cloud ?? true   // owner 2026-11-07: 1.618 shows the cloud band by default
        preferences = saved.preferences
        if let data = saved.snapshot, let cached = try? API.decoder.decode(FibSnapshot.self, from: data), Self.currentWeek(cached, now: Date()) {
            snapshot = cached
        }
    }

    private var lastRaw: Data?
    private func save() {
        let value = Saved(symbol: symbol, period: period.rawValue, inner: showInternal, averages: showAverages, cloud: showCloud, preferences: preferences, snapshot: lastRaw)
        if let data = try? JSONEncoder().encode(value) { UserDefaults.standard.set(data, forKey: Self.key) }
    }

    // MARK: polling

    func poll() async {
        while !Task.isCancelled {
            _ = try? await refresh()
            try? await Task.sleep(for: .seconds(5))
        }
        fresh = false
    }

    @discardableResult
    func refresh() async throws -> Bool {
        clock = Date()
        do {
            let (value, raw) = try await Self.fetch()
            guard Self.currentWeek(value, now: Date()) else { throw APIError(status: 0, message: "区间周次过期") }
            let previousWeek = snapshot?.week
            snapshot = value; lastRaw = raw; fresh = true; error = nil; clock = Date()
            let visible = rows
            // Keep a coin opened from 监控 even when it has no touch this week.
            if previousWeek != value.week || symbol.isEmpty { symbol = visible.first?.symbol ?? "" } else { save() }
            return true
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            fresh = false; self.error = "触价监控暂未连接"
            throw error
        }
    }

    private static func fetch() async throws -> (FibSnapshot, Data) {
        var request = URLRequest(url: AppConfig.serviceURL.appendingPathComponent("/v1/market/monday-fib"))
        request.timeoutInterval = 9
        request.cachePolicy = .reloadIgnoringLocalCacheData
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw APIError(status: (response as? HTTPURLResponse)?.statusCode ?? 0, message: "触价监控暂未连接") }
        return (try parse(data), data)
    }

    /// fibData.ts parseFibSnapshot: validated rows, newest touch first.
    static func parse(_ data: Data) throws -> FibSnapshot {
        let raw = try API.decoder.decode(FibSnapshot.self, from: data)
        guard raw.timezone == nil || raw.timezone == "America/New_York", raw.week.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil,
              raw.weekStartMs < raw.mondayEndMs, raw.mondayEndMs < raw.nextWeekMs else { throw APIError(status: 0, message: "监控数据格式异常") }
        let items = raw.items.filter { row in
            row.symbol.range(of: #"^[A-Z0-9]{1,40}-USDT-SWAP$"#, options: .regularExpression) != nil
                && [row.high, row.low, row.upper, row.lower].allSatisfy(\.isFinite) && row.high > row.low && row.low > 0
                && !row.touches.isEmpty
                && row.touches.allSatisfy { side, touch in side == touch.side.rawValue && ["live", "history"].contains(touch.source) && touch.occurredMs >= raw.weekStartMs && touch.occurredMs <= raw.nowMs }
        }
        .sorted { a, b in
            let x = a.latestTouch?.occurredMs ?? 0, y = b.latestTouch?.occurredMs ?? 0
            return x == y ? a.symbol < b.symbol : x > y
        }
        return FibSnapshot(nowMs: raw.nowMs, week: raw.week, timezone: raw.timezone, weekStartMs: raw.weekStartMs, mondayEndMs: raw.mondayEndMs, nextWeekMs: raw.nextWeekMs,
                           mondayComplete: raw.mondayComplete, connected: raw.connected, lastMessageMs: raw.lastMessageMs, items: items)
    }

    static func currentWeek(_ s: FibSnapshot, now: Date) -> Bool {
        let ms = now.timeIntervalSince1970 * 1000
        return ms >= s.weekStartMs && ms < s.nextWeekMs
    }

    // MARK: derived

    var current: FibSnapshot? { snapshot.flatMap { Self.currentWeek($0, now: max(clock, Date())) ? $0 : nil } }
    var rows: [FibContract] { current?.items.filter { visible($0.symbol) } ?? [] }
    /// Touch row for the charted coin, if it touched this week.
    var selected: FibContract? { current?.items.first { $0.symbol == chartSymbol } }
    /// The charted coin: the chosen one, else the newest touch.
    var chartSymbol: String { symbol.isEmpty ? (rows.first?.symbol ?? "") : symbol }

    /// Open any coin (monitor rows, alarm coins).
    func focus(_ next: String) { symbol = next }
    var live: Bool {
        guard fresh, error == nil, let s = current, s.connected, let last = s.lastMessageMs else { return false }
        return clock.timeIntervalSince1970 * 1000 - last < 90_000
    }

    func range(for row: FibContract) -> FibRange? {
        guard let s = snapshot else { return nil }
        return FibRange(high: row.high, low: row.low, upper: row.upper, lower: row.lower, start: s.mondayEndMs / 1000, end: s.nextWeekMs / 1000)
    }

    // MARK: preferences

    func visible(_ symbol: String) -> Bool { preferences.hiddenWeek != current?.week || !preferences.hidden.contains(symbol) }
    func isFavorite(_ symbol: String) -> Bool { preferences.favorites.contains(symbol) }
    func toggleFavorite(_ symbol: String) {
        if let i = preferences.favorites.firstIndex(of: symbol) { preferences.favorites.remove(at: i) } else { preferences.favorites.append(symbol) }
        save()
    }
    func hide(_ symbol: String) {
        let week = current?.week ?? ""
        if preferences.hiddenWeek != week { preferences.hiddenWeek = week; preferences.hidden = [] }
        if !preferences.hidden.contains(symbol) { preferences.hidden.append(symbol) }
        if self.symbol == symbol { self.symbol = rows.first?.symbol ?? "" }
        save()
    }
    func restore(_ symbol: String) {
        guard preferences.hiddenWeek == current?.week else { return }
        preferences.hidden.removeAll { $0 == symbol }
        save()
    }
}

/// Completed NY Monday range drawn from the Monday close to the next week (fibData.ts fibRangeFor).
struct FibRange: Equatable {
    let high: Double, low: Double, upper: Double, lower: Double
    let start: Double, end: Double

    struct Level: Identifiable { let price: Double; let title: String; let kind: Kind; var id: String { title } }
    enum Kind { case upper, lower, monday, inner, mid }

    /// Workbench replica: upper 1.618 red / lower green (2 px), Monday high/low blue; 内部线 adds 0.705 / 0.618 / 0.5.
    func levels(inner: Bool) -> [Level] {
        let r = high - low
        var rows: [Level] = [Level(price: upper, title: "上 1.618", kind: .upper), Level(price: high, title: "周一高", kind: .monday)]
        if inner {
            rows += [Level(price: low + 0.705 * r, title: "上 0.705", kind: .inner), Level(price: low + 0.618 * r, title: "上 0.618", kind: .inner),
                     Level(price: low + 0.5 * r, title: "中点 0.5", kind: .mid),
                     Level(price: high - 0.618 * r, title: "下 0.618", kind: .inner), Level(price: high - 0.705 * r, title: "下 0.705", kind: .inner)]
        }
        rows += [Level(price: low, title: "周一低", kind: .monday), Level(price: lower, title: "下 1.618", kind: .lower)]
        return rows.filter { $0.price > 0 }
    }
}
