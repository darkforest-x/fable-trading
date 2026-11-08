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

/// Previous weeks' NY Monday ranges per symbol (owner 2026-11-08 “也要支持历史的高低点 1.618 的线”),
/// computed from 1H bars (~5 weeks via the service relay, cached on disk like the chart series).
/// Each range is drawn from that Monday's close to the next week, like the current one.
@MainActor @Observable
final class MondayHistory {
    private(set) var ranges: [String: [FibRange]] = [:]
    private var loaded: [String: Date] = [:]

    func load(_ symbol: String, connection: Connection?) async {
        guard !symbol.isEmpty, Date().timeIntervalSince(loaded[symbol] ?? .distantPast) > 1800 else { return }
        loaded[symbol] = Date()
        var bars = CandleCache.read(symbol: symbol, period: .h1)
        if !bars.isEmpty { ranges[symbol] = Self.compute(bars) }
        do {
            var page = try await CandleStore.page(symbol: symbol, period: .h1, before: nil, connection: connection)
            bars = Candle.merge(bars, page, limit: 2000)
            for _ in 0..<2 {   // ~37 days in all
                guard let first = page.first else { break }
                page = try await CandleStore.page(symbol: symbol, period: .h1, before: Int(first.time), connection: connection)
                bars = Candle.merge(page, bars, limit: 2000)
            }
            CandleCache.write(symbol: symbol, period: .h1, bars: bars)
            ranges[symbol] = Self.compute(bars)
        } catch {
            loaded[symbol] = nil
        }
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
