import Foundation

enum FibCoinStripSource: String, CaseIterable, Identifiable {
    case monday
    case monthFirstWeek

    var id: String { rawValue }
    var title: String { self == .monday ? "周一" : "月首周" }
    var queryValue: String { self == .monday ? "week" : "month" }
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

    /// The live chart's selected month row is keyed by the current-month event, not the weekly row
    /// that happens to be displayed behind its monthly overlays. A confirmed first touch stays
    /// eligible for a later manual quick entry after its notification window has passed.
    static func monthlyQuickEntry(events: [MonthFirstWeekTouch], month: String?, symbol: String,
                                  nowMs: Double) -> FibQuickEntry? {
        guard let month, !symbol.isEmpty, nowMs.isFinite,
              let event = latestTouch(events, month: month, symbol: symbol),
              event.touchConfirmed, event.occurredMs.isFinite, event.occurredMs <= nowMs else { return nil }
        return FibQuickEntry(symbol: event.symbol, side: event.side == .upper ? "short" : "long",
                             source: .monthFirstWeek, periodKey: event.month)
    }

    static func mondayQuickEntry(row: FibContract?, week: String?, live: Bool) -> FibQuickEntry? {
        guard live, let row, let touch = row.latestTouch, let week else { return nil }
        return FibQuickEntry(symbol: row.symbol, side: touch.side == .upper ? "short" : "long",
                             source: .monday, periodKey: week)
    }

    static func latestTouch(_ events: [MonthFirstWeekTouch], month: String?, symbol: String) -> MonthFirstWeekTouch? {
        guard let month else { return nil }
        return events.filter { $0.month == month && $0.symbol == symbol }.max {
            $0.occurredMs == $1.occurredMs ? $0.eventId > $1.eventId : $0.occurredMs < $1.occurredMs
        }
    }

    /// The source toggle only adds monthly overlays in the monthly signal mode; the Monday range stays
    /// in CandleChart's existing `range` input and is therefore retained alongside these four lines.
    static func monthlyLines(source: FibCoinStripSource, ranges: [MonthFirstWeekRange], bars: [Candle], period: CandlePeriod) -> [ChartLine] {
        guard source == .monthFirstWeek else { return [] }
        return MonthFirstWeekChartProjection.lines(ranges: ranges, bars: bars, period: period)
    }
}

struct FibQuickEntry: Equatable {
    let symbol: String
    let side: String
    let source: FibCoinStripSource
    let periodKey: String
}
