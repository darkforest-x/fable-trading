import XCTest
@testable import SpikeDiscipline

@MainActor
final class MondayHistoryTests: XCTestCase {
    private let newYork = TimeZone(identifier: "America/New_York")!

    private func monday(_ day: Int) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = newYork
        calendar.firstWeekday = 2
        return calendar.date(from: DateComponents(year: 2026, month: 11, day: day))!
    }

    private func weekInterval(_ date: Date) throws -> DateInterval {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = newYork
        calendar.firstWeekday = 2
        return try XCTUnwrap(calendar.dateInterval(of: .weekOfYear, for: date))
    }

    private func hourlyBars(start: Date, high: Double, low: Double) -> [Candle] {
        (0..<24).map { hour in
            let isHigh = hour == 6
            let isLow = hour == 17
            let center = (high + low) / 2
            return Candle(time: start.timeIntervalSince1970 + Double(hour) * 3_600,
                         open: center, high: isHigh ? high : center + 100,
                         low: isLow ? low : center - 100, close: center,
                         volume: 1, confirmed: true)
        }
    }

    func testCurrentUntriggeredMondayRangeUsesVerifiedNovemberNinthExtrema() throws {
        let start = monday(9)
        let week = try weekInterval(start)
        let bars = hourlyBars(start: start, high: 75_549.4, low: 73_711.1)
        let history = MondayHistory()

        XCTAssertNil(history.currentRange(for: "BTC-USDT-SWAP", now: start.addingTimeInterval(23 * 3_600 + 3_599)))

        let range = try XCTUnwrap(MondayHistory.completedMondayRange(bars, week: week))

        XCTAssertEqual(range.high, 75_549.4, accuracy: 1e-8)
        XCTAssertEqual(range.low, 73_711.1, accuracy: 1e-8)
        XCTAssertEqual(range.upper, 76_685.4694, accuracy: 1e-7)
        XCTAssertEqual(range.lower, 72_575.0306, accuracy: 1e-7)
        XCTAssertEqual(range.start, monday(10).timeIntervalSince1970, accuracy: 1)
        XCTAssertEqual(range.end, monday(16).timeIntervalSince1970, accuracy: 1)
    }

    func testPreviousMondayRangeUsesVerifiedNovemberSecondExtremaAcrossDSTBoundary() throws {
        let start = monday(2)
        let week = try weekInterval(start)
        let bars = hourlyBars(start: start, high: 78_429.9, low: 76_520.0)

        let range = try XCTUnwrap(MondayHistory.completedMondayRange(bars, week: week))

        XCTAssertEqual(range.high, 78_429.9, accuracy: 1e-8)
        XCTAssertEqual(range.low, 76_520.0, accuracy: 1e-8)
        XCTAssertEqual(range.upper, 79_610.2182, accuracy: 1e-7)
        XCTAssertEqual(range.lower, 75_339.6818, accuracy: 1e-7)
        XCTAssertEqual(range.start, monday(3).timeIntervalSince1970, accuracy: 1)
        XCTAssertEqual(range.end, monday(9).timeIntervalSince1970, accuracy: 1)
    }

    func testRangeRequiresAllTwentyFourConfirmedContiguousHours() throws {
        let start = monday(9)
        let week = try weekInterval(start)
        let bars = hourlyBars(start: start, high: 75_549.4, low: 73_711.1)

        XCTAssertNil(MondayHistory.completedMondayRange(Array(bars.dropLast()), week: week))

        var unconfirmed = bars
        unconfirmed[23] = Candle(time: bars[23].time, open: bars[23].open, high: bars[23].high,
                                 low: bars[23].low, close: bars[23].close, volume: bars[23].volume,
                                 confirmed: false)
        XCTAssertNil(MondayHistory.completedMondayRange(unconfirmed, week: week))

        var gapped = bars
        gapped.remove(at: 10)
        XCTAssertNil(MondayHistory.completedMondayRange(gapped, week: week))
    }
}
