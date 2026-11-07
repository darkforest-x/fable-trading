import Foundation
import Observation

/// Display-only candles for one symbol × period (1.x candleSession.ts): history pages through the
/// service relay (/v1/market/candles, public OKX fallback), live bars over the public OKX business
/// WebSocket (`candle<period>` channel). While the socket is down the latest page is polled.
/// Never an execution price or risk input.
@MainActor @Observable
final class CandleStore {
    private(set) var bars: [Candle] = []
    private(set) var symbol = ""
    private(set) var period: CandlePeriod = .m15
    private(set) var loading = false
    private(set) var historyEnd = false
    private(set) var error: String?
    private(set) var streaming = false
    /// Bumps when a new symbol/period replaces the series (chart resets its viewport).
    private(set) var generation = 0

    private var connection: Connection?
    private var socketTask: URLSessionWebSocketTask?
    private var runner: Task<Void, Never>?
    private var olderTask: Task<Void, Never>?
    private var lastSocketMessage = Date()

    func show(symbol: String, period: CandlePeriod, connection: Connection?) {
        guard symbol != self.symbol || period != self.period || connection != self.connection || runner == nil else { return }
        stop()
        self.symbol = symbol; self.period = period; self.connection = connection
        bars = []; historyEnd = false; error = nil; generation += 1
        guard !symbol.isEmpty else { return }
        let generation = generation
        runner = Task { await self.run(generation) }
    }

    func stop() {
        runner?.cancel(); runner = nil
        olderTask?.cancel(); olderTask = nil
        socketTask?.cancel(with: .goingAway, reason: nil); socketTask = nil
        streaming = false
    }

    private func current(_ generation: Int) -> Bool { generation == self.generation && !Task.isCancelled }

    private func run(_ generation: Int) async {
        loading = true
        do {
            let first = try await page(before: nil)
            guard current(generation) else { return }
            bars = Candle.merge(bars, first); error = nil
        } catch {
            if current(generation) { self.error = "K线暂不可用" }
        }
        loading = false
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await self.socketLoop(generation) }
            group.addTask { await self.pollLoop(generation) }
        }
    }

    /// Polls the latest page every 3 s only while the socket is not delivering.
    private func pollLoop(_ generation: Int) async {
        while current(generation) {
            try? await Task.sleep(for: .seconds(3))
            guard current(generation), !streaming else { continue }
            if let latest = try? await page(before: nil), current(generation) {
                bars = Candle.merge(bars, latest); error = nil
            }
        }
    }

    private func socketLoop(_ generation: Int) async {
        var attempt = 0
        while current(generation) {
            let task = URLSession.shared.webSocketTask(with: AppConfig.candleSocketURL)
            socketTask = task
            task.resume()
            let channel = "candle" + period.rawValue
            let subscribe = #"{"op":"subscribe","args":[{"channel":"\#(channel)","instId":"\#(symbol)"}]}"#
            do {
                try await task.send(.string(subscribe))
                lastSocketMessage = Date()
                // Ping every 15 s; a socket silent for 30 s is cancelled, which ends receive() below.
                let watchdog = Task {
                    while !Task.isCancelled {
                        try? await Task.sleep(for: .seconds(15))
                        if Date().timeIntervalSince(self.lastSocketMessage) > 30 { task.cancel(with: .goingAway, reason: nil); return }
                        try? await task.send(.string("ping"))
                    }
                }
                defer { watchdog.cancel() }
                while current(generation) {
                    let message = try await task.receive()
                    lastSocketMessage = Date()
                    guard case .string(let text) = message, text != "pong", let data = text.data(using: .utf8) else { continue }
                    guard let frame = try? JSONDecoder().decode(Frame.self, from: data), frame.arg?.channel == channel, frame.arg?.instId == symbol, let rows = frame.data else { continue }
                    let incoming = Candle.parse(rows)
                    guard current(generation), !incoming.isEmpty else { continue }
                    attempt = 0; streaming = true
                    bars = Candle.merge(bars, incoming)
                }
            } catch {}
            task.cancel(with: .goingAway, reason: nil)
            streaming = false
            guard current(generation) else { return }
            try? await Task.sleep(for: .seconds(min(15, pow(2, Double(attempt)))))
            attempt += 1
        }
    }

    private struct Frame: Decodable {
        struct Arg: Decodable { let channel: String?; let instId: String? }
        let arg: Arg?
        let data: [[String]]?
    }

    /// Older history when the chart nears its left edge.
    func loadOlder() {
        guard olderTask == nil, !historyEnd, let first = bars.first, !symbol.isEmpty else { return }
        let generation = generation
        olderTask = Task {
            defer { if self.generation == generation { olderTask = nil } }
            guard let older = try? await page(before: Int(first.time)), self.generation == generation else { return }
            if older.isEmpty || older.allSatisfy({ $0.time >= first.time }) { historyEnd = true; return }
            bars = Candle.merge(older, bars)
        }
    }

    private func page(before: Int?) async throws -> [Candle] {
        var query = [URLQueryItem(name: "symbol", value: symbol), URLQueryItem(name: "period", value: period.rawValue)]
        if let before { query.append(URLQueryItem(name: "before", value: String(before))) }
        if let connection, let value: CandlePage = try? await API.get("/v1/market/candles", query: query, connection: connection) {
            return Candle.parse(value.data)
        }
        // Public OKX fallback (same rows) only against the production service: a loopback fixture
        // must never mix real market prices into synthetic candles. `after` pages backwards in OKX's naming.
        guard AppConfig.serviceURL == AppConfig.productionURL else { throw APIError(status: 0, message: "K线暂不可用") }
        var components = URLComponents(url: AppConfig.okxRestURL.appendingPathComponent(before == nil ? "api/v5/market/candles" : "api/v5/market/history-candles"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "instId", value: symbol), URLQueryItem(name: "bar", value: period.rawValue), URLQueryItem(name: "limit", value: "300")]
            + (before.map { [URLQueryItem(name: "after", value: String($0 * 1000))] } ?? [])
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = 9
        let (data, _) = try await URLSession.shared.data(for: request)
        return Candle.parse(try JSONDecoder().decode(CandlePage.self, from: data).data)
    }
}
