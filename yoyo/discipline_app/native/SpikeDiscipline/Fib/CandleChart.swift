import SwiftUI

/// Native candle chart (replaces lightweight-charts in CandlePanel.tsx): drag to pan, pinch to zoom,
/// long-press for the crosshair, the NY Monday range levels clipped to their week, optional 内部线
/// and 六均线, plus entry/stop lines for the ticket. Display only.
struct ChartLine: Equatable, Identifiable {
    let price: Double
    let title: String
    let color: Color
    var dashed = false
    var id: String { title }
}

enum ChartPalette {
    static let up = Color(red: 0.188, green: 0.820, blue: 0.345)       // #30d158
    static let down = Color(red: 1.0, green: 0.271, blue: 0.227)       // #ff453a
    static let accent = Color(red: 0.039, green: 0.518, blue: 1.0)     // #0a84ff
    static let amber = Color(red: 1.0, green: 0.624, blue: 0.039)      // #ff9f0a
    static let reference = Color(red: 0.557, green: 0.557, blue: 0.576) // #8e8e93
}

struct CandleChart: View {
    let bars: [Candle]
    let generation: Int
    let period: CandlePeriod
    let decimals: Int
    var range: FibRange? = nil
    var innerLines = false
    var averages = false
    var lines: [ChartLine] = []
    var watermark: String? = nil
    var followToken = 0
    var onNeedOlder: () -> Void = {}

    @Environment(\.colorScheme) private var scheme
    @State private var spacing: CGFloat = 7
    @State private var offset: CGFloat = 0          // bars scrolled back from the live edge
    @State private var panStart: CGFloat?
    @State private var zoomStart: CGFloat?
    @State private var cross: CGPoint?
    @State private var crossHaptic = -1

    private let axisWidth: CGFloat = 58
    private let timeHeight: CGFloat = 20
    private let rightPad: CGFloat = 4

    var body: some View {
        GeometryReader { proxy in
            let plot = CGSize(width: max(1, proxy.size.width - axisWidth), height: max(1, proxy.size.height - timeHeight))
            let layout = Layout(bars: bars, spacing: spacing, offset: offset, plot: plot, rightPad: rightPad, period: period, range: range, lines: lines)
            ZStack(alignment: .topLeading) {
                Canvas(rendersAsynchronously: false) { context, size in draw(&context, size: size, layout: layout) }
                legend(layout)
                    .padding(.leading, 10).padding(.top, 6)
                    .allowsHitTesting(false)
            }
            .contentShape(.rect)
            .gesture(pan(layout))
            .simultaneousGesture(zoom(layout))
            .simultaneousGesture(crosshair(layout))
            .onChange(of: layout.firstVisible) { _, first in
                if first < 12 && bars.count >= 55 { onNeedOlder() }
            }
        }
        .onChange(of: generation) { _, _ in offset = 0; spacing = 7; cross = nil }
        .onChange(of: followToken) { _, _ in withAnimation(.snappy(duration: 0.35)) { offset = 0 } }
        .onChange(of: bars.last?.time) { old, new in
            // Keep a scrolled-back view still while live bars arrive (lightweight-charts behaviour).
            if let old, let new, new > old, offset > 0.5 { offset += CGFloat((new - old) / period.seconds) }
        }
        .onChange(of: bars.first?.time) { _, _ in }
        .accessibilityElement()
        .accessibilityLabel("\(watermark ?? "") \(period.label) K线")
    }

    // MARK: gestures

    private func pan(_ layout: Layout) -> some Gesture {
        DragGesture(minimumDistance: 4)
            .onChanged { value in
                guard cross == nil else { return }
                if panStart == nil { panStart = offset }
                offset = clampOffset(panStart! + value.translation.width / spacing, layout: layout)
            }
            .onEnded { value in
                guard let start = panStart else { return }
                panStart = nil
                let target = clampOffset(start + value.predictedEndTranslation.width / spacing, layout: layout)
                withAnimation(.smooth(duration: 0.45)) { offset = target }
            }
    }

    private func zoom(_ layout: Layout) -> some Gesture {
        MagnifyGesture()
            .onChanged { value in
                if zoomStart == nil { zoomStart = spacing }
                spacing = min(28, max(2.2, zoomStart! * value.magnification))
            }
            .onEnded { _ in zoomStart = nil; Haptics.tap() }
    }

    private func crosshair(_ layout: Layout) -> some Gesture {
        LongPressGesture(minimumDuration: 0.3)
            .sequenced(before: DragGesture(minimumDistance: 0))
            .onChanged { value in
                if case .second(true, let drag) = value {
                    let point = drag?.location ?? cross ?? .zero
                    cross = CGPoint(x: min(max(point.x, 0), layout.plot.width), y: min(max(point.y, 0), layout.plot.height))
                    let index = layout.index(atX: cross!.x)
                    if index != crossHaptic { crossHaptic = index; Haptics.select() }
                }
            }
            .onEnded { _ in withAnimation(.easeOut(duration: 0.2)) { cross = nil }; crossHaptic = -1 }
    }

    private func clampOffset(_ value: CGFloat, layout: Layout) -> CGFloat {
        let visible = layout.plot.width / spacing
        return min(max(value, -visible * 0.5), max(0, CGFloat(bars.count) - visible * 0.3))
    }

    // MARK: drawing

    private struct Layout {
        let bars: [Candle]
        let spacing: CGFloat
        let rightIndex: CGFloat
        let plot: CGSize
        let period: CandlePeriod
        let firstVisible: Int
        let lastVisible: Int
        let low: Double
        let high: Double

        init(bars: [Candle], spacing: CGFloat, offset: CGFloat, plot: CGSize, rightPad: CGFloat, period: CandlePeriod, range: FibRange?, lines: [ChartLine]) {
            self.bars = bars; self.spacing = spacing; self.plot = plot; self.period = period
            rightIndex = CGFloat(bars.count - 1) + rightPad - offset
            firstVisible = max(0, Int(floor(rightIndex - plot.width / spacing)))
            lastVisible = min(bars.count - 1, Int(ceil(rightIndex)))
            var lo = Double.infinity, hi = -Double.infinity
            if firstVisible <= lastVisible {
                for bar in bars[firstVisible...lastVisible] { lo = min(lo, bar.low); hi = max(hi, bar.high) }
            }
            // The range joins autoscale while its week is on screen (FibRangePrimitive.autoscaleInfo).
            if let range, firstVisible <= lastVisible, bars[lastVisible].time + period.seconds > range.start, bars[firstVisible].time < range.end {
                lo = min(lo, max(0, range.lower)); hi = max(hi, range.upper)
            }
            for line in lines where line.price > 0 { lo = min(lo, line.price); hi = max(hi, line.price) }
            if !lo.isFinite || !hi.isFinite { lo = 0; hi = 1 }
            if hi - lo < hi * 1e-6 { let pad = max(hi * 0.001, 1e-9); lo -= pad; hi += pad }
            let pad = (hi - lo) * 0.07
            // Keep ~28 pt above the highest content for the OHLC legend.
            let legend = (hi - lo + 2 * pad) * Double(28 / max(plot.height - 28, 1))
            low = lo - pad; high = hi + pad + legend
        }

        func x(_ index: CGFloat) -> CGFloat { plot.width - (rightIndex - index) * spacing }
        func y(_ price: Double) -> CGFloat { CGFloat((high - price) / (high - low)) * plot.height }
        func price(atY y: CGFloat) -> Double { high - Double(y / plot.height) * (high - low) }
        func index(atX x: CGFloat) -> Int { min(max(Int((rightIndex - (plot.width - x) / spacing).rounded()), 0), max(0, bars.count - 1)) }

        /// Fractional bar index for a timestamp, extrapolated past either end by the period.
        func index(atTime t: Double) -> CGFloat {
            guard let first = bars.first, let last = bars.last else { return 0 }
            if t <= first.time { return CGFloat((t - first.time) / period.seconds) }
            if t >= last.time { return CGFloat(bars.count - 1) + CGFloat((t - last.time) / period.seconds) }
            var lo = 0, hi = bars.count - 1
            while lo + 1 < hi { let mid = (lo + hi) / 2; if bars[mid].time <= t { lo = mid } else { hi = mid } }
            let a = bars[lo].time, b = bars[hi].time
            return CGFloat(lo) + CGFloat((t - a) / max(b - a, 1))
        }
    }

    private func format(_ value: Double) -> String { value.formatted(.number.precision(.fractionLength(decimals)).grouping(.never)) }

    private func draw(_ context: inout GraphicsContext, size: CGSize, layout: Layout) {
        let plot = layout.plot
        let gridColor = scheme == .dark ? Color.white.opacity(0.06) : Color(red: 0.235, green: 0.235, blue: 0.263).opacity(0.07)
        let textColor = scheme == .dark ? Color.white.opacity(0.55) : Color(red: 0.43, green: 0.43, blue: 0.45)

        if let watermark {
            context.draw(Text(watermark).font(.system(size: 44, weight: .bold)).foregroundStyle(Color.primary.opacity(scheme == .dark ? 0.07 : 0.06)),
                         at: CGPoint(x: plot.width / 2, y: plot.height / 2))
        }

        // Price grid + axis labels.
        let ticks = niceTicks(low: layout.low, high: layout.high, count: 6)
        let lastY = layout.bars.last.map { layout.y($0.close) }
        for value in ticks {
            let y = layout.y(value)
            guard y > 8, y < plot.height - 4 else { continue }
            if let lastY, abs(lastY - y) < 14 { continue }   // the live price tag owns that spot
            context.stroke(Path { $0.move(to: CGPoint(x: 0, y: y)); $0.addLine(to: CGPoint(x: plot.width, y: y)) }, with: .color(gridColor), lineWidth: 1)
            context.draw(Text(format(value)).font(.system(size: 10).monospacedDigit()).foregroundStyle(textColor), at: CGPoint(x: plot.width + 6, y: y), anchor: .leading)
        }

        guard layout.firstVisible <= layout.lastVisible else { return }
        var clipped = context
        clipped.clip(to: Path(CGRect(origin: .zero, size: plot)))

        // Volume (bottom 14 %, own scale).
        let visible = layout.bars[layout.firstVisible...layout.lastVisible]
        let maxVolume = visible.map(\.volume).max() ?? 0
        let body = max(1, layout.spacing * 0.72)
        if maxVolume > 0 {
            for (offset, bar) in visible.enumerated() {
                let x = layout.x(CGFloat(layout.firstVisible + offset))
                let h = CGFloat(bar.volume / maxVolume) * plot.height * 0.14
                clipped.fill(Path(CGRect(x: x - body / 2, y: plot.height - h, width: body, height: h)),
                             with: .color((bar.close >= bar.open ? ChartPalette.up : ChartPalette.down).opacity(0.13)))
            }
        }

        // Range levels, clipped to the completed Monday → next week window.
        if let range {
            let x1 = max(0, layout.x(layout.index(atTime: range.start))), x2 = min(plot.width, layout.x(layout.index(atTime: range.end)))
            if x2 > x1 {
                let levels = range.levels(inner: innerLines)
                for level in levels {
                    let y = layout.y(level.price)
                    guard y >= 0, y <= plot.height else { continue }
                    let (color, width, dashed) = style(level.kind)
                    clipped.stroke(Path { $0.move(to: CGPoint(x: x1, y: y)); $0.addLine(to: CGPoint(x: x2, y: y)) },
                                   with: .color(color), style: StrokeStyle(lineWidth: width, dash: dashed ? [4, 4] : []))
                }
                // Labels: 1.618 and Monday lines first; a label that would overlap one already drawn is skipped.
                var taken: [CGFloat] = []
                for level in levels.sorted(by: { priority($0.kind) < priority($1.kind) }) {
                    let y = layout.y(level.price), labelY = max(14, y - 4)
                    guard y >= 0, y <= plot.height, !taken.contains(where: { abs($0 - labelY) < 13 }) else { continue }
                    let label = Text("\(level.title) (\(format(level.price)))").font(.system(size: 11, weight: .medium)).foregroundStyle(style(level.kind).0)
                    let resolved = clipped.resolve(label)
                    if x2 - x1 > resolved.measure(in: plot).width + 8 {
                        clipped.draw(resolved, at: CGPoint(x: x2 - 4, y: labelY), anchor: .bottomTrailing)
                        taken.append(labelY)
                    }
                }
            }
        }

        // Candles.
        for (offset, bar) in visible.enumerated() {
            let x = layout.x(CGFloat(layout.firstVisible + offset))
            let color = bar.close >= bar.open ? ChartPalette.up : ChartPalette.down
            let top = layout.y(max(bar.open, bar.close)), bottom = layout.y(min(bar.open, bar.close))
            clipped.stroke(Path { $0.move(to: CGPoint(x: x, y: layout.y(bar.high))); $0.addLine(to: CGPoint(x: x, y: layout.y(bar.low))) }, with: .color(color), lineWidth: 1)
            clipped.fill(Path(CGRect(x: x - body / 2, y: top, width: body, height: max(1, bottom - top))), with: .color(color))
        }

        // Six moving averages: SMA solid, EMA dashed; blue / amber / grey for 20 / 60 / 120.
        if averages {
            for (kind, n, color) in [("sma", 20, ChartPalette.accent), ("sma", 60, ChartPalette.amber), ("sma", 120, ChartPalette.reference),
                                     ("ema", 20, ChartPalette.accent), ("ema", 60, ChartPalette.amber), ("ema", 120, ChartPalette.reference)] {
                let values = kind == "sma" ? Indicators.sma(layout.bars, n) : Indicators.ema(layout.bars, n)
                var path = Path(), started = false
                for i in max(layout.firstVisible - 1, n - 1)...max(layout.lastVisible, n - 1) where i < values.count {
                    guard let v = values[i] else { continue }
                    let point = CGPoint(x: layout.x(CGFloat(i)), y: layout.y(v))
                    if started { path.addLine(to: point) } else { path.move(to: point); started = true }
                }
                clipped.stroke(path, with: .color(color.opacity(0.9)), style: StrokeStyle(lineWidth: 1.2, dash: kind == "ema" ? [5, 3] : []))
            }
        }

        // Ticket entry / stop lines.
        for line in lines where line.price > 0 {
            let y = layout.y(line.price)
            clipped.stroke(Path { $0.move(to: CGPoint(x: 0, y: y)); $0.addLine(to: CGPoint(x: plot.width, y: y)) },
                           with: .color(line.color), style: StrokeStyle(lineWidth: 1.4, dash: line.dashed ? [5, 4] : []))
            clipped.draw(Text("\(line.title) \(format(line.price))").font(.system(size: 10, weight: .semibold)).foregroundStyle(line.color),
                         at: CGPoint(x: 6, y: y - 3), anchor: .bottomLeading)
        }

        // Last price tag on the axis.
        if let last = layout.bars.last {
            let y = layout.y(last.close), color = last.close >= last.open ? ChartPalette.up : ChartPalette.down
            if y >= 0 && y <= plot.height {
                clipped.stroke(Path { $0.move(to: CGPoint(x: 0, y: y)); $0.addLine(to: CGPoint(x: plot.width, y: y)) },
                               with: .color(color.opacity(0.55)), style: StrokeStyle(lineWidth: 1, dash: [2, 3]))
                let rect = CGRect(x: plot.width + 1, y: y - 9, width: axisWidth - 2, height: 18)
                context.fill(Path(roundedRect: rect, cornerRadius: 5), with: .color(color))
                context.draw(Text(format(last.close)).font(.system(size: 10, weight: .semibold).monospacedDigit()).foregroundStyle(.white),
                             at: CGPoint(x: rect.midX, y: rect.midY))
            }
        }

        // Time axis.
        let stride = max(1, Int(ceil(78 / layout.spacing)))
        var i = layout.firstVisible - layout.firstVisible % stride
        while i <= layout.lastVisible {
            if i >= 0 {
                let x = layout.x(CGFloat(i))
                if x > 18 && x < plot.width - 18 {
                    context.draw(Text(timeLabel(layout.bars[i].time, previous: i >= stride ? layout.bars[i - stride].time : nil))
                                    .font(.system(size: 10).monospacedDigit()).foregroundStyle(textColor),
                                 at: CGPoint(x: x, y: plot.height + timeHeight / 2 + 1))
                }
            }
            i += stride
        }

        // Crosshair.
        if let cross {
            let index = layout.index(atX: cross.x), x = layout.x(CGFloat(index))
            let line = Color.primary.opacity(0.35)
            context.stroke(Path { $0.move(to: CGPoint(x: x, y: 0)); $0.addLine(to: CGPoint(x: x, y: plot.height)) }, with: .color(line), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
            context.stroke(Path { $0.move(to: CGPoint(x: 0, y: cross.y)); $0.addLine(to: CGPoint(x: plot.width, y: cross.y)) }, with: .color(line), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
            let rect = CGRect(x: plot.width + 1, y: cross.y - 9, width: axisWidth - 2, height: 18)
            context.fill(Path(roundedRect: rect, cornerRadius: 5), with: .color(Color(white: 0.23)))
            context.draw(Text(format(layout.price(atY: cross.y))).font(.system(size: 10, weight: .semibold).monospacedDigit()).foregroundStyle(.white),
                         at: CGPoint(x: rect.midX, y: rect.midY))
        }
    }

    private func priority(_ kind: FibRange.Kind) -> Int {
        switch kind { case .upper, .lower: 0; case .monday: 1; case .mid: 2; case .inner: 3 }
    }

    private func style(_ kind: FibRange.Kind) -> (Color, CGFloat, Bool) {
        switch kind {
        case .upper: (ChartPalette.down, 2, false)
        case .lower: (ChartPalette.up, 2, false)
        case .monday: (ChartPalette.accent, 1, false)
        case .inner: (ChartPalette.accent.opacity(0.5), 1, false)
        case .mid: (ChartPalette.reference, 1, true)
        }
    }

    @ViewBuilder private func legend(_ layout: Layout) -> some View {
        let bar = cross.map { layout.bars.isEmpty ? nil : layout.bars[layout.index(atX: $0.x)] } ?? layout.bars.last
        if let bar {
            HStack(spacing: 8) {
                ForEach([("开", bar.open), ("高", bar.high), ("低", bar.low), ("收", bar.close)], id: \.0) { item in
                    HStack(spacing: 2) {
                        Text(item.0).foregroundStyle(.secondary)
                        Text(format(item.1)).foregroundStyle(bar.close >= bar.open ? ChartPalette.up : ChartPalette.down)
                    }
                }
            }
            .font(.system(size: 10.5, weight: .medium).monospacedDigit())
        }
    }

    private static let dayFormatter: DateFormatter = { let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "MM/dd"; return f }()
    private static let timeFormatter: DateFormatter = { let f = DateFormatter(); f.timeZone = TimeZone(identifier: "Asia/Shanghai"); f.dateFormat = "HH:mm"; return f }()

    private func timeLabel(_ t: Double, previous: Double?) -> String {
        let date = Date(timeIntervalSince1970: t)
        if period == .d1 { return Self.dayFormatter.string(from: date) }
        if let previous, Self.dayFormatter.string(from: Date(timeIntervalSince1970: previous)) != Self.dayFormatter.string(from: date) {
            return Self.dayFormatter.string(from: date)
        }
        return Self.timeFormatter.string(from: date)
    }

    private func niceTicks(low: Double, high: Double, count: Int) -> [Double] {
        let span = high - low
        guard span > 0, span.isFinite else { return [] }
        let raw = span / Double(count), magnitude = pow(10, floor(log10(raw)))
        let step = [1, 2, 2.5, 5, 10].map { $0 * magnitude }.first { $0 >= raw } ?? raw
        var value = ceil(low / step) * step, result: [Double] = []
        while value <= high && result.count < 20 { result.append(value); value += step }
        return result
    }
}

/// SMA / EMA 20 · 60 · 120 (indicators.ts); EMA seeded with the SMA of the first `period` closes.
enum Indicators {
    static func sma(_ bars: [Candle], _ period: Int) -> [Double?] {
        var out = [Double?](repeating: nil, count: bars.count), sum = 0.0
        for (i, bar) in bars.enumerated() {
            sum += bar.close
            if i >= period { sum -= bars[i - period].close }
            if i >= period - 1 { out[i] = sum / Double(period) }
        }
        return out
    }

    static func ema(_ bars: [Candle], _ period: Int) -> [Double?] {
        var out = [Double?](repeating: nil, count: bars.count)
        guard bars.count >= period else { return out }
        let k = 2 / Double(period + 1)
        var value = bars[0..<period].reduce(0) { $0 + $1.close } / Double(period)
        out[period - 1] = value
        for i in period..<bars.count { value = bars[i].close * k + value * (1 - k); out[i] = value }
        return out
    }
}
