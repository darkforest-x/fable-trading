import SwiftUI

struct FibChooser: View {
    @Environment(FibStore.self) private var fib
    @Environment(FibNavigation.self) private var navigation
    @Environment(TouchFeed.self) private var scores
    @Environment(\.dismiss) private var dismiss
    let source: FibCoinStripSource
    let monthFeed: MonthFirstWeekFeed

    var body: some View {
        let ranking = FibMarketRanking.make(source: source, week: fib.rows, month: monthFeed.snapshot) {
            scores.score($0, $1)
        }
        ContractPickerSheet(symbol: fib.chartSymbol, touchRanking: ranking) { symbol in
            Haptics.select(); navigation.showLiveChart(); fib.focus(symbol); dismiss()
        }
    }

    static func price(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        if value >= 1000 { return value.formatted(.number.precision(.fractionLength(0...1))) }
        return value.formatted(.number.precision(.significantDigits(1...5)).grouping(.never))
    }
}
