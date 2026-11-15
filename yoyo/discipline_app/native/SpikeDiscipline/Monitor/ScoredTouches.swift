import Foundation
import Observation

struct ScoredTouch: Decodable, Equatable, Identifiable {
    let id: Int
    let week: String
    let symbol: String
    let side: Edge
    let candleMs: Double
    let score: Double

    var direction: Side { side == .lower ? .long : .short }

    /// Filter the stored precision before formatting badges. Equal ranks use event time, then ledger ID.
    static func visible(_ rows: [Self], side: String, sort: String, quotes: [String: Quote]) -> [Self] {
        let matches = rows.filter { $0.score >= 0.30 && (side == "all" || $0.direction.rawValue == side) }
        let volumes = sort == "volume" ? quotes.compactMapValues { Trading.turnover($0) } : [:]
        return matches.sorted { a, b in
            switch sort {
            case "earliest":
                return (a.candleMs, a.id) < (b.candleMs, b.id)
            case "score" where a.score != b.score:
                return a.score > b.score
            case "volume":
                let left = volumes[a.symbol] ?? -1, right = volumes[b.symbol] ?? -1
                if left != right { return left > right }
            default: break
            }
            return (a.candleMs, a.id) > (b.candleMs, b.id)
        }
    }
}

struct ScoredTouchPage: Decodable {
    struct Today: Decodable { let rows: [ScoredTouch] }
    struct History: Decodable {
        let offset: Int
        let total: Int
        let hasMore: Bool
        let rows: [ScoredTouch]
    }
    let nowMs: Double
    let dayStartMs: Double
    let today: Today
    let history: History
}

/// Build a whole, consistently paged archive before publishing it so score and volume sorting include older weeks.
@MainActor @Observable
final class ScoredTouches {
    private(set) var today: [ScoredTouch] = []
    private(set) var history: [ScoredTouch] = []
    private(set) var nowMs: Double = 0
    private(set) var error: String?
    var loaded: Bool { nowMs > 0 }
    private var lastArchiveRead = Date.distantPast
    private var dayStartMs: Double = 0

    func watch(connection: Connection, session: SessionStore) async {
        while !Task.isCancelled {
            do {
                try await refresh { offset in
                    try await API.get("/v1/monitor/scored-touches",
                                      query: [.init(name: "offset", value: String(offset)), .init(name: "limit", value: "100")],
                                      connection: connection)
                }
                error = nil
            } catch {
                guard !Task.isCancelled else { return }
                self.error = error.localizedDescription
                session.handle(error)
            }
            try? await Task.sleep(for: .seconds(30))
        }
    }

    func refresh(page: (Int) async throws -> ScoredTouchPage) async throws {
        let first = try await page(0)
        let head = first.history
        let needsArchive = dayStartMs != first.dayStartMs || history.count != head.total
            || !history.starts(with: head.rows) || Date().timeIntervalSince(lastArchiveRead) >= 300
        if needsArchive {
            var part = first
            var rows: [ScoredTouch] = []
            var ids = Set<Int>()
            while true {
                let slice = part.history
                guard part.dayStartMs == first.dayStartMs, slice.total == head.total, slice.offset == rows.count,
                      slice.rows.allSatisfy({ ids.insert($0.id).inserted }) else { throw archiveChanged }
                rows.append(contentsOf: slice.rows)
                if !slice.hasMore { break }
                guard !slice.rows.isEmpty, rows.count < head.total else { throw archiveChanged }
                try Task.checkCancellation()
                part = try await page(rows.count)
            }
            guard rows.count == head.total else { throw archiveChanged }
            try Task.checkCancellation()
            history = rows
            dayStartMs = first.dayStartMs
            lastArchiveRead = Date()
        }
        try Task.checkCancellation()
        today = first.today.rows
        nowMs = first.nowMs
    }

    private var archiveChanged: APIError { APIError(status: 409, message: "触价记录已更新，正在重新读取") }
}
