"""Paper trading of the Monday 1.618 limit-reclaim v2 rule (owner 2026-10-25).

Owner: monitor the strategy in the signal center as simulated live trading, a
60m confirmation track (recommended) and an isolated 5m research track, both
resting the limit at the 1.618. Rules follow the v2 description
(https://app.notion.com/p/4048856479af81d3bfc5c73c26ed7e35) and the backtests
in experiments/active/exp-monday-limit-reclaim-*:
  fill   = first live weekly-side touch of the 1.618 from MondayFibService
           (OKX USDT swaps, NY Monday range), at the level, maker fee;
  target = resting limit at the near Monday edge, maker fee;
  confirm= close of the m-minute candle after the touch candle (OKX REST bar):
           strictly back inside the 1.618 arms a stop at the entry, otherwise
           exit at that close (taker);
  score  = frozen logistic model (monday_reclaim_score.json, copied from
           exp-monday-limit-reclaim-recent-20261025-v1, fitted on fills before
           2025-01-01); only the top 40 percent (score >= threshold) enters the
           track account, every signal is still tracked as a shadow position;
  sizing = 1000 USDT per track, 10 percent of realized equity per trade, open
           notional <= 1x equity.
Features use only candles closed before the touch bar. Within one 1m update a
stop beats a target; the fill minute can only reach the target if it closes
beyond it. Live features come from OKX, the model from Binance history, and
cluster24 counts OKX symbols, so the score is approximate. No account, key or
order endpoint is used.

Owner 2026-10-25: manual partial/full paper closes keep an immutable entry,
realize tranches at a fresh public quote and serialize with automatic exits.

Owner 2026-10-26: a separate v3 track (API key "v3", frozen in
monday_reclaim_v3.json) runs next to v2 with its own 1000 USDT: 60m
confirmation, entries only at score >= 0.5, target range 0.618 for scores
0.5-0.6 and the opposite 1.618 from 0.6, a resting -30 percent backstop from
the fill until the confirmation (a reclaim replaces it with the entry stop),
and notional linear in score. Lower scores are kept as v3 shadow rows with the
near target. Intrabar order as in the backtest: in any minute, the backstop or
breakeven stop beats a target, and the fill minute's extreme counts against the
backstop.

Owner 2026-10-26 (exp-monday-limit-be-scalein-compare-20261026-v1): every track
moves its breakeven to entry +0.17 percent (fees and slippage) for confirmations
decided from now on; a reclaim close that has not cleared that price exits at
the close ("reclaimed_inside_be"). A separate "v3add" account (own 1000 USDT)
runs v3 plus, for scores >= 0.6, an equal-notional add resting 5 percent beyond
the entry from the fill until the parent's confirmation close. The add shares
the parent's reclaim test (parent entry), breakeven price, backstop and target;
it is cancelled if the parent closes first, except when the parent's backstop
fires in the minute that also reaches the add (the add filled on the way).

Owner 2026-10-26 (booklet spike-HW v1, exp-monday-limit-spike-hw-20261026-v1): a fifth account "spike" (own
1000 USDT) takes score >= 0.6 touches whose 15m EMA order score is >= 3 (EMA 8/13/21/34/55, ewm adjust=False,
on 15m candles closed before the touch's 5m bar, side-aligned as in yoyo.data.indicators); near Monday edge
target, 60m confirmation, -18 percent backstop until the confirmation, breakeven +0.17 percent; 10 slots at 5x:
margin = realized equity / 10, notional = 5 x margin, at most 10 open, a loss never exceeds the margin (the stop
sits inside the 19 percent liquidation distance; liquidation itself is not simulated). Score >= 0.6 touches that
fail the order filter or find no free slot are kept as shadow rows. Every signal stores its EMA order score.

Owner 2026-10-27 (profit lock, exp-monday-limit-be-lock-compare-20261027-v1): a sixth account "v3lock" (own
1000 USDT) runs the v3 rule unchanged except that a reclaim moves its stop to entry +0.5 percent instead of
+0.17 percent; a reclaim close short of that price exits at the close. It runs next to v3 as a forward check:
in sample the lock raised the v3 win rate from 34-40 to 66-73 percent with the same mean.

Owner 2026-10-27 (high-payoff version, exp-monday-limit-langlang-20261027-v1 / exp-monday-limit-pyramid-20261027-v1):
a seventh account "allin3" takes score >= 0.6 touches with the opposite 1.618 target, 60m confirmation, a -30 percent
backstop until the confirmation and breakeven +0.17 percent, all-in at 3x: margin = realized equity, notional = 3 x
margin, one position at a time (later touches are "busy" shadow rows), a loss never exceeds the margin. As in the
sizing replay, a close that leaves the account below 100 USDT is a blow-up: the rest counts as lost, a new 1000 USDT
stake starts and the stake count rises; progress is equity / (1000 x stakes). A backstop at 3x already loses about 90
percent, so liquidation (about -33 percent) is not simulated separately.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from functools import wraps
import uuid
import math
from pathlib import Path
import queue
import sqlite3
import threading
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from yoyo.data.candle_hub import validate

MODEL = Path(__file__).with_name("monday_reclaim_score.json")
V3 = Path(__file__).with_name("monday_reclaim_v3.json")
TRACKS = (60, 5)
V3_TRACK = 1060  # internal id of the v3 rule; API key "v3"
V3ADD_TRACK, V3ADD_LEG = 2060, 2061  # v3 + scale-in account and its add legs; API key "v3add"
CONFIRM = {60: 60, 5: 5, V3_TRACK: 60, V3ADD_TRACK: 60, V3ADD_LEG: 60}
BE_OFFSET_BP = 17.0
ADD = dict(from_score=.6, drawdown_pct=5.)
ACCOUNT = {V3ADD_LEG: V3ADD_TRACK}
SPIKE_TRACK = 3060  # spike-HW v1 account; API key "spike"
CONFIRM[SPIKE_TRACK] = 60
SPIKE = dict(min_score=.6, min_order=3, backstop_pct=18., slots=10, leverage=5.)
V3LOCK_TRACK = 4060  # v3 with a +0.5 percent profit lock at the reclaim; API key "v3lock"
CONFIRM[V3LOCK_TRACK] = 60
V3LOCK = dict(breakeven_offset_bp=50.)
ALLIN3_TRACK = 5060  # all-in 3x high-payoff account; API key "allin3"
CONFIRM[ALLIN3_TRACK] = 60
ALLIN3 = dict(min_score=.6, backstop_pct=30., leverage=3., blowup_below=100.)
KEYS = {V3_TRACK: "v3", V3ADD_TRACK: "v3add", SPIKE_TRACK: "spike", V3LOCK_TRACK: "v3lock", ALLIN3_TRACK: "allin3"}
EMA_SPANS = (8, 13, 21, 34, 55)
MAKER_BP, TAKER_BP, SLIP_BP = 2.0, 5.0, 10.0
START_EQUITY, FRACTION, GROSS = 1000.0, 0.10, 1.0
EXCLUDE = {"BTC", "ETH", "XAU", "XAG", "PAXG", "XAUT"}
MANUAL_QUOTE_MAX_MS = 15_000
MIN5, HOUR, DAY = 300_000, 3_600_000, 86_400_000


def serialized(method):
    """Quote exits, confirmation and manual reductions share one writer lock."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.write_lock:
            return method(self, *args, **kwargs)
    return call


def track_key(track):
    return KEYS.get(account(track), str(track))


def track_id(key):
    return {v: k for k, v in KEYS.items()}.get(str(key)) or int(key)


def ema_order(m15, side, candle_ms):
    """Side-aligned count of ordered EMA pairs on the last 15m candle closed before the touch's 5m bar."""
    touch_bar = candle_ms//MIN5*MIN5
    closes = [b[4] for b in m15 if b[0]+3*MIN5 <= touch_bar]
    if len(closes) < 200:
        raise ValueError("short_history")
    last = []
    for span in EMA_SPANS:
        a, e = 2/(span+1), closes[0]
        for c in closes[1:]:
            e += a*(c-e)
        last.append(e)
    return sum((last[i] >= last[i+1]) if side == 1 else (last[i] <= last[i+1]) for i in range(4))


def account(track):
    return ACCOUNT.get(track, track)


def members(acct):
    return (acct, V3ADD_LEG) if acct == V3ADD_TRACK else (acct,)


class PaperCloseError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class CloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    percentage: float = Field(gt=0, le=100, strict=True, allow_inf_nan=False)
    expected_revision: int = Field(ge=0, strict=True)
    request_id: uuid.UUID


class ScoreModel:
    def __init__(self, path=MODEL):
        spec = json.loads(Path(path).read_text())
        self.features, self.mean, self.scale = spec["features"], spec["mean"], spec["scale"]
        self.coef, self.intercept, self.threshold = spec["coef"], spec["intercept"], spec["top40_threshold"]
        self.trained_before = spec["trained_before"]

    def score(self, x):
        z = self.intercept + sum(c*(x[f]-m)/s for f, c, m, s in zip(self.features, self.coef, self.mean, self.scale))
        return 1/(1+math.exp(-z))


def bars(raw, bar, now):
    """Sorted (ts, open, high, low, close, quote volume) rows from an OKX candle payload."""
    rows = validate(raw, bar, now)
    return [(int(ts), *(float(v) for v in r[1:5]), float(r[7])) for ts, r in sorted(rows.items(), key=lambda kv: int(kv[0]))]


def features(five, hourly, side, levels, touch_ms, opp_first, cluster24):
    """Entry-time features with the backtest definitions (monday_limit_reclaim_patterns{,3})."""
    touch_bar = touch_ms//MIN5*MIN5
    pre = [b for b in five if b[0] < touch_bar]
    hours = [b for b in hourly if b[0]+HOUR <= touch_bar]
    high, low = levels["high"], levels["low"]
    level = levels["lower"] if side == 1 else levels["upper"]
    if len(pre) < 289 or len(hours) < 169:
        raise ValueError("short_history")
    tr = [max(h-l, abs(h-pre[i-1][4]), abs(l-pre[i-1][4])) for i, (_, _, h, l, _, _) in enumerate(pre[-14:], len(pre)-14)]
    vol = sum(tr)/14/pre[-1][4]
    last_hour = sum(b[5] for b in pre[-12:])
    base = sorted(b[5] for b in hours[-169:-1])
    median = (base[83]+base[84])/2
    edge = low if side == 1 else high
    crossed = [b[0] for b in hours if b[0] >= levels["end"] and b[0] < (five[0][0] if five else touch_bar)
               and (b[3] <= edge if side == 1 else b[2] >= edge)]
    crossed += [b[0] for b in five if levels["end"] <= b[0] <= touch_bar and (b[3] <= edge if side == 1 else b[2] >= edge)]
    if not crossed:
        raise ValueError("edge_cross_unknown")
    monday_close = next(b[4] for b in hourly if b[0] == levels["end"]-HOUR)
    pos = (monday_close-low)/(high-low)
    return dict(bucket=math.floor(math.log2(vol)*4), log_vol_ratio=math.log(max(last_hour/median, .05)) if median > 0 else math.log(.05),
                log_approach=math.log1p(max(0., (touch_bar-min(crossed))/60_000)), opp_first=int(opp_first),
                monday_close_side=(1-pos) if side == 1 else pos,
                log_liquidity=math.log10(max(sum(b[5] for b in hours[-168:])/7, 1e4)),
                toward24=-side*100*(pre[-1][4]/pre[-289][4]-1), cluster24=float(cluster24), side=float(side),
                range_pct=100*(high-low)/level)


def settle(side, entry, exit_price, reason):
    gross = side*(exit_price-entry)/entry*1e4
    fee = MAKER_BP+(MAKER_BP if reason == "target" else TAKER_BP)
    return gross, fee, gross-fee, gross-fee-(0. if reason == "target" else SLIP_BP)


class ReclaimPaper:
    def __init__(self, runtime, fib, model_path=MODEL, v3_path=V3, enable_v3=True, enable_v3add=True, enable_spike=True,
                 enable_v3lock=True, enable_allin3=True):
        self.write_lock = threading.RLock()
        self.fib, self.client, self.model = fib, fib.client, ScoreModel(model_path)
        self.v3 = json.loads(Path(v3_path).read_text()) if enable_v3 else None
        self.add = dict(ADD) if self.v3 and enable_v3add else None
        self.spike = dict(SPIKE) if enable_spike else None
        self.v3lock = dict(V3LOCK) if self.v3 and enable_v3lock else None
        self.allin = dict(ALLIN3) if enable_allin3 else None
        self.path = Path(runtime)/"monday-reclaim-paper.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events = queue.Queue(maxsize=200_000)
        self.stop = threading.Event()
        self.thread = None
        self.watch = frozenset()
        self.error = None
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY, week TEXT, symbol TEXT, side INTEGER, level REAL, target REAL,
                    high REAL, low REAL, candle_ms INTEGER, observed_ms INTEGER, status TEXT, score REAL,
                    taken INTEGER, features TEXT, error TEXT, UNIQUE(week, symbol, side));
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY, signal_id INTEGER, track INTEGER, symbol TEXT, side INTEGER,
                    entry REAL, target REAL, fill_ms INTEGER, decision_ms INTEGER, stage TEXT, be_from_ms INTEGER,
                    taken INTEGER, notional REAL, last_seen_ms INTEGER, exit_ms INTEGER, exit_price REAL,
                    reason TEXT, gross_bp REAL, fee_bp REAL, net_bp REAL, net_slip_bp REAL, pnl REAL,
                    note TEXT, UNIQUE(signal_id, track));
                CREATE TABLE IF NOT EXISTS exits (
                    id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL, notional REAL NOT NULL,
                    exit_ms INTEGER NOT NULL, price REAL NOT NULL, reason TEXT NOT NULL,
                    gross_bp REAL, fee_bp REAL, net_bp REAL, net_slip_bp REAL, pnl REAL);
                CREATE TABLE IF NOT EXISTS manual_requests (
                    request_id TEXT PRIMARY KEY, position_id INTEGER, percentage REAL,
                    expected_revision INTEGER, response TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tracks (track INTEGER PRIMARY KEY, equity REAL, started_ms INTEGER);
                CREATE TABLE IF NOT EXISTS refills (
                    id INTEGER PRIMARY KEY, track INTEGER NOT NULL, ms INTEGER NOT NULL, position_id INTEGER,
                    equity_before REAL NOT NULL, equity_after REAL NOT NULL);
            """)
            if "stakes" not in {r["name"] for r in db.execute("PRAGMA table_info(tracks)")}:
                db.execute("ALTER TABLE tracks ADD COLUMN stakes INTEGER NOT NULL DEFAULT 1")
            columns = {r["name"] for r in db.execute("PRAGMA table_info(positions)")}
            for name, spec in {"remaining_notional": "REAL", "realized_pnl": "REAL NOT NULL DEFAULT 0",
                               "close_revision": "INTEGER NOT NULL DEFAULT 0", "manual_exit_ms": "INTEGER",
                               "backstop": "REAL", "be_price": "REAL", "parent_id": "INTEGER", "reclaim_ref": "REAL"}.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE positions ADD COLUMN {name} {spec}")
            if "ema_order" not in {r["name"] for r in db.execute("PRAGMA table_info(signals)")}:
                db.execute("ALTER TABLE signals ADD COLUMN ema_order INTEGER")
            db.execute("UPDATE positions SET remaining_notional=CASE WHEN stage='closed' THEN 0 ELSE notional END "
                       "WHERE remaining_notional IS NULL")
            if "realized_pnl" not in columns:
                db.execute("UPDATE positions SET realized_pnl=COALESCE(pnl,0) WHERE stage='closed'")
            for m in (TRACKS+((V3_TRACK,) if self.v3 else ())+((V3ADD_TRACK,) if self.add else ())+((SPIKE_TRACK,) if self.spike else ())
                      +((V3LOCK_TRACK,) if self.v3lock else ())+((ALLIN3_TRACK,) if self.allin else ())):
                db.execute("INSERT OR IGNORE INTO tracks (track,equity,started_ms) VALUES (?,?,?)", (m, START_EQUITY, self.client.clock()))
        self._refresh_watch()

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # Listener interface used by MondayFibService: enqueue only, never block the stream.
    def on_touch(self, levels, symbol, side, price, observed_ms, candle_ms, source):
        if source == "live":
            self._put(("touch", dict(levels), symbol, side, price, observed_ms, candle_ms))

    def on_quote(self, symbol, quote):
        if symbol in self.watch:
            self._put(("quote", symbol, dict(quote)))

    def _put(self, item):
        try:
            self.events.put_nowait(item)
        except queue.Full:
            self.error = "event_queue_full"

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="reclaim-paper", daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def _run(self):
        last_tick = last_catch = 0
        self._catch_up()
        while not self.stop.is_set():
            try:
                item = self.events.get(timeout=1)
                if item[0] == "touch":
                    self.handle_touch(*item[1:])
                else:
                    self.handle_quote(*item[1:])
            except queue.Empty:
                pass
            except Exception as err:
                self.error = f"{type(err).__name__}"
            now = self.client.clock()
            if now-last_tick >= 5000:
                last_tick = now
                try:
                    self.decide(now)
                    if now-last_catch >= 120_000:
                        last_catch = now
                        self._catch_up()
                except Exception as err:
                    self.error = f"{type(err).__name__}"

    def _refresh_watch(self):
        with self.db() as db:
            self.watch = frozenset(r["symbol"] for r in db.execute("SELECT DISTINCT symbol FROM positions WHERE stage<>'closed'"))

    def _context(self, levels, symbol, side_text, candle_ms):
        other = "upper" if side_text == "lower" else "lower"
        with self.fib.book.db() as db:
            opp = db.execute("SELECT candle_ms FROM touches WHERE week=? AND symbol=? AND side=?",
                             (levels["week"], symbol, other)).fetchone()
            cluster = db.execute("SELECT COUNT(DISTINCT symbol) n FROM touches WHERE side=? AND symbol<>? "
                                 "AND candle_ms>=? AND candle_ms<?", (side_text, symbol, candle_ms-DAY, candle_ms)).fetchone()["n"]
        return bool(opp and opp["candle_ms"] < candle_ms), cluster

    @serialized
    def handle_touch(self, levels, symbol, side_text, price, observed_ms, candle_ms):
        side = 1 if side_text == "lower" else -1
        level = levels[side_text]
        target = levels["low"] if side == 1 else levels["high"]
        status, score, taken, feats, error, order = "scored", None, 0, None, None, None
        if symbol.split("-")[0] in EXCLUDE:
            status = "excluded"
        else:
            try:
                now = self.client.clock()
                five = bars(self.client.get("/api/v5/market/candles", {"instId": symbol, "bar": "5m", "limit": "300"}), "5m", now)
                hourly = bars(self.client.get("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "300"}), "1H", now)
                opp, cluster = self._context(levels, symbol, side_text, candle_ms)
                feats = features(five, hourly, side, levels, candle_ms, opp, cluster)
                score = self.model.score(feats)
                taken = int(score >= self.model.threshold)
            except Exception as err:
                status, error = "feature_error", f"{type(err).__name__}: {err}"[:200]
            try:
                m15 = bars(self.client.get("/api/v5/market/candles", {"instId": symbol, "bar": "15m", "limit": "300"}), "15m", self.client.clock())
                order = ema_order(m15, side, candle_ms)
            except Exception:
                order = None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("INSERT OR IGNORE INTO signals (week,symbol,side,level,target,high,low,candle_ms,observed_ms,"
                             "status,score,taken,features,error,ema_order) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (levels["week"], symbol, side, level, target, levels["high"], levels["low"], candle_ms,
                              observed_ms, status, score, taken, json.dumps(feats) if feats else None, error, order))
            if cur.rowcount != 1 or status == "excluded":
                return
            sid = cur.lastrowid
            for m in TRACKS:
                step = m*60_000
                decision = candle_ms//step*step+2*step
                notional, note = 0., None
                if taken:
                    equity = db.execute("SELECT equity FROM tracks WHERE track=?", (m,)).fetchone()["equity"]
                    opened = db.execute("SELECT COALESCE(SUM(COALESCE(remaining_notional,notional)),0) s FROM positions WHERE track=? AND stage<>'closed' "
                                        "AND taken=1", (m,)).fetchone()["s"]
                    notional = max(0., min(FRACTION*equity, GROSS*equity-opened))
                    if notional <= 0:
                        note = "no_capacity"
                db.execute("INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,decision_ms,stage,"
                           "taken,notional,last_seen_ms,note,remaining_notional) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (sid, m, symbol, side, level, target, candle_ms, decision, "pending_confirm",
                            int(taken and notional > 0), notional, candle_ms, note, notional))
            if self.v3:
                self._open_v3(db, sid, levels, symbol, side, level, candle_ms, score)
            if self.add:
                pid, notional, target3, decision3, backstop3 = self._open_v3(db, sid, levels, symbol, side, level, candle_ms, score, V3ADD_TRACK)
                if notional > 0 and score is not None and score >= self.add["from_score"]:
                    db.execute("INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,decision_ms,stage,taken,"
                               "notional,last_seen_ms,note,remaining_notional,backstop,parent_id,reclaim_ref) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (sid, V3ADD_LEG, symbol, side, level*(1-side*self.add["drawdown_pct"]/100), target3, candle_ms,
                                decision3, "pending_fill", 0, 0., candle_ms, "add_pending", 0., backstop3, pid, level))
            if self.v3lock:
                self._open_v3(db, sid, levels, symbol, side, level, candle_ms, score, V3LOCK_TRACK)
            if self.spike and score is not None and score >= self.spike["min_score"]:
                self._open_spike(db, sid, symbol, side, level, target, candle_ms, order)
            if self.allin and score is not None and score >= self.allin["min_score"]:
                self._open_allin(db, sid, levels, symbol, side, level, candle_ms)
        self._refresh_watch()

    def _open_allin(self, db, sid, levels, symbol, side, level, candle_ms):
        """allin3 row: opposite 1.618 target, -30 percent backstop, notional = 3 x realized equity, one at a time."""
        a = self.allin
        target = levels["upper"] if side == 1 else levels["lower"]
        notional, note = 0., None
        if db.execute("SELECT COUNT(*) n FROM positions WHERE track=? AND stage<>'closed' AND taken=1", (ALLIN3_TRACK,)).fetchone()["n"]:
            note = "busy"
        else:
            equity = db.execute("SELECT equity FROM tracks WHERE track=?", (ALLIN3_TRACK,)).fetchone()["equity"]
            notional = max(0., equity*a["leverage"])
        decision = candle_ms//HOUR*HOUR+2*HOUR
        backstop = level*(1-side*a["backstop_pct"]/100)
        db.execute("INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,decision_ms,stage,"
                   "taken,notional,last_seen_ms,note,remaining_notional,backstop) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, ALLIN3_TRACK, symbol, side, level, target, candle_ms, decision, "pending_confirm",
                    int(notional > 0), notional, candle_ms, note, notional, backstop))

    def _refill(self, db, position_id, ms):
        """Below the blow-up line the rest counts as lost and a new 1000 USDT stake starts (as in the sizing replay)."""
        equity = db.execute("SELECT equity FROM tracks WHERE track=?", (ALLIN3_TRACK,)).fetchone()["equity"]
        if equity < self.allin["blowup_below"]:
            db.execute("INSERT INTO refills (track,ms,position_id,equity_before,equity_after) VALUES (?,?,?,?,?)",
                       (ALLIN3_TRACK, ms, position_id, equity, START_EQUITY))
            db.execute("UPDATE tracks SET equity=?,stakes=stakes+1 WHERE track=?", (START_EQUITY, ALLIN3_TRACK))

    def _leverage(self, track):
        """Margin leverage of a slot whose loss is capped at its margin, else None."""
        if track == SPIKE_TRACK and self.spike:
            return self.spike["leverage"]
        if track == ALLIN3_TRACK and self.allin:
            return self.allin["leverage"]
        return None

    def _open_spike(self, db, sid, symbol, side, level, target, candle_ms, order):
        """spike-HW v1 row: near target, -18 percent backstop, margin = equity / slots at the given leverage."""
        sp = self.spike
        notional, note = 0., None
        if order is None:
            note = "order_missing"
        elif order < sp["min_order"]:
            note = f"order_{order}"
        else:
            busy = db.execute("SELECT COUNT(*) n FROM positions WHERE track=? AND stage<>'closed' AND taken=1", (SPIKE_TRACK,)).fetchone()["n"]
            if busy >= sp["slots"]:
                note = "no_slot"
            else:
                equity = db.execute("SELECT equity FROM tracks WHERE track=?", (SPIKE_TRACK,)).fetchone()["equity"]
                notional = max(0., equity/sp["slots"]*sp["leverage"])
        decision = candle_ms//HOUR*HOUR+2*HOUR
        backstop = level*(1-side*sp["backstop_pct"]/100)
        db.execute("INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,decision_ms,stage,"
                   "taken,notional,last_seen_ms,note,remaining_notional,backstop) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, SPIKE_TRACK, symbol, side, level, target, candle_ms, decision, "pending_confirm",
                    int(notional > 0), notional, candle_ms, note, notional, backstop))

    def _open_v3(self, db, sid, levels, symbol, side, level, candle_ms, score, track=V3_TRACK):
        """v3 row: score-dependent target, -30 percent backstop, notional linear in score."""
        v3, width = self.v3, levels["high"]-levels["low"]
        if score is not None and score >= v3["far_score"]:
            target = levels["upper"] if side == 1 else levels["lower"]
        elif score is not None and score >= v3["min_score"]:
            target = levels["low"]+.618*width if side == 1 else levels["high"]-.618*width
        else:
            target = levels["low"] if side == 1 else levels["high"]
        step = v3["confirm_minutes"]*60_000
        decision = candle_ms//step*step+2*step
        notional, note = 0., None
        if score is not None and score >= v3["min_score"]:
            sz = v3["sizing"]
            k = min(sz["fraction"]*(score-sz["cut"])/sz["mean_excess"], sz["cap"])
            equity = db.execute("SELECT equity FROM tracks WHERE track=?", (track,)).fetchone()["equity"]
            notional = max(0., min(k*equity, GROSS*equity-self._opened(db, track)))
            if notional <= 0:
                note = "no_capacity"
        backstop = level*(1-side*v3["backstop_pct"]/100)
        cur = db.execute("INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,decision_ms,stage,"
                         "taken,notional,last_seen_ms,note,remaining_notional,backstop) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (sid, track, symbol, side, level, target, candle_ms, decision, "pending_confirm",
                          int(notional > 0), notional, candle_ms, note, notional, backstop))
        return cur.lastrowid, notional, target, decision, backstop

    def _opened(self, db, acct):
        tracks = members(acct)
        return db.execute(f"SELECT COALESCE(SUM(COALESCE(remaining_notional,notional)),0) s FROM positions WHERE track IN "
                          f"({','.join('?'*len(tracks))}) AND stage<>'closed' AND taken=1", tracks).fetchone()["s"]

    def _cancel_add(self, db, p, ms, reason):
        db.execute("UPDATE positions SET stage='closed',exit_ms=?,reason=?,taken=0,notional=0,remaining_notional=0,pnl=0 "
                   "WHERE id=? AND stage='pending_fill'", (ms, reason, p["id"]))

    def _try_fill_add(self, db, p, q):
        """Fill a resting add leg from one 1m update, or cancel it; returns the live row or None."""
        parent = db.execute("SELECT * FROM positions WHERE id=?", (p["parent_id"],)).fetchone()
        s = p["side"]
        reached = (q["low"] <= p["entry"]) if s == 1 else (q["high"] >= p["entry"])
        if q["candle_ms"] < p["fill_ms"]//60_000*60_000:
            return None
        if parent is None or parent["stage"] == "closed":
            # The parent's backstop lies beyond the add: in that minute the add filled on the way down.
            if not (parent is not None and parent["reason"] == "backstop" and parent["exit_ms"] == q["candle_ms"] and reached):
                self._cancel_add(db, p, q["candle_ms"], "add_cancelled")
                return None
        elif q["candle_ms"] >= p["decision_ms"]:
            self._cancel_add(db, p, q["candle_ms"], "add_expired")
            return None
        if not reached:
            db.execute("UPDATE positions SET last_seen_ms=MAX(last_seen_ms,?) WHERE id=?", (q["candle_ms"], p["id"]))
            return None
        equity = db.execute("SELECT equity FROM tracks WHERE track=?", (account(p["track"]),)).fetchone()["equity"]
        notional = max(0., min(parent["notional"], GROSS*equity-self._opened(db, account(p["track"]))))
        if notional <= 0:
            self._cancel_add(db, p, q["candle_ms"], "add_no_capacity")
            return None
        db.execute("UPDATE positions SET stage='pending_confirm',taken=1,notional=?,remaining_notional=?,fill_ms=?,last_seen_ms=?,"
                   "note=NULL WHERE id=?", (notional, notional, q["candle_ms"], q["candle_ms"], p["id"]))
        return db.execute("SELECT * FROM positions WHERE id=?", (p["id"],)).fetchone()

    def _record_exit(self, db, p, amount, exit_ms, price, reason):
        """Allocate original entry cost once across each realized tranche."""
        gross, fee, net, net_slip = settle(p["side"], p["entry"], price, reason)
        pnl = amount*net/1e4
        lev = self._leverage(p["track"])
        if lev:
            pnl = max(pnl, -amount/lev)  # a leveraged slot cannot lose more than its margin
        cur = db.execute("INSERT INTO exits (position_id,notional,exit_ms,price,reason,gross_bp,fee_bp,net_bp,net_slip_bp,pnl) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)", (p["id"], amount, exit_ms, price, reason, gross, fee, net, net_slip, pnl))
        remaining = max(0., p["remaining_notional"]-amount)
        db.execute("UPDATE positions SET remaining_notional=?,realized_pnl=realized_pnl+?,close_revision=close_revision+1 "
                   "WHERE id=?", (remaining, pnl, p["id"]))
        db.execute("UPDATE tracks SET equity=equity+? WHERE track=?", (pnl, account(p["track"])))
        if remaining == 0:
            totals = db.execute("SELECT SUM(notional*gross_bp)/? gross,SUM(notional*fee_bp)/? fee,"
                                "SUM(notional*net_bp)/? net,SUM(notional*net_slip_bp)/? slip,SUM(pnl) pnl "
                                "FROM exits WHERE position_id=?", (p["notional"],)*4+(p["id"],)).fetchone()
            db.execute("UPDATE positions SET stage='closed',exit_ms=?,exit_price=?,reason=?,gross_bp=?,fee_bp=?,net_bp=?,"
                       "net_slip_bp=?,pnl=? WHERE id=?", (exit_ms, price, reason, totals["gross"], totals["fee"],
                       totals["net"], totals["slip"], totals["pnl"], p["id"]))
            if p["track"] == ALLIN3_TRACK and self.allin:
                self._refill(db, p["id"], exit_ms)
        return dict(exit_id=cur.lastrowid, position_id=p["id"], symbol=p["symbol"], track=p["track"],
                    closed_notional=amount, closed_quantity=amount/p["entry"], price=price, exit_ms=exit_ms,
                    fee=amount*fee/1e4, pnl=pnl, remaining_notional=remaining, remaining_quantity=remaining/p["entry"],
                    revision=p["close_revision"]+1, fully_closed=remaining == 0, paper_only=True)

    def _close(self, db, p, exit_ms, price, reason):
        # Re-read under the writer lock, never settle a stale selected row twice.
        p = db.execute("SELECT * FROM positions WHERE id=?", (p["id"],)).fetchone()
        if p["stage"] == "closed":
            return
        if p["taken"] and p["notional"] > 0:
            self._record_exit(db, p, p["remaining_notional"], exit_ms, price, reason)
        else:
            gross, fee, net, net_slip = settle(p["side"], p["entry"], price, reason)
            db.execute("UPDATE positions SET stage='closed',exit_ms=?,exit_price=?,reason=?,gross_bp=?,fee_bp=?,net_bp=?,"
                       "net_slip_bp=?,pnl=0,remaining_notional=0 WHERE id=?", (exit_ms, price, reason, gross, fee, net, net_slip, p["id"]))

    @serialized
    def close_position(self, position_id, percentage, expected_revision, request_id):
        """Paper-only reduction at a fresh public quote; no exchange order IO.

        Percentage applies to remaining entry notional. Maker entry cost is
        allocated pro rata; manual exits use the existing taker/slippage model.
        Durable request IDs make uncertain retries safe across process restarts.
        """
        if (isinstance(percentage, bool) or not isinstance(percentage, (float, int))
                or not math.isfinite(percentage) or not 0 < percentage <= 100
                or type(expected_revision) is not int or expected_revision < 0):
            raise PaperCloseError("平仓比例或仓位版本无效", 422)
        try:
            request_id = str(uuid.UUID(str(request_id)))
        except ValueError:
            raise PaperCloseError("平仓请求标识无效", 422)
        with self.db() as db:
            previous = db.execute("SELECT * FROM manual_requests WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if (previous["position_id"], previous["percentage"], previous["expected_revision"]) != (position_id, percentage, expected_revision):
                    raise PaperCloseError("该请求标识已用于另一笔平仓")
                return json.loads(previous["response"])
            p = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        if not p:
            raise PaperCloseError("模拟持仓不存在", 404)
        if p["stage"] == "closed" or not p["taken"] or not p["remaining_notional"]:
            raise PaperCloseError("该记录已平仓或未分配模拟资金")
        if p["close_revision"] != expected_revision:
            raise PaperCloseError("仓位已变化，请刷新后重新选择平仓比例")
        now = self.client.clock()
        with self.fib.lock:
            q = dict(self.fib.quotes.get(p["symbol"], {}))
            connected = self.fib.connected
        if (not connected or not isinstance(q.get("price"), (int, float)) or not math.isfinite(q["price"])
                or q["price"] <= 0 or not 0 <= now-q.get("received_ms", 0) <= MANUAL_QUOTE_MAX_MS
                or not 0 <= now-q.get("candle_ms", 0) < 120_000):
            raise PaperCloseError("行情未连接或报价过期，暂不能模拟平仓")
        # Existing automatic exits have priority over a later manual request.
        self.decide(now, p["symbol"])
        self.handle_quote(p["symbol"], q)
        now = self.client.clock()
        if now-q["received_ms"] > MANUAL_QUOTE_MAX_MS:
            raise PaperCloseError("报价已过期，请重试")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            p = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
            if p["stage"] == "closed" or p["close_revision"] != expected_revision:
                raise PaperCloseError("仓位已自动退出或发生变化，请刷新持仓")
            if p["stage"] == "pending_confirm" and p["decision_ms"]+3000 <= now:
                raise PaperCloseError("此前的确认结算尚未完成，请稍后重试")
            amount = p["remaining_notional"] if percentage == 100 else p["remaining_notional"]*percentage/100
            if (amount <= 0 or p["remaining_notional"]-amount == p["remaining_notional"]
                    or amount == p["remaining_notional"] and percentage != 100):
                raise PaperCloseError("平仓比例过小或精度不足", 422)
            result = self._record_exit(db, p, amount, now, q["price"], "manual")
            db.execute("UPDATE positions SET manual_exit_ms=? WHERE id=?", (now, position_id))
            db.execute("INSERT INTO manual_requests VALUES (?,?,?,?,?)", (request_id, position_id, percentage, expected_revision, json.dumps(result)))
        self._refresh_watch()
        return result

    @serialized
    def handle_quote(self, symbol, q):
        """One 1m candle update (cumulative minute high/low, latest close)."""
        with self.db() as db:
            for p in db.execute("SELECT * FROM positions WHERE symbol=? AND stage<>'closed' ORDER BY (parent_id IS NOT NULL), id",
                                (symbol,)).fetchall():
                if p["stage"] == "pending_fill":
                    p = self._try_fill_add(db, p, q)
                    if p is None:
                        continue
                if p["manual_exit_ms"] and (q["candle_ms"] < p["manual_exit_ms"]//60_000*60_000
                        or q.get("received_ms", p["manual_exit_ms"]) < p["manual_exit_ms"]):
                    continue
                minute = p["fill_ms"]//60_000*60_000
                if q["candle_ms"] < minute:
                    continue
                s, entry, target = p["side"], p["entry"], p["target"]
                after_fill = q["candle_ms"] > minute
                reached = (q["high"] >= target) if s == 1 else (q["low"] <= target)
                closes_beyond = (q["price"] >= target) if s == 1 else (q["price"] <= target)
                be = p["be_price"] if p["be_price"] is not None else entry
                stop = (p["stage"] == "breakeven" and q["candle_ms"] >= p["be_from_ms"]
                        and ((q["low"] <= be) if s == 1 else (q["high"] >= be)))
                bs = p["backstop"]
                backstop = (bs is not None and p["stage"] == "pending_confirm"
                            and ((q["low"] <= bs) if s == 1 else (q["high"] >= bs)))
                exit_ms = q["candle_ms"]
                if p["manual_exit_ms"] and exit_ms <= p["manual_exit_ms"]:
                    # A same-minute automatic fill cannot predate a partial fill.
                    exit_ms = max(p["manual_exit_ms"], q.get("received_ms", exit_ms+60_000))
                if stop:
                    self._close(db, p, exit_ms, be, "breakeven")
                elif backstop:
                    self._close(db, p, exit_ms, bs, "backstop")
                elif reached and (after_fill or closes_beyond):
                    self._close(db, p, exit_ms, target, "target")
                else:
                    db.execute("UPDATE positions SET last_seen_ms=MAX(last_seen_ms,?) WHERE id=?", (q["candle_ms"], p["id"]))
        self._refresh_watch()

    @serialized
    def decide(self, due_by, symbol=None):
        """Confirmation at the close of the m-minute candle after the touch candle."""
        now = self.client.clock()
        with self.db() as db:
            due = db.execute("SELECT * FROM positions WHERE stage='pending_confirm' AND decision_ms+3000<=? "
                             "AND (? IS NULL OR symbol=?)", (due_by, symbol, symbol)).fetchall()
        for p in due:
            step = CONFIRM[p["track"]]*60_000
            bar = "1H" if CONFIRM[p["track"]] == 60 else "5m"
            limit = str(min(300, (now-p["decision_ms"])//step+3))
            try:
                rows = bars(self.client.get("/api/v5/market/candles", {"instId": p["symbol"], "bar": bar, "limit": limit}), bar, now)
            except Exception:
                continue
            window = [b for b in rows if b[0] == p["decision_ms"]-step]
            if not window:
                continue
            close = window[0][4]
            ref = p["reclaim_ref"] if p["reclaim_ref"] is not None else p["entry"]
            offset = self.v3lock["breakeven_offset_bp"] if p["track"] == V3LOCK_TRACK and self.v3lock else BE_OFFSET_BP
            be = ref*(1+p["side"]*offset/1e4)
            inside = close > ref if p["side"] == 1 else close < ref
            beyond_be = close > be if p["side"] == 1 else close < be
            with self.db() as db:
                cur = db.execute("SELECT * FROM positions WHERE id=?", (p["id"],)).fetchone()
                if cur["stage"] != "pending_confirm":
                    continue
                if inside and beyond_be:
                    db.execute("UPDATE positions SET stage='breakeven',be_from_ms=?,be_price=? WHERE id=?", (p["decision_ms"], be, p["id"]))
                elif inside:
                    self._close(db, cur, p["decision_ms"], close, "reclaimed_inside_be")
                else:
                    self._close(db, cur, p["decision_ms"], close, "failed_reclaim")
        with self.db() as db:
            for p in db.execute("SELECT * FROM positions WHERE stage='pending_fill' AND decision_ms+3000<=? AND (? IS NULL OR symbol=?)",
                                (due_by, symbol, symbol)).fetchall():
                self._cancel_add(db, p, p["decision_ms"], "add_expired")
        self._refresh_watch()

    @serialized
    def _catch_up(self):
        """Replay completed 1m candles missed while the stream or process was down."""
        now = self.client.clock()
        with self.db() as db:
            stale = db.execute("SELECT symbol, MIN(last_seen_ms) seen FROM positions WHERE stage<>'closed' "
                               "GROUP BY symbol HAVING seen<?", (now-3*60_000,)).fetchall()
        for row in stale:
            try:
                rows = bars(self.client.get("/api/v5/market/candles", {"instId": row["symbol"], "bar": "1m", "limit": "300"}), "1m", now)
            except Exception:
                continue
            for ts, o, h, l, c, _ in rows:
                if ts >= row["seen"] and ts+60_000 <= now:
                    self.decide(ts, row["symbol"])  # arm or fail confirmations due before this minute
                    self.handle_quote(row["symbol"], {"candle_ms": ts, "high": h, "low": l, "price": c})

    def analytics(self, track=60, days=0):
        from .reclaim_analytics import build_analytics
        track = track_id(track)
        tracks = members(track); marks = ",".join("?"*len(tracks))
        with self.fib.lock:
            quotes, connected = dict(self.fib.quotes), self.fib.connected
        with self.db() as db:
            db.execute("BEGIN")
            acct = dict(db.execute("SELECT * FROM tracks WHERE track=?", (track,)).fetchone())
            positions = [dict(r, track=track) for r in db.execute(f"SELECT * FROM positions WHERE track IN ({marks})", tracks)]
            exits = [dict(r) for r in db.execute(f"SELECT e.* FROM exits e JOIN positions p ON p.id=e.position_id WHERE p.track IN ({marks})", tracks)]
            acct["refills"] = [dict(r) for r in db.execute("SELECT ms time_ms, equity_after-equity_before amount FROM refills "
                                                           "WHERE track=? ORDER BY ms,id", (track,))]
        acct["start_equity"] = START_EQUITY
        return build_analytics(positions, exits, acct, quotes, self.client.clock(), days, connected)

    def snapshot(self):
        with self.fib.lock:
            quotes = dict(self.fib.quotes)
        with self.db() as db:
            db.execute("BEGIN")
            exits = {}
            for row in db.execute("SELECT * FROM exits ORDER BY exit_ms,id"):
                exits.setdefault(row["position_id"], []).append(dict(row))
            tracks = {}
            for t in db.execute("SELECT * FROM tracks ORDER BY track DESC").fetchall():
                m = t["track"]
                tr = members(m); marks = ",".join("?"*len(tr))
                # Read original entry and EMA order scores even after the signal has left the
                # recent-touch page. LEFT JOIN preserves legacy position records.
                # Add legs that never filled are bookkeeping only and are left out.
                closed = [dict(r) for r in db.execute("SELECT p.*, s.score, s.ema_order FROM positions p LEFT JOIN signals s ON s.id=p.signal_id "
                                                     f"WHERE p.track IN ({marks}) AND p.stage='closed' "
                                                     "AND NOT (p.parent_id IS NOT NULL AND p.taken=0) ORDER BY p.exit_ms DESC", tr)]
                for r in closed:
                    r["exits"] = exits.get(r["id"], [])
                taken = [r for r in closed if r["taken"]]
                open_rows, pending_adds = [], []
                for r in db.execute("SELECT p.*, s.score, s.ema_order FROM positions p JOIN signals s ON s.id=p.signal_id "
                                    f"WHERE p.track IN ({marks}) AND p.stage<>'closed' ORDER BY p.fill_ms DESC", tr):
                    r = dict(r); q = quotes.get(r["symbol"], {})
                    if r["stage"] == "pending_fill":  # resting add order, not a position yet
                        pending_adds.append(r)
                        continue
                    r["mark"] = q.get("price")
                    r["quote_ms"] = q.get("received_ms")
                    r["exits"] = exits.get(r["id"], [])
                    r["unrealized_bp"] = r["side"]*(q["price"]-r["entry"])/r["entry"]*1e4 if q.get("price") else None
                    open_rows.append(r)
                if m == ALLIN3_TRACK:
                    extra = dict(stakes=t["stakes"], injected=START_EQUITY*t["stakes"], multiple=t["equity"]/(START_EQUITY*t["stakes"]),
                                 refills=[dict(r) for r in db.execute("SELECT * FROM refills WHERE track=? ORDER BY ms,id", (m,))])
                else:
                    extra = {}
                tracks[track_key(m)] = dict(confirm_minutes=CONFIRM.get(m, m), rule=KEYS.get(m, "v2"), **extra,
                    equity=t["equity"], start_equity=START_EQUITY, started_ms=t["started_ms"],
                    open=open_rows, closed=closed[:100], pending_adds=pending_adds,
                    # Latest fill is independent of the exit-ordered history page.
                    latest_funded=max((r for r in open_rows + closed if r["taken"] == 1 and r["notional"] > 0),
                                      key=lambda r: (r["fill_ms"], r["id"]), default=None),
                    stats=dict(taken_closed=len(taken), wins=sum(r["net_bp"] > 0 for r in taken),
                               mean_net_bp=(sum(r["net_bp"] for r in taken)/len(taken)) if taken else None,
                               shadow_closed=len(closed), shadow_mean_net_bp=(sum(r["net_bp"] for r in closed)/len(closed)) if closed else None))
            signals = [dict(r) for r in db.execute("SELECT * FROM signals ORDER BY candle_ms DESC LIMIT 100")]
        for s in signals:
            s["features"] = json.loads(s["features"]) if s["features"] else None
        return dict(now_ms=self.client.clock(), strategy="monday_1618_limit_reclaim_v2", paper_only=True,
                    model=dict(threshold=self.model.threshold, trained_before=self.model.trained_before, features=self.model.features),
                    sizing=dict(start_equity=START_EQUITY, fraction=FRACTION, gross=GROSS), v3=self.v3, v3add=self.add, spike=self.spike,
                    v3lock=self.v3lock, allin3=self.allin,
                    breakeven_offset_bp=BE_OFFSET_BP,
                    fees_bp=dict(maker=MAKER_BP, taker=TAKER_BP, report_slippage=SLIP_BP),
                    worker=dict(alive=bool(self.thread and self.thread.is_alive()), queue=self.events.qsize(), error=self.error),
                    tracks=tracks, signals=signals)


def install(app, engine):
    app.state.monday_reclaim_paper = engine

    @app.get("/api/monday-reclaim-paper/analytics")
    def analytics(track: Literal["60", "5", "v3", "v3add", "spike", "v3lock", "allin3"] = "60", days: Literal["0", "7", "30"] = "0"):
        return engine.analytics(track, int(days))

    @app.post("/api/monday-reclaim-paper/positions/{position_id}/close")
    def close_position(position_id: int, payload: CloseRequest, request: Request):
        if (request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}"
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
                or request.headers.get("x-spike-action") != "close-reclaim-paper"):
            raise HTTPException(403, "请从模拟盘页面提交平仓")
        try:
            return engine.close_position(position_id, payload.percentage, payload.expected_revision, str(payload.request_id))
        except PaperCloseError as err:
            raise HTTPException(err.status, str(err)) from err

    @app.get("/api/monday-reclaim-paper")
    def snapshot():
        return engine.snapshot()
