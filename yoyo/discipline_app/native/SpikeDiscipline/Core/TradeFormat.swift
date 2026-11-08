import Foundation

/// Trading display formats from the 1.x app (PositionCard / activityFormat / Statistics fmt).
extension Fmt {
    /// en-US grouping with fixed decimals; "—" when missing (fmt in PositionCard.tsx / Statistics.tsx).
    static func amount(_ value: Double?, _ digits: Int = 2) -> String {
        guard let value, value.isFinite else { return "—" }
        return value.formatted(.number.precision(.fractionLength(digits)).locale(Locale(identifier: "en_US")))
    }
    static func amount(_ text: String?, _ digits: Int = 2) -> String { amount(text.flatMap(Double.init), digits) }

    static func signed(_ value: Double?, _ digits: Int = 2) -> String {
        guard let value, value.isFinite else { return "—" }
        return (value > 0 ? "+" : "") + amount(value, digits)
    }

    /// Price decimals from the instrument tick, clamped to 2…12.
    static func decimals(tick: String?) -> Int {
        let value = Double(tick ?? "") ?? 0.01
        return min(12, max(2, Int(ceil(-log10(value > 0 ? value : 0.01) - 1e-9))))
    }

    static func price(_ text: String?, tick: String?) -> String { amount(text, decimals(tick: tick)) }

    /// Exchange/book timestamps are seconds; always Beijing time (activityFormat.ts).
    static func beijing(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite, seconds > 0 else { return "—" }
        return beijingFormatter.string(from: Date(timeIntervalSince1970: seconds))
    }
    private static let beijingFormatter: DateFormatter = {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "Asia/Shanghai")
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"; return f
    }()

    /// Beijing calendar day "yyyy-MM-dd" (today filters).
    static func beijingDay(_ date: Date) -> String { dayKeyFormatter.string(from: date) }
    private static let dayKeyFormatter: DateFormatter = {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "Asia/Shanghai")
        f.dateFormat = "yyyy-MM-dd"; return f
    }()

    static func coinQuantity(_ text: String?) -> String {
        guard let value = text.flatMap(Double.init), value.isFinite else { return "—" }
        return value.formatted(.number.precision(.fractionLength(0...12)).locale(Locale(identifier: "en_US")))
    }

    /// Percent string from a server fraction ("0.53" → "53.0").
    static func fractionPercent(_ text: String?, _ digits: Int = 1) -> String {
        guard let value = text.flatMap(Double.init) else { return "—" }
        return amount(value * 100, digits)
    }

    static func closeReason(_ reason: String?) -> String {
        ["exchange": "交易所平仓", "manual": "手动平仓", "stop": "止损触发", "take_profit": "止盈触发"][reason ?? ""]
            ?? ((reason ?? "").contains("R") ? reason! : "未记录")
    }

    static func marginMode(_ mode: String?) -> String { mode == "cross" ? "全仓" : "逐仓" }

    /// Trim trailing zeros of a server fraction × 100 (“0.05” → “5”).
    static func plainPercent(_ text: String?) -> String {
        guard let value = text.flatMap(Double.init) else { return "—" }
        return (value * 100).formatted(.number.precision(.fractionLength(0...4)).grouping(.never))
    }
}
