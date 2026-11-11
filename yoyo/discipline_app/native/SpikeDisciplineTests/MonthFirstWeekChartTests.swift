import XCTest
@testable import SpikeDiscipline

final class MonthFirstWeekChartTests: XCTestCase {
    func testEventChartUsesServiceFirstWeekFlagsAndMarksMatchingCandleOpen() throws {
        let value = try decode(markerMs: 1_793_505_600_000, firstWeeks: [true, true, false],
                               formingTimeMs: 1_793_512_800_000)
        try value.validate(eventId: "2026-11|HUMA-USDT-SWAP|lower", period: CandlePeriod.h1)

        XCTAssertEqual(value.chartHighlightedBarTimes?.count, 2)
        XCTAssertEqual(value.chartHighlightedBarTimes?.contains(1_793_502_000.0), true)
        XCTAssertEqual(value.chartMarkers.count, 1)
        XCTAssertEqual(value.chartMarkers[0].time, 1_793_505_600.0)
        XCTAssertEqual(value.chartCandles.last?.confirmed, false)
        XCTAssertEqual(value.chartRange?.highTitle, "首周高")
        XCTAssertEqual(value.chartRange?.lowTitle, "首周低")
    }

    func testNumericZeroWithoutMatchingCandleIsNotRendered() throws {
        let value = try decode(markerMs: 0)
        XCTAssertEqual(value.markerMs, 0, "Zero is decoded as a supplied value, not treated as null.")
        XCTAssertNil(value.locatedMarkerMs)
        XCTAssertTrue(value.chartMarkers.isEmpty)
        XCTAssertThrowsError(try value.validate(eventId: "2026-11|HUMA-USDT-SWAP|lower", period: CandlePeriod.h1))
    }

    func testMissingHistoricalMarkerDoesNotInventAFirstCandle() throws {
        let value = try decode(source: "history", markerMs: nil, firstWeeks: nil)
        try value.validate(eventId: "2026-11|HUMA-USDT-SWAP|lower", period: .h1)

        XCTAssertTrue(value.chartMarkers.isEmpty)
        XCTAssertNil(value.chartHighlightedBarTimes, "Older mock payloads fall back to anchor timestamps.")
        XCTAssertEqual(value.chartHighlightRange?.lowerBound, value.anchor.startMs / 1000)
    }

    func testSymbolChartAllowsNegativeLowerAndDegenerateKnownRange() throws {
        let negativeLower = try decode(eventId: nil, source: nil, side: nil, level: nil, markerMs: nil,
                                       observation: nil, high: 0.0003, low: 0.0002, upper: 0.0003618,
                                       lower: -0.00014836, firstWeeks: [true, false, false], symbol: "HUMA-USDT-SWAP")
        try negativeLower.validate(symbol: "HUMA-USDT-SWAP", period: .h1)
        XCTAssertNil(negativeLower.chartRange, "A non-positive extension must not distort the candle scale.")
        XCTAssertEqual(negativeLower.chartLines.map(\.title), ["上 1.618", "首周高", "首周低"])

        let flat = try decode(eventId: nil, source: nil, side: nil, level: nil, markerMs: nil,
                              observation: nil, high: 0.0002, low: 0.0002, upper: nil, lower: nil)
        try flat.validate(symbol: "AKE-USDT-SWAP", period: .h1)
        XCTAssertEqual(flat.chartCandles.count, 3)
        XCTAssertNil(flat.chartRange)
    }

    func testOldMockWithoutFirstWeekFlagUsesCurrentAnchorRange() throws {
        let value = try decode(firstWeeks: nil)
        XCTAssertNil(value.chartHighlightedBarTimes)
        XCTAssertEqual(value.chartHighlightRange, (value.anchor.startMs / 1000)..<(value.anchor.readyMs / 1000))
    }

    private func decode(
        eventId: String? = "2026-11|HUMA-USDT-SWAP|lower",
        source: String? = "history",
        side: String? = "lower",
        level: Double? = 0.0002,
        markerMs: Double? = 1_793_505_600_000,
        observation: (Double, Double)? = (1_793_505_610_000, 1_793_509_200_000),
        high: Double? = 0.0003,
        low: Double? = 0.0002,
        upper: Double? = 0.0003618,
        lower: Double? = 0.0001382,
        firstWeeks: [Bool?]? = [true, true, false],
        formingTimeMs: Double? = nil,
        symbol: String? = nil
    ) throws -> MonthFirstWeekChartSnapshot {
        let times: [Double] = [1_793_502_000_000, 1_793_505_600_000, 1_793_512_800_000]
        let candleRows: [[String: Any]] = times.enumerated().map { index, time in
            var row: [String: Any] = ["time": time, "open": 0.00021, "high": 0.00025,
                                      "low": 0.00018, "close": 0.00022, "volume": 123.0]
            if let firstWeeks, index < firstWeeks.count, let firstWeek = firstWeeks[index] {
                row["first_week"] = firstWeek
            }
            return row
        }
        var anchor: [String: Any] = ["start_ms": 1_793_505_600_000.0, "ready_ms": 1_793_512_800_000.0,
                                     "expires_ms": 1_796_000_000_000.0]
        anchor["high"] = boxed(high); anchor["low"] = boxed(low)
        anchor["upper"] = boxed(upper); anchor["lower"] = boxed(lower)
        let observationStart = observation?.0
        let observationEnd = observation?.1
        let payload: [String: Any] = [
            "event_id": boxed(eventId), "symbol": symbol ?? (eventId == nil ? "AKE-USDT-SWAP" : "HUMA-USDT-SWAP"),
            "month": "2026-11", "side": boxed(side), "source": boxed(source), "bar": "1H",
            "level": boxed(level), "anchor": anchor, "candles": candleRows,
            "forming_time_ms": boxed(formingTimeMs), "marker_ms": boxed(markerMs),
            "observation_start_ms": boxed(observationStart), "observation_end_ms": boxed(observationEnd),
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        return try API.decoder.decode(MonthFirstWeekChartSnapshot.self, from: data)
    }

    private func boxed<T>(_ value: T?) -> Any { value.map { $0 as Any } ?? NSNull() }
}
