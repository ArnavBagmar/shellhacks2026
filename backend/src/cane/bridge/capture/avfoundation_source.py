"""Native macOS capture that can actually select the X4's 2880x1440 mode.

Ported from ~/Desktop/insta360 (Arnav's prototype).

OpenCV's AVFoundation backend hardcodes a session preset and resets the device
format, so it can never reach the panorama mode (``docs/insta360feed.md``).
This source talks to AVFoundation through PyObjC, picks the camera by name,
sets the active format and holds the configuration lock for the whole session
-- releasing the lock lets the preset win again.

PyObjC is imported inside :func:`_load_backend`, never at module scope, so
this module imports cleanly on any platform and in the test suite.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np

from cane.bridge.capture.base import Frame
from cane.bridge.capture.latest import LatestFrameStore
from cane.bridge.capture.opencv_source import SourceOpenError
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)

X4_NAME_SUBSTRING = "insta360"
X4_FRAME_SIZE = (2880, 1440)
PIXEL_FORMAT_BGRA = 1111970369  # kCVPixelFormatType_32BGRA
BGRA_CHANNELS = 4
FIRST_FRAME_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 5.0
LOCK_FLAG = 1  # kCVPixelBufferLock_ReadOnly
INSTALL_HINT = "uv sync --extra mac"
AVCONFERENCED_HINT = (
    "macOS's video-conference daemon usually holds the camera's configuration "
    "lock; it relaunches on its own, so run: pkill -x avconferenced"
)


class CameraNotFoundError(SourceOpenError):
    """No connected camera matched the requested name."""


def bgra_bytes_to_bgr(
    buffer: bytes | memoryview, width: int, height: int, bytes_per_row: int
) -> np.ndarray:
    """Convert a row-padded BGRA pixel buffer into a contiguous (H, W, 3) BGR array."""
    rows = np.frombuffer(buffer, dtype=np.uint8).reshape(
        height, bytes_per_row // BGRA_CHANNELS, BGRA_CHANNELS
    )
    return np.ascontiguousarray(rows[:, :width, :3])


@dataclass(frozen=True)
class _Backend:
    """The PyObjC modules, imported lazily so the bridge works without them."""

    avf: object
    quartz: object
    libdispatch: object
    core_media: object
    objc: object
    nsobject: type


def _load_backend() -> _Backend:  # pragma: no cover - needs macOS + PyObjC
    try:
        import AVFoundation as avf
        import CoreMedia as core_media
        import libdispatch
        import objc
        import Quartz as quartz
        from Foundation import NSObject
    except ImportError as exc:
        raise SourceOpenError(
            f"native X4 capture needs the PyObjC extra ({exc}); run: {INSTALL_HINT}"
        ) from exc
    return _Backend(avf, quartz, libdispatch, core_media, objc, NSObject)


def _make_delegate_class(backend: _Backend, store: LatestFrameStore) -> type:  # pragma: no cover
    quartz, core_media = backend.quartz, backend.core_media
    protocol = backend.objc.protocolNamed("AVCaptureVideoDataOutputSampleBufferDelegate")

    class _FrameDelegate(backend.nsobject, protocols=[protocol]):  # type: ignore[misc,valid-type]
        def captureOutput_didOutputSampleBuffer_fromConnection_(  # noqa: N802
            self, output: object, sample_buffer: object, connection: object
        ) -> None:
            pixel_buffer = core_media.CMSampleBufferGetImageBuffer(sample_buffer)
            quartz.CVPixelBufferLockBaseAddress(pixel_buffer, LOCK_FLAG)
            try:
                width = quartz.CVPixelBufferGetWidth(pixel_buffer)
                height = quartz.CVPixelBufferGetHeight(pixel_buffer)
                bytes_per_row = quartz.CVPixelBufferGetBytesPerRow(pixel_buffer)
                raw = quartz.CVPixelBufferGetBaseAddress(pixel_buffer).as_buffer(
                    bytes_per_row * height
                )
                image = bgra_bytes_to_bgr(raw, width, height, bytes_per_row)
            finally:
                quartz.CVPixelBufferUnlockBaseAddress(pixel_buffer, LOCK_FLAG)
            store.push(image, now_ms())

    return _FrameDelegate


class AVFoundationSource:  # pragma: no cover - needs real hardware
    """Live camera selected by name with an explicit capture format (macOS only)."""

    def __init__(self, name_substring: str, frame_size: tuple[int, int] | None) -> None:
        self._backend = _load_backend()
        self._name_substring = name_substring
        self._frame_size = frame_size
        self._name = f"avf:{name_substring}"
        self._store = LatestFrameStore()
        self._session: object | None = None
        self._device: object | None = None
        self._delegate: object | None = None
        self._locked = False
        self._seq = 0
        self._start()

    @property
    def name(self) -> str:
        return self._name

    @property
    def fps(self) -> float:
        ranges = self._device.activeFormat().videoSupportedFrameRateRanges()  # type: ignore[union-attr]
        return float(ranges[0].maxFrameRate()) if ranges else 0.0

    def read(self) -> Frame | None:
        """Wait for the next delegate callback. ``None`` means the camera
        stalled for ``READ_TIMEOUT_S``; ``GrabThread`` decides what to do.
        """
        frame = self._store.wait_for_new(self._seq, READ_TIMEOUT_S)
        if frame is None:
            return None
        self._seq = frame.seq
        return frame

    def close(self) -> None:
        if self._session is not None and self._session.isRunning():  # type: ignore[union-attr]
            self._session.stopRunning()  # type: ignore[union-attr]
        if self._locked and self._device is not None:
            self._device.unlockForConfiguration()  # type: ignore[union-attr]
            self._locked = False
        self._session = None

    def _start(self) -> None:
        self._device = self._find_device(self._name_substring)
        self._name = f"avf:{self._device.localizedName()}"  # type: ignore[union-attr]
        self._session, self._delegate = self._build_session()
        self._locked = self._apply_format(self._frame_size)
        # The lock stays held while running; releasing it lets the session
        # preset reset the format back to 1920x1080.
        self._session.startRunning()  # type: ignore[union-attr]
        if self._store.wait_for_new(0, FIRST_FRAME_TIMEOUT_S) is None:
            self.close()
            raise SourceOpenError(
                f"{self._name}: no frames within {FIRST_FRAME_TIMEOUT_S:.0f}s. "
                "Is the camera in Webcam USB mode and is this terminal granted camera access "
                "(System Settings > Privacy & Security > Camera)?"
            )

    def _find_device(self, name_substring: str) -> object:
        avf = self._backend.avf
        needle = name_substring.lower()
        names: list[str] = []
        for device in avf.AVCaptureDevice.devicesWithMediaType_(avf.AVMediaTypeVideo):
            label = str(device.localizedName())
            names.append(label)
            if needle in label.lower():
                return device
        raise CameraNotFoundError(
            f"no camera matching {name_substring!r}; connected cameras: {names or ['none']}. "
            "Plug the X4 in over USB-C and choose Webcam on its screen."
        )

    def _build_session(self) -> tuple[object, object]:
        avf, quartz = self._backend.avf, self._backend.quartz
        libdispatch = self._backend.libdispatch
        session = avf.AVCaptureSession.alloc().init()
        device_input, error = avf.AVCaptureDeviceInput.deviceInputWithDevice_error_(
            self._device, None
        )
        if device_input is None:
            raise SourceOpenError(f"{self._name}: cannot open the capture input ({error})")
        session.addInput_(device_input)
        output = avf.AVCaptureVideoDataOutput.alloc().init()
        output.setVideoSettings_({quartz.kCVPixelBufferPixelFormatTypeKey: PIXEL_FORMAT_BGRA})
        output.setAlwaysDiscardsLateVideoFrames_(True)
        delegate = _make_delegate_class(self._backend, self._store).alloc().init()
        queue = libdispatch.dispatch_queue_create(b"cane.bridge.capture", None)
        output.setSampleBufferDelegate_queue_(delegate, queue)
        session.addOutput_(output)
        return session, delegate

    def _apply_format(self, frame_size: tuple[int, int] | None) -> bool:
        """Select the requested format; returns True if the config lock is held."""
        if frame_size is None:
            return False
        get_dims = self._backend.core_media.CMVideoFormatDescriptionGetDimensions
        offered: list[tuple[int, int]] = []
        wanted = None
        for fmt in self._device.formats():  # type: ignore[union-attr]
            dims = get_dims(fmt.formatDescription())
            size = (int(dims.width), int(dims.height))
            offered.append(size)
            if size == frame_size and wanted is None:
                wanted = fmt
        if wanted is None:
            raise SourceOpenError(
                f"{self._name} does not offer {frame_size[0]}x{frame_size[1]}; it offers {offered}"
            )
        locked, error = self._device.lockForConfiguration_(None)  # type: ignore[union-attr]
        if not locked:
            raise SourceOpenError(
                f"{self._name}: cannot change format, another process holds it ({error}). "
                f"{AVCONFERENCED_HINT}"
            )
        self._device.setActiveFormat_(wanted)  # type: ignore[union-attr]
        logger.info("%s: active format set to %dx%d", self._name, *frame_size)
        return True


def open_x4(
    name_substring: str = X4_NAME_SUBSTRING,
    frame_size: tuple[int, int] = X4_FRAME_SIZE,
    settle_s: float = 0.0,
) -> AVFoundationSource:  # pragma: no cover - needs real hardware
    """Open the Insta360 X4 in stitched-equirect webcam mode."""
    source = AVFoundationSource(name_substring, frame_size)
    if settle_s > 0:
        time.sleep(settle_s)
    return source
