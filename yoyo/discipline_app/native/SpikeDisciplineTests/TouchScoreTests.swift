import XCTest
@testable import SpikeDiscipline

/// 2.6.0 (owner 2026-11-08 item 1): a 最新触价 row shows the score of its own touch, never the coin's
/// best score of the day; widgets highlight by the owner's threshold. Synthetic values only.
final class TouchScoreTests: XCTestCase {
    private func signal(_ symbol: String, _ side: Side, _ score: Double, _ at: Double) -> PaperSignal {
        PaperSignal(id: at, symbol: symbol, side: side, level: nil, score: score, status: "scored", taken: false, observedMs: at)
    }

    func testRecentSignalMatchesItsOwnTouchByEdgeAndTime() {
        let touch = FibTouch(side: .lower, source: "live", occurredMs: 1_000_000)
        let signals = [signal("AAA-USDT-SWAP", .long, 0.9, 1_000_000 + 3_600_000),   // same coin and edge, an hour later
                       signal("AAA-USDT-SWAP", .short, 0.8, 1_000_000),              // the other edge
                       signal("BBB-USDT-SWAP", .long, 0.7, 1_000_000),               // another coin
                       signal("AAA-USDT-SWAP", .long, 0.31, 1_000_000 + 20_000)]     // this touch
        XCTAssertEqual(TouchFeed.match(signals, symbol: "AAA-USDT-SWAP", touch: touch)?.score, 0.31)
        let older = FibTouch(side: .lower, source: "live", occurredMs: 900_000_000)
        XCTAssertNil(TouchFeed.match(signals, symbol: "AAA-USDT-SWAP", touch: older), "no signal within 10 minutes → no score")
    }

    @MainActor func testWeekScoresDecodeAndKeyByCoinAndEdge() throws {
        let json = #"{"week":"2026-11-02","scores":[{"symbol":"AAA-USDT-SWAP","side":"upper","candle_ms":1,"observed_ms":2,"score":0.58,"status":"scored"}]}"#
        let value = try API.decoder.decode(TouchScores.self, from: Data(json.utf8))
        XCTAssertEqual(value.scores.first?.side, .upper)
        XCTAssertEqual(TouchFeed.key("AAA-USDT-SWAP", .upper), "AAA-USDT-SWAP|upper")
    }

    func testWidgetHighTouchesFollowTheThreshold() {
        let snapshot = WidgetSnapshot(updated: 0, touches: [
            .init(symbol: "A-USDT-SWAP", side: "long", score: 0.52, at: 3), .init(symbol: "B-USDT-SWAP", side: "short", score: 0.88, at: 2),
            .init(symbol: "C-USDT-SWAP", side: "long", score: 0.31, at: 1), .init(symbol: "D-USDT-SWAP", side: "long", score: nil, at: 0)],
                                      positions: [], threshold: 0.5)
        XCTAssertEqual(snapshot.highTouches.map(\.symbol), ["B-USDT-SWAP", "A-USDT-SWAP"])
        var stricter = snapshot; stricter.threshold = 0.6
        XCTAssertEqual(stricter.highTouches.map(\.symbol), ["B-USDT-SWAP"])
        XCTAssertEqual(WidgetSnapshot.coin("B-USDT-SWAP"), "B")
    }
}

/// Display-only launch cache: keyed by connection scope, never shared across accounts or services.
final class LaunchCacheTests: XCTestCase {
    private struct Box: Codable, Equatable, Sendable { let v: Int }

    func testRoundTripIsScopedToTheConnection() async throws {
        LaunchCache.write(Box(v: 7), "unit", scope: "scope-a")
        for _ in 0..<100 where LaunchCache.read(Box.self, "unit", scope: "scope-a") == nil { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertEqual(LaunchCache.read(Box.self, "unit", scope: "scope-a"), Box(v: 7))
        XCTAssertNil(LaunchCache.read(Box.self, "unit", scope: "scope-b"))
        XCTAssertNil(LaunchCache.read(Box.self, "unit", scope: ""))
    }
}

/// Cloudflare edge failures read as a reconnecting tunnel, never as Cloudflare's English page.
final class EdgeFailureTests: XCTestCase {
    func testEdgeStatusesMapToChineseReconnectAndServiceErrorsDoNot() {
        XCTAssertEqual(API.edgeFailure(530), "服务连接中断（隧道 1033），正在自动重连")
        XCTAssertEqual(API.edgeFailure(522), "服务连接中断（522），正在自动重连")
        XCTAssertNotNil(API.edgeFailure(502))
        for status in [400, 401, 409, 422, 503] { XCTAssertNil(API.edgeFailure(status), "\(status) is the service's own answer") }
    }
}
