import UIKit

/// App-wide haptics (owner 2026-11-07 “震动反馈怎么都没了”): taps, page and segment changes,
/// toggles, refresh and alarms. One switch in 设置 turns them off, as in the 1.x app.
enum Haptics {
    static var enabled: Bool {
        get { UserDefaults.standard.object(forKey: "haptics.enabled") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "haptics.enabled") }
    }
    private static let light = UIImpactFeedbackGenerator(style: .light)
    private static let medium = UIImpactFeedbackGenerator(style: .medium)
    private static let soft = UIImpactFeedbackGenerator(style: .soft)
    private static let selection = UISelectionFeedbackGenerator()
    private static let notice = UINotificationFeedbackGenerator()

    static func tap() { guard enabled else { return }; light.impactOccurred() }
    static func press() { guard enabled else { return }; soft.impactOccurred(intensity: 0.8) }
    static func open() { guard enabled else { return }; medium.impactOccurred() }
    static func select() { guard enabled else { return }; selection.selectionChanged() }
    static func success() { guard enabled else { return }; notice.notificationOccurred(.success) }
    static func warning() { guard enabled else { return }; notice.notificationOccurred(.warning) }
    static func alarm() { guard enabled else { return }; notice.notificationOccurred(.error) }
}
