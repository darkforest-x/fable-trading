import Observation
import SwiftUI

/// Completed monthly first-week anchors returned by the read-only levels endpoint.
/// The range is valid from `readyMs` until `expiresMs`; `startMs` records the source month window.
struct MonthFirstWeekRange: Codable, Equatable, Identifiable {
    let month: String
    let startMs: Double
    let readyMs: Double
    let expiresMs: Double
    let high: Double
    let low: Double
    let upper: Double
    let lower: Double

    var id: String { month }

    func isValid() -> Bool {
        month.range(of: #"^\d{4}-(0[1-9]|1[0-2])$"#, options: .regularExpression) != nil
            && startMs.isFinite && readyMs.isFinite && expiresMs.isFinite
            && startMs < readyMs && readyMs < expiresMs
            && [high, low, upper, lower].allSatisfy(\.isFinite)
            && high > low && low > 0 && upper > high && lower < low
    }

    /// Four named levels match the web chart: thin first-week high/low and emphasized 1.618 extensions.
    /// The chart only includes levels overlapping the visible time window in its automatic price scale.
    var chartLines: [ChartLine] {
        let start = readyMs / 1000, end = expiresMs / 1000
        let label = "\(month) 首周"
        return [
            ChartLine(price: upper, title: "\(label) 上 1.618", color: MonthTouchStyle.tint,
                      startTime: start, endTime: end, lineWidth: 1.8),
            ChartLine(price: high, title: "\(label) 高", color: MonthTouchStyle.tint.opacity(0.52),
                      startTime: start, endTime: end, lineWidth: 0.8),
            ChartLine(price: low, title: "\(label) 低", color: MonthTouchStyle.tint.opacity(0.52),
                      startTime: start, endTime: end, lineWidth: 0.8),
            ChartLine(price: lower, title: "\(label) 下 1.618", color: MonthTouchStyle.tint,
                      startTime: start, endTime: end, lineWidth: 1.8),
        ].filter { $0.price > 0 }
    }
}

struct MonthFirstWeekLevelsSnapshot: Codable {
    let symbol: String
    let ranges: [MonthFirstWeekRange]

    func validate(symbol expected: String) throws {
        guard symbol == expected, ranges.allSatisfy({ $0.isValid() }),
              Set(ranges.map(\.month)).count == ranges.count else {
            throw APIError(status: 502, message: "月首周 1.618 区间数据格式异常")
        }
    }

    var oldestFirst: [MonthFirstWeekRange] { ranges.sorted { $0.startMs < $1.startMs } }
}

enum MonthFirstWeekChartProjection {
    static func lines(ranges: [MonthFirstWeekRange], bars: [Candle], period: CandlePeriod) -> [ChartLine] {
        guard let first = bars.first?.time, let last = bars.last?.time else { return [] }
        let end = last + period.seconds
        return ranges.filter { $0.expiresMs / 1000 > first && $0.readyMs / 1000 < end }
            .flatMap(\.chartLines)
    }
}

/// Cheap, read-only cache for monthly overlays on the ordinary 1.618 chart.
@MainActor @Observable
final class MonthFirstWeekLevels {
    private(set) var ranges: [String: [MonthFirstWeekRange]] = [:]
    private(set) var errors: [String: String] = [:]

    func watch(_ symbol: String, connection: Connection?) async {
        guard !symbol.isEmpty, let connection else { return }
        while !Task.isCancelled {
            await load(symbol, connection: connection)
            try? await Task.sleep(for: .seconds(60))
        }
    }

    func load(_ symbol: String, connection: Connection?) async {
        guard !symbol.isEmpty else { return }
        do {
            let next: MonthFirstWeekLevelsSnapshot = try await API.get(
                "/v1/monitor/month-first-week-1618/levels",
                query: [URLQueryItem(name: "symbol", value: symbol)], connection: connection)
            try next.validate(symbol: symbol)
            guard !Task.isCancelled else { return }
            ranges[symbol] = next.oldestFirst
            errors[symbol] = nil
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            errors[symbol] = error.localizedDescription
        }
    }

    func chartLines(symbol: String, bars: [Candle], period: CandlePeriod) -> [ChartLine] {
        MonthFirstWeekChartProjection.lines(ranges: ranges[symbol] ?? [], bars: bars, period: period)
    }
}
