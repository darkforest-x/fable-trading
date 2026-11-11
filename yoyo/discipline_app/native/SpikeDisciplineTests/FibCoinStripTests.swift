import XCTest
@testable import SpikeDiscipline

final class FibCoinStripTests: XCTestCase {
    func testMonthlySourceHasCompactTwoWaySwitch() {
        XCTAssertEqual(FibCoinStripSource.monday.title, "周一")
        XCTAssertEqual(FibCoinStripSource.monthFirstWeek.title, "月首周")
        XCTAssertEqual(FibCoinStripSource.monday.next, .monthFirstWeek)
        XCTAssertEqual(FibCoinStripSource.monthFirstWeek.next, .monday)
    }

    func testMonthlyStripUsesNewestEventPerCoinAndKeepsPinnedCoinsOutOfRollingChips() {
        let events = [
            touch(id: 1, month: "2026-11", symbol: "HUMA-USDT-SWAP", occurredMs: 100),
            touch(id: 2, month: "2026-11", symbol: "AKE-USDT-SWAP", occurredMs: 250),
            touch(id: 3, month: "2026-11", symbol: "BTC-USDT-SWAP", occurredMs: 400),
            touch(id: 4, month: "2026-10", symbol: "NEW-USDT-SWAP", occurredMs: 500),
            touch(id: 5, month: "2026-11", symbol: "HUMA-USDT-SWAP", occurredMs: 300),
        ]

        let rows = FibCoinStripProjection.monthlyTouches(events, month: "2026-11", excluding: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])

        XCTAssertEqual(rows.map(\.symbol), ["HUMA-USDT-SWAP", "AKE-USDT-SWAP"])
        XCTAssertEqual(rows.map(\.id), [5, 2])
        XCTAssertTrue(FibCoinStripProjection.monthlyTouches(events, month: nil, excluding: []).isEmpty)
    }

    func testMonthlyOverlayLinesAppearOnlyForMonthFirstWeekSource() {
        let range = MonthFirstWeekRange(month: "2026-11", startMs: 80_000, readyMs: 90_000, expiresMs: 150_000,
                                       high: 3, low: 2, upper: 4, lower: 1)
        let bars = [Candle(time: 100, open: 2, high: 3, low: 1.5, close: 2.5, volume: 1, confirmed: true)]

        XCTAssertTrue(FibCoinStripProjection.monthlyLines(source: .monday, ranges: [range], bars: bars, period: .m1).isEmpty)
        XCTAssertEqual(FibCoinStripProjection.monthlyLines(source: .monthFirstWeek, ranges: [range], bars: bars, period: .m1).count, 4)
    }

    private func touch(id: Int, month: String, symbol: String, occurredMs: Double) -> MonthFirstWeekTouch {
        MonthFirstWeekTouch(id: id, eventId: "\(month)|\(symbol)|lower", type: MonthTouchStyle.kind, month: month,
                            symbol: symbol, side: .lower, level: 1, price: 1, candleExtreme: 1,
                            occurredMs: occurredMs, candleMs: occurredMs, createdMs: occurredMs,
                            expiresMs: occurredMs + 60_000, freshUntilMs: occurredMs + 60_000,
                            source: "live", touchConfirmed: true, candleConfirmed: true)
    }
}
