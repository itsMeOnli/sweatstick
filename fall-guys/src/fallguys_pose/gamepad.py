"""Virtual Xbox 360 pad output.

Going through an emulated gamepad rather than synthetic keypresses is the
whole reason this works: most games ignore ``pyautogui``-style input
entirely, and even where keys land you get binary left/right instead of an
analog stick. Fall Guys sees a normal controller and swaps its button prompts
to Xbox glyphs, which is also your confirmation that it bound correctly.

Windows only — ``vgamepad`` drives the ViGEmBus kernel driver, and there is
no equivalent on macOS or Linux. Use :class:`NullGamepad` to work on the
logic anywhere else.
"""

from __future__ import annotations

import platform
import time
from abc import ABC, abstractmethod

from .intents import Intent


class GamepadUnavailable(RuntimeError):
    """Raised when the virtual gamepad cannot be created here."""


class Gamepad(ABC):
    """Receives an :class:`Intent` each frame and drives the pad."""

    name = "base"

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def apply(self, intent: Intent, now: float) -> None: ...

    @abstractmethod
    def neutral(self) -> None:
        """Release everything. Called whenever tracking or the camera drops."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> Gamepad:
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            self.neutral()
        finally:
            self.close()


class _ButtonPulse:
    """Stretches a one-frame gesture into a press the game will notice."""

    def __init__(self, hold: float) -> None:
        self.hold = hold
        self._until = 0.0

    def update(self, triggered: bool, now: float) -> bool:
        if triggered:
            self._until = now + self.hold
        return now < self._until

    def reset(self) -> None:
        self._until = 0.0


class VGamepad(Gamepad):
    """The real thing: an emulated Xbox 360 controller."""

    name = "vgamepad"

    def __init__(self, button_hold: float = 0.12) -> None:
        self._pad = None
        self._jump = _ButtonPulse(button_hold)
        self._dive = _ButtonPulse(button_hold)
        self._last: tuple | None = None

    def open(self) -> None:
        if platform.system() != "Windows":
            raise GamepadUnavailable(
                "vgamepad needs Windows — it talks to the ViGEmBus kernel "
                f"driver, which this machine ({platform.system()}) does not "
                "have. Use `--gamepad null` to run the HUD without output."
            )
        try:
            import vgamepad as vg
        except ImportError as exc:
            raise GamepadUnavailable(
                "vgamepad is not installed. Run `pip install vgamepad` and "
                "accept the ViGEmBus driver prompt that appears during install."
            ) from exc

        try:
            self._pad = vg.VX360Gamepad()
        except Exception as exc:  # pragma: no cover - driver dependent
            raise GamepadUnavailable(
                "Could not create a virtual gamepad, which nearly always means "
                "ViGEmBus did not install. Try `pip install --force-reinstall "
                "vgamepad` to retrigger the driver prompt, and reboot if it "
                f"asks. Underlying error: {exc}"
            ) from exc

        self._buttons = vg.XUSB_BUTTON
        self.neutral()

    def apply(self, intent: Intent, now: float) -> None:
        if self._pad is None:
            raise RuntimeError("Gamepad used before open()")

        if intent.paused or not intent.tracked:
            self.neutral()
            return

        jump = self._jump.update(intent.jump, now)
        dive = self._dive.update(intent.dive, now)
        state = (round(intent.steer, 3), round(intent.forward, 3), jump, dive)
        if state == self._last:
            return

        self._pad.left_joystick_float(
            x_value_float=intent.steer, y_value_float=intent.forward
        )
        for pressed, code in (
            (jump, self._buttons.XUSB_GAMEPAD_A),
            (dive, self._buttons.XUSB_GAMEPAD_X),
        ):
            if pressed:
                self._pad.press_button(button=code)
            else:
                self._pad.release_button(button=code)
        self._pad.update()
        self._last = state

    def neutral(self) -> None:
        if self._pad is None:
            return
        self._jump.reset()
        self._dive.reset()
        self._pad.reset()
        self._pad.update()
        self._last = (0.0, 0.0, False, False)

    def close(self) -> None:
        if self._pad is not None:
            self._pad.reset()
            self._pad.update()
            self._pad = None


class NullGamepad(Gamepad):
    """Records what would have been sent. Lets the HUD run with no game."""

    name = "null"

    def __init__(self, button_hold: float = 0.12) -> None:
        self._jump = _ButtonPulse(button_hold)
        self._dive = _ButtonPulse(button_hold)
        self.last: tuple[float, float, bool, bool] = (0.0, 0.0, False, False)

    def open(self) -> None:
        self.neutral()

    def apply(self, intent: Intent, now: float) -> None:
        if intent.paused or not intent.tracked:
            self.neutral()
            return
        self.last = (
            intent.steer,
            intent.forward,
            self._jump.update(intent.jump, now),
            self._dive.update(intent.dive, now),
        )

    def neutral(self) -> None:
        self._jump.reset()
        self._dive.reset()
        self.last = (0.0, 0.0, False, False)

    def close(self) -> None:
        self.neutral()


def create_gamepad(name: str, button_hold: float = 0.12) -> Gamepad:
    """Build and open a gamepad backend by name."""
    backends = {"vgamepad": VGamepad, "null": NullGamepad}
    if name not in backends:
        raise ValueError(
            f"Unknown gamepad backend '{name}'. Choose from: {', '.join(backends)}"
        )
    pad = backends[name](button_hold=button_hold)
    pad.open()
    return pad


def selftest(seconds: float = 5.0) -> None:
    """Peg the left stick right, so you can confirm Windows sees the pad.

    Open https://hardwaretester.com/gamepad (better than ``joy.cpl``, which
    is deprecated on Windows 11 and only shows a tiny crosshair) and watch
    the X axis slam to 1.0 while this runs.
    """
    pad = VGamepad()
    pad.open()
    try:
        pad._pad.left_joystick_float(x_value_float=1.0, y_value_float=0.0)
        pad._pad.update()
        print(f"Left stick X held at 1.0 for {seconds:.0f}s — check the tester.")
        time.sleep(seconds)
    finally:
        pad.neutral()
        pad.close()
    print("Done. If the axis moved, the virtual pad works.")
