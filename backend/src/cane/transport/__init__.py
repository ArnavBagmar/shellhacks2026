"""cane.transport: NTP clock handshake + WebSocket envelope (D1-D4).

``EventLinkServer`` is deliberately not re-exported here: this package's
``__init__.py`` runs on every import of ``cane.transport``, including the
one ``python -m cane.transport.server`` performs for its parent package
before executing ``server.py`` as ``__main__`` -- eagerly importing
``server`` here would load it twice and trip runpy's
"found in sys.modules ... prior to execution" warning. Import it directly
from ``cane.transport.server`` instead.
"""

from cane.transport.clock_sync import (
    JUMP_THRESHOLD_MS,
    PING_INTERVAL_S,
    WINDOW,
    ClockNotSyncedError,
    NtpSample,
    OffsetEstimator,
    PhoneClock,
)
from cane.transport.messages import (
    EventMessage,
    MalformedMessageError,
    PhoneHealth,
    Ping,
    Pong,
    Unknown,
    decode,
    encode,
    to_wire_dict,
)

__all__ = [
    "JUMP_THRESHOLD_MS",
    "PING_INTERVAL_S",
    "WINDOW",
    "ClockNotSyncedError",
    "EventMessage",
    "MalformedMessageError",
    "NtpSample",
    "OffsetEstimator",
    "PhoneClock",
    "PhoneHealth",
    "Ping",
    "Pong",
    "Unknown",
    "decode",
    "encode",
    "to_wire_dict",
]
