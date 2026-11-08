import ActivityKit
import Foundation
import WidgetKit

/// Feeds the widgets and the position Live Activity from what the app already reads (owner
/// 2026-11-08 items 4–5). Throttled: the snapshot is written at most every 20 s, timelines reload at
/// most every 5 minutes (or at once when the newest touch changes), the activity updates every 10 s.
@MainActor
enum WidgetBridge {
    private static var lastWrite = Date.distantPast
    private static var lastReload = Date.distantPast
    private static var snapshot = WidgetSnapshot.load() ?? WidgetSnapshot(updated: 0, touches: [], positions: [], threshold: 0.5)

    static func touches(_ signals: [PaperSignal]) {
        let today = WidgetSnapshot.beijingDay(Date())
        let rows = signals.compactMap { s -> WidgetSnapshot.Touch? in
            guard let at = s.observedMs, WidgetSnapshot.beijingDay(Date(timeIntervalSince1970: at / 1000)) == today,
                  s.status == "scored" else { return nil }
            return .init(symbol: s.symbol, side: s.side.rawValue, score: s.score, at: at)
        }.sorted { $0.at > $1.at }
        let changed = rows.first != snapshot.touches.first
        snapshot.touches = rows
        publish(force: changed)
    }

    static func state(_ state: AccountState) {
        snapshot.positions = state.positions.map { .init(symbol: $0.symbol, side: $0.side, leverage: $0.leverage, pnl: Double($0.pnl) ?? 0) }
        snapshot.account = state.accountId
        publish(force: false)
    }

    private static func publish(force: Bool) {
        snapshot.threshold = UserDefaults.standard.object(forKey: "touch.highScore") as? Double ?? 0.5
        guard force || Date().timeIntervalSince(lastWrite) > 20 else { return }
        snapshot.updated = Date().timeIntervalSince1970
        lastWrite = Date()
        snapshot.save()
        if force || Date().timeIntervalSince(lastReload) > 300 {
            lastReload = Date()
            WidgetCenter.shared.reloadAllTimelines()
        }
    }
}

/// One Live Activity for the newest open position (设置 · 锁屏实时活动, on by default). The app updates
/// it while running; its push token goes to the service so updates continue in the background once
/// the owner has configured APNs. Ends (stays 15 minutes) when the position closes.
@MainActor
enum PositionActivities {
    private static var lastUpdate = Date.distantPast
    private static var registered: Set<String> = []

    static var enabled: Bool { UserDefaults.standard.object(forKey: "activity.enabled") as? Bool ?? true }

    static func sync(_ state: AccountState) {
        let open = state.positions
        for activity in Activity<PositionActivityAttributes>.activities where !enabled || !open.contains(where: { $0.id == activity.attributes.positionId }) {
            let final = activity.content.state
            Task { await activity.end(.init(state: final, staleDate: nil), dismissalPolicy: enabled ? .after(Date().addingTimeInterval(900)) : .immediate) }
        }
        guard enabled, ActivityAuthorizationInfo().areActivitiesEnabled, let position = open.max(by: { ($0.opened ?? 0) < ($1.opened ?? 0) }) else { return }
        let content = ActivityContent(state: contentState(position, state), staleDate: Date().addingTimeInterval(180))
        if let current = Activity<PositionActivityAttributes>.activities.first(where: { $0.attributes.positionId == position.id }) {
            guard Date().timeIntervalSince(lastUpdate) > 10 else { return }
            lastUpdate = Date()
            Task { await current.update(content) }
            return
        }
        guard Activity<PositionActivityAttributes>.activities.isEmpty else { return }
        let decimals = Fmt.decimals(tick: state.instruments[position.symbol]?.tick)
        let attributes = PositionActivityAttributes(positionId: position.id, symbol: position.symbol, side: position.side,
                                                    leverage: position.leverage, entry: Double(position.entry) ?? 0, decimals: decimals)
        guard let activity = try? Activity.request(attributes: attributes, content: content, pushType: PushRegistry.available ? .token : nil) else { return }
        lastUpdate = Date()
        Task {
            for await token in activity.pushTokenUpdates {
                let hex = token.map { String(format: "%02x", $0) }.joined()
                guard !registered.contains(hex) else { continue }
                registered.insert(hex)
                await PushRegistry.shared.follow(activityToken: hex, positionId: position.id)
            }
        }
    }

    static func contentState(_ position: Position, _ state: AccountState) -> PositionActivityAttributes.ContentState {
        let pnl = Double(position.pnl) ?? 0, margin = Double(position.margin) ?? 0
        let mark = state.quotes[position.symbol].flatMap { Double($0.last) } ?? Double(position.entry) ?? 0
        return .init(mark: mark, pnl: pnl, roe: margin > 0 ? pnl / margin * 100 : nil, stop: Double(position.stop) ?? 0,
                     updated: Date().timeIntervalSince1970)
    }
}
