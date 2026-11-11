import XCTest
@testable import SpikeDiscipline

final class MonthFirstWeekLevelsTests: XCTestCase {
    func testCompletedMonthlyAnchorDrawsFourNamedLevelsFromReadyUntilExpiry() throws {
        let payload: [String: Any] = [
            "symbol": "HUMA-USDT-SWAP",
            "ranges": [[
                "month": "2026-11", "start_ms": 1_793_030_400_000.0,
                "ready_ms": 1_793_635_200_000.0, "expires_ms": 1_796_227_200_000.0,
                "high": 0.00031, "low": 0.00020, "upper": 0.000378, "lower": 0.000132,
            ]],
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        let snapshot = try API.decoder.decode(MonthFirstWeekLevelsSnapshot.self, from: data)
        try snapshot.validate(symbol: "HUMA-USDT-SWAP")

        let lines = try XCTUnwrap(snapshot.ranges.first).chartLines
        XCTAssertEqual(lines.map(\.title), [
            "2026-11 首周 上 1.618", "2026-11 首周 高", "2026-11 首周 低", "2026-11 首周 下 1.618",
        ])
        XCTAssertEqual(lines.map(\.lineWidth), [1.8, 0.8, 0.8, 1.8])
        XCTAssertTrue(lines.allSatisfy(\.fitsScale))
        XCTAssertTrue(lines.allSatisfy { $0.startTime == 1_793_635_200.0 && $0.endTime == 1_796_227_200.0 })
    }

    func testInvalidOrWrongContractAnchorIsRejected() throws {
        let payload: [String: Any] = [
            "symbol": "BTC-USDT-SWAP",
            "ranges": [[
                "month": "2026-11", "start_ms": 10.0, "ready_ms": 20.0, "expires_ms": 30.0,
                "high": 2.0, "low": 1.0, "upper": 3.0, "lower": 0.5,
            ]],
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        let snapshot = try API.decoder.decode(MonthFirstWeekLevelsSnapshot.self, from: data)
        XCTAssertThrowsError(try snapshot.validate(symbol: "ETH-USDT-SWAP"))
    }

    func testLineProjectionIncludesOnlyRangesOverlappingLoadedBars() {
        let older = MonthFirstWeekRange(month: "2026-09", startMs: 10_000, readyMs: 20_000, expiresMs: 30_000,
                                       high: 3, low: 2, upper: 4, lower: 1)
        let current = MonthFirstWeekRange(month: "2026-11", startMs: 80_000, readyMs: 90_000, expiresMs: 150_000,
                                          high: 3, low: 2, upper: 4, lower: 1)
        let bars = [Candle(time: 100, open: 2, high: 3, low: 1.5, close: 2.5, volume: 1, confirmed: true)]

        let lines = MonthFirstWeekChartProjection.lines(ranges: [older, current], bars: bars, period: .m1)
        XCTAssertEqual(lines.map(\.title), ["2026-11 首周 上 1.618", "2026-11 首周 高", "2026-11 首周 低", "2026-11 首周 下 1.618"])
    }
}
