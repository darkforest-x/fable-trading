import Observation
import SwiftUI

/// Complete, read-only monthly 1.618 snapshot from `/v1/monitor/month-first-week-1618`.
/// Keeps month/window/coverage and worker health beside the durable touch rows.
@MainActor @Observable
final class MonthFirstWeekFeed {
    private(set) var snapshot: MonthFirstWeekSnapshot?
    private(set) var error: String?
    private(set) var loaded = false
    private(set) var lastUpdated = Date.distantPast

    var events: [MonthFirstWeekTouch] { snapshot?.touches ?? [] }

    func watch(connection: Connection, session: SessionStore) async {
        while !Task.isCancelled {
            await load(connection: connection, session: session)
            try? await Task.sleep(for: .seconds(10))
        }
    }

    func load(connection: Connection, session: SessionStore) async {
        do {
            let next: MonthFirstWeekSnapshot = try await API.get("/v1/monitor/month-first-week-1618", connection: connection)
            guard next.schema == "month_first_week_1618.v1", next.month == next.window.month else {
                throw APIError(status: 502, message: "月首周 1.618 快照版本不匹配")
            }
            snapshot = next
            error = nil
            loaded = true
            lastUpdated = Date()
        } catch is CancellationError {
            return
        } catch {
            self.error = error.localizedDescription
            session.handle(error)
        }
    }
}
