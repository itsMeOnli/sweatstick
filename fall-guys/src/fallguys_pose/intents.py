"""Pose landmarks in, controller intents out. Pure logic, no hardware.

This module imports neither ``cv2`` nor ``vgamepad``, and that constraint is
the reason the tests exist: every threshold and state machine below can be
exercised with hand-written landmarks on any machine, with no camera, no
ViGEmBus driver, and no game running.

Two ideas run through all of it:

**Everything is divided by torso length.** A lean of 0.25 means the same
thing at 2m from the camera as at 4m, which is why there is no calibration
step to forget to run.

**Forward motion is a rate, not a switch.** The engine watches your knees
alternate, measures the interval between steps, and converts that cadence
into stick magnitude. Jog harder, move faster.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

# Landmark names this module expects, mapped to MediaPipe Pose indices.
# Kept here rather than in the tracker so the pure layer owns its own
# vocabulary and the tracker is the thing that has to conform.
LANDMARKS: dict[str, int] = {
    "nose": 0,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
}

_EPSILON = 1e-6


@dataclass(frozen=True)
class Point:
    """A landmark in normalized image space; ``y`` grows *downward*."""

    x: float
    y: float
    visibility: float = 1.0


@dataclass(frozen=True)
class Pose:
    """One frame of landmarks. Empty means nothing was detected."""

    points: dict[str, Point] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.points)

    def has(self, *names: str, min_visibility: float = 0.5) -> bool:
        for name in names:
            point = self.points.get(name)
            if point is None or point.visibility < min_visibility:
                return False
        return True


EMPTY_POSE = Pose()

_REQUIRED = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
)


@dataclass(frozen=True)
class Intent:
    """What the body is asking the game to do, plus the numbers behind it.

    ``steer`` and ``forward`` are stick axes in ``[-1, 1]`` and ``[0, 1]``.
    The remaining fields are diagnostics — the HUD draws them so that tuning
    is a matter of watching a bar cross a tick rather than guessing.
    """

    tracked: bool = False
    steer: float = 0.0
    forward: float = 0.0
    jump: bool = False
    dive: bool = False
    paused: bool = False

    # Diagnostics
    lean: float = 0.0
    cadence: float = 0.0
    hip_rise: float = 0.0
    pause_progress: float = 0.0


IDLE = Intent()


def _midpoint(a: Point, b: Point) -> Point:
    return Point((a.x + b.x) / 2.0, (a.y + b.y) / 2.0, min(a.visibility, b.visibility))


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _ramp(value: float, low: float, high: float) -> float:
    """Map ``[low, high]`` onto ``[0, 1]``, flat outside that band."""
    if high - low < _EPSILON:
        return 1.0 if value >= high else 0.0
    return _clamp((value - low) / (high - low), 0.0, 1.0)


@dataclass
class Signals:
    """Raw normalized measurements for one frame."""

    torso: float
    lean: float
    left_knee_lift: float
    right_knee_lift: float
    hip_y: float
    wrists_above_shoulders: bool
    hand_span: float
    shoulder_span: float
    wrists_above_nose: bool
    hands_together: bool


def measure(pose: Pose, min_visibility: float = 0.5) -> Signals | None:
    """Reduce a pose to normalized measurements, or ``None`` if untrackable.

    Every returned length is in torso-lengths, which is what makes the
    thresholds in :class:`IntentEngine` independent of your distance from the
    camera.
    """
    if not pose or not pose.has(*_REQUIRED, min_visibility=min_visibility):
        return None

    points = pose.points
    shoulder_mid = _midpoint(points["left_shoulder"], points["right_shoulder"])
    hip_mid = _midpoint(points["left_hip"], points["right_hip"])

    torso = math.hypot(shoulder_mid.x - hip_mid.x, shoulder_mid.y - hip_mid.y)
    if torso < _EPSILON:
        return None

    left_wrist = points.get("left_wrist")
    right_wrist = points.get("right_wrist")
    nose = points.get("nose")

    # Arm gestures degrade to "not happening" rather than failing the whole
    # frame, so losing a wrist for a moment costs you a dive, not your legs.
    if left_wrist is not None and right_wrist is not None:
        wrists_above_shoulders = (
            left_wrist.y < points["left_shoulder"].y
            and right_wrist.y < points["right_shoulder"].y
        )
        hand_span = abs(left_wrist.x - right_wrist.x) / torso
        wrists_above_nose = nose is not None and (
            left_wrist.y < nose.y and right_wrist.y < nose.y
        )
        hands_together = hand_span < 0.45
    else:
        wrists_above_shoulders = False
        hand_span = 0.0
        wrists_above_nose = False
        hands_together = False

    return Signals(
        torso=torso,
        lean=(shoulder_mid.x - hip_mid.x) / torso,
        left_knee_lift=(hip_mid.y - points["left_knee"].y) / torso,
        right_knee_lift=(hip_mid.y - points["right_knee"].y) / torso,
        hip_y=hip_mid.y / torso,
        wrists_above_shoulders=wrists_above_shoulders,
        hand_span=hand_span,
        shoulder_span=abs(
            points["left_shoulder"].x - points["right_shoulder"].x
        )
        / torso,
        wrists_above_nose=wrists_above_nose,
        hands_together=hands_together,
    )


@dataclass
class StepCounter:
    """Turns alternating knee lifts into a cadence in steps per second.

    Measured from the intervals *between* steps rather than by counting
    events in a rolling window. The window approach overestimates by roughly
    1/window — a true 1.0 steps/sec reads as 1.5 in a 2-second window — which
    is enough to creep forward while standing still.

    Alternation is required: pumping one knee repeatedly does not accumulate
    cadence, which keeps a single twitchy leg from driving you off a ledge.
    """

    lift_threshold: float = 0.10
    release_threshold: float = 0.05
    #: Cadence decays to zero if no step lands within this long.
    step_timeout: float = 0.90
    #: How many recent intervals to average.
    history: int = 4

    _raised: dict[str, bool] = field(default_factory=lambda: {"left": False, "right": False})
    _last_leg: str | None = field(default=None)
    _last_step_time: float | None = field(default=None)
    _intervals: deque[float] = field(default_factory=lambda: deque(maxlen=4))
    steps: int = 0

    def __post_init__(self) -> None:
        self._intervals = deque(maxlen=self.history)

    def update(self, left_lift: float, right_lift: float, now: float) -> float:
        for leg, lift in (("left", left_lift), ("right", right_lift)):
            if self._raised[leg]:
                if lift < self.release_threshold:
                    self._raised[leg] = False
            elif lift > self.lift_threshold:
                self._raised[leg] = True
                self._register_step(leg, now)

        return self.cadence(now)

    def _register_step(self, leg: str, now: float) -> None:
        if leg == self._last_leg:
            # Same leg twice: a bounce or a tracking wobble, not a stride.
            return
        if self._last_step_time is not None:
            interval = now - self._last_step_time
            if interval > _EPSILON:
                self._intervals.append(interval)
        self._last_leg = leg
        self._last_step_time = now
        self.steps += 1

    def cadence(self, now: float) -> float:
        if self._last_step_time is None or not self._intervals:
            return 0.0
        if now - self._last_step_time > self.step_timeout:
            return 0.0
        mean_interval = sum(self._intervals) / len(self._intervals)
        if mean_interval < _EPSILON:
            return 0.0
        return 1.0 / mean_interval

    def reset(self) -> None:
        self._raised = {"left": False, "right": False}
        self._last_leg = None
        self._last_step_time = None
        self._intervals.clear()


@dataclass
class HipBaseline:
    """A slow-moving reference for hip height, so a jump is a *rise*.

    Tracking the baseline rather than using a fixed value means it follows
    you drifting nearer the camera or settling into a crouch, and only a
    fast rise above it counts.
    """

    tau: float = 0.80
    value: float | None = None

    def update(self, hip_y: float, dt: float) -> float:
        if self.value is None or dt <= 0.0:
            self.value = hip_y
            return hip_y
        alpha = dt / (self.tau + dt)
        self.value += alpha * (hip_y - self.value)
        return self.value

    def reset(self) -> None:
        self.value = None


class IntentEngine:
    """Stateful translation of poses into :class:`Intent`.

    Call :meth:`update` once per frame with a monotonic timestamp.
    """

    def __init__(self, settings=None) -> None:
        from .config import Settings  # local import keeps this module standalone

        self.settings = settings or Settings()
        tuning = self.settings.tuning

        self.steps = StepCounter(
            lift_threshold=tuning.step_threshold,
            release_threshold=tuning.step_release,
            step_timeout=tuning.step_timeout,
        )
        self.hip_baseline = HipBaseline(tuning.hip_baseline_tau)

        # -inf, not 0.0: initialising cooldowns to zero suppresses the very
        # first jump whenever the clock starts near zero. Invisible under
        # time.monotonic(), wrong under any test that starts at t=0.
        self._last_jump = -math.inf
        self._last_dive = -math.inf

        self._pause_since: float | None = None
        self._pause_armed = True
        self._paused = self.settings.runtime.start_paused
        self._last_time: float | None = None
        self._steer = 0.0

        self.jumps = 0
        self.dives = 0

    @property
    def paused(self) -> bool:
        return self._paused

    def update(self, pose: Pose, now: float) -> Intent:
        dt = 0.0 if self._last_time is None else max(0.0, now - self._last_time)
        self._last_time = now

        signals = measure(pose, self.settings.pose.min_visibility)
        if signals is None:
            # Body lost: stop moving. A dropped detection should never leave
            # the stick pinned forward.
            self.steps.reset()
            self._steer = 0.0
            return Intent(tracked=False, paused=self._paused)

        tuning = self.settings.tuning

        paused = self._update_pause(signals, now)
        cadence = self.steps.update(
            signals.left_knee_lift, signals.right_knee_lift, now
        )
        baseline = self.hip_baseline.update(signals.hip_y, dt)
        # y grows downward, so a rise is the baseline minus current height.
        hip_rise = baseline - signals.hip_y

        # Smooth the steering axis only. Everything else is event-shaped and
        # already has hysteresis or a cooldown of its own.
        target_steer = _clamp(signals.lean / max(tuning.lean_full, _EPSILON))
        if dt > 0.0:
            alpha = dt / (tuning.steer_smoothing + dt)
            self._steer += alpha * (target_steer - self._steer)
        else:
            self._steer = target_steer

        forward = _ramp(cadence, tuning.cadence_min, tuning.cadence_max)

        jump = self._check_jump(hip_rise, now)
        dive = self._check_dive(signals, now)

        if paused:
            return Intent(
                tracked=True,
                paused=True,
                lean=signals.lean,
                cadence=cadence,
                hip_rise=hip_rise,
                pause_progress=self._pause_progress(now),
            )

        return Intent(
            tracked=True,
            steer=_clamp(self._steer),
            forward=forward,
            jump=jump,
            dive=dive,
            paused=False,
            lean=signals.lean,
            cadence=cadence,
            hip_rise=hip_rise,
            pause_progress=self._pause_progress(now),
        )

    def _update_pause(self, signals: Signals, now: float) -> bool:
        """Both hands on head for a full second toggles pause.

        This gesture is load-bearing rather than a nicety: gamepad input
        follows window focus, so once Fall Guys has focus the preview window
        cannot receive a keypress. Without it there is no way to stop the
        stick mid-match short of alt-tabbing.
        """
        holding = signals.wrists_above_nose and signals.hands_together
        hold_seconds = self.settings.tuning.pause_hold

        if not holding:
            # Releasing re-arms the toggle. Without this, keeping your hands
            # up would flip pause on and off once per second.
            self._pause_since = None
            self._pause_armed = True
            return self._paused

        if not self._pause_armed:
            return self._paused

        if self._pause_since is None:
            self._pause_since = now
        elif now - self._pause_since >= hold_seconds:
            self._paused = not self._paused
            self._pause_since = None
            self._pause_armed = False
            if self._paused:
                self.steps.reset()
                self._steer = 0.0
        return self._paused

    def _pause_progress(self, now: float) -> float:
        if self._pause_since is None:
            return 0.0
        return _clamp(
            (now - self._pause_since) / max(self.settings.tuning.pause_hold, _EPSILON),
            0.0,
            1.0,
        )

    def _check_jump(self, hip_rise: float, now: float) -> bool:
        tuning = self.settings.tuning
        if hip_rise < tuning.jump_threshold:
            return False
        if now - self._last_jump < tuning.jump_cooldown:
            return False
        self._last_jump = now
        self.jumps += 1
        return True

    def _check_dive(self, signals: Signals, now: float) -> bool:
        tuning = self.settings.tuning
        # Wide specifically so it cannot be confused with hands-on-head pause,
        # which requires the hands close together.
        wide = signals.hand_span > signals.shoulder_span * tuning.dive_width_ratio
        if not (signals.wrists_above_shoulders and wide):
            return False
        if now - self._last_dive < tuning.dive_cooldown:
            return False
        self._last_dive = now
        self.dives += 1
        return True

    def reset(self) -> None:
        self.steps.reset()
        self.hip_baseline.reset()
        self._steer = 0.0
        self._pause_since = None
        self._pause_armed = True
        self._last_time = None
