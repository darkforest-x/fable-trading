import XCTest
@testable import SpikeDiscipline

final class ScoredTouchesTests: XCTestCase {
    private func event(_ id: Int, _ score: Double, _ edge: Edge = .lower) -> ScoredTouch {
        ScoredTouch(id: id, week: "2026-11-02", symbol: "TEST-USDT-SWAP", side: edge,
                    candleMs: Double(id) * 60_000, score: score)
    }

    private func page(_ today: [ScoredTouch], _ past: [ScoredTouch], offset: Int, total: Int,
                      start: Double = 120_000) -> ScoredTouchPage {
        .init(nowMs: start + 1_000, dayStartMs: start, today: .init(rows: today),
              history: .init(offset: offset, total: total, hasMore: offset + past.count < total, rows: past))
    }

    func testRawCutoffIncludesExactlyPointThreeAndSortsBothSides() {
        let rows = [event(1, 0.30), event(2, 0.29999), event(3, 0.62, .upper), event(4, 0.30)]
        XCTAssertEqual(ScoredTouch.visible(rows, side: "all", sort: "score", quotes: [:]).map(\.id), [3, 4, 1])
        XCTAssertEqual(ScoredTouch.visible(rows, side: "long", sort: "earliest", quotes: [:]).map(\.id), [1, 4])
        XCTAssertEqual(ScoredTouch.visible(rows, side: "short", sort: "latest", quotes: [:]).map(\.id), [3])
    }

    @MainActor func testArchiveBeyondFirstPageAndMidnightRegrouping() async throws {
        let store = ScoredTouches()
        let first = event(1, 0.82), second = event(2, 0.31), third = event(3, 0.44)
        var offsets: [Int] = []
        try await store.refresh { offset in
            offsets.append(offset)
            return self.page([third], offset == 0 ? [second] : [first], offset: offset, total: 2)
        }
        XCTAssertEqual(offsets, [0, 1])
        XCTAssertEqual(store.history.map(\.id), [2, 1])
        try await store.refresh { _ in self.page([], [third, second, first], offset: 0, total: 3, start: 86_520_000) }
        XCTAssertTrue(store.today.isEmpty)
        XCTAssertEqual(store.history.map(\.id), [3, 2, 1])
    }

    @MainActor func testIncompleteOrDuplicatePagesKeepLastCompleteSnapshot() async throws {
        let store = ScoredTouches()
        let previous = event(1, 0.41), newer = event(2, 0.50)
        try await store.refresh { _ in self.page([], [previous], offset: 0, total: 1) }
        do {
            try await store.refresh { self.page([], [newer], offset: $0, total: 2) }
            XCTFail("duplicate rows must not replace the archive")
        } catch {
            XCTAssertEqual(store.history, [previous])
        }
    }
}
