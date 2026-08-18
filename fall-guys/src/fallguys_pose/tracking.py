"""MediaPipe Pose wrapper.

The only job here is turning a BGR frame into the :class:`~fallguys_pose.intents.Pose`
that the pure logic understands. Keeping it separate is what lets
``intents.py`` stay importable — and testable — without MediaPipe installed.
"""

from __future__ import annotations

from .intents import LANDMARKS, Point, Pose


class PoseTracker:
    """Runs MediaPipe Pose and emits named landmarks."""

    def __init__(
        self,
        model_complexity: int = 1,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        try:
            import mediapipe as mp
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "mediapipe is required. Note that it needs Python 3.11 or "
                "3.12 — 3.13+ will fail to install it. "
                "Run: pip install mediapipe==0.10.14"
            ) from exc

        self._mp = mp
        self._pose = mp.solutions.pose.Pose(
            model_complexity=model_complexity,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
            smooth_landmarks=True,
        )
        #: Kept so the HUD can draw the skeleton without re-running detection.
        self.last_result = None

    def process(self, bgr_frame) -> Pose:
        import cv2

        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        result = self._pose.process(rgb)
        self.last_result = result

        if not result.pose_landmarks:
            return Pose()

        landmarks = result.pose_landmarks.landmark
        points = {
            name: Point(
                x=landmarks[index].x,
                y=landmarks[index].y,
                visibility=getattr(landmarks[index], "visibility", 1.0),
            )
            for name, index in LANDMARKS.items()
        }
        return Pose(points=points)

    def draw_skeleton(self, bgr_frame) -> None:
        """Overlay the tracked skeleton in place, if there is one."""
        if self.last_result is None or not self.last_result.pose_landmarks:
            return
        self._mp.solutions.drawing_utils.draw_landmarks(
            bgr_frame,
            self.last_result.pose_landmarks,
            self._mp.solutions.pose.POSE_CONNECTIONS,
        )

    def close(self) -> None:
        self._pose.close()

    def __enter__(self) -> PoseTracker:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
