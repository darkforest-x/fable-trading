import XCTest
@testable import SpikeDiscipline

/// Same cases as tests/discipline_app/monitor_data.test.ts (web freshAlerts), now for the native port.
final class AlertLogicTests: XCTestCase {
    private let now: Double = 1_000_000_000
    private func fib(_ id: String, at: Double, pushed: Bool = true) -> NotificationEvent {
        NotificationEvent(id: id, kind: "fib", side: .lower, atMs: at, pushed: pushed, symbol: "SUI-USDT-SWAP", level: nil, price: nil,
                          status: pushed ? "pushed" : "below_threshold", n: nil, symbols: nil, active: nil, test: nil, acknowledged: nil)
    }
    private func alarm(_ id: String, active: Bool = true, acknowledged: Bool = false) -> NotificationEvent {
        NotificationEvent(id: id, kind: "alarm", side: .upper, atMs: now, pushed: true, symbol: nil, level: nil, price: nil,
                          status: nil, n: 29, symbols: [], active: active, test: false, acknowledged: acknowledged)
    }

    private func monthTouch(_ id: String, source: String = "live", freshUntil: Double? = nil,
                            touchConfirmed: Bool = true, candleConfirmed: Bool = false) -> NotificationEvent {
        NotificationEvent(id: id, kind: "month_first_week_touch", side: .upper, atMs: now, pushed: false,
                          symbol: "BTC-USDT-SWAP", level: 100, price: 101, status: "not_subscribed",
                          n: nil, symbols: nil, active: nil, test: nil, acknowledged: nil,
                          type: "month_first_week_touch", eventId: "2026-11|BTC-USDT-SWAP|upper", month: "2026-11",
                          occurredMs: now, candleMs: now, createdMs: now, expiresMs: now + 1_000_000,
                          freshUntilMs: freshUntil ?? now + 90_000, source: source,
                          touchConfirmed: touchConfirmed, candleConfirmed: candleConfirmed, apnsStatus: "not_subscribed")
    }

    func testMonthCloseIsIndependentFromItsTouchAndUsesCloseTime() throws {
        func event(_ side: Edge = .upper, close: Double = 99, source: String = "live") throws -> NotificationEvent {
            let json = """
            {"id":"reclaim","kind":"month_first_week_1h_reclaim","symbol":"SOL-USDT-SWAP","side":"\(side.rawValue)","pushed":false,"source":"\(source)",
             "at_ms":\(now),"close_ms":\(now),"close":\(close),"level":100,"fresh_until_ms":\(now+90000),
             "occurred_ms":\(now-1200000),"touch_event_id":"2026-11|SOL-USDT-SWAP|\(side.rawValue)"}
            """
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(NotificationEvent.self, from: Data(json.utf8))
        }
        let record = try event()
        func ids(_ rows: [NotificationEvent], at: Double, seen: Set<String>? = ["touch"]) -> [String] {
            AlertLogic.fresh(rows, seen: seen, now: at, prefs: AlertPrefs()).alerts.map(\.id)
        }
        XCTAssertEqual(ids([record], at: now), ["reclaim"])
        XCTAssertTrue(try event(.lower, close: 101).isLiveReclaim)
        XCTAssertFalse(try event(.lower, close: 100).isLiveReclaim)
        XCTAssertTrue(ids([try event(close: 101), try event(source: "history")], at: now).isEmpty)
        XCTAssertTrue(ids([record], at: now - 1).isEmpty)
        XCTAssertTrue(ids([record], at: now + 90001).isEmpty)
        XCTAssertTrue(ids([record], at: now, seen: nil).isEmpty)
        XCTAssertTrue(ids([record], at: now, seen: ["reclaim"]).isEmpty)
        XCTAssertTrue(AlertLogic.fresh([record], seen: [], now: now, prefs: AlertPrefs(monthFirstWeekTouch: false)).alerts.isEmpty)
    }

    func testFirstSnapshotOnlySeeds() {
        let seeded = AlertLogic.fresh([alarm("alarm:1"), fib("fib:a", at: now)], seen: nil, now: now, prefs: AlertPrefs())
        XCTAssertTrue(seeded.alerts.isEmpty)
        XCTAssertEqual(seeded.seen, ["alarm:1", "fib:a"])
    }

    func testOneAlertPerBatchAlarmFirst() {
        let seen: Set<String> = ["alarm:1", "fib:a"]
        let next = AlertLogic.fresh([fib("fib:b", at: now), alarm("alarm:2"), alarm("alarm:1")], seen: seen, now: now, prefs: AlertPrefs())
        XCTAssertEqual(next.alerts.map(\.id), ["alarm:2"])
        XCTAssertEqual(AlertLogic.fresh([fib("fib:d", at: now)], seen: next.seen, now: now, prefs: AlertPrefs()).alerts.map(\.id), ["fib:d"])
    }

    func testStaleUnpushedAcknowledgedAndDisabledNeverRing() {
        let seen: Set<String> = []
        XCTAssertTrue(AlertLogic.fresh([fib("fib:e", at: now - AlertLogic.fibWindowMs - 1)], seen: seen, now: now, prefs: AlertPrefs()).alerts.isEmpty)
        XCTAssertTrue(AlertLogic.fresh([fib("fib:f", at: now, pushed: false)], seen: seen, now: now, prefs: AlertPrefs()).alerts.isEmpty)
        XCTAssertTrue(AlertLogic.fresh([alarm("alarm:3", acknowledged: true), alarm("alarm:4", active: false)], seen: seen, now: now, prefs: AlertPrefs()).alerts.isEmpty)
        let off = AlertPrefs(popup: false, sound: true, fib: false)
        XCTAssertTrue(AlertLogic.fresh([alarm("alarm:5"), fib("fib:g", at: now)], seen: seen, now: now, prefs: off).alerts.isEmpty)
    }

    func testMonthTouchForegroundAlertIsIndependentOfApnsReceiptAndRequiresLiveFreshFact() {
        let seen: Set<String> = []
        let touch = monthTouch("month:live")
        XCTAssertTrue(touch.isLiveMonthTouch)
        XCTAssertFalse(monthTouch("month:history", source: "history").isLiveMonthTouch)
        XCTAssertEqual(AlertLogic.fresh([touch], seen: seen, now: now, prefs: AlertPrefs()).alerts.map(\.id), ["month:live"])
        XCTAssertTrue(AlertLogic.fresh([monthTouch("month:history", source: "history")], seen: seen, now: now, prefs: AlertPrefs()).alerts.isEmpty)
        XCTAssertTrue(AlertLogic.fresh([monthTouch("month:uncertain", touchConfirmed: false)], seen: seen, now: now, prefs: AlertPrefs()).alerts.isEmpty)
        XCTAssertTrue(AlertLogic.fresh([monthTouch("month:stale", freshUntil: now - 1)], seen: seen, now: now, prefs: AlertPrefs()).alerts.isEmpty)
        let off = AlertPrefs(popup: true, sound: true, fib: true, monthFirstWeekTouch: false)
        XCTAssertTrue(AlertLogic.fresh([monthTouch("month:off")], seen: seen, now: now, prefs: off).alerts.isEmpty)
    }
}
