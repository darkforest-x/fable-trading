import Foundation

struct AlertPrefs: Equatable {
    var popup = true
    var sound = true
    var fib = true
    var monthFirstWeekTouch = true
}

/// Which notifications deserve an in-app alert (port of monitorFormat.freshAlerts, same tests).
/// The first snapshot after connecting only seeds `seen`; afterwards at most one alert per batch.
enum AlertLogic {
    static let fibWindowMs: Double = 20 * 60 * 1000
    static let monthTouchWindowMs: Double = 90 * 1000

    static func fresh(_ events: [NotificationEvent], seen: Set<String>?, now: Double, prefs: AlertPrefs) -> (alerts: [NotificationEvent], seen: Set<String>) {
        var next = seen ?? []
        var alerts: [NotificationEvent] = []
        for e in events where !next.contains(e.id) {
            next.insert(e.id)
            guard seen != nil else { continue }
            if e.kind == "alarm", prefs.popup, e.active == true, e.acknowledged != true { alerts.append(e) }
            if e.kind == "fib", prefs.fib, e.pushed, let at = e.atMs, now - at <= fibWindowMs { alerts.append(e) }
            if e.isLiveMonthTouch, prefs.monthFirstWeekTouch, let until = e.freshUntilMs,
               now >= (e.occurredMs ?? .greatestFiniteMagnitude), now <= until,
               until - (e.occurredMs ?? until) <= monthTouchWindowMs { alerts.append(e) }
        }
        if let alarm = alerts.first(where: { $0.kind == "alarm" }) { return ([alarm], next) }
        if let monthTouch = alerts.first(where: { $0.kind == "month_first_week_touch" }) { return ([monthTouch], next) }
        if let fib = alerts.first(where: { $0.kind == "fib" }) { return ([fib], next) }
        return ([], next)
    }
}
