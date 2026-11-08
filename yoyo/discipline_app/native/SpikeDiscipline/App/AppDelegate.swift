import SwiftUI
import UIKit

/// UIKit hooks SwiftUI does not expose: per-page orientation (owner 2026-11-08 item 6 — only the
/// 1.618 / 合约 charts may turn landscape) and the APNs device token (item 4, native push).
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, supportedInterfaceOrientationsFor window: UIWindow?) -> UIInterfaceOrientationMask {
        Orientation.allowed
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in PushRegistry.shared.received(token: hex) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in PushRegistry.shared.failed(error.localizedDescription) }
    }
}

/// Orientation lock: portrait everywhere except the chart pages.
@MainActor
enum Orientation {
    nonisolated(unsafe) static var allowed: UIInterfaceOrientationMask = .portrait

    private static var scene: UIWindowScene? {
        UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first { $0.activationState == .foregroundActive }
            ?? UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
    }

    /// Allow landscape on a chart page; leaving one turns the phone UI back to portrait.
    static func allowLandscape(_ allow: Bool) {
        let next: UIInterfaceOrientationMask = allow ? .allButUpsideDown : .portrait
        guard next != allowed else { return }
        allowed = next
        guard let scene else { return }
        if !allow { scene.requestGeometryUpdate(.iOS(interfaceOrientations: .portrait)) }
        scene.windows.forEach { $0.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations() }
    }

    /// Rotate button (works with the system rotation lock on).
    static func rotate(landscape: Bool) {
        guard let scene else { return }
        if landscape { allowed = .allButUpsideDown }
        scene.windows.forEach { $0.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations() }
        scene.requestGeometryUpdate(.iOS(interfaceOrientations: landscape ? .landscapeRight : .portrait))
    }
}
