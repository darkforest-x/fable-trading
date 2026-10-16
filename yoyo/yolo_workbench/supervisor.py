"""Restart the isolated worker when pending observations have no process owner.

Uses its OS lock, not a stale PID or a timeout that could interrupt active
inference. This independent watcher never participates in the market pulse.
"""
from __future__ import annotations
import fcntl
import logging
import threading
from .store import Book
from .worker import start
from .store import now


class Supervisor:
    def __init__(self,runtime):
        self.runtime=runtime;self.book=Book(runtime);self.stop=threading.Event();self.thread=None

    def ensure(self):
        control=self.book.meta('control') or {}
        if control.get('stop_worker') or control.get('maintenance_until_ms',0)>now():return False
        needed=any(j['status'] in ('queued','running') for j in self.book.jobs(100)) or any(s['enabled'] for s in self.book.schedules())
        if not needed:return False
        with (self.book.runtime/'worker.lock').open('a+') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return False
        start(self.runtime)
        return True

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear()
        def watch():
            while not self.stop.is_set():
                try:self.ensure()
                except Exception:logging.getLogger(__name__).exception('YOLO worker supervision failed')
                self.stop.wait(20)
        self.thread=threading.Thread(target=watch,name='yolo-workbench-supervisor',daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
