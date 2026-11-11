import Observation
import SwiftUI

/// Event-specific monthly-touch data consumed by the existing 1.618 chart.
struct MonthFirstWeekChartRequest: Hashable, Identifiable {
    let eventId: String
    var id: String { "event:\(eventId)" }

    static func event(_ eventId: String) -> Self { Self(eventId: eventId) }
}

@MainActor @Observable
final class MonthFirstWeekChartFeed {
    private(set) var snapshot: MonthFirstWeekChartSnapshot?
    private(set) var loading = false
    private(set) var error: String?

    func load(request: MonthFirstWeekChartRequest, period: CandlePeriod, connection: Connection?) async {
        loading = true
        error = nil
        defer { loading = false }

        do {
            let query = [
                URLQueryItem(name: "event_id", value: request.eventId),
                URLQueryItem(name: "period", value: period.rawValue),
            ]
            let next: MonthFirstWeekChartSnapshot = try await API.get(
                "/v1/monitor/month-first-week-1618/chart", query: query, connection: connection)
            try next.validate(eventId: request.eventId, period: period)
            guard !Task.isCancelled else { return }
            snapshot = next
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            if let api = error as? APIError, api.status == 404 {
                self.error = "这条月首周触价记录暂时没有图表（404）"
            } else if let api = error as? APIError, api.status == 503 {
                self.error = "月首周 K 线服务暂不可用（503）"
            } else {
                self.error = error.localizedDescription
            }
        }
    }
}
