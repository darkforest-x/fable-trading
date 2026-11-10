import Foundation

// Read-only projections served by yoyo/discipline_app/monitor_feed.py (snake_case JSON).
enum Side: String, Codable { case long, short }
enum Edge: String, Codable { case upper, lower }

struct Health: Codable { let service: String; let mode: String; let liveEnabled: Bool; let version: String }
struct StateProbe: Codable { let mode: String; let accountId: String? }

struct PaperAccount: Codable, Identifiable, Hashable {
    struct Stats: Codable, Hashable { let closed: Double?; let wins: Double?; let meanNetBp: Double?; var winPct: Double? = nil }
    /// live-breadth rule (breadth_paper.py RULE): coin-count band, observation and holding window.
    struct BreadthRule: Codable, Hashable {
        let countMin: Double?
        let countMax: Double?
        let barMinutes: Double?
        let observeMinutes: Double?
        let holdDays: Double?
        let funded: String?
        let shadows: [String]
        let leverage: Double?
        let takerBp: Double?
    }
    struct Hold: Codable, Hashable { let symbol: String?; let fillMs: Double?; let dueMs: Double? }
    let key: String
    let label: String
    let group: String
    let equity: Double?
    let startEquity: Double?
    let returnPct: Double?
    let startedMs: Double?
    let stats: Stats
    // 2.7.0 strategy frame; optional so a 2.6 service still decodes.
    var title: String? = nil
    var kind: String? = nil
    var pnl: Double? = nil
    var unrealizedPnl: Double? = nil
    var markedEquity: Double? = nil
    var markedPnl: Double? = nil
    var markedReturnPct: Double? = nil
    var refilled: Bool? = nil
    var openCount: Int? = nil
    var slotCount: Int? = nil
    var phase: String? = nil
    var heartbeatMs: Double? = nil
    var breadthRule: BreadthRule? = nil
    var hold: Hold? = nil
    var id: String { key }
    var isBreadth: Bool { kind == "breadth" }
    var isCloud: Bool { kind == "cloud" || key == "live-cloud-v17" }
    var displayEquity: Double? { isCloud ? markedEquity ?? equity : equity }
    var displayPnl: Double? { isCloud ? markedPnl ?? pnl : pnl }
    var displayReturnPct: Double? { isCloud ? markedReturnPct ?? returnPct : returnPct }
    var subtitle: String { title ?? "" }
}

/// A strategy with a research booklet but no forward paper account (breadth-ema20).
struct ResearchStrategy: Codable, Identifiable, Hashable {
    let key: String
    let label: String
    let title: String
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
    /// Breadth comparison row (BTC/ETH): never moves the account.
    var shadow: Bool? = nil
    /// Breadth holding: the six-day exit time.
    var dueMs: Double? = nil
    var rowKey: String { "\(Int(id ?? 0))-\(symbol)-\(Int(fillMs ?? 0))" }
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

/// One market-wide breadth signal of live-breadth (breadth_paper.py ``paper_signal``).
struct BreadthSignal: Codable, Identifiable, Hashable {
    let id: Double?
    let symbol: String?
    let status: String
    let candleMs: Double?
    let observedMs: Double?
    let decisionMs: Double?
    let n: Double?
    let newN: Double?
    var rowId: String { "\(Int(id ?? 0))-\(Int(candleMs ?? 0))" }
}

struct PaperSnapshot: Codable {
    struct Detail: Codable {
        let key: String
        let open: [PaperPosition]
        let closed: [PaperPosition]
        var touches: [PaperSignal]? = nil
        var breadthSignals: [BreadthSignal]? = nil
        var cloudEvents: [CloudEvent]? = nil
    }
    let nowMs: Double
    let accounts: [PaperAccount]
    let detail: Detail
    let signals: [PaperSignal]
    var research: [ResearchStrategy]? = nil
    var equityBasis: String? = nil
}

/// Read-only signal-filter outcome from the independent cloud-v17 paper account.
struct CloudEvent: Codable, Hashable, Identifiable {
    let id: Double?
    let symbol: String
    let side: Side
    let status: String
    let reason: String?
    let timeMs: Double?
    let entryPrice: Double?
    var rowId: String { "\(Int(id ?? timeMs ?? 0))-\(symbol)-\(status)" }
}

/// Older records of one account (before today), 50 per page.
struct PaperHistoryPage: Codable {
    let track: String
    let tab: String
    let total: Int
    let hasMore: Bool
    let nowMs: Double?
    let count: Int
    let positions: [PaperPosition]
    let touches: [PaperSignal]
    let breadthSignals: [BreadthSignal]
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
    // Additive monthly 1.618 contract. APNs status is separate from TG/Bark receipts.
    var type: String? = nil
    var eventId: String? = nil
    var month: String? = nil
    var occurredMs: Double? = nil
    var candleMs: Double? = nil
    var createdMs: Double? = nil
    var expiresMs: Double? = nil
    var freshUntilMs: Double? = nil
    var source: String? = nil
    var touchConfirmed: Bool? = nil
    var candleConfirmed: Bool? = nil
    var apnsStatus: String? = nil

    var isLiveMonthTouch: Bool { kind == "month_first_week_touch" && source == "live" && touchConfirmed == true }
}

struct NotificationSnapshot: Codable {
    struct Channels: Codable {
        struct Fib: Codable { let bark: Bool; let barkConfigured: Bool; let barkSent: Int; let telegram: Bool; let minScore: Double? }
        struct AlarmChannel: Codable { let bark: Bool; let telegram: Bool; let threshold: Int?; let windowMinutes: Int? }
        struct MonthFirstWeekTouch: Codable {
            let bark: Bool
            let barkConfigured: Bool
            let barkSent: Int
            let apnsConfigured: Bool
            let apnsStatus: DeliveryStatus?
            struct DeliveryStatus: Codable { let counts: [String: Int]?; let cursor: Int? }
        }
        let fib: Fib
        let alarm: AlarmChannel
        var monthFirstWeekTouch: MonthFirstWeekTouch? = nil
    }
    let nowMs: Double
    let events: [NotificationEvent]
    let channels: Channels
}

struct MonthFirstWeekTouch: Codable, Identifiable, Hashable {
    let id: Int
    let eventId: String
    let type: String
    let month: String
    let symbol: String
    let side: Edge
    let level: Double
    let price: Double
    let candleExtreme: Double
    let occurredMs: Double
    let candleMs: Double
    let createdMs: Double
    let expiresMs: Double
    let freshUntilMs: Double
    let source: String
    let touchConfirmed: Bool
    let candleConfirmed: Bool
}

struct MonthFirstWeekItem: Codable, Identifiable, Hashable {
    var id: String { symbol }
    let symbol: String
    let state: String
    let high: Double?
    let low: Double?
    let upper: Double?
    let lower: Double?
    let updatedMs: Double?
    let historyGaps: Int?
}

struct MonthFirstWeekSnapshot: Codable {
    struct Window: Codable {
        let month: String
        let startMs: Double
        let readyMs: Double?
        let expiresMs: Double
        let complete: Bool
    }
    struct Coverage: Codable {
        let universeSize: Int
        let readySymbols: Int
        let incompleteSymbols: Int
        let pendingSymbols: Int
        let expected1hBars: Int?
        let source: String?
    }
    struct Stream: Codable { let connected: Bool; let lastMessageMs: Double?; let error: String? }
    struct Worker: Codable {
        let alive: Bool
        let lastCycleMs: Double?
        let error: String?
        let symbolCount: Int?
        let readyCount: Int?
        let incompleteCount: Int?
        let pendingCount: Int?
    }
    let schema: String
    let nowMs: Double
    let timezone: String
    let month: String
    let window: Window
    let coverage: Coverage
    let stream: Stream
    let worker: Worker
    let items: [MonthFirstWeekItem]
    let touches: [MonthFirstWeekTouch]
}
