"""Video capture that fails loudly and recovers on its own.

Two failure modes drove the design of this module, both learned the hard way:

**Silent startup failure.** A bare ``cv2.VideoCapture(0)`` on Windows uses
the MSMF backend, which regularly opens "successfully" and then never
delivers a frame. If the read loop just ``continue``s on failure you get an
infinite loop with no window and no error. So opening is verified by actually
pulling a frame, and it raises with a diagnosis if it cannot.

**Mid-session death.** A phone used as a webcam streams fine for ten minutes,
then thermally throttles: the frames go black and the rate collapses to about
1fps. The capture layer detects that and reopens the stream rather than
feeding a black frame to the tracker and leaving the stick wherever it was.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import CameraSettings, resolve_capture_backend

#: Tried in order by :func:`probe_sources` on Windows; harmless elsewhere.
PROBE_BACKENDS = ("dshow", "msmf", "any")


class CameraError(RuntimeError):
    """Raised when a camera cannot be opened or produces nothing usable."""


def _is_black(frame, black_level: int) -> bool:
    """Cheap all-black test, sampling every 8th pixel in both axes.

    A throttled phone camera sends near-black rather than exactly black, so
    this compares the sampled maximum against a small threshold instead of
    testing for zero.
    """
    sample = frame[::8, ::8]
    return bool(sample.max() <= black_level)


@dataclass
class CameraStatus:
    ok: bool = True
    message: str = ""
    reconnects: int = 0


class Camera:
    """A capture source that verifies itself and reconnects when it stalls."""

    def __init__(self, settings: CameraSettings) -> None:
        self.settings = settings
        self.status = CameraStatus()
        self._capture = None
        self._bad_since: float | None = None

    # -- lifecycle --------------------------------------------------------

    def open(self) -> Camera:
        self._capture = self._open_capture()
        return self

    def _open_capture(self):
        import cv2

        source = self.settings.source
        # A URL source (IP Webcam and friends) must not be given a platform
        # capture backend; OpenCV picks the right one for the stream itself.
        if isinstance(source, str) and not source.isdigit():
            capture = cv2.VideoCapture(source)
            label = source
        else:
            index = int(source)
            capture = cv2.VideoCapture(index, resolve_capture_backend(self.settings.backend))
            label = f"index {index} via {self.settings.backend}"

        if not capture.isOpened():
            capture.release()
            raise CameraError(
                f"Could not open camera ({label}).\n"
                "In rough order of likelihood:\n"
                "  1. Windows camera privacy: Settings -> Privacy & security -> "
                "Camera. The 'let desktop apps access your camera' toggle is "
                "separate from the main one and is the one people miss.\n"
                "  2. Another app has the camera open (Teams, Zoom, the phone "
                "bridge's own preview window).\n"
                "  3. Wrong index or backend. Run `fallguys-pose check-camera`."
            )

        if not isinstance(source, str) or source.isdigit():
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
            capture.set(cv2.CAP_PROP_FPS, self.settings.fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Opening is not the same as working. Pull a real frame before
        # returning, so the failure surfaces here rather than as a black
        # window five seconds later.
        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            raise CameraError(
                f"Camera ({label}) opened but delivered no frames. "
                "This is the classic MSMF-backend failure on Windows laptops "
                "— try `backend = \"dshow\"`, or run "
                "`fallguys-pose check-camera` to find a combination that works."
            )
        return capture

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> Camera:
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- reading ----------------------------------------------------------

    def read(self):
        """Return the next usable frame, or ``None`` while the source is bad.

        Never raises for a transient stall — the caller is expected to zero
        the gamepad and keep going while :attr:`status` reports the problem.
        """
        if self._capture is None:
            raise RuntimeError("Camera.read() called before open()")

        now = time.monotonic()
        ok, frame = self._capture.read()

        if not ok or frame is None:
            self._mark_bad("Camera stopped delivering frames", now)
            return None
        if _is_black(frame, self.settings.black_level):
            # Almost always thermal throttling on a phone used as a webcam.
            self._mark_bad("Camera is sending black frames", now)
            return None

        self._bad_since = None
        self.status.ok = True
        self.status.message = ""
        return frame

    def _mark_bad(self, message: str, now: float) -> None:
        self.status.ok = False
        self.status.message = message
        if self._bad_since is None:
            self._bad_since = now
        elif now - self._bad_since >= self.settings.reconnect_after:
            self._reconnect()

    def _reconnect(self) -> None:
        self.status.reconnects += 1
        self.status.message = f"Reconnecting (attempt {self.status.reconnects})"
        self.close()
        try:
            self._capture = self._open_capture()
            self._bad_since = None
            self.status.message = "Reconnected"
        except CameraError as exc:
            self.status.message = str(exc).splitlines()[0]
            self._bad_since = time.monotonic()


def probe_sources(max_index: int = 4) -> list[tuple[int, str, tuple[int, int]]]:
    """Try every index/backend pair and report which actually deliver a frame.

    Returns ``(index, backend_name, (width, height))`` for each working
    combination. Opening a camera is not proof it works, so each candidate is
    required to hand over a real frame.
    """
    import cv2

    working: list[tuple[int, str, tuple[int, int]]] = []
    for index in range(max_index):
        for backend in PROBE_BACKENDS:
            capture = cv2.VideoCapture(index, resolve_capture_backend(backend))
            try:
                if not capture.isOpened():
                    continue
                ok, frame = capture.read()
                if ok and frame is not None:
                    height, width = frame.shape[:2]
                    working.append((index, backend, (width, height)))
            finally:
                capture.release()
    return working
