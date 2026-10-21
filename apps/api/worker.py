"""RQ worker process with periodic orphaned-temp-blob cleanup."""
from __future__ import annotations

import threading
import time

from rq import Worker
from rq.serializers import JSONSerializer

from .jobs import analysis_queue, redis_connection
from .temp_blobs import cleanup_expired_blobs
from .tombstones import cleanup_expired_tombstones


def _cleanup_loop() -> None:
    next_alert_tick = 0.0
    while True:
        try:
            cleanup_expired_blobs()
            cleanup_expired_tombstones()
        except Exception:
            # No identifiers or exception text are logged: these can contain filesystem details.
            pass
        if time.monotonic() >= next_alert_tick:
            try:
                from .alert_runtime import scheduler_tick
                scheduler_tick()
            except Exception:
                # Alert outages do not log user IDs, health values or provider data.
                pass
            next_alert_tick = time.monotonic() + 60
        time.sleep(60)


def main() -> None:
    from . import main as api_main
    api_main.load_model()
    threading.Thread(target=_cleanup_loop, daemon=True, name="temp-blob-expiry").start()
    Worker([analysis_queue()], connection=redis_connection(), serializer=JSONSerializer).work()


if __name__ == "__main__":
    main()
