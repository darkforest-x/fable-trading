import AudioToolbox
import Observation
import SwiftUI

/// Foreground watcher (owner 2026-11-06): polls the read-only feed every 8 s while the app is active,
/// raises one in-app banner per new market alarm or fresh 1.618 push. Background pushes stay Bark.
@MainActor @Observable
final class NotificationStore {
    var data: NotificationSnapshot?
    var error: String?
    var banner: NotificationEvent?
    var prefs = AlertPrefs() { didSet { savePrefs() } }
    private(set) var openedAt: Double = UserDefaults.standard.double(forKey: "notify.openedAt")
    private var seen: Set<String>? = (UserDefaults.standard.array(forKey: "notify.seen") as? [String]).map(Set.init)

    init() {
        let d = UserDefaults.standard
        if d.object(forKey: "notify.popup") != nil {
            prefs = AlertPrefs(popup: d.bool(forKey: "notify.popup"), sound: d.bool(forKey: "notify.sound"),
                               fib: d.bool(forKey: "notify.fib"),
                               monthFirstWeekTouch: d.object(forKey: "notify.monthFirstWeekTouch") as? Bool ?? true)
        }
    }

    var unread: Int { data?.events.filter { ($0.pushed || $0.kind == "month_first_week_touch") && ($0.atMs ?? 0) > openedAt }.count ?? 0 }

    func poll(connection: Connection, session: SessionStore) async {
        while !Task.isCancelled {
            await load(connection: connection, session: session)
            try? await Task.sleep(for: .seconds(8))
        }
    }

    func load(connection: Connection, session: SessionStore) async {
        do {
            let next: NotificationSnapshot
            do {
                next = try await API.get("/v1/notifications", connection: connection)
            } catch let failure as APIError where failure.status == 404 {
                // A native App may be newer than its discipline service. Keep existing notices
                // working until the additive unified inbox route has been deployed.
                next = try await API.get("/v1/monitor/notifications", connection: connection)
            }
            let first = seen == nil
            let result = AlertLogic.fresh(next.events, seen: seen, now: next.nowMs, prefs: prefs)
            seen = result.seen
            UserDefaults.standard.set(Array(result.seen.suffix(300)), forKey: "notify.seen")
            if first { markRead(at: next.nowMs) }
            data = next; error = nil
            if let alert = result.alerts.first { raise(alert) }
        } catch {
            if !(error is CancellationError) { self.error = error.localizedDescription; session.handle(error) }
        }
    }

    func markRead(at time: Double? = nil) {
        openedAt = time ?? data?.nowMs ?? Date().timeIntervalSince1970 * 1000
        UserDefaults.standard.set(openedAt, forKey: "notify.openedAt")
    }

    private func raise(_ event: NotificationEvent) {
        withAnimation(.spring(response: 0.45, dampingFraction: 0.78)) { banner = event }
        if event.kind == "alarm" { Haptics.alarm() } else { Haptics.warning() }
        if prefs.sound { AudioServicesPlaySystemSound(event.kind == "alarm" ? 1005 : 1057) }
        if event.kind != "alarm" {
            let id = event.id
            Task { try? await Task.sleep(for: .seconds(7)); if banner?.id == id { withAnimation(.easeOut(duration: 0.3)) { banner = nil } } }
        }
    }

    private func savePrefs() {
        let d = UserDefaults.standard
        d.set(prefs.popup, forKey: "notify.popup"); d.set(prefs.sound, forKey: "notify.sound"); d.set(prefs.fib, forKey: "notify.fib")
        d.set(prefs.monthFirstWeekTouch, forKey: "notify.monthFirstWeekTouch")
    }
}
