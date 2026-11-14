import XCTest
@testable import SpikeDiscipline

final class MarketDepthTests: XCTestCase {
    private let symbol = "BTC-USDT-SWAP"

    func testBooks5SnapshotKeepsContractLotsAndSortsSides() throws {
        let asks: [[String]] = [
            ["100.03", "23", "0", "2"], ["100.01", "7.5", "0", "1"],
            ["100.02", "12", "0", "3"], ["100.04", "9", "0", "1"],
            ["100.05", "5", "0", "1"], ["100.06", "99", "0", "9"]
        ]
        let bids: [[String]] = [
            ["99.98", "18", "0", "2"], ["100.00", "8", "0", "1"],
            ["99.99", "11", "0", "1"], ["99.97", "6", "0", "1"],
            ["99.96", "4", "0", "1"]
        ]
        let received = Date(timeIntervalSince1970: 1_800_000_000)
        let snapshot = try XCTUnwrap(decode(channel: "books5", symbol: symbol, asks: asks, bids: bids,
                                            timestamp: "1800000000000", receivedAt: received))

        XCTAssertEqual(snapshot.symbol, symbol)
        XCTAssertEqual(snapshot.asks.map(\.price), ["100.01", "100.02", "100.03", "100.04", "100.05"])
        XCTAssertEqual(snapshot.bids.map(\.price), ["100.00", "99.99", "99.98", "99.97", "99.96"])
        XCTAssertEqual(snapshot.asks.first?.contracts, "7.5")
        XCTAssertEqual(snapshot.timestampMs, 1_800_000_000_000)
        XCTAssertEqual(snapshot.receivedAt, received)
    }

    func testDecoderRejectsWrongContractAndChannel() throws {
        let asks = [["100.01", "5", "0", "1"]]
        let bids = [["100.00", "7", "0", "1"]]
        XCTAssertNil(decode(channel: "books5", symbol: "ETH-USDT-SWAP", asks: asks, bids: bids))
        XCTAssertNil(decode(channel: "books", symbol: symbol, asks: asks, bids: bids))
    }

    func testDecoderRejectsEmptyOrNonPositiveDepth() throws {
        XCTAssertNil(decode(channel: "books5", symbol: symbol, asks: [], bids: [["100", "1"]]))
        XCTAssertNil(decode(channel: "books5", symbol: symbol, asks: [["100.01", "0"]], bids: [["100", "1"]]))
        XCTAssertNil(decode(channel: "books5", symbol: symbol, asks: [["NaN", "2"]], bids: [["100", "1"]]))
    }

    func testDecoderRejectsStaleAndFarFutureExchangeTimestamps() throws {
        let received = Date(timeIntervalSince1970: 1_800_000_000)
        let asks = [["100.01", "5", "0", "1"]]
        let bids = [["100.00", "7", "0", "1"]]
        XCTAssertNil(decode(channel: "books5", symbol: symbol, asks: asks, bids: bids,
                            timestamp: "1799999984000", receivedAt: received))
        XCTAssertNil(decode(channel: "books5", symbol: symbol, asks: asks, bids: bids,
                            timestamp: "1800000006000", receivedAt: received))
    }

    func testSubscriptionErrorIsRecognized() throws {
        let error = Data(#"{"event":"error","code":"60012","msg":"Invalid request"}"#.utf8)
        let acknowledgement = Data(#"{"event":"subscribe","code":"0"}"#.utf8)
        XCTAssertTrue(MarketDepthDecoder.isSubscriptionError(error))
        XCTAssertFalse(MarketDepthDecoder.isSubscriptionError(acknowledgement))
    }

    func testSelectionExpiresAfterDocumentedQuietBookRefreshWindow() throws {
        let received = Date(timeIntervalSince1970: 1_800_000_000)
        let snapshot = try XCTUnwrap(decode(channel: "books5", symbol: symbol,
                                            asks: [["100.01", "5", "0", "1"]],
                                            bids: [["100.00", "7", "0", "1"]],
                                            timestamp: "1800000000000", receivedAt: received))
        XCTAssertTrue(snapshot.isTouchable(at: received.addingTimeInterval(60)))
        XCTAssertFalse(snapshot.isTouchable(at: received.addingTimeInterval(66)))
    }

    func testBookPriceCanOnlyBecomeTickAlignedLimitInput() {
        XCTAssertEqual(Trading.priceAtTick("100.005", tick: "0.01"), "100.01")
        XCTAssertNil(Trading.priceAtTick("100.01", tick: nil))
        XCTAssertNil(Trading.priceAtTick("0", tick: "0.01"))
    }

    @MainActor
    func testLateWatchCannotReactivateADeactivatedViewKey() async throws {
        let model = MarketDepthModel()
        let key = "BTC-USDT-SWAP|true|old-view"
        model.select(key: key)
        model.deactivate(key: key)

        await model.watch(token: UUID(), key: key, symbol: symbol,
                          url: try XCTUnwrap(URL(string: "ws://127.0.0.1:18924/ws/v5/public")), enabled: true)

        XCTAssertEqual(model.status, .idle)
        XCTAssertNil(model.snapshot)
    }

    private func decode(channel: String,
                        symbol: String,
                        asks: [[String]],
                        bids: [[String]],
                        timestamp: String? = nil,
                        receivedAt: Date = Date()) -> MarketDepthSnapshot? {
        let value: [String: Any] = [
            "arg": ["channel": channel, "instId": symbol],
            "data": [["instId": symbol, "asks": asks, "bids": bids,
                      "ts": timestamp ?? String(Int(receivedAt.timeIntervalSince1970 * 1000)), "seqId": 12]]
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: value) else { return nil }
        return MarketDepthDecoder.snapshot(from: data, expectedSymbol: self.symbol, receivedAt: receivedAt)
    }
}
