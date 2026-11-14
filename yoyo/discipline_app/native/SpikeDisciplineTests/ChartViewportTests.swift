import XCTest
@testable import SpikeDiscipline

final class ChartViewportTests: XCTestCase {
    func testFiveAndFifteenMinuteInitialWindowUsesOnlyVisibleCandleOHLC() throws {
        for periodSeconds in [300.0, 900.0] {
            let bars = candles(count: 100, periodSeconds: periodSeconds)
            let right = CGFloat(bars.count - 1) + 5
            let left = right - 322 / 7
            let indices = try XCTUnwrap(ChartVisibleBarWindow.indices(count: bars.count, left: left, right: right))
            XCTAssertEqual(indices, 58...99, "the existing 7 pt phone viewport should keep about 40–55 bars visible")
            let bounds = try XCTUnwrap(ChartVisibleBarWindow.ohlc(bars, indices: indices))
            XCTAssertGreaterThan(bounds.low, 99)
            XCTAssertLessThan(bounds.high, 101)

            let visibleStart = bars[indices.lowerBound].time
            let visibleEnd = bars[indices.upperBound].time + periodSeconds
            let references = [
                ChartPriceLevel(price: 145, startTime: visibleStart - periodSeconds * 30,
                                endTime: visibleEnd + periodSeconds * 30),
                ChartPriceLevel(price: 69, startTime: visibleStart - periodSeconds * 30,
                                endTime: visibleEnd + periodSeconds * 30),
            ]
            let candleScale = ChartPriceScale(ohlc: bounds)
            var referenceScale = candleScale
            for level in references {
                referenceScale.include(level, visibleStart: visibleStart, visibleEnd: visibleEnd)
            }
            let candleOnly = candleScale.domain(topFraction: 0.06, bottomFraction: 0.08)
            let withReferencePrices = referenceScale.domain(topFraction: 0.06, bottomFraction: 0.08)
            XCTAssertLessThan(candleOnly.high - candleOnly.low, 2)
            XCTAssertLessThan(withReferencePrices.low, 70)
            XCTAssertGreaterThan(withReferencePrices.high, 144)
        }
    }

    func testReferenceFitOnlyUsesLinesOverlappingVisibleTimeWindow() throws {
        let bars = candles(count: 100, periodSeconds: 300)
        let indices = try XCTUnwrap(ChartVisibleBarWindow.indices(count: bars.count, left: 58, right: 104))
        let bounds = try XCTUnwrap(ChartVisibleBarWindow.ohlc(bars, indices: indices))
        let start = bars[indices.lowerBound].time
        let end = bars[indices.upperBound].time + 300
        let expired = ChartPriceLevel(price: 1_000, startTime: start - 600, endTime: start)
        let future = ChartPriceLevel(price: 0.01, startTime: end, endTime: end + 600)
        let lines = [expired, future]

        var lineScale = ChartPriceScale(ohlc: bounds)
        for line in lines { lineScale.include(line, visibleStart: start, visibleEnd: end) }
        let withInactiveLines = lineScale.domain(topFraction: 0.06, bottomFraction: 0.08)
        let candleOnly = ChartPriceScale(ohlc: bounds).domain(topFraction: 0.06, bottomFraction: 0.08)
        XCTAssertEqual(withInactiveLines, candleOnly)
    }

    func testLiveScaleDoesNotDependOnDisplayingOffscreenReferences() {
        XCTAssertFalse(ChartReferenceScalePolicy.includesReferences(callerAllows: true, explicitReplay: false))
        XCTAssertTrue(ChartReferenceScalePolicy.includesReferences(callerAllows: true, explicitReplay: true))
        XCTAssertFalse(ChartReferenceScalePolicy.includesReferences(callerAllows: false, explicitReplay: true))
        let prices = [145.0, 123, 100, 98, 145, 67, 79, 102, .infinity, 0]
        let edges = ChartEdgeCatalog(prices.map { ChartEdgeLevel(title: String($0), price: $0) }, visible: 98...102)
        XCTAssertEqual(edges.above.map(\.price), [123, 145])
        XCTAssertEqual(edges.below.map(\.price), [79, 67])
    }

    func testExecutionPricesRemainInExplicitReplayDomainWhenReferenceFitIsDisabled() throws {
        let bounds = ChartOHLCBounds(low: 99, high: 101)
        let plain = ChartPriceScale(ohlc: bounds).domain(topFraction: 0.06, bottomFraction: 0.08)
        var replayScale = ChartPriceScale(ohlc: bounds)
        replayScale.include(110)
        let replay = replayScale.domain(topFraction: 0.06, bottomFraction: 0.08)
        XCTAssertLessThan(plain.high, 102)
        XCTAssertGreaterThan(replay.high, 110)
        XCTAssertLessThan(replay.low, plain.low, "fitting the real execution expands the complete replay domain")
    }

    func testVerticalPanAndZoomStillTransformTheOHLCDomain() throws {
        let bounds = ChartOHLCBounds(low: 99, high: 101)
        let fit = { (zoom: Double, shift: Double) in
            ChartPriceScale(ohlc: bounds).domain(topFraction: 0.06, bottomFraction: 0.08,
                                                 yZoom: zoom, yShift: shift)
        }
        let normal = fit(1, 0), zoomed = fit(2, 0), panned = fit(1, 0.25)
        XCTAssertEqual(zoomed.high - zoomed.low, (normal.high - normal.low) * 2, accuracy: 1e-9)
        XCTAssertGreaterThan((panned.low + panned.high) / 2, (normal.low + normal.high) / 2)
    }

    func testIndependentWallpaperKeepsWicksClearOfVolumeWithoutChangingOverlayLayouts() {
        let height = 480.0
        let emptyBottomReserve = 34 / height
        let independent = ChartPlotReserve.bottomFraction(wallpaper: true, hasBottomOverlay: false,
                                                           bottomReserve: emptyBottomReserve, plotHeight: height)
        XCTAssertEqual(independent, 0.08 + 28 / height, accuracy: 1e-12)
        XCTAssertGreaterThanOrEqual(independent * height, 24 + 0.08 * height + 4)

        let overlayBottomReserve = (155 + 34) / height
        let existingOverlay = ChartPlotReserve.bottomFraction(wallpaper: true, hasBottomOverlay: true,
                                                               bottomReserve: overlayBottomReserve, plotHeight: height)
        XCTAssertEqual(existingOverlay, min(0.6, overlayBottomReserve + 0.04), accuracy: 1e-12)
        XCTAssertEqual(ChartPlotReserve.bottomFraction(wallpaper: false, hasBottomOverlay: false,
                                                        bottomReserve: 0, plotHeight: height), 0.07)
    }

    private func candles(count: Int, periodSeconds: Double) -> [Candle] {
        (0..<count).map { index in
            let close = 100 + Double(index % 5) * 0.02
            let low = index == 0 ? 1 : close - 0.05
            let high = index == 0 ? 1_000 : close + 0.05
            let time = 1_700_000_000 + Double(index) * periodSeconds
            return Candle(time: time, open: close, high: high, low: low, close: close,
                          volume: 1, confirmed: true)
        }
    }
}
