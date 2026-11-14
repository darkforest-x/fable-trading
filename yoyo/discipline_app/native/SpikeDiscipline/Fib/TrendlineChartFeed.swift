import Foundation
import Observation

struct TrendlineChartSnapshot: Equatable {
    let routeId: Int
    let symbol: String
    let period: CandlePeriod
    let candles: [Candle]
    let markerMs: Double
}

/// Loads real OKX candles around one verified trendline event and checks the 1H close against the
/// event record before the chart can present its historical overlay.
@MainActor @Observable
final class TrendlineChartFeed {
    private(set) var snapshot: TrendlineChartSnapshot?
    private(set) var loading = false
    private(set) var error: String?

    func load(route: TrendlineEventRoute, period: CandlePeriod, connection: Connection?) async {
        loading = true
        error = nil
        defer { loading = false }

        let event = route.event
        guard event.isValidUpbreak else {
            snapshot = nil
            error = "趋势突破记录无效"
            return
        }
        let step = period.seconds * 1000
        let markerMs = floor((event.confirmedMs - 1) / step) * step
        guard markerMs.isFinite, markerMs > 0, let before = Int(exactly: (markerMs + step) / 1000) else {
            snapshot = nil
            error = "历史 K 线时间无效"
            return
        }

        do {
            let source = try await CandleStore.page(symbol: event.symbol, period: period.source,
                                                     before: before, connection: connection)
            let bars = period == .h12
                ? Candle.aggregate(source, seconds: period.seconds, source: period.source.seconds)
                : source
            guard !Task.isCancelled else { return }
            guard bars.contains(where: { $0.time * 1000 == markerMs && $0.confirmed }) else {
                throw APIError(status: 0, message: "此周期没有已收盘的突破 K 线")
            }
            if period == .h1 {
                guard let hit = bars.first(where: { $0.time * 1000 == event.candleMs }), hit.confirmed,
                      abs(hit.close - event.close) <= 1e-9 * max(event.close, 1e-12) else {
                    throw APIError(status: 0, message: "历史收盘价与突破记录不一致")
                }
            }
            snapshot = TrendlineChartSnapshot(routeId: route.id, symbol: event.symbol, period: period,
                                              candles: bars, markerMs: markerMs)
            error = nil
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            snapshot = nil
            self.error = error.localizedDescription
        }
    }
}
