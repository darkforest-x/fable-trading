import Observation
import SwiftUI
import UserNotifications

/// Native push registration (owner 2026-11-08 item 4 “后台提醒还靠 Bark” → 原生推送).
/// Asks for permission once, registers this phone's APNs token with the discipline service, and keeps
/// the 1.618 / 报警 choices of 设置 · 通知 in sync. The service sends nothing until the owner adds an
/// APNs key on the Mac; `serverConfigured` reports that honestly. In the foreground the in-app banner
/// already shows the event, so the system banner is suppressed; tapping a notification opens its page.
@MainActor @Observable
final class PushRegistry: NSObject, UNUserNotificationCenterDelegate {
    static let shared = PushRegistry()

    private(set) var token: String?
    private(set) var authorized: Bool?
    private(set) var serverConfigured: Bool?
    /// The service predates the push routes (needs the owner-approved service restart).
    private(set) var serverOutdated = false
    private(set) var error: String?
    /// A tapped notification asks the root view to open this page / coin.
    var openRequest: (kind: String, symbol: String?)?

    private var connection: Connection?
    private var prefs = (fib: true, alarm: true)

    /// Debug device builds are signed for the APNs sandbox; release builds for production.
    nonisolated static var environment: String {
        #if DEBUG
        "development"
        #else
        "production"
        #endif
    }

    /// False when the build has no APNs entitlement (personal signing team; see tools/genpbx.py PUSH).
    nonisolated static var available: Bool {
        #if APNS
        true
        #else
        false
        #endif
    }

    func start(connection: Connection?, fib: Bool, alarm: Bool) async {
        self.connection = connection
        prefs = (fib, alarm)
        guard Self.available else { return }
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .notDetermined: authorized = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        case .authorized, .provisional, .ephemeral: authorized = true
        default: authorized = false
        }
        if authorized == true { UIApplication.shared.registerForRemoteNotifications() }
        if token != nil { await upload() } else { await refreshStatus() }
    }

    func received(token: String) {
        self.token = token
        error = nil
        Task { await upload() }
    }

    func failed(_ message: String) { error = message }

    func update(fib: Bool, alarm: Bool) {
        prefs = (fib, alarm)
        guard Self.available else { return }
        Task { await upload() }
    }

    private func upload() async {
        guard let token, let connection else { return }
        do {
            let status: PushStatus = try await API.post("/v1/push/device", body: [
                "token": token, "environment": Self.environment, "fib": prefs.fib, "alarm": prefs.alarm], connection: connection)
            serverConfigured = status.configured
        } catch let failure as APIError where failure.status == 404 {
            serverOutdated = true   // service not updated yet
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func refreshStatus() async {
        guard let connection else { return }
        do {
            let status: PushStatus = try await API.get("/v1/push/status", connection: connection)
            serverConfigured = status.configured
        } catch let failure as APIError where failure.status == 404 {
            serverOutdated = true
        } catch {}
    }

    /// Follow one open position in a Live Activity (server pushes its updates when configured).
    func follow(activityToken: String, positionId: String) async {
        guard let connection else { return }
        let _: PushStatus? = try? await API.post("/v1/push/activity", body: [
            "token": activityToken, "environment": Self.environment, "position_id": positionId], connection: connection)
    }

    // MARK: UNUserNotificationCenterDelegate

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        []   // the in-app banner (NotificationStore) already shows it
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        let kind = info["kind"] as? String ?? "fib", symbol = info["symbol"] as? String
        await MainActor.run { openRequest = (kind, symbol) }
    }
}

struct PushStatus: Codable { let configured: Bool; let devices: Int; let activities: Int }
