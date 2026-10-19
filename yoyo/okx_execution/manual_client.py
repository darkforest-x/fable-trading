"""Manual worker reuses TLS connections for fresh GETs; mutations never retry.

No preview response cache is shared with execution. Every get signs and reads
a new exchange receipt, including reads after leverage or order writes.
Signal-center transport and its account remain unchanged.
"""
from contextlib import contextmanager
import time
from .client import Client
from .preview_reads import PooledOpener, PreviewClient, PreviewReads, ReadPacer


class ManualClient(Client):
    def __init__(self, *args, read_opener=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.read_opener = read_opener or PooledOpener()
        self.reader = PreviewClient(self, self.read_opener, ReadPacer())

    def get(self, path, **query):
        return self.reader.get(path, **query)

    def close(self):
        self.read_opener.close()

    @contextmanager
    def read_scope(self, requests):
        """Parallelize one reconciliation phase; discard every receipt on exit."""
        reads = PreviewReads(self, lambda: int(time.time()*1000))
        try:
            for path, query in requests:
                reads.submit(path, **query)
            yield reads
        finally:
            reads.close()
