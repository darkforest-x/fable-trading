import Observation
import SwiftUI

/// The authenticated monitor projection of the existing valid 1H-close / 4H-resistance events.
/// This feed does not calculate breakouts; it only displays the worker's verified upbreak rows.
struct TrendlineBreakEvent: Decodable, Equatable, Hashable, Identifiable {
    let eventId: String
    let symbol: String
    let confirmedMs: Double
    let candleMs: Double
    let level: Double
    let close: Double
    let aMs: Double
    let aPrice: Double
    let bMs: Double
    let bPrice: Double
    let validitySchema: Int
    let validityStatus: String

    var id: String { eventId }
    var risePct: Double { (close / level - 1) * 100 }
    var isValidUpbreak: Bool {
        [confirmedMs, candleMs, level, close, aMs, aPrice, bMs, bPrice].allSatisfy(\.isFinite)
            && level > 0 && close > level && aPrice > bPrice && aMs < bMs && bMs < candleMs
            && confirmedMs == candleMs + 3_600_000 && validitySchema == 2 && validityStatus == "valid"
    }

    func chartLine(endingAt endMs: Double, startingAt startMs: Double? = nil) -> ChartLine? {
        guard isValidUpbreak, endMs.isFinite, endMs > aMs,
              let visibleStart = startMs, visibleStart.isFinite else { return nil }
        let clippedStart = max(aMs, visibleStart)
        let clippedEnd = min(candleMs, endMs)
        guard clippedEnd > clippedStart else { return nil }
        let slope = (bPrice - aPrice) / (bMs - aMs)
        let startPrice = aPrice + slope * (clippedStart - aMs)
        let endPrice = aPrice + slope * (clippedEnd - aMs)
        guard startPrice.isFinite, endPrice.isFinite, startPrice > 0, endPrice > 0 else { return nil }
        return ChartLine(price: startPrice, title: "4H 趋势线", color: ChartPalette.down,
                         dashed: true, startTime: clippedStart / 1000, endTime: clippedEnd / 1000,
                         endPrice: endPrice, lineWidth: 1.7, fitsScale: false)
    }
}

struct TrendlineBreakCoverage: Decodable {
    let covered: Int?
    let symbolsTotal: Int?
}

struct TrendlineBreakSnapshot: Decodable {
    let items: [TrendlineBreakEvent]
    let total: Int
    let hasMore: Bool
    let state: String
    let coverage: TrendlineBreakCoverage
    let asofMs: Double?
}

struct TrendlineCoordinates: Decodable, Equatable {
    let aMs: Double
    let aPrice: Double
    let bMs: Double
    let bPrice: Double
    let knownMs: Double
    let bornMs: Double?
    let retiredMs: Double?
    let asofMs: Double

    var valid: Bool {
        [aMs, aPrice, bMs, bPrice, knownMs, asofMs].allSatisfy(\.isFinite)
            && aMs < bMs && aPrice > bPrice && aPrice > 0 && bPrice > 0 && knownMs <= asofMs
    }

    func chartLine(endingAt endMs: Double, title: String = "4H 趋势线") -> ChartLine? {
        guard valid, endMs.isFinite, endMs > aMs else { return nil }
        let slope = (bPrice - aPrice) / (bMs - aMs)
        let endPrice = bPrice + slope * (endMs - bMs)
        guard endPrice.isFinite, endPrice > 0 else { return nil }
        return ChartLine(price: aPrice, title: title, color: ChartPalette.down, dashed: true,
                         startTime: aMs / 1000, endTime: endMs / 1000,
                         endPrice: endPrice, lineWidth: 1.7, fitsScale: false)
    }

    func wasActive(at timeMs: Double) -> Bool {
        guard valid, timeMs.isFinite, knownMs <= timeMs,
              (bornMs.map { $0 <= timeMs } ?? true) else { return false }
        return retiredMs.map { timeMs < $0 } ?? true
    }
}

struct TrendlineLineSnapshot: Decodable {
    let schema: String
    let symbol: String
    let status: String
    let reason: String?
    let line: TrendlineCoordinates?
    let asofMs: Double?
}

@MainActor @Observable
final class TrendlineBreakFeed {
    private(set) var items: [TrendlineBreakEvent] = []
    private(set) var snapshot: TrendlineBreakSnapshot?
    private(set) var error: String?
    private(set) var loaded = false
    private(set) var updatedAt: Date?

    func watch(connection: Connection, session: SessionStore) async {
        while !Task.isCancelled {
            do {
                let next: TrendlineBreakSnapshot = try await API.get("/v1/monitor/trendline-breaks", connection: connection)
                guard next.items.allSatisfy(\.isValidUpbreak) else {
                    throw APIError(status: 0, message: "趋势突破记录校验失败")
                }
                guard !Task.isCancelled else { return }
                snapshot = next
                items = next.items
                error = nil
                loaded = true
                updatedAt = Date()
            } catch is CancellationError {
                return
            } catch {
                self.error = error.localizedDescription
                session.handle(error)
            }
            try? await Task.sleep(for: .seconds(15))
        }
    }
}

/// Per-symbol active 4H line. A ready response with `line == nil` means there is no current candidate;
/// an unavailable response or request error stays distinct and is shown as unavailable in the chart.
@MainActor @Observable
final class TrendlineLineFeed {
    private(set) var snapshot: TrendlineLineSnapshot?
    private(set) var error: String?

    func watch(symbol: String, connection: Connection, session: SessionStore) async {
        guard symbol.range(of: #"^[A-Z0-9]{1,32}-USDT-SWAP$"#, options: .regularExpression) != nil else {
            snapshot = nil
            error = "合约代码无效"
            return
        }
        while !Task.isCancelled {
            do {
                let next: TrendlineLineSnapshot = try await API.get(
                    "/v1/monitor/trendline-line", query: [URLQueryItem(name: "symbol", value: symbol)], connection: connection)
                guard next.schema == "trendline_line.v1", next.symbol == symbol,
                      ["ready", "unavailable"].contains(next.status),
                      next.line?.valid != false, next.status != "unavailable" || next.line == nil else {
                    throw APIError(status: 0, message: "4H 趋势线数据校验失败")
                }
                guard !Task.isCancelled else { return }
                snapshot = next
                error = nil
            } catch is CancellationError {
                return
            } catch {
                snapshot = nil
                self.error = error.localizedDescription
                session.handle(error)
            }
            try? await Task.sleep(for: .seconds(30))
        }
    }

    func chartLine(endingAt endMs: Double, historicalTime: Double? = nil) -> ChartLine? {
        guard let snapshot, snapshot.status == "ready", let line = snapshot.line, line.valid else { return nil }
        let actualEndMs = historicalTime ?? endMs
        guard actualEndMs.isFinite, actualEndMs > line.aMs else { return nil }
        if let historicalTime, !line.wasActive(at: historicalTime) { return nil }
        return line.chartLine(endingAt: actualEndMs)
    }

    var availabilityLabel: String {
        if error != nil || snapshot?.status == "unavailable" { return "4H 趋势线暂不可用" }
        guard let snapshot, snapshot.status == "ready" else { return "正在读取 4H 趋势线" }
        return snapshot.line == nil ? "当前无有效的 4H 下行趋势线" : "当前 4H 下行趋势线"
    }
}
