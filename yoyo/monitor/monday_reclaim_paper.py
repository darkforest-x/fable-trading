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
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import queue
import sqlite3
import threading

from yoyo.data.candle_hub import validate

MODEL = Path(__file__).with_name("monday_reclaim_score.json")
TRACKS = (60, 5)
MAKER_BP, TAKER_BP, SLIP_BP = 2.0, 5.0, 10.0
START_EQUITY, FRACTION, GROSS = 1000.0, 0.10, 1.0
EXCLUDE = {"BTC", "ETH", "XAU", "XAG", "PAXG", "XAUT"}
MIN5, HOUR, DAY = 300_000, 3_600_000, 86_400_000


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
    def __init__(self, runtime, fib, model_path=MODEL):
        self.fib, self.client, self.model = fib, fib.client, ScoreModel(model_path)
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
                CREATE TABLE IF NOT EXISTS tracks (track INTEGER PRIMARY KEY, equity REAL, started_ms INTEGER);
            """)
            for m in TRACKS:
                db.execute("INSERT OR IGNORE INTO tracks VALUES (?,?,?)", (m, START_EQUITY, self.client.clock()))
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

    def handle_touch(self, levels, symbol, side_text, price, observed_ms, candle_ms):
        side = 1 if side_text == "lower" else -1
        level = levels[side_text]
        target = levels["low"] if side == 1 else levels["high"]
        status, score, taken, feats, error = "scored", None, 0, None, None
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
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("INSERT OR IGNORE INTO signals (week,symbol,side,level,target,high,low,candle_ms,observed_ms,"
                             "status,score,taken,features,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (levels["week"], symbol, side, level, target, levels["high"], levels["low"], candle_ms,
                              observed_ms, status, score, taken, json.dumps(feats) if feats else None, error))
            if cur.rowcount != 1 or status == "excluded":
                return
            sid = cur.lastrowid
            for m in TRACKS:
                step = m*60_000
                decision = candle_ms//step*step+2*step
                notional, note = 0., None
                if taken:
                    equity = db.execute("SELECT equity FROM tracks WHERE track=?", (m,)).fetchone()["equity"]
                    opened = db.execute("SELECT COALESCE(SUM(notional),0) s FROM positions WHERE track=? AND stage<>'closed' "
                                        "AND taken=1", (m,)).fetchone()["s"]
                    notional = max(0., min(FRACTION*equity, GROSS*equity-opened))
                    if notional <= 0:
                        note = "no_capacity"
                db.execute("INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,decision_ms,stage,"
                           "taken,notional,last_seen_ms,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (sid, m, symbol, side, level, target, candle_ms, decision, "pending_confirm",
                            int(taken and notional > 0), notional, candle_ms, note))
        self._refresh_watch()

    def _close(self, db, p, exit_ms, price, reason):
        gross, fee, net, net_slip = settle(p["side"], p["entry"], price, reason)
        pnl = p["notional"]*net/1e4 if p["taken"] else 0.
        db.execute("UPDATE positions SET stage='closed',exit_ms=?,exit_price=?,reason=?,gross_bp=?,fee_bp=?,net_bp=?,"
                   "net_slip_bp=?,pnl=? WHERE id=?", (exit_ms, price, reason, gross, fee, net, net_slip, pnl, p["id"]))
        if p["taken"]:
            db.execute("UPDATE tracks SET equity=equity+? WHERE track=?", (pnl, p["track"]))

    def handle_quote(self, symbol, q):
        """One 1m candle update (cumulative minute high/low, latest close)."""
        with self.db() as db:
            for p in db.execute("SELECT * FROM positions WHERE symbol=? AND stage<>'closed'", (symbol,)).fetchall():
                minute = p["fill_ms"]//60_000*60_000
                if q["candle_ms"] < minute:
                    continue
                s, entry, target = p["side"], p["entry"], p["target"]
                after_fill = q["candle_ms"] > minute
                reached = (q["high"] >= target) if s == 1 else (q["low"] <= target)
                closes_beyond = (q["price"] >= target) if s == 1 else (q["price"] <= target)
                stop = (p["stage"] == "breakeven" and q["candle_ms"] >= p["be_from_ms"]
                        and ((q["low"] <= entry) if s == 1 else (q["high"] >= entry)))
                if stop:
                    self._close(db, p, q["candle_ms"], entry, "breakeven")
                elif reached and (after_fill or closes_beyond):
                    self._close(db, p, q["candle_ms"], target, "target")
                else:
                    db.execute("UPDATE positions SET last_seen_ms=MAX(last_seen_ms,?) WHERE id=?", (q["candle_ms"], p["id"]))
        self._refresh_watch()

    def decide(self, due_by, symbol=None):
        """Confirmation at the close of the m-minute candle after the touch candle."""
        now = self.client.clock()
        with self.db() as db:
            due = db.execute("SELECT * FROM positions WHERE stage='pending_confirm' AND decision_ms+3000<=? "
                             "AND (? IS NULL OR symbol=?)", (due_by, symbol, symbol)).fetchall()
        for p in due:
            step = p["track"]*60_000
            bar = "1H" if p["track"] == 60 else "5m"
            limit = str(min(300, (now-p["decision_ms"])//step+3))
            try:
                rows = bars(self.client.get("/api/v5/market/candles", {"instId": p["symbol"], "bar": bar, "limit": limit}), bar, now)
            except Exception:
                continue
            window = [b for b in rows if b[0] == p["decision_ms"]-step]
            if not window:
                continue
            close = window[0][4]
            inside = close > p["entry"] if p["side"] == 1 else close < p["entry"]
            with self.db() as db:
                cur = db.execute("SELECT * FROM positions WHERE id=?", (p["id"],)).fetchone()
                if cur["stage"] != "pending_confirm":
                    continue
                if inside:
                    db.execute("UPDATE positions SET stage='breakeven',be_from_ms=? WHERE id=?", (p["decision_ms"], p["id"]))
                else:
                    self._close(db, cur, p["decision_ms"], close, "failed_reclaim")
        self._refresh_watch()

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

    def snapshot(self):
        with self.fib.lock:
            quotes = dict(self.fib.quotes)
        with self.db() as db:
            tracks = {}
            for t in db.execute("SELECT * FROM tracks ORDER BY track DESC").fetchall():
                m = t["track"]
                closed = [dict(r) for r in db.execute("SELECT * FROM positions WHERE track=? AND stage='closed' ORDER BY exit_ms DESC", (m,))]
                taken = [r for r in closed if r["taken"]]
                open_rows = []
                for r in db.execute("SELECT p.*, s.score FROM positions p JOIN signals s ON s.id=p.signal_id "
                                    "WHERE p.track=? AND p.stage<>'closed' ORDER BY p.fill_ms DESC", (m,)):
                    r = dict(r); q = quotes.get(r["symbol"], {})
                    r["mark"] = q.get("price")
                    r["unrealized_bp"] = r["side"]*(q["price"]-r["entry"])/r["entry"]*1e4 if q.get("price") else None
                    open_rows.append(r)
                tracks[str(m)] = dict(confirm_minutes=m, equity=t["equity"], start_equity=START_EQUITY, started_ms=t["started_ms"],
                    open=open_rows, closed=closed[:100],
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
                    sizing=dict(start_equity=START_EQUITY, fraction=FRACTION, gross=GROSS),
                    fees_bp=dict(maker=MAKER_BP, taker=TAKER_BP, report_slippage=SLIP_BP),
                    worker=dict(alive=bool(self.thread and self.thread.is_alive()), queue=self.events.qsize(), error=self.error),
                    tracks=tracks, signals=signals)


def install(app, engine):
    app.state.monday_reclaim_paper = engine

    @app.get("/api/monday-reclaim-paper")
    def snapshot():
        return engine.snapshot()
