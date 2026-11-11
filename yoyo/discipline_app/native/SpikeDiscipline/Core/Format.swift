import Foundation

/// Display helpers shared by every screen (ported from the tested monitorFormat.ts).
enum Fmt {
    static func coin(_ symbol: String) -> String { symbol.replacingOccurrences(of: "-USDT-SWAP", with: "") }

    static func money(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        return v.formatted(.number.precision(.fractionLength(2)).grouping(.automatic))
    }

    /// Keep integer tiers compact while preserving fractional OKX leverage tiers.
    static func leverage(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        return value.formatted(.number.precision(.fractionLength(0...2)).grouping(.never))
    }

    static func signedPercent(_ v: Double?, digits: Int = 2) -> String {
        guard let v, v.isFinite else { return "—" }
        let text = String(format: "%.\(digits)f", abs(v))
        if Double(text) == 0 { return "\(text)%" }
        return (v > 0 ? "+" : "−") + text + "%"
    }

    enum Tone { case up, down, flat }
    static func tone(_ v: Double?) -> Tone {
        guard let v, v.isFinite, v != 0 else { return .flat }
        return v > 0 ? .up : .down
    }

    /// Enough significant digits for low-priced contracts.
    static func price(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        let a = abs(v)
        let digits: Int
        if a >= 1000 { digits = 1 }
        else if a >= 1 { digits = min(4, max(2, 4 - Int(floor(log10(a))))) }
        else { digits = min(10, max(4, 3 - Int(floor(log10(a))))) }
        return v.formatted(.number.precision(.fractionLength(digits)).grouping(.automatic))
    }

    private static let beijing = TimeZone(identifier: "Asia/Shanghai")!
    private static func formatter(_ pattern: String) -> DateFormatter {
        let f = DateFormatter(); f.timeZone = beijing; f.locale = Locale(identifier: "zh_CN"); f.dateFormat = pattern; return f
    }
    private static let dayKey = formatter("yyyy-MM-dd"), hm = formatter("HH:mm"), mdhm = formatter("MM-dd HH:mm"), md = formatter("M月d日")

    static func clock(_ ms: Double?, now: Double = Date().timeIntervalSince1970 * 1000) -> String {
        guard let ms, ms > 0 else { return "—" }
        let d = Date(timeIntervalSince1970: ms / 1000), n = Date(timeIntervalSince1970: now / 1000)
        return dayKey.string(from: d) == dayKey.string(from: n) ? hm.string(from: d) : mdhm.string(from: d)
    }

    static func dayLabel(_ ms: Double?, now: Double = Date().timeIntervalSince1970 * 1000) -> String {
        guard let ms, ms > 0 else { return "更早" }
        let d = Date(timeIntervalSince1970: ms / 1000)
        if dayKey.string(from: d) == dayKey.string(from: Date(timeIntervalSince1970: now / 1000)) { return "今天" }
        if dayKey.string(from: d) == dayKey.string(from: Date(timeIntervalSince1970: (now - 86_400_000) / 1000)) { return "昨天" }
        return md.string(from: d)
    }

    static func groupByDay<T>(_ rows: [T], now: Double, time: (T) -> Double?) -> [(label: String, rows: [T])] {
        var groups: [(label: String, rows: [T])] = []
        for row in rows {
            let label = dayLabel(time(row), now: now)
            if groups.last?.label == label { groups[groups.count - 1].rows.append(row) } else { groups.append((label, [row])) }
        }
        return groups
    }

    static func signalStatus(_ s: PaperSignal) -> String {
        switch s.status {
        case "excluded": return "已排除"
        case "feature_error": return "评分失败"
        default: return s.taken ? "通过评分" : "未通过评分"
        }
    }

    static func reason(_ r: String?) -> String {
        ["target": "到达目标", "breakeven": "保本止损", "backstop": "兜底止损", "reclaimed_inside_be": "收回未越过保本价",
         "failed_reclaim": "确认未收回", "manual": "手动平仓"][r ?? ""] ?? "已平仓"
    }

    static func stage(_ s: String) -> String { ["pending_confirm": "等待确认", "breakeven": "已保本"][s] ?? "持仓中" }
    static func edge(_ e: Edge) -> String { e == .upper ? "上沿 1.618" : "下沿 1.618" }

    static func pushStatus(_ e: NotificationEvent) -> String {
        if e.kind == "alarm" { return e.test == true ? "测试" : "已推送" }
        return ["pushed": "已推送", "pushing": "推送中", "below_threshold": "未达推送阈值", "score_unavailable": "评分不可用",
                "history": "历史回补"][e.status ?? ""] ?? "未推送"
    }
}
