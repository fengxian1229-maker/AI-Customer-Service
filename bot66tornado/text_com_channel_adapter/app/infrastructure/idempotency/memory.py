from collections import OrderedDict
from threading import Lock


class InMemoryIdempotencyStore:
    """Simple process-local dedup store.

    Production replacement: Redis SET NX with TTL or DB unique key on channel_event_id.
    """

    def __init__(self, max_size: int = 10_000):
        self._max_size = max_size
        self._items: OrderedDict[str, None] = OrderedDict()
        self._lock = Lock()

    def seen_or_mark(self, key: str) -> bool:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return True
            self._items[key] = None
            if len(self._items) > self._max_size:
                self._items.popitem(last=False)
            return False
