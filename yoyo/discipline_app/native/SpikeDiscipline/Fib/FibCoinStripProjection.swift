import Foundation

enum FibCoinStripSource: String, CaseIterable, Identifiable {
    case monday
    case monthFirstWeek

    var id: String { rawValue }
    var title: String { self == .monday ? "周一" : "月首周" }
    var next: Self { self == .monday ? .monthFirstWeek : .monday }
}

enum FibCoinStripProjection {
    /// Month-first-week chips stay one per symbol, newest touch first, and never duplicate pinned coins.
    static func monthlyTouches(_ events: [MonthFirstWeekTouch], month: String?, excluding pinned: Set<String>) -> [MonthFirstWeekTouch] {
        guard let month else { return [] }
        var latestBySymbol: [String: MonthFirstWeekTouch] = [:]
        for event in events where event.month == month && !pinned.contains(event.symbol) {
            guard let previous = latestBySymbol[event.symbol] else {
                latestBySymbol[event.symbol] = event
                continue
            }
            if event.occurredMs > previous.occurredMs
                || (event.occurredMs == previous.occurredMs && event.eventId < previous.eventId) {
                latestBySymbol[event.symbol] = event
            }
        }
        return latestBySymbol.values.sorted {
            $0.occurredMs == $1.occurredMs ? $0.symbol < $1.symbol : $0.occurredMs > $1.occurredMs
        }
    }

    /// The source toggle only adds monthly overlays in the monthly signal mode; the Monday range stays
    /// in CandleChart's existing `range` input and is therefore retained alongside these four lines.
    static func monthlyLines(source: FibCoinStripSource, ranges: [MonthFirstWeekRange], bars: [Candle], period: CandlePeriod) -> [ChartLine] {
        guard source == .monthFirstWeek else { return [] }
        return MonthFirstWeekChartProjection.lines(ranges: ranges, bars: bars, period: period)
    }
}
