import Foundation

/// Fixed production service; DEBUG builds may point at the loopback fixture
/// (`-SpikeServiceURL http://127.0.0.1:18923`) for simulator acceptance only.
enum AppConfig {
    static let productionURL = URL(string: "https://discipline.spikeok.com")!

    static var serviceURL: URL {
        #if DEBUG
        if let value = UserDefaults.standard.string(forKey: "SpikeServiceURL"),
           let url = URL(string: value), ["127.0.0.1", "localhost"].contains(url.host ?? "") {
            return url
        }
        #endif
        return productionURL
    }

    static let okxRestURL = URL(string: "https://www.okx.com")!

    /// Public OKX business channel for live candles; the loopback fixture serves the same frames.
    static var candleSocketURL: URL {
        if let host = serviceURL.host, ["127.0.0.1", "localhost"].contains(host), let port = serviceURL.port {
            return URL(string: "ws://\(host):\(port)/ws/v5/business")!
        }
        return URL(string: "wss://ws.okx.com/ws/v5/business")!
    }

    #if DEBUG
    /// Fixture-only token injected by a launch argument; never used in release builds.
    static var qaToken: String? { UserDefaults.standard.string(forKey: "SpikeQAToken") }
    static var qaTab: String? { UserDefaults.standard.string(forKey: "SpikeQATab") }
    static var qaSegment: String? { UserDefaults.standard.string(forKey: "SpikeQASegment") }
    static var qaWidgets: Bool { UserDefaults.standard.bool(forKey: "SpikeQAWidgets") }
    #endif
}
