import Observation
import SwiftUI

/// Recent scored 1.618 touches (/v1/monitor/paper signals) plus this week's per-touch scores
/// (/v1/monitor/touch-scores), shared by 监控 · 触价, the 1.618 coin strip, the chart markers and the
/// 最新触价 list so every place shows the same score for the same touch. Polls only while watched.
///
/// Owner 2026-11-08 item 1: a row shows the score of ITS touch (one per week × coin × edge), never the
/// coin's best score of the day. Before the service has the touch-scores route, recent signals are
/// matched to a touch by coin, edge and observation time instead.
@MainActor @Observable
final class TouchFeed {
    private(set) var signals: [PaperSignal] = []
    private(set) var week: [String: TouchScore] = [:]
    private(set) var nowMs: Double = 0
    private(set) var error: String?
    private(set) var loaded = false
    private var weekLoadedAt = Date.distantPast
    private var weekUnsupported = false

    static func key(_ symbol: String, _ edge: Edge) -> String { "\(symbol)|\(edge.rawValue)" }

    /// The score of one touch; nil while unknown (not scored yet, or touched before monitoring).
    func score(_ symbol: String, _ touch: FibTouch?) -> Double? {
        guard let touch else { return nil }
        if let hit = week[Self.key(symbol, touch.side)] { return hit.score }
        return Self.match(signals, symbol: symbol, touch: touch)?.score
    }

    /// Recent-signal fallback: same coin, the edge's direction (lower → long), observed within 10 minutes.
    nonisolated static func match(_ signals: [PaperSignal], symbol: String, touch: FibTouch) -> PaperSignal? {
        let side: Side = touch.side == .lower ? .long : .short
        return signals.filter { $0.symbol == symbol && $0.side == side && abs(($0.observedMs ?? 0) - touch.occurredMs) <= 600_000 }
            .min { abs(($0.observedMs ?? 0) - touch.occurredMs) < abs(($1.observedMs ?? 0) - touch.occurredMs) }
    }

    /// This week's touches of one contract as chart markers, each with its own score.
    func markers(_ row: FibContract?) -> [ChartMarker] {
        guard let row else { return [] }
        return row.touches.values.map { touch in
            ChartMarker(time: touch.occurredMs / 1000, price: touch.side == .upper ? row.upper : row.lower, edge: touch.side,
                        score: score(row.symbol, touch))
        }
    }

    func watch(connection: Connection, session: SessionStore) async {
        while !Task.isCancelled {
            do {
                let next: PaperSnapshot = try await API.get("/v1/monitor/paper", query: [URLQueryItem(name: "track", value: "live-wick6x3")], connection: connection)
                withAnimation(.snappy) { signals = next.signals.uniqued { $0.rowId }; nowMs = next.nowMs }
                WidgetBridge.touches(signals)
                error = nil; loaded = true
            } catch is CancellationError {
                return
            } catch {
                self.error = error.localizedDescription; session.handle(error)
            }
            await loadWeek(connection: connection)
            try? await Task.sleep(for: .seconds(15))
        }
    }

    private func loadWeek(connection: Connection) async {
        guard !weekUnsupported, Date().timeIntervalSince(weekLoadedAt) >= 30 else { return }
        do {
            let next: TouchScores = try await API.get("/v1/monitor/touch-scores", connection: connection)
            week = Dictionary(next.scores.map { (Self.key($0.symbol, $0.side), $0) }, uniquingKeysWith: { a, _ in a })
            weekLoadedAt = Date()
        } catch let error as APIError where error.status == 404 {
            weekUnsupported = true   // older service: keep the recent-signal match
        } catch {}
    }
}

struct TouchScore: Codable, Hashable { let symbol: String; let side: Edge; let candleMs: Double?; let score: Double?; let status: String }
struct TouchScores: Codable { let week: String?; let scores: [TouchScore] }

/// Score badge: scores at or above the owner's threshold are highlighted (Settings · 触价评分高亮).
struct ScoreBadge: View {
    let score: Double?
    /// Coin-strip size (Stitch 5640c0c0…: “TIA 0.88” capsule after the coin name).
    var small = false
    @AppStorage("touch.highScore") private var threshold = 0.5
    var body: some View {
        let high = (score ?? -1) >= threshold
        Text(score.map { String(format: "%.2f", $0) } ?? "—")
            .font(.scaled(size: small ? 10 : 13, weight: high ? .bold : .semibold).monospacedDigit())
            .lineLimit(1).fixedSize()
            .foregroundStyle(high ? Color.white : Color.muted)
            .padding(.horizontal, high ? (small ? 5 : 8) : 0).padding(.vertical, high ? (small ? 1.5 : 3) : 0)
            .background {
                if high {
                    Capsule().fill(LinearGradient(colors: [Color(red: 1, green: 0.62, blue: 0.04), Color(red: 0.96, green: 0.45, blue: 0.1)],
                                                  startPoint: .topLeading, endPoint: .bottomTrailing))
                }
            }
            .accessibilityLabel("评分 \(score.map { String(format: "%.2f", $0) } ?? "无")\(high ? "，高分" : "")")
    }
}
