import Observation
import SwiftUI

/// Today's scored 1.618 touches (/v1/monitor/paper signals), shared by 监控 · 触价, the 1.618 coin strip
/// and the 最新触价 list so every place shows the same score. Polls only while something is watching.
@MainActor @Observable
final class TouchFeed {
    private(set) var signals: [PaperSignal] = []
    private(set) var nowMs: Double = 0
    private(set) var error: String?
    private(set) var loaded = false
    private var watchers = 0

    /// Highest score seen today per symbol.
    var scoreBySymbol: [String: Double] {
        signals.reduce(into: [:]) { out, s in if let v = s.score { out[s.symbol] = max(out[s.symbol] ?? -1, v) } }
    }

    func watch(connection: Connection, session: SessionStore) async {
        watchers += 1
        defer { watchers -= 1 }
        while !Task.isCancelled {
            do {
                let next: PaperSnapshot = try await API.get("/v1/monitor/paper", query: [URLQueryItem(name: "track", value: "live-wick6x3")], connection: connection)
                withAnimation(.snappy) { signals = next.signals.uniqued { $0.rowId }; nowMs = next.nowMs }
                error = nil; loaded = true
            } catch is CancellationError {
                return
            } catch {
                self.error = error.localizedDescription; session.handle(error)
            }
            try? await Task.sleep(for: .seconds(15))
        }
    }
}

/// Score badge: scores at or above the owner's threshold are highlighted (Settings · 触价评分高亮).
struct ScoreBadge: View {
    let score: Double?
    @AppStorage("touch.highScore") private var threshold = 0.5
    var body: some View {
        let high = (score ?? -1) >= threshold
        Text(score.map { String(format: "%.2f", $0) } ?? "—")
            .font(.system(size: 13, weight: high ? .bold : .semibold).monospacedDigit())
            .foregroundStyle(high ? Color.white : Color.muted)
            .padding(.horizontal, high ? 8 : 0).padding(.vertical, high ? 3 : 0)
            .background {
                if high {
                    Capsule().fill(LinearGradient(colors: [Color(red: 1, green: 0.62, blue: 0.04), Color(red: 0.96, green: 0.45, blue: 0.1)],
                                                  startPoint: .topLeading, endPoint: .bottomTrailing))
                }
            }
            .accessibilityLabel("评分 \(score.map { String(format: "%.2f", $0) } ?? "无")\(high ? "，高分" : "")")
    }
}
