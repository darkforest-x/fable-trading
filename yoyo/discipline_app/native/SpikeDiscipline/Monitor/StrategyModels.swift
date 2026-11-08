import Foundation

// 2.7.0 strategy frame (owner 2026-11-09): booklet and saved backtest trades, served read-only by
// yoyo/discipline_app/strategy_book.py from the monitor's own published files. Historical results are
// Binance backtests, not live P&L.

/// A research table cell: text, number or blank.
enum TableCell: Decodable, Hashable {
    case text(String), number(Double), empty
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .empty }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .text(v) }
        else { self = .empty }
    }
    var display: String {
        switch self {
        case .text(let t): return t
        case .number(let v):
            if v == v.rounded() && abs(v) < 1e7 { return String(Int(v)) }
            return v.formatted(.number.precision(.fractionLength(abs(v) >= 100 ? 1 : 2)).grouping(.automatic))
        case .empty: return "—"
        }
    }
}

struct ResearchTable: Decodable, Hashable {
    var title: String? = nil
    var note: String? = nil
    let headers: [String]
    let rows: [[TableCell]]
}

/// ["2024", 12.5] pairs from the history file (yearly / monthly).
struct PeriodValue: Decodable, Hashable, Identifiable {
    let period: String
    let value: Double?
    var id: String { period }
    init(from decoder: Decoder) throws {
        var c = try decoder.unkeyedContainer()
        period = try c.decode(String.self)
        value = try c.decodeIfPresent(Double.self)
    }
}

struct Booklet: Decodable {
    struct Fact: Decodable, Hashable { let label: String; let value: String; let unit: String }
    struct Step: Decodable, Hashable { let title: String; let text: String }
    struct Summary: Decodable, Hashable {
        let start: Double?, end: Double?, pnl: Double?, returnPct: Double?, maxDrawdownPct: Double?, winPct: Double?
        let trades: Double?, profitFactor: Double?, noLosses: Bool?, cashAdded: Double?, avgWin: Double?, avgLoss: Double?
    }
    struct History: Decodable {
        let startMs: Double
        let endMs: Double
        let note: String?
        let refilled: Bool
        let unit: String
        let summary: Summary
        let drawdownBasis: String?
        /// [time ms, equity, drawdown %, (cash)]
        let points: [[Double]]
        let yearly: [PeriodValue]
        let monthly: [PeriodValue]
        let sides: ResearchTable?
    }
    struct Research: Decodable {
        let status: String?
        let note: String?
        let detail: String?
        let table: ResearchTable?
        let capital: ResearchTable?
        let controls: ResearchTable?
        let sections: [ResearchTable]
        let methodology: String?
        let periodNote: String?
    }
    let key: String
    let label: String
    let title: String
    let kind: String
    let paper: Bool
    let facts: [Fact]
    let flow: [Step]
    let history: History?
    let historyError: String?
    let research: Research?
    let backtest: Bool
    let notes: [String]
}

struct BacktestTrade: Decodable, Hashable, Identifiable {
    let id: Double
    let symbol: String
    let coin: String
    let side: Side
    let entryMs: Double?
    let exitMs: Double?
    let pnl: Double?
    let returnPct: Double?
    let exitReason: String?
    let score: Double?
    let count: Double?
    let newCoins: Double?
    let kind: String?
    let entryPrice: Double?
    let exitPrice: Double?
    let entryStartMs: Double?
    let entryEndMs: Double?
    let exitStartMs: Double?
    let exitEndMs: Double?
}

struct BacktestPage: Decodable {
    struct Meta: Decodable {
        let trades: Double?
        let pnl: Double?
        let start: String?
        let end: String?
        let source: String?
        let note: String?
        let basis: String?
        let limitation: String?
    }
    let track: String
    let total: Int
    let offset: Int
    let hasMore: Bool
    let meta: Meta
    let rows: [BacktestTrade]
}

struct BacktestChartData: Decodable {
    struct Bar: Decodable { let time: Double; let open: Double; let high: Double; let low: Double; let close: Double; let volume: Double }
    struct Week: Decodable {
        let week: String?
        let startMs: Double
        let endMs: Double
        let expiresMs: Double?
        let high: Double
        let low: Double
        let upper: Double
        let lower: Double
    }
    let track: String
    let trade: BacktestTrade
    let bar: String?
    let source: String?
    let note: String?
    let candles: [Bar]
    let weeklyRanges: [Week]
}

/// Pages pushed inside the account / booklet sheet.
enum StrategyPage: Hashable {
    case booklet(key: String, label: String)
    case backtest(key: String, label: String)
    case trade(key: String, trade: BacktestTrade)
}

enum StrategyFmt {
    private static let utc: DateFormatter = {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "UTC"); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd HH:mm"; return f
    }()
    private static let utcShort: DateFormatter = {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "UTC"); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "MM-dd HH:mm"; return f
    }()
    private static let utcDay: DateFormatter = {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "UTC"); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"; return f
    }()
    static func utcTime(_ ms: Double?) -> String { ms.map { utc.string(from: Date(timeIntervalSince1970: $0 / 1000)) } ?? "—" }
    static func utcShortTime(_ ms: Double?) -> String { ms.map { utcShort.string(from: Date(timeIntervalSince1970: $0 / 1000)) } ?? "—" }
    static func utcDate(_ ms: Double?) -> String { ms.map { utcDay.string(from: Date(timeIntervalSince1970: $0 / 1000)) } ?? "—" }

    /// "+1,234.56" with a real minus sign.
    static func signedMoney(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        let text = abs(v).formatted(.number.precision(.fractionLength(2)).grouping(.automatic))
        return v > 0 ? "+" + text : v < 0 ? "−" + text : text
    }

    static func number(_ v: Double?, digits: Int = 1) -> String {
        guard let v, v.isFinite else { return "—" }
        return v.formatted(.number.precision(.fractionLength(digits)).grouping(.automatic))
    }

    /// "2天4小时" style span.
    static func span(_ ms: Double) -> String {
        let minutes = max(0, Int(ms / 60_000))
        let days = minutes / 1440, hours = (minutes % 1440) / 60, mins = minutes % 60
        if days > 0 { return hours > 0 ? "\(days)天\(hours)小时" : "\(days)天" }
        if hours > 0 { return "\(hours)小时\(mins)分" }
        return "\(mins)分"
    }

    /// Breadth signal state (monday-reclaim-paper.js BREADTH_STATUS).
    static func breadthStatus(_ s: BreadthSignal, now: Double) -> String {
        switch s.status {
        case "taken": return "已开仓"
        case "closed": return "已平仓"
        case "followers": return "观察期有新币 · 取消" + (s.newN.map { " +\(Int($0))" } ?? "")
        case "busy": return "已有持仓 · 跳过"
        case "out_of_band": return "币数不在区间"
        case "watching":
            if let due = s.decisionMs, due > now { return "观察中 · 剩 " + span(due - now) }
            return "观察中"
        case "entering": return "等待开仓价"
        case "price_missing": return "缺少开仓价"
        default: return "状态待核对"
        }
    }

    enum Tone { case open, watch, warn, muted }
    static func breadthTone(_ status: String) -> Tone {
        switch status {
        case "taken", "closed": .open
        case "watching", "entering": .watch
        case "busy": .warn
        default: .muted
        }
    }

    static func decimals(_ price: Double?) -> Int {
        guard let price, price > 0 else { return 4 }
        return min(10, max(2, 4 - Int(floor(log10(price)))))
    }
}
