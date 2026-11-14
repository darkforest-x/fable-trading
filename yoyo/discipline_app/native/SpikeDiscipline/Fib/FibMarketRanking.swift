import Foundation

/// Touch timestamps and scores come from the source selected on the chart, never from price changes.
struct FibMarketRanking: Equatable {
    struct Touch: Equatable {
        let at: Double
        let side: Edge
        let score: Double?
    }
    let source: FibCoinStripSource
    let bySymbol: [String: [Touch]]
    var sorts: [(String, String)] {
        [("latest", "最新触价"), ("earliest", "最早触价")] + (source == .monday ? [("score", "触价评分")] : [])
    }

    @MainActor static func make(source: FibCoinStripSource, week: [FibContract], month: MonthFirstWeekSnapshot?,
                                score: (String, FibTouch) -> Double?) -> Self {
        var rows: [String: [Touch]] = [:]
        if source == .monday {
            for row in week {
                rows[row.symbol] = row.touches.values.map { Touch(at: $0.occurredMs, side: $0.side, score: score(row.symbol, $0)) }
            }
        } else if let month {
            for t in month.touches where t.month == month.month && t.touchConfirmed {
                let at = t.source == "history" ? t.candleMs : t.occurredMs
                rows[t.symbol, default: []].append(Touch(at: at, side: t.side, score: nil))
            }
        }
        return Self(source: source, bySymbol: rows)
    }

    func selectedTouch(_ symbol: String, sort: String) -> Touch? {
        let rows = (bySymbol[symbol] ?? []).filter { $0.at.isFinite && $0.at > 0 }
        if sort == "score" {
            return rows.filter { $0.score?.isFinite == true }.max { ($0.score!, $0.at) < ($1.score!, $1.at) }
        }
        return sort == "earliest" ? rows.min { $0.at < $1.at } : rows.max { $0.at < $1.at }
    }

    func ranked(_ symbols: [String], sort: String) -> [String] {
        let values = symbols.map { symbol -> (String, Double?) in
            let t = selectedTouch(symbol, sort: sort)
            return (symbol, sort == "score" ? t?.score : t?.at)
        }
        return values.sorted { a, b in
            switch (a.1, b.1) {
            case let (x?, y?) where x != y: return sort == "earliest" ? x < y : x > y
            case (_?, nil): return true
            case (nil, _?): return false
            default: return a.0 < b.0
            }
        }.map(\.0)
    }
}
