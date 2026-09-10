"""Durable, causal SPIKE V1 -> YOLO-extra confirmation for notifications.

The raw V1 signal remains authoritative.  YOLO reads only closed OHLCV and six
close-source moving averages in its p..p+9 causal window, and may add a later
record; it never invalidates, delays, recolors, or replaces the raw V1 event.
"""
from __future__ import annotations

from collections import OrderedDict
import logging
import threading

from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor import (FRESH_MS, MODEL_KIND, MODEL_PROTOCOL, MODEL_PROFILE_ID,
                          MODEL_SHA256, MODEL_MAX_WAIT, MONITORED_TIMEFRAMES, TIMEFRAMES)
from yoyo.monitor.policy import finite, is_tv_start, is_model_signal

LOG = logging.getLogger("spike.model")


def pending_proof(event):
    return dict(status="pending", protocol=MODEL_PROTOCOL, profile_id=MODEL_PROFILE_ID,
                model_sha256=MODEL_SHA256, max_wait_bars=MODEL_MAX_WAIT, wait_bars=0,
                expires_at_ms=event["bar_close_ms"] + MODEL_MAX_WAIT * TIMEFRAMES[event["timeframe"]],
                last_checked_close_ms=None, reason=None)


def confirmation(event, proposal, candle, checked_at):
    """Link a detector proposal to a V1 raw event at a closed endpoint."""
    if (proposal.get("structural_pass") is not True or proposal.get("side") != event["side"]
            or proposal.get("model_sha256") != MODEL_SHA256):
        return None
    step = TIMEFRAMES[event["timeframe"]]
    a, b = proposal["core_start_ms"], proposal["core_end_ms"]
    # A detector core must begin no later than the V1 source bar.  This binds
    # the extra confirmation to the same causal launch window without making
    # an IMACD/focus predicate part of the V1 signal definition.
    if a > event["bar_open_ms"] or b < a:
        return None
    proof = dict(proposal, status="confirmed", protocol=MODEL_PROTOCOL,
                 profile_id=MODEL_PROFILE_ID, max_wait_bars=MODEL_MAX_WAIT,
                 wait_bars=(candle["t"] - event["bar_open_ms"]) // step,
                 confirmation_close_ms=candle["t"] + step, checked_at_ms=checked_at,
                 last_checked_close_ms=candle["t"] + step, reason=None)
    derived = dict(protocol=MODEL_PROTOCOL, kind=MODEL_KIND, source="live", confirmation="yolo",
                   direction="long", venue=event.get("venue", "okx"), symbol=event["symbol"],
                   timeframe=event["timeframe"], timeframe_min=event["timeframe_min"], side="long", price=candle["c"],
                   bar_open_ms=candle["t"], bar_close_ms=candle["t"] + step,
                   signal_close_time=candle["t"] + step, is_closed=True,
                   source_sha256=event["source_sha256"], entry_reference="next_open", executable_entry_time=None,
                   detected_at_ms=checked_at,
                   source_event_id=event["id"], indicator={k:v for k,v in event.items() if k != "model"},
                   model=proof)
    if not is_model_signal(derived):
        raise ValueError("invalid_model_proposal")
    return derived


class ModelGate:
    """One serialized worker, coalesced per-market inputs and durable progress.

    Public scan threads only register arrows and submit memory snapshots. Two
    images per eligible endpoint are shared across same-market candidates.
    A restart resumes the last unexamined endpoint, never replays receipts.
    """
    def __init__(self, store, clock, stop_event, detector=None, *, telegram_enabled=False):
        self.store, self.clock, self.stop_event = store, clock, stop_event
        self.detector = detector
        self.telegram_enabled = telegram_enabled is True
        self._condition = threading.Condition()
        self._queue = OrderedDict()
        self._active = None
        self._error = None
        self._processed = 0
        self._last_checked = None

    def register(self, event):
        if event.get("timeframe") not in MONITORED_TIMEFRAMES or not is_tv_start(event):
            return False
        return self.store.register_candidate(event, pending_proof(event))

    def submit(self, symbol, timeframe, candles):
        if timeframe not in MONITORED_TIMEFRAMES:
            return
        if not self.store.list_candidates(1, symbol, timeframe, pending_only=True):
            return
        with self._condition:
            self._queue[(symbol, timeframe)] = candles
            self._condition.notify()

    def status(self):
        detector = self.detector.status() if self.detector is not None else {}
        counts = self.store.candidate_counts()
        error = self._error or detector.get("error")
        return dict(status="error" if error or counts.get("error") else
                    ("ready" if detector.get("ready") else "loading"),
                    loaded=bool(detector.get("ready")), last_error=error,
                    queue_depth=len(self._queue), active=self._active,
                    processed_endpoints=self._processed, last_checked_at_ms=self._last_checked,
                    model_sha256=MODEL_SHA256, profile_id=MODEL_PROFILE_ID,
                    max_wait_bars=MODEL_MAX_WAIT, candidates=counts, detector=detector)

    def run(self):
        while not self.stop_event.is_set():
            try:
                if self.detector is None:
                    from yoyo.monitor.yolo_detector import YoloDetector
                    self.detector = YoloDetector()
                self.detector.warmup()
                self._error = None
                break
            except Exception as exc:
                self._error = "model_unavailable:" + type(exc).__name__
                LOG.error("model unavailable: %s", type(exc).__name__)
                self.stop_event.wait(30)
        while not self.stop_event.is_set():
            with self._condition:
                if not self._queue:
                    self._condition.wait(timeout=3)
                    continue
                key, candles = self._queue.popitem(last=False)
            self._active = {"symbol": key[0], "timeframe": key[1]}
            try:
                self.process(*key, candles)
                self._error = None
            except Exception as exc:
                self._error = "model_worker_failure:" + type(exc).__name__
                LOG.error("model worker failure: %s", type(exc).__name__)
            finally:
                self._active = None

    def process(self, symbol, timeframe, candles):
        """Replay available closed endpoints in order, never infer future bars.

        Columns used: t/md/c and detector's causal OHLC/MA prefix. Progress is
        persisted only after a complete inference; errors retry the endpoint.
        Once md invalidates a candidate it cannot be revived by a later match.
        """
        if timeframe not in MONITORED_TIMEFRAMES:
            return
        step, now = TIMEFRAMES[timeframe], self.clock()
        closed = [r for r in candles if r["t"] + step <= now]
        if not closed:
            return
        by_time = {r["t"]: r for r in closed}
        cache = {}
        for event in self.store.list_candidates(2000, symbol, timeframe, pending_only=True):
            proof, p = dict(event["model"]), event["bar_open_ms"]
            cursor = proof.get("last_checked_close_ms")
            first = max(p, cursor if cursor is not None else p)
            last = min(closed[-1]["t"], p + MODEL_MAX_WAIT * step)
            if first > last:
                continue
            for end in range(first, last + 1, step):
                if self.stop_event.is_set():
                    return
                proof.update(checked_at_ms=self.clock(), wait_bars=(end-p)//step)
                path = [by_time.get(t) for t in range(p, end+1, step)]
                if any(r is None for r in path):
                    proof.update(status="expired" if now > proof["expires_at_ms"] + step else "error",
                                 reason="confirmation_history_unavailable" if now > proof["expires_at_ms"] + step
                                 else "missing_causal_candles")
                    self.store.update_candidate(event["id"], proof)
                    break
                try:
                    if end not in cache:
                        try:
                            cache[end] = self.detector.predict([r for r in closed if r["t"] <= end], symbol, timeframe, end)
                            self._processed += 1
                            self._last_checked = self.clock()
                        except Exception as exc:
                            cache[end] = exc
                    if isinstance(cache[end], Exception):
                        raise cache[end]
                    matches = []
                    for proposal in cache[end]:
                        derived = confirmation(event, proposal, by_time[end], self.clock())
                        if derived is not None:
                            matches.append(derived)
                    matches.sort(key=lambda e: (-e["model"]["confidence"], e["model"]["window_len"],
                                                e["model"].get("detection_id", "")))
                except Exception as exc:
                    proof.update(status="error", reason="inference_unavailable:" + type(exc).__name__)
                    self.store.update_candidate(event["id"], proof)
                    break
                if matches:
                    self._commit(event, matches[0], closed)
                    break
                proof.update(status="expired" if end == p + MODEL_MAX_WAIT * step else "pending",
                             reason="no_match_within_wait" if end == p + MODEL_MAX_WAIT * step else None,
                             last_checked_close_ms=end+step)
                self.store.update_candidate(event["id"], proof)

    def _commit(self, original, event, candles):
        now = self.clock()
        stream = self.store.timeframe_activation(event["timeframe"], protocol=MODEL_PROTOCOL)
        common = (stream is not None and original["bar_close_ms"] > stream
                  and 0 <= now - event["bar_close_ms"] <= FRESH_MS)
        tg = (self.store.get_meta("notification_policy:" + MODEL_PROTOCOL, {}).get("activated_ms")
              if self.telegram_enabled else None)
        bark = self.store.get_meta("notification_policy:bark:" + MODEL_PROTOCOL, {}).get("activated_ms")
        notify = self.telegram_enabled and common and tg is not None and original["bar_close_ms"] > tg
        bark_notify = (common and bark is not None and original["bar_close_ms"] > bark
                       and delivery_error(self.store, event, now, "bark") is None)
        photo, error = None, None
        if notify:
            try:
                from yoyo.monitor.snapshot import render_signal
                photo = render_signal(event, [r for r in candles if r["t"] <= event["bar_open_ms"]])
            except Exception:
                error = "snapshot_unavailable"
        self.store.confirm_candidate(original["id"], event, notify=notify, bark_notify=bark_notify,
                                     telegram_photo=photo, photo_error=error)
