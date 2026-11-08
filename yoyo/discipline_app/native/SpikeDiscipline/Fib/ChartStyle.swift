import Observation
import SwiftUI
import UIKit

/// Chart appearance (owner 2026-11-08 “K 线颜色支持自定义”), mirroring the workbench appearance
/// settings (yoyo/monitor/static/monday-chart-appearance.js + cloud-candle-colors.js): presets
/// 工作台 (grey candles, NY-Monday candles green — the web mobile default), 经典 (green / red), or custom
/// colours; the Monday highlight and the previous weeks' ranges can be switched. Display only.
@MainActor @Observable
final class ChartStyle {
    enum Palette: String, CaseIterable, Identifiable {
        case workbench, classic, custom
        var id: String { rawValue }
        var title: String { switch self { case .workbench: "工作台"; case .classic: "经典红绿"; case .custom: "自定义" } }
    }

    var palette: Palette { didSet { save() } }
    var customUp: Color { didSet { save() } }
    var customDown: Color { didSet { save() } }
    var mondayColor: Color { didSet { save() } }
    var mondayHighlight: Bool { didSet { save() } }
    var showHistory: Bool { didSet { save() } }

    private static let key = "chart.style.v1"
    private struct Saved: Codable { var palette = "workbench"; var up = "169873"; var down = "D7355D"; var monday = "30D158"; var mondayHighlight = true; var history = true }

    init() {
        let saved = UserDefaults.standard.data(forKey: Self.key).flatMap { try? JSONDecoder().decode(Saved.self, from: $0) } ?? Saved()
        palette = Palette(rawValue: saved.palette) ?? .workbench
        customUp = Color(hex: saved.up) ?? ChartPalette.up
        customDown = Color(hex: saved.down) ?? ChartPalette.down
        mondayColor = Color(hex: saved.monday) ?? ChartPalette.hex(0x30D158)
        mondayHighlight = saved.mondayHighlight
        showHistory = saved.history
    }

    private func save() {
        let value = Saved(palette: palette.rawValue, up: customUp.hexString, down: customDown.hexString, monday: mondayColor.hexString,
                          mondayHighlight: mondayHighlight, history: showHistory)
        if let data = try? JSONEncoder().encode(value) { UserDefaults.standard.set(data, forKey: Self.key) }
    }

    var colors: ChartColors {
        switch palette {
        case .workbench: ChartColors(up: ChartPalette.hex(0x8B9099), down: ChartPalette.hex(0x8B9099), monday: mondayHighlight ? mondayColor : nil)
        case .classic: ChartColors(up: ChartPalette.up, down: ChartPalette.down, monday: mondayHighlight ? mondayColor : nil)
        case .custom: ChartColors(up: customUp, down: customDown, monday: mondayHighlight ? mondayColor : nil)
        }
    }
}

struct ChartColors: Equatable {
    var up: Color
    var down: Color
    var monday: Color?
    static let classic = ChartColors(up: ChartPalette.up, down: ChartPalette.down, monday: nil)
}

extension Color {
    init?(hex: String) {
        let s = hex.trimmingCharacters(in: CharacterSet(charactersIn: "# "))
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self = ChartPalette.hex(v)
    }
    var hexString: String {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        UIColor(self).getRed(&r, green: &g, blue: &b, alpha: &a)
        return String(format: "%02X%02X%02X", Int(round(r * 255)), Int(round(g * 255)), Int(round(b * 255)))
    }
}

/// Previous weeks' NY Monday ranges per symbol (owner 2026-11-08 “也要支持历史的高低点 1.618 的线”;
/// 11-09 “历史的区间线只能看到最近几周的，我需要看全历史的”). Weeks are filled backwards from the newest
/// completed Monday to the oldest bar the chart has loaded: one 1H page ending at a Tuesday 00:00 NY
/// holds that Monday and the one before. A completed Monday never changes, so each week is computed
/// once and kept on disk per service host and contract; the walk stops at the listing (empty page).
/// Each range is drawn from that Monday's close to the next week, like the current one.
@MainActor @Observable
final class MondayHistory {
    private(set) var ranges: [String: [FibRange]] = [:]
    private var weeks: [String: [Double: FibRange]] = [:]      // keyed by Tuesday 00:00 NY (range.start)
    private var checked: [String: Set<Double>] = [:]           // Tuesdays fetched without a usable Monday
    private var listed: [String: Bool] = [:]                   // walked back to the listing
    private var wanted: [String: Double] = [:]
    private var busy: Set<String> = []

    private static let calendar: Calendar = {
        var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "America/New_York")!; c.firstWeekday = 2; return c
    }()

    /// Fill completed Mondays back to `from` (epoch seconds; default ~5 weeks).
    func load(_ symbol: String, from: Double? = nil, connection: Connection?) async {
        guard !symbol.isEmpty else { return }
        if weeks[symbol] == nil { restore(symbol) }
        guard connection != nil else { return }   // the caller's task id includes the connection
        let target = from ?? Date().addingTimeInterval(-35 * 86_400).timeIntervalSince1970
        wanted[symbol] = min(wanted[symbol] ?? .infinity, target)
        guard !busy.contains(symbol) else { return }   // the running walk reads the wider target
        busy.insert(symbol)
        // Not tied to the view task: when the chart's oldest bar moves, the view task is replaced,
        // and the walk must keep going to the new target instead of dying with the old task.
        Task { await walk(symbol, target: target, connection: connection) }
    }

    private func walk(_ symbol: String, target: Double, connection: Connection?) async {
        defer { busy.remove(symbol); save(symbol) }
        let cal = Self.calendar
        guard let current = cal.dateInterval(of: .weekOfYear, for: Date()) else { return }
        var monday = cal.date(byAdding: .weekOfYear, value: -1, to: current.start)!
        var fetched = 0
        while listed[symbol] != true, monday.timeIntervalSince1970 + 7 * 86_400 > (wanted[symbol] ?? target) {
            let tuesday = cal.date(byAdding: .day, value: 1, to: monday)!.timeIntervalSince1970
            if weeks[symbol]?[tuesday] == nil && checked[symbol]?.contains(tuesday) != true {
                guard let bars = try? await CandleStore.page(symbol: symbol, period: .h1, before: Int(tuesday), connection: connection) else { return }
                if bars.isEmpty { listed[symbol] = true; break }
                for range in Self.compute(bars) { weeks[symbol, default: [:]][range.start] = range }
                if weeks[symbol]?[tuesday] == nil { checked[symbol, default: []].insert(tuesday) }
                fetched += 1
                if fetched % 4 == 1 { publish(symbol) }
            }
            monday = cal.date(byAdding: .weekOfYear, value: -1, to: monday)!
        }
        publish(symbol)
    }

    private func publish(_ symbol: String) {
        ranges[symbol] = (weeks[symbol] ?? [:]).values.sorted { $0.start < $1.start }
    }

    // MARK: disk (completed weeks only; keyed by service host so fixture and production never mix)

    private struct Saved: Codable { var weeks: [[Double]]; var checked: [Double]; var listed: Bool }

    private static func url(_ symbol: String) -> URL? {
        guard let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else { return nil }
        let host = "\(AppConfig.serviceURL.host ?? "")-\(AppConfig.serviceURL.port ?? 443)"
        return base.appendingPathComponent("monday-ranges", isDirectory: true).appendingPathComponent("\(host)-\(symbol).json")
    }

    private func restore(_ symbol: String) {
        weeks[symbol] = [:]
        guard let url = Self.url(symbol), let data = try? Data(contentsOf: url),
              let saved = try? JSONDecoder().decode(Saved.self, from: data) else { return }
        for row in saved.weeks where row.count == 4 && row[0] > row[1] {
            let (high, low, start, end) = (row[0], row[1], row[2], row[3]), r = high - low
            weeks[symbol]![start] = FibRange(high: high, low: low, upper: high + 0.618 * r, lower: low - 0.618 * r, start: start, end: end)
        }
        checked[symbol] = Set(saved.checked)
        listed[symbol] = saved.listed
        publish(symbol)
    }

    private func save(_ symbol: String) {
        guard let url = Self.url(symbol), let all = weeks[symbol] else { return }
        let saved = Saved(weeks: all.values.map { [$0.high, $0.low, $0.start, $0.end] }, checked: Array(checked[symbol] ?? []), listed: listed[symbol] ?? false)
        guard let data = try? JSONEncoder().encode(saved) else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }

    /// Completed Mondays before the current NY week.
    static func compute(_ bars: [Candle]) -> [FibRange] {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        calendar.firstWeekday = 2
        guard let first = bars.first, let current = calendar.dateInterval(of: .weekOfYear, for: Date()) else { return [] }
        var out: [FibRange] = []
        var week = calendar.dateInterval(of: .weekOfYear, for: Date(timeIntervalSince1970: first.time))
        while let w = week, w.start < current.start {
            let monday = w.start.timeIntervalSince1970
            let tuesday = calendar.date(byAdding: .day, value: 1, to: w.start)!.timeIntervalSince1970
            let day = bars.filter { $0.time >= monday && $0.time < tuesday }
            if day.count >= 20, let high = day.map(\.high).max(), let low = day.map(\.low).min(), high > low {
                let r = high - low
                out.append(FibRange(high: high, low: low, upper: high + 0.618 * r, lower: low - 0.618 * r, start: tuesday, end: w.end.timeIntervalSince1970))
            }
            week = calendar.dateInterval(of: .weekOfYear, for: w.end.addingTimeInterval(3600))
        }
        return out
    }
}
