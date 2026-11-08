import Foundation
import Observation

/// Display-only candles for one symbol × period (1.x candleSession.ts): history pages through the
/// service relay (/v1/market/candles, public OKX fallback), live bars over the public OKX business
/// WebSocket (`candle<period>` channel). While the socket is down the latest page is polled.
/// Never an execution price or risk input.
@MainActor @Observable
final class CandleStore {
    /// Bars at `period` (12H is aggregated from the 4H source series).
    private(set) var bars: [Candle] = []
    private var raw: [Candle] = [] { didSet { bars = period == .h12 ? Candle.aggregate(raw, seconds: period.seconds, source: period.source.seconds) : raw } }
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
        historyEnd = false; error = nil; generation += 1
        // Draw the cached series at once (owner 2026-11-08 “启动加载 K 线很慢”), then refresh it.
        raw = symbol.isEmpty ? [] : CandleCache.read(symbol: symbol, period: period.source)
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
            // A cached series older than the fresh page's first bar may have a gap: start clean then.
            if let oldest = first.first?.time, let cachedLast = raw.last?.time, cachedLast < oldest { raw = [] }
            raw = Candle.merge(raw, first); error = nil
            CandleCache.write(symbol: symbol, period: period.source, bars: raw)
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
                raw = Candle.merge(raw, latest); error = nil
                CandleCache.write(symbol: symbol, period: period.source, bars: raw)
            }
        }
    }

    private func socketLoop(_ generation: Int) async {
        var attempt = 0
        while current(generation) {
            let task = URLSession.shared.webSocketTask(with: AppConfig.candleSocketURL)
            socketTask = task
            task.resume()
            let channel = "candle" + period.source.rawValue
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
                    raw = Candle.merge(raw, incoming)
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
        guard olderTask == nil, !historyEnd, let first = raw.first, !symbol.isEmpty else { return }
        let generation = generation
        olderTask = Task {
            defer { if self.generation == generation { olderTask = nil } }
            guard let older = try? await page(before: Int(first.time)), self.generation == generation else { return }
            if older.isEmpty || older.allSatisfy({ $0.time >= first.time }) { historyEnd = true; return }
            raw = Candle.merge(older, raw)
        }
    }

    func stopAndSave() { if !symbol.isEmpty { CandleCache.write(symbol: symbol, period: period.source, bars: raw) }; stop() }

    private func page(before: Int?) async throws -> [Candle] {
        try await Self.page(symbol: symbol, period: period.source, before: before, connection: connection)
    }

    /// One page of OKX rows: service relay first, public OKX only against the production service.
    static func page(symbol: String, period: CandlePeriod, before: Int?, connection: Connection?) async throws -> [Candle] {
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

/// Native on-disk candle cache (Caches/candles): the last 800 bars per service host × symbol × period,
/// written off the main thread. Keyed by host so fixture and production series never mix.
enum CandleCache {
    private static let queue = DispatchQueue(label: "candle-cache", qos: .utility)
    private static var directory: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("candles", isDirectory: true)
    }
    private static func url(_ symbol: String, _ period: CandlePeriod) -> URL {
        let host = (AppConfig.serviceURL.host ?? "service") + "-" + String(AppConfig.serviceURL.port ?? 443)
        return directory.appendingPathComponent("\(host)_\(symbol)_\(period.rawValue).bin")
    }

    static func read(symbol: String, period: CandlePeriod) -> [Candle] {
        guard let data = try? Data(contentsOf: url(symbol, period)),
              let rows = try? PropertyListDecoder().decode([[Double]].self, from: data) else { return [] }
        let bars = rows.compactMap { r -> Candle? in
            r.count == 7 ? Candle(time: r[0], open: r[1], high: r[2], low: r[3], close: r[4], volume: r[5], confirmed: r[6] == 1) : nil
        }
        // A week-old series is not worth drawing.
        guard let last = bars.last, Date().timeIntervalSince1970 - last.time < 7 * 86_400 else { return [] }
        return bars
    }

    static func write(symbol: String, period: CandlePeriod, bars: [Candle]) {
        let rows = bars.suffix(800).map { [$0.time, $0.open, $0.high, $0.low, $0.close, $0.volume, $0.confirmed ? 1 : 0] }
        let target = url(symbol, period)
        queue.async {
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let encoder = PropertyListEncoder(); encoder.outputFormat = .binary
            if let data = try? encoder.encode(Array(rows)) { try? data.write(to: target, options: .atomic) }
        }
    }
}
