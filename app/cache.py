"""A small in-memory cache for the read-heavy pages (dashboard, alerts, reports, admin overview).

Why: every page load runs several database queries. The numbers only change when someone writes
(adds a reading, logs maintenance, changes a role), so we keep each answer for a short time and
throw the whole cache away on any successful write.

Limits, on purpose: it lives in this process only (the container runs one worker), and it is
never used for anything that depends on who is asking.
"""
import functools
import threading
import time

_lock = threading.Lock()
_store: dict[tuple, tuple[float, object]] = {}
_stats = {"hits": 0, "misses": 0}


def _enabled() -> bool:
    import os
    return os.getenv("CACHE_ENABLED", "true").strip().lower() not in ("0", "false", "no")


def cached(name: str, ttl: float = 30.0):
    """Cache a route's answer for `ttl` seconds, keyed by its query parameters (not the db session)."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            if not _enabled():
                return fn(*args, **kwargs)
            key = (name, tuple(sorted((k, repr(v)) for k, v in kwargs.items() if k != "db")))
            now = time.monotonic()
            with _lock:
                hit = _store.get(key)
                if hit and hit[0] > now:
                    _stats["hits"] += 1
                    return hit[1]
            value = fn(*args, **kwargs)
            with _lock:
                _stats["misses"] += 1
                _store[key] = (now + ttl, value)
            return value

        return inner

    return wrap


def clear():
    with _lock:
        _store.clear()


def stats() -> dict:
    with _lock:
        total = _stats["hits"] + _stats["misses"]
        return {"entries": len(_store), "hits": _stats["hits"], "misses": _stats["misses"],
                "hitRate": round(_stats["hits"] / total, 2) if total else 0.0, "enabled": _enabled()}


def reset_stats():
    with _lock:
        _stats["hits"] = _stats["misses"] = 0
