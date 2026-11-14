import Observation

struct MonthlyTouchRoute: Equatable, Identifiable {
    let id: Int
    let eventId: String
    let symbol: String
    let month: String
    let side: Edge?
    let occurredMs: Double?
}

struct TrendlineEventRoute: Equatable, Identifiable {
    let id: Int
    let event: TrendlineBreakEvent
    var symbol: String { event.symbol }
}

/// A monthly touch opens the existing 1.618 page in an event replay mode.
@MainActor @Observable
final class FibNavigation {
    private(set) var monthlyTouch: MonthlyTouchRoute?
    private(set) var trendlineEvent: TrendlineEventRoute?
    private var nextID = 0

    func openMonthlyTouch(eventId: String, symbol: String, month: String, side: Edge? = nil, occurredMs: Double? = nil) {
        guard !eventId.isEmpty, !symbol.isEmpty else { return }
        nextID += 1
        trendlineEvent = nil
        monthlyTouch = MonthlyTouchRoute(id: nextID, eventId: eventId, symbol: symbol, month: month,
                                         side: side, occurredMs: occurredMs)
    }

    func openTrendlineEvent(_ event: TrendlineBreakEvent) {
        nextID += 1
        monthlyTouch = nil
        trendlineEvent = TrendlineEventRoute(id: nextID, event: event)
    }

    func showLiveChart() { monthlyTouch = nil; trendlineEvent = nil }
}
