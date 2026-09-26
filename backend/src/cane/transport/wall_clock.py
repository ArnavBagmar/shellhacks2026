"""The one wall-clock read the transport's I/O layer shares.

``clock_sync.py`` stays pure (no ``time.time()`` calls anywhere in it);
``server.py``'s ping loop and ``__main__.py``'s demo-event loop both read
"now" through this single function so there is exactly one place that
does.
"""

from __future__ import annotations

import time


def now_ms() -> int:
    """Current wall-clock time as integer Unix-epoch milliseconds."""
    return int(time.time() * 1000)
