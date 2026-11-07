import Foundation

// Read-only projections served by yoyo/discipline_app/monitor_feed.py (snake_case JSON).
enum Side: String, Codable { case long, short }
enum Edge: String, Codable { case upper, lower }

struct Health: Codable { let service: String; let mode: String; let liveEnabled: Bool; let version: String }
struct StateProbe: Codable { let mode: String; let accountId: String? }

struct PaperAccount: Codable, Identifiable, Hashable {
    struct Stats: Codable, Hashable { let closed: Double?; let wins: Double?; let meanNetBp: Double? }
    let key: String
    let label: String
    let group: String
    let equity: Double?
    let startEquity: Double?
    let returnPct: Double?
    let startedMs: Double?
    let stats: Stats
    var id: String { key }
}

struct PaperPosition: Codable, Identifiable, Hashable {
    let id: Double?
    let symbol: String
    let side: Side
    let entry: Double?
    let target: Double?
    let stage: String
    let funded: Bool
    let notional: Double?
    let fillMs: Double?
    let score: Double?
    let mark: Double?
    let unrealizedBp: Double?
    let stop: Double?
    let exitPrice: Double?
    let exitMs: Double?
    let reason: String?
    let netBp: Double?
    let pnl: Double?
}

struct PaperSignal: Codable, Identifiable, Hashable {
    let id: Double?
    let symbol: String
    let side: Side
    let level: Double?
    let score: Double?
    let status: String
    let taken: Bool
    let observedMs: Double?
    var rowId: String { "\(id ?? 0)-\(symbol)" }
}

struct PaperSnapshot: Codable {
    struct Detail: Codable { let key: String; let open: [PaperPosition]; let closed: [PaperPosition] }
    let nowMs: Double
    let accounts: [PaperAccount]
    let detail: Detail
    let signals: [PaperSignal]
}

struct Alarm: Codable, Identifiable, Hashable {
    let id: Double?
    let side: Edge
    let firstMs: Double?
    let lastMs: Double?
    let n: Int
    let symbols: [String]
    let active: Bool
    let acknowledged: Bool
    let test: Bool
    var rowId: Double { id ?? firstMs ?? 0 }
}

struct AlertSnapshot: Codable {
    let nowMs: Double
    let threshold: Int?
    let windowMinutes: Int?
    let workerAlive: Bool
    let alerts: [Alarm]
}

struct CloudSignal: Codable, Identifiable, Hashable {
    struct Band: Codable, Hashable { let high: Double?; let low: Double?; let upper: Double?; let lower: Double? }
    let id: String
    let symbol: String
    let side: Side
    let timeframe: String
    let confirmedMs: Double?
    let entry: Double?
    let stop: Double?
    let stopPct: Double?
    let target: Double?
    let plannedRr: Double?
    let freshness: String
    let historical: Bool
    let week: String
    let range: Band
}

struct CloudSnapshot: Codable {
    struct Status: Codable { let state: String; let total: Int?; let completed: Int?; let lastScanMs: Double?; let workerAlive: Bool }
    let nowMs: Double
    let nextCloseMs: Double?
    let total: Int
    let status: Status
    let items: [CloudSignal]
}

struct NotificationEvent: Codable, Identifiable, Hashable {
    let id: String
    let kind: String          // "alarm" | "fib"
    let side: Edge
    let atMs: Double?
    let pushed: Bool
    let symbol: String?
    let level: Double?
    let price: Double?
    let status: String?
    let n: Int?
    let symbols: [String]?
    let active: Bool?
    let test: Bool?
    let acknowledged: Bool?
}

struct NotificationSnapshot: Codable {
    struct Channels: Codable {
        struct Fib: Codable { let bark: Bool; let barkConfigured: Bool; let barkSent: Int; let telegram: Bool; let minScore: Double? }
        struct AlarmChannel: Codable { let bark: Bool; let telegram: Bool; let threshold: Int?; let windowMinutes: Int? }
        let fib: Fib
        let alarm: AlarmChannel
    }
    let nowMs: Double
    let events: [NotificationEvent]
    let channels: Channels
}
