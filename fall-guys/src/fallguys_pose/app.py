"""The run loop that wires capture, tracking, intents and gamepad together."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .camera import Camera, CameraError
from .config import Settings
from .gamepad import Gamepad, create_gamepad
from .hud import Hud
from .intents import IntentEngine, Pose
from .tracking import PoseTracker


@dataclass
class SessionSummary:
    """What the session amounted to, printed on exit."""

    duration: float = 0.0
    steps: int = 0
    jumps: int = 0
    dives: int = 0
    frames: int = 0
    reconnects: int = 0

    @property
    def average_fps(self) -> float:
        return self.frames / self.duration if self.duration > 0 else 0.0

    def render(self) -> str:
        minutes, seconds = divmod(int(self.duration), 60)
        return (
            "\n"
            f"  duration    {minutes}m {seconds:02d}s\n"
            f"  steps       {self.steps}\n"
            f"  jumps       {self.jumps}\n"
            f"  dives       {self.dives}\n"
            f"  average fps {self.average_fps:.1f}\n"
            + (f"  reconnects  {self.reconnects}\n" if self.reconnects else "")
        )


class FpsMeter:
    """Smoothed frame rate, so the HUD number does not flicker."""

    def __init__(self, tau: float = 0.5) -> None:
        self.tau = tau
        self.value = 0.0

    def update(self, dt: float) -> float:
        if dt <= 0.0:
            return self.value
        instant = 1.0 / dt
        if self.value == 0.0:
            self.value = instant
        else:
            alpha = dt / (self.tau + dt)
            self.value += alpha * (instant - self.value)
        return self.value


def run_session(settings: Settings, gamepad: Gamepad | None = None) -> SessionSummary:
    """Run until the window is closed or Q is pressed.

    Pass ``gamepad`` to supply your own; otherwise one is built from
    ``settings.runtime.gamepad``.
    """
    import cv2

    owns_gamepad = gamepad is None
    if gamepad is None:
        gamepad = create_gamepad(
            settings.runtime.gamepad, settings.tuning.button_hold
        )

    engine = IntentEngine(settings)
    hud = Hud(settings)
    fps_meter = FpsMeter()
    summary = SessionSummary()

    tracker = PoseTracker(
        model_complexity=settings.pose.model_complexity,
        min_detection_confidence=settings.pose.min_detection_confidence,
        min_tracking_confidence=settings.pose.min_tracking_confidence,
    )

    window = "fallguys-pose"
    started = time.monotonic()
    last_frame_time = started

    try:
        with Camera(settings.camera) as camera:
            if settings.runtime.show_hud:
                cv2.namedWindow(window, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(window, settings.camera.width, settings.camera.height)

            while True:
                now = time.monotonic()
                dt = now - last_frame_time
                last_frame_time = now

                frame = camera.read()
                if frame is None:
                    # Stalled or black. Drop the stick immediately — a frozen
                    # frame must never leave you sprinting off a ledge.
                    gamepad.neutral()
                    if settings.runtime.show_hud:
                        blank = _status_frame(settings, camera.status.message)
                        cv2.imshow(window, blank)
                        if _quit_requested(window):
                            break
                    else:
                        time.sleep(0.05)
                    continue

                # The webcam image is mirrored so the preview reads like a
                # mirror; MediaPipe's own left/right labels flip with it,
                # which is what we want since every measurement is symmetric
                # or expressed in screen space.
                frame = cv2.flip(frame, 1)

                pose: Pose = tracker.process(frame)
                intent = engine.update(pose, now)
                gamepad.apply(intent, now)

                summary.frames += 1

                if settings.runtime.show_hud:
                    tracker.draw_skeleton(frame)
                    hud.draw(
                        frame,
                        intent,
                        now,
                        fps_meter.update(dt),
                        engine.steps.steps,
                        engine.jumps,
                        engine.dives,
                        camera_message="" if camera.status.ok else camera.status.message,
                    )
                    cv2.imshow(window, frame)
                    if _quit_requested(window):
                        break
                elif dt < 0.001:
                    time.sleep(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        gamepad.neutral()
        if owns_gamepad:
            gamepad.close()
        tracker.close()
        if settings.runtime.show_hud:
            cv2.destroyAllWindows()

        summary.duration = time.monotonic() - started
        summary.steps = engine.steps.steps
        summary.jumps = engine.jumps
        summary.dives = engine.dives

    return summary


def _quit_requested(window: str) -> bool:
    """True when Q is pressed or the preview window is closed."""
    import cv2

    key = cv2.waitKey(1) & 0xFF
    if key in (ord("q"), 27):
        return True
    try:
        return cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def _status_frame(settings: Settings, message: str):
    """A black frame carrying an error banner, for when capture is down."""
    import cv2
    import numpy as np

    frame = np.zeros((settings.camera.height, settings.camera.width, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        message or "No camera",
        (20, settings.camera.height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (70, 70, 240),
        2,
        cv2.LINE_AA,
    )
    return frame


def check_camera(max_index: int = 4, preview: bool = True) -> int:
    """Report which camera index/backend pairs deliver frames.

    Returns a process exit code so the CLI can just hand it back.
    """
    from .camera import probe_sources

    print("Probing camera indices and backends...\n")
    working = probe_sources(max_index)

    if not working:
        print("No working camera found. In rough order of likelihood:")
        print("  1. Windows camera privacy settings — the 'desktop apps' toggle")
        print("     (Settings -> Privacy & security -> Camera) is separate from")
        print("     the main camera toggle and is the one people miss.")
        print("  2. Another application already has the camera open.")
        print("  3. A phone-as-webcam bridge is not running, or its PC-side app")
        print("     is not connected.")
        return 1

    print(f"{'index':>6}  {'backend':<8}  resolution")
    for index, backend, (width, height) in working:
        print(f"{index:>6}  {backend:<8}  {width}x{height}")

    index, backend, _ = working[0]
    print("\nPut these in fallguys.toml:\n")
    print(f"[camera]\nsource = {index}\nbackend = \"{backend}\"\n")

    if preview:
        _preview(index, backend)
    return 0


def _preview(index: int, backend: str) -> None:
    """Show the first working camera so you can frame the shot."""
    import cv2

    from .config import resolve_capture_backend

    print("Previewing — press Q to close. Head AND knees must be in frame.")
    capture = cv2.VideoCapture(index, resolve_capture_backend(backend))
    window = "camera check"
    try:
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            cv2.imshow(window, cv2.flip(frame, 1))
            if _quit_requested(window):
                break
    finally:
        capture.release()
        cv2.destroyAllWindows()


__all__ = ["run_session", "check_camera", "SessionSummary", "CameraError"]
