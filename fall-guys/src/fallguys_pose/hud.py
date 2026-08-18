"""The preview overlay.

The HUD is a tuning instrument, not decoration. Each meter is drawn with a
white tick at its configured threshold, so tuning becomes visual: run in
place, watch whether the hip-rise bar crosses its tick, and if it does you
know to raise ``jump_threshold`` before it costs you a round.

The stick dot shows exactly what Fall Guys is receiving, which is the fastest
way to tell "the gesture didn't fire" apart from "the gesture fired and the
game ignored it".
"""

from __future__ import annotations

from .config import Settings
from .intents import Intent

# BGR, because OpenCV.
WHITE = (255, 255, 255)
GREY = (140, 140, 140)
DARK = (40, 40, 40)
GREEN = (90, 220, 120)
AMBER = (60, 190, 250)
RED = (70, 70, 240)
BLUE = (240, 180, 90)


def _bar(
    frame,
    origin: tuple[int, int],
    width: int,
    height: int,
    fraction: float,
    tick: float | None,
    colour,
    label: str,
    value_text: str,
) -> None:
    import cv2

    x, y = origin
    cv2.rectangle(frame, (x, y), (x + width, y + height), DARK, -1)
    filled = int(width * max(0.0, min(1.0, fraction)))
    if filled > 0:
        cv2.rectangle(frame, (x, y), (x + filled, y + height), colour, -1)
    cv2.rectangle(frame, (x, y), (x + width, y + height), GREY, 1)

    if tick is not None:
        tick_x = x + int(width * max(0.0, min(1.0, tick)))
        cv2.line(frame, (tick_x, y - 3), (tick_x, y + height + 3), WHITE, 2)

    cv2.putText(
        frame, label, (x, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1, cv2.LINE_AA
    )
    cv2.putText(
        frame,
        value_text,
        (x + width + 10, y + height - 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        WHITE,
        1,
        cv2.LINE_AA,
    )


def _stick(frame, centre: tuple[int, int], radius: int, steer: float, forward: float) -> None:
    import cv2

    cx, cy = centre
    cv2.circle(frame, (cx, cy), radius, DARK, -1)
    cv2.circle(frame, (cx, cy), radius, GREY, 1)
    cv2.line(frame, (cx - radius, cy), (cx + radius, cy), (70, 70, 70), 1)
    cv2.line(frame, (cx, cy - radius), (cx, cy + radius), (70, 70, 70), 1)
    # Stick y is positive-up; screen y is positive-down.
    dot = (cx + int(steer * radius), cy - int(forward * radius))
    cv2.circle(frame, dot, 7, GREEN, -1)
    cv2.putText(
        frame,
        "left stick",
        (cx - radius, cy + radius + 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        WHITE,
        1,
        cv2.LINE_AA,
    )


def _banner(frame, text: str, colour) -> None:
    import cv2

    height, width = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (width, 42), colour, -1)
    size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)[0]
    cv2.putText(
        frame,
        text,
        ((width - size[0]) // 2, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (20, 20, 20),
        2,
        cv2.LINE_AA,
    )


class Hud:
    """Draws the overlay onto each frame."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._flash_until: dict[str, float] = {}

    def flash(self, name: str, now: float, seconds: float = 0.35) -> None:
        """Light up a gesture label briefly when it fires."""
        self._flash_until[name] = now + seconds

    def _flashing(self, name: str, now: float) -> bool:
        return now < self._flash_until.get(name, 0.0)

    def draw(
        self,
        frame,
        intent: Intent,
        now: float,
        fps: float,
        steps: int,
        jumps: int,
        dives: int,
        camera_message: str = "",
    ) -> None:
        import cv2

        tuning = self.settings.tuning
        height, width = frame.shape[:2]

        # --- Meters, each with its threshold marked ------------------------
        bar_x, bar_w, bar_h = 16, 200, 14
        y = 70

        # Cadence is drawn across the full move range so the tick sits where
        # you start moving, not at an arbitrary point on the scale.
        cadence_scale = max(tuning.cadence_max * 1.2, 0.1)
        _bar(
            frame,
            (bar_x, y),
            bar_w,
            bar_h,
            intent.cadence / cadence_scale,
            tuning.cadence_min / cadence_scale,
            GREEN,
            "cadence (steps/s)",
            f"{intent.cadence:.2f}",
        )

        y += 46
        rise_scale = max(tuning.jump_threshold * 2.0, 0.1)
        _bar(
            frame,
            (bar_x, y),
            bar_w,
            bar_h,
            intent.hip_rise / rise_scale,
            tuning.jump_threshold / rise_scale,
            AMBER,
            "hip rise (jump)",
            f"{intent.hip_rise:+.3f}",
        )

        y += 46
        lean_scale = max(tuning.lean_full * 2.0, 0.1)
        _bar(
            frame,
            (bar_x, y),
            bar_w,
            bar_h,
            (intent.lean + lean_scale / 2) / lean_scale,
            0.5 + tuning.lean_full / lean_scale / 2,
            BLUE,
            "lean (steer)",
            f"{intent.lean:+.3f}",
        )

        # --- What the game is actually receiving ---------------------------
        _stick(frame, (width - 90, 110), 60, intent.steer, intent.forward)

        # --- Gesture flashes -----------------------------------------------
        if intent.jump:
            self.flash("JUMP", now)
        if intent.dive:
            self.flash("DIVE", now)

        label_y = height - 60
        for name, colour in (("JUMP", AMBER), ("DIVE", BLUE)):
            active = self._flashing(name, now)
            cv2.putText(
                frame,
                name,
                (bar_x, label_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                colour if active else (70, 70, 70),
                2 if active else 1,
                cv2.LINE_AA,
            )
            label_y += 30

        # --- Counters and status -------------------------------------------
        cv2.putText(
            frame,
            f"{fps:5.1f} fps   steps {steps}   jumps {jumps}   dives {dives}",
            (bar_x, height - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            WHITE,
            1,
            cv2.LINE_AA,
        )

        if intent.pause_progress > 0.0 and not intent.paused:
            cv2.putText(
                frame,
                f"pause in {1.0 - intent.pause_progress:.1f}s",
                (width - 200, height - 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                WHITE,
                1,
                cv2.LINE_AA,
            )

        # Banner priority runs worst-first: a dead camera matters more than a
        # lost body, which matters more than being paused.
        if camera_message:
            _banner(frame, camera_message.upper(), RED)
        elif not intent.tracked:
            _banner(frame, "NO POSE - STEP BACK INTO FRAME", RED)
        elif intent.paused:
            _banner(frame, "PAUSED - HANDS ON HEAD TO RESUME", AMBER)
