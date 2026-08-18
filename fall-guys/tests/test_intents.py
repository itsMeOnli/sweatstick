"""Tests for the pure intent logic.

All of these build synthetic landmarks, so they need no camera, no ViGEmBus
driver and no game. That is the point of keeping ``intents.py`` free of
``cv2`` and ``vgamepad``.

Landmark coordinates are normalized image space with y growing *downward*,
so a smaller y means higher up the body.
"""

from __future__ import annotations

import math

import pytest

from fallguys_pose.config import Settings
from fallguys_pose.intents import (
    HipBaseline,
    IntentEngine,
    Point,
    Pose,
    StepCounter,
    measure,
)


def make_pose(
    *,
    shoulder_y: float = 0.30,
    hip_y: float = 0.55,
    centre_x: float = 0.50,
    shoulder_offset: float = 0.0,
    shoulder_half_width: float = 0.09,
    left_knee_y: float = 0.75,
    right_knee_y: float = 0.75,
    wrist_y: float | None = None,
    wrist_half_span: float = 0.09,
    nose_y: float = 0.22,
    visibility: float = 1.0,
) -> Pose:
    """Build a frontal skeleton.

    Defaults are a neutral standing pose: torso length 0.25, knees below the
    hips, hands down at the sides. ``shoulder_offset`` shifts the shoulders
    sideways relative to the hips, which is what the lean measurement reads.
    """
    if wrist_y is None:
        wrist_y = hip_y + 0.05  # hands hanging at the sides

    shoulder_x = centre_x + shoulder_offset
    points = {
        "nose": Point(shoulder_x, nose_y, visibility),
        "left_shoulder": Point(shoulder_x - shoulder_half_width, shoulder_y, visibility),
        "right_shoulder": Point(shoulder_x + shoulder_half_width, shoulder_y, visibility),
        "left_wrist": Point(centre_x - wrist_half_span, wrist_y, visibility),
        "right_wrist": Point(centre_x + wrist_half_span, wrist_y, visibility),
        "left_hip": Point(centre_x - 0.07, hip_y, visibility),
        "right_hip": Point(centre_x + 0.07, hip_y, visibility),
        "left_knee": Point(centre_x - 0.07, left_knee_y, visibility),
        "right_knee": Point(centre_x + 0.07, right_knee_y, visibility),
    }
    return Pose(points=points)


def hop(rise: float = 0.06) -> Pose:
    """A mid-air pose: the *whole body* rises, so torso length is unchanged.

    This matters. Lowering only ``hip_y`` would shorten the torso, and since
    hip height is normalized by torso length that reads as the hips moving
    *down*. A jump has to move the shoulders and head too.
    """
    return make_pose(
        shoulder_y=0.30 - rise,
        hip_y=0.55 - rise,
        nose_y=0.22 - rise,
        left_knee_y=0.75 - rise,
        right_knee_y=0.75 - rise,
    )


# --------------------------------------------------------------------------
# measure()
# --------------------------------------------------------------------------


def test_measure_returns_none_without_a_pose():
    assert measure(Pose()) is None


def test_measure_returns_none_when_landmarks_are_low_confidence():
    assert measure(make_pose(visibility=0.1)) is None


def test_neutral_stance_has_no_lean():
    signals = measure(make_pose())
    assert signals is not None
    assert signals.lean == pytest.approx(0.0, abs=1e-9)


def test_lean_is_signed_and_normalized_by_torso():
    signals = measure(make_pose(shoulder_offset=0.05))
    assert signals is not None
    # Torso is the shoulder-to-hip vector, so leaning lengthens it slightly.
    expected = 0.05 / math.hypot(0.05, 0.25)
    assert signals.lean == pytest.approx(expected, abs=1e-9)
    assert measure(make_pose(shoulder_offset=-0.05)).lean < 0


def test_measurements_are_invariant_to_distance_from_camera():
    """The whole reason there is no calibration step.

    Two skeletons of identical shape but different apparent size must
    produce identical normalized measurements.
    """
    near = measure(make_pose(shoulder_y=0.20, hip_y=0.70, shoulder_offset=0.10))
    far = measure(make_pose(shoulder_y=0.40, hip_y=0.65, shoulder_offset=0.05))
    assert near is not None and far is not None
    assert near.lean == pytest.approx(far.lean, abs=1e-6)


def test_knee_lift_is_positive_when_the_knee_rises_above_the_hip():
    signals = measure(make_pose(left_knee_y=0.45))
    assert signals is not None
    assert signals.left_knee_lift > 0
    assert signals.right_knee_lift < 0


# --------------------------------------------------------------------------
# StepCounter
# --------------------------------------------------------------------------


def march(counter: StepCounter, interval: float, steps: int, start: float = 0.0) -> float:
    """Drive alternating knee lifts at a fixed interval; returns the end time."""
    now = start
    for index in range(steps):
        left = 0.20 if index % 2 == 0 else 0.0
        right = 0.0 if index % 2 == 0 else 0.20
        counter.update(left, right, now)
        now += interval
    return now


def test_cadence_is_zero_before_any_steps():
    assert StepCounter().cadence(0.0) == 0.0


def test_cadence_measures_the_interval_between_steps():
    """Regression: cadence must not be events-in-a-window.

    Counting events inside a rolling window overestimates the rate by roughly
    1/window — a true 1.0 steps/sec reads as 1.5 in a two-second window,
    which is enough to creep forward while barely moving.
    """
    counter = StepCounter()
    end = march(counter, interval=1.0, steps=6)
    assert counter.cadence(end - 1.0) == pytest.approx(1.0, abs=0.01)


def test_faster_stepping_gives_higher_cadence():
    slow = StepCounter()
    slow_end = march(slow, interval=0.5, steps=6)
    fast = StepCounter()
    fast_end = march(fast, interval=0.25, steps=6)
    assert fast.cadence(fast_end - 0.25) > slow.cadence(slow_end - 0.5)


def test_cadence_decays_to_zero_when_you_stop():
    """Stopping must stop you, not coast for the length of a window."""
    counter = StepCounter(step_timeout=0.9)
    end = march(counter, interval=0.4, steps=6)
    assert counter.cadence(end) > 0
    assert counter.cadence(end + 2.0) == 0.0


def test_the_same_knee_pumping_does_not_accumulate_cadence():
    """Alternation is required, so one twitchy leg cannot drive you forward."""
    counter = StepCounter()
    now = 0.0
    for _ in range(6):
        counter.update(0.20, 0.0, now)
        now += 0.2
        counter.update(0.0, 0.0, now)  # release below threshold
        now += 0.2
    assert counter.steps == 1
    assert counter.cadence(now) == 0.0


def test_a_knee_must_drop_below_release_before_stepping_again():
    counter = StepCounter(lift_threshold=0.10, release_threshold=0.05)
    counter.update(0.20, 0.0, 0.0)
    counter.update(0.15, 0.0, 0.1)  # still up: not a new step
    assert counter.steps == 1


# --------------------------------------------------------------------------
# HipBaseline
# --------------------------------------------------------------------------


def test_hip_baseline_starts_at_the_first_sample():
    baseline = HipBaseline()
    assert baseline.update(2.0, 0.033) == 2.0


def test_hip_baseline_tracks_slow_drift_but_lags_a_fast_hop():
    baseline = HipBaseline(tau=0.8)
    for _ in range(60):
        baseline.update(2.0, 1 / 30)
    settled = baseline.value
    assert settled == pytest.approx(2.0, abs=0.01)

    # One frame of a sudden rise must barely move the reference, or a jump
    # would erase its own evidence.
    baseline.update(1.7, 1 / 30)
    assert baseline.value == pytest.approx(2.0, abs=0.02)


# --------------------------------------------------------------------------
# IntentEngine
# --------------------------------------------------------------------------


def test_lost_pose_produces_no_movement():
    engine = IntentEngine()
    intent = engine.update(Pose(), 0.0)
    assert not intent.tracked
    assert intent.steer == 0.0
    assert intent.forward == 0.0
    assert not intent.jump


def test_standing_still_does_not_move():
    engine = IntentEngine()
    intent = engine.update(make_pose(), 0.0)
    for step in range(1, 30):
        intent = engine.update(make_pose(), step / 30)
    assert intent.tracked
    assert intent.forward == 0.0
    assert intent.steer == pytest.approx(0.0, abs=1e-6)


def test_a_moderate_lean_steers_proportionally():
    engine = IntentEngine()
    intent = engine.update(make_pose(), 0.0)
    for step in range(1, 90):
        intent = engine.update(make_pose(shoulder_offset=0.03), step / 30)
    expected = (0.03 / math.hypot(0.03, 0.25)) / Settings().tuning.lean_full
    assert intent.steer == pytest.approx(expected, abs=0.01)
    assert 0.0 < intent.steer < 1.0


def test_a_big_lean_saturates_the_stick():
    engine = IntentEngine()
    intent = engine.update(make_pose(), 0.0)
    for step in range(1, 90):
        intent = engine.update(make_pose(shoulder_offset=0.10), step / 30)
    assert intent.steer == pytest.approx(1.0, abs=0.01)


def test_running_in_place_drives_the_stick_forward():
    engine = IntentEngine()
    now = 0.0
    intent = None
    for index in range(20):
        knee_up = 0.30 if index % 2 == 0 else 0.75
        knee_down = 0.75 if index % 2 == 0 else 0.30
        intent = engine.update(
            make_pose(left_knee_y=knee_up, right_knee_y=knee_down), now
        )
        now += 0.25  # 4 steps/sec, above cadence_max
    assert intent is not None
    assert intent.forward == pytest.approx(1.0, abs=0.01)


def test_the_first_jump_is_not_swallowed():
    """Regression: cooldowns initialised to 0.0 suppress the first gesture.

    Under ``time.monotonic()`` the clock is large enough to hide this, but at
    t=0 a zero-initialised cooldown eats the very first jump.
    """
    engine = IntentEngine()
    assert engine._last_jump == -math.inf

    engine.update(make_pose(), 0.0)
    for step in range(1, 40):  # let the baseline settle
        engine.update(make_pose(), step / 30)
    intent = engine.update(hop(), 40 / 30)
    assert intent.jump


def test_jump_respects_its_cooldown():
    engine = IntentEngine()
    for step in range(40):
        engine.update(make_pose(), step / 30)

    first = engine.update(hop(), 40 / 30)
    second = engine.update(hop(), 41 / 30)
    assert first.jump
    assert not second.jump  # within jump_cooldown


def test_dive_requires_arms_up_and_wide():
    engine = IntentEngine()
    engine.update(make_pose(), 0.0)

    # Up but narrow: that is the pause gesture's shape, not a dive.
    narrow = engine.update(
        make_pose(wrist_y=0.10, wrist_half_span=0.03, nose_y=0.22), 0.1
    )
    assert not narrow.dive

    wide = engine.update(make_pose(wrist_y=0.10, wrist_half_span=0.20), 0.2)
    assert wide.dive


def test_hands_on_head_toggles_pause_after_the_hold():
    settings = Settings()
    engine = IntentEngine(settings)
    hands_on_head = make_pose(wrist_y=0.15, wrist_half_span=0.04, nose_y=0.22)

    now = 0.0
    engine.update(hands_on_head, now)
    assert not engine.paused

    # Held for less than pause_hold: still running.
    now = settings.tuning.pause_hold * 0.5
    assert not engine.update(hands_on_head, now).paused

    now = settings.tuning.pause_hold + 0.05
    assert engine.update(hands_on_head, now).paused


def test_pausing_zeroes_movement():
    settings = Settings()
    engine = IntentEngine(settings)
    hands_on_head = make_pose(
        wrist_y=0.15, wrist_half_span=0.04, nose_y=0.22, shoulder_offset=0.05
    )
    engine.update(hands_on_head, 0.0)
    intent = engine.update(hands_on_head, settings.tuning.pause_hold + 0.05)
    assert intent.paused
    assert intent.steer == 0.0
    assert intent.forward == 0.0


def test_releasing_the_gesture_prevents_immediate_retoggle():
    """Holding hands on head must not flip pause repeatedly."""
    settings = Settings()
    engine = IntentEngine(settings)
    hands_on_head = make_pose(wrist_y=0.15, wrist_half_span=0.04, nose_y=0.22)

    engine.update(hands_on_head, 0.0)
    engine.update(hands_on_head, settings.tuning.pause_hold + 0.05)
    assert engine.paused

    # Keep holding for another full second without releasing.
    engine.update(hands_on_head, settings.tuning.pause_hold + 0.10)
    intent = engine.update(hands_on_head, settings.tuning.pause_hold * 2 + 0.15)
    assert intent.paused, "pause flipped back while the gesture was still held"
