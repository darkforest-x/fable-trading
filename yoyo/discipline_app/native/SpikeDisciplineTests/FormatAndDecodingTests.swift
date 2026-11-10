import XCTest
@testable import SpikeDiscipline

final class FormatAndDecodingTests: XCTestCase {
    func testSignsPrecisionAndTone() {
        XCTAssertEqual(Fmt.signedPercent(1.603), "+1.60%")
        XCTAssertEqual(Fmt.signedPercent(-3.98), "−3.98%")
        XCTAssertEqual(Fmt.signedPercent(0.001), "0.00%")
        XCTAssertEqual(Fmt.signedPercent(nil), "—")
        XCTAssertEqual(Fmt.price(104_253.44), "104,253.4")
        XCTAssertEqual(Fmt.price(0.0012076838), "0.001208")
        XCTAssertEqual(Fmt.price(1.2), "1.2000")
        XCTAssertEqual(Fmt.coin("SOL-USDT-SWAP"), "SOL")
        XCTAssertEqual(Fmt.reason("failed_reclaim"), "确认未收回")
    }

    func testBeijingDayGrouping() {
        let now = 1_793_955_600_000.0 // 2026-11-06 17:00 Beijing
        XCTAssertEqual(Fmt.dayLabel(now - 3_600_000, now: now), "今天")
        XCTAssertEqual(Fmt.dayLabel(now - 86_400_000, now: now), "昨天")
        let groups = Fmt.groupByDay([now, now - 60_000, now - 86_400_000], now: now) { $0 }
        XCTAssertEqual(groups.map(\.label), ["今天", "昨天"])
        XCTAssertEqual(groups.map(\.rows.count), [2, 1])
    }

    /// Payload shapes exactly as monitor_feed.py projects them (nulls and integers included).
    func testDecodesServicePayloads() throws {
        let paper = #"{"now_ms":1793958680127,"paper_only":true,"equity_basis":"realized","accounts":[{"key":"live-allin3","label":"live-allin3","group":"live","equity":1016.03,"start_equity":1000.0,"return_pct":1.603,"started_ms":1793353880127,"stats":{"closed":12,"wins":7,"mean_net_bp":8.5}}],"detail":{"key":"live-allin3","open":[{"id":100,"symbol":"SOL-USDT-SWAP","side":"long","entry":1.2,"target":1.1,"stage":"open","funded":true,"notional":300.0,"fill_ms":1,"score":0.62,"mark":1.21,"unrealized_bp":35.0,"remaining_notional":300.0,"stop":1.44}],"closed":[]},"signals":[{"id":null,"symbol":"SUI-USDT-SWAP","side":"short","level":0.5,"score":null,"status":"scored","taken":false,"observed_ms":1}]}"#
        let p = try API.decoder.decode(PaperSnapshot.self, from: Data(paper.utf8))
        XCTAssertEqual(p.accounts.first?.stats.closed, 12)
        XCTAssertEqual(p.detail.open.first?.side, .long)
        XCTAssertNil(p.signals.first?.id)

        let notes = #"{"now_ms":10,"events":[{"id":"alarm:3","kind":"alarm","side":"lower","n":26,"symbols":["AAA-USDT-SWAP"],"at_ms":2,"first_ms":1,"active":false,"test":false,"acknowledged":true,"pushed":true},{"id":"fib:2026-11-02:AAA-USDT-SWAP:lower:1","kind":"fib","symbol":"AAA-USDT-SWAP","side":"lower","level":1.0,"price":0.99,"at_ms":5,"status":"pushed","pushed":true}],"channels":{"fib":{"bark":true,"bark_configured":true,"bark_sent":7,"telegram":true,"min_score":0.4},"alarm":{"bark":false,"telegram":true,"threshold":25,"window_minutes":5}}}"#
        let n = try API.decoder.decode(NotificationSnapshot.self, from: Data(notes.utf8))
        XCTAssertEqual(n.events.map(\.kind), ["alarm", "fib"])
        XCTAssertEqual(n.channels.fib.barkSent, 7)
        XCTAssertEqual(Fmt.pushStatus(n.events[1]), "已推送")

        let monthTouch = #"{"now_ms":1793958680127,"events":[{"id":"month_first_week_touch:2026-11|BTC-USDT-SWAP|upper","kind":"month_first_week_touch","type":"month_first_week_touch","event_id":"2026-11|BTC-USDT-SWAP|upper","month":"2026-11","symbol":"BTC-USDT-SWAP","side":"upper","level":100.0,"price":101.0,"at_ms":1793958600000,"occurred_ms":1793958600000,"fresh_until_ms":1793958690000,"source":"live","touch_confirmed":true,"candle_confirmed":false,"pushed":false,"apns_status":"not_subscribed"}],"channels":{"fib":{"bark":true,"bark_configured":true,"bark_sent":7,"telegram":true,"min_score":0.4},"alarm":{"bark":false,"telegram":true,"threshold":25,"window_minutes":5},"month_first_week_touch":{"bark":true,"bark_configured":true,"bark_sent":3,"apns_configured":false,"apns_status":{"counts":{"sent":1},"cursor":2}}}}"#
        let monthly = try API.decoder.decode(NotificationSnapshot.self, from: Data(monthTouch.utf8))
        XCTAssertEqual(monthly.events[0].kind, "month_first_week_touch")
        XCTAssertEqual(monthly.events[0].eventId, "2026-11|BTC-USDT-SWAP|upper")
        XCTAssertEqual(monthly.events[0].apnsStatus, "not_subscribed")
        XCTAssertTrue(monthly.channels.monthFirstWeekTouch?.bark == true)
        XCTAssertEqual(monthly.channels.monthFirstWeekTouch?.barkSent, 3)
        XCTAssertEqual(monthly.channels.monthFirstWeekTouch?.apnsStatus?.counts?["sent"], 1)

        let snapshot = #"{"schema":"month_first_week_1618.v1","now_ms":1793958680127,"timezone":"America/New_York","month":"2026-11","window":{"month":"2026-11","start_ms":1793505600000,"ready_ms":1793512800000,"expires_ms":1794103200000,"complete":true},"coverage":{"universe_size":240,"ready_symbols":239,"incomplete_symbols":1,"pending_symbols":0,"expected_1h_bars":12,"source":"okx"},"stream":{"connected":true,"last_message_ms":1793958680120,"error":null},"worker":{"alive":true,"last_cycle_ms":1793958680000,"error":null,"symbol_count":240,"ready_count":239,"incomplete_count":1,"pending_count":0},"items":[{"symbol":"BTC-USDT-SWAP","state":"ready","high":100,"low":90,"upper":106.18,"lower":83.82,"updated_ms":1793958680000}],"touches":[{"id":1,"event_id":"2026-11|BTC-USDT-SWAP|upper","type":"month_first_week_touch","month":"2026-11","symbol":"BTC-USDT-SWAP","side":"upper","level":106.18,"price":106.2,"candle_extreme":106.2,"occurred_ms":1793958600000,"candle_ms":1793958560000,"created_ms":1793958600010,"expires_ms":1794103200000,"fresh_until_ms":1793958690000,"source":"live","touch_confirmed":true,"candle_confirmed":false}]}"#
        let monthlySnapshot = try API.decoder.decode(MonthFirstWeekSnapshot.self, from: Data(snapshot.utf8))
        XCTAssertTrue(monthlySnapshot.window.complete)
        XCTAssertEqual(monthlySnapshot.month, "2026-11")
        XCTAssertEqual(monthlySnapshot.coverage.readySymbols, 239)
        XCTAssertEqual(monthlySnapshot.touches.first?.eventId, "2026-11|BTC-USDT-SWAP|upper")
    }
}
