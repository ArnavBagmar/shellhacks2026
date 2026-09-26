"""The X4 bridge: latest-frame grab, six-crop dewarp, one-in-flight sender.

Two rules from ``docs/camera-sync-design.md`` Sec 3.3 shape every module here,
and both of them fail *silently* if broken:

1. Grab the newest frame only -- a dedicated thread, one slot, never a queue.
2. One request in flight, ever -- drop the frame rather than let the uplink
   queue grow.

Nothing in this package imports PyObjC at module scope; the macOS-native
capture source is behind a guarded import in ``capture.avfoundation_source``.
"""
