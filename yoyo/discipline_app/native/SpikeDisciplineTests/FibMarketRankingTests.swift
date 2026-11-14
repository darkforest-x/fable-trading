import XCTest
@testable import SpikeDiscipline

final class FibMarketRankingTests: XCTestCase {
    func testSortsUseEachContractsOwnTouchesAndKeepUntouchedLast() {
        let data = FibMarketRanking(source: .monday, bySymbol: [
            "A": [.init(at: 4, side: .upper, score: nil), .init(at: 1, side: .lower, score: 0.7)],
            "B": [.init(at: 6, side: .lower, score: 0.1)], "D": [.init(at: 1, side: .upper, score: nil)]
        ])
        let all = ["C","D","B","A"]
        XCTAssertEqual(data.ranked(all, sort: "latest"), ["B","A","D","C"])
        XCTAssertEqual(data.ranked(all, sort: "earliest"), ["A","D","B","C"])
        XCTAssertEqual(data.ranked(all, sort: "score").prefix(2), ["A","B"])
        XCTAssertEqual(data.selectedTouch("A", sort: "earliest")?.side, .lower)
        XCTAssertFalse(FibMarketRanking(source: .monthFirstWeek, bySymbol: [:]).sorts.contains { $0.0 == "score" })
    }
}
