"""Settings, with defaults in code and overrides in TOML.

Every tuning number lives here rather than scattered through the logic,
because "how far do I have to lean" is a per-room, per-body, per-camera
value. The defaults are the ones that ended up working in practice; the
comments record which way to move each one when it misbehaves.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

#: Looked for in the working directory unless ``--config`` says otherwise.
DEFAULT_CONFIG_NAME = "fallguys.toml"


@dataclass
class CameraSettings:
    #: Device index, or an MJPEG URL like ``http://192.168.1.5:8080/video``
    #: for Android's IP Webcam app (no virtual-camera driver needed).
    source: int | str = 0
    #: ``dshow`` is the reliable one on Windows. The default MSMF backend
    #: hangs or silently fails on a lot of laptop webcams.
    backend: str = "dshow"
    #: 640x480 on purpose: MediaPipe downsamples internally anyway, so higher
    #: capture resolution costs phone heat and CPU while buying almost no
    #: tracking accuracy. Phone-as-webcam setups throttle at 1080p.
    width: int = 640
    height: int = 480
    fps: int = 30
    #: A stream that goes black or stops delivering is reopened after this long.
    reconnect_after: float = 3.0
    #: Frames darker than this (sampled) count as black.
    black_level: int = 8


@dataclass
class PoseSettings:
    #: Drop to 0 if you fall below 25fps. The accuracy loss is small; the
    #: latency win is not, and latency is what decides whether Hit Parade is
    #: fun or infuriating.
    model_complexity: int = 1
    min_detection_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    min_visibility: float = 0.5


@dataclass
class TuningSettings:
    """The numbers you will actually touch."""

    # --- Steering -------------------------------------------------------
    #: Lean (in torso-lengths) that produces full stick deflection.
    #: Lower it if steering feels stiff and you have to fall over to turn.
    lean_full: float = 0.25
    #: Seconds of smoothing on the steering axis.
    steer_smoothing: float = 0.06

    # --- Running in place -----------------------------------------------
    #: Knee lift that counts as a step. Lower to 0.06 if running in place
    #: never registers — usually a sign your knees are cropped out of frame.
    step_threshold: float = 0.10
    #: Knee must drop below this before the same leg can step again.
    step_release: float = 0.05
    #: Cadence falls to zero if no step lands within this long, so you stop
    #: when you stop instead of coasting.
    step_timeout: float = 0.90
    #: Steps/sec at which you start moving, and at which you hit full speed.
    cadence_min: float = 1.2
    cadence_max: float = 3.5

    # --- Jump ------------------------------------------------------------
    #: Hip rise above its rolling baseline, in torso-lengths. THIS IS THE ONE
    #: YOU WILL TOUCH FIRST. Your hips bob while running in place; if that bob
    #: crosses the threshold you will jump constantly. Raise to 0.22, then
    #: 0.25, until a 30-second run is clean — but no higher than still catches
    #: a deliberate hop. That tension is the whole tuning job.
    jump_threshold: float = 0.18
    jump_cooldown: float = 0.40

    # --- Dive ------------------------------------------------------------
    #: Hands must be this much wider than your shoulders. Being *wide* is what
    #: separates a dive from the hands-on-head pause gesture.
    dive_width_ratio: float = 1.15
    dive_cooldown: float = 0.60

    # --- Pause -----------------------------------------------------------
    #: Seconds of hands-on-head before pause toggles.
    pause_hold: float = 1.00

    # --- Output ----------------------------------------------------------
    #: How long a jump/dive button stays held. A single-frame press gets
    #: dropped by the game.
    button_hold: float = 0.12
    hip_baseline_tau: float = 0.80


@dataclass
class RuntimeSettings:
    #: ``vgamepad`` for real output, ``null`` to watch the HUD with no game.
    gamepad: str = "vgamepad"
    show_hud: bool = True
    #: Start paused so the stick is not live before you have stepped back.
    start_paused: bool = False
    print_summary: bool = True


@dataclass
class Settings:
    camera: CameraSettings = field(default_factory=CameraSettings)
    pose: PoseSettings = field(default_factory=PoseSettings)
    tuning: TuningSettings = field(default_factory=TuningSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build(cls: type, data: Any):
    """Construct a nested dataclass from a mapping.

    Unknown keys raise. A typo in a config file should not leave you
    wondering why raising ``jump_threshold`` changed nothing.
    """
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise TypeError(f"Expected a table for {cls.__name__}, got {type(data).__name__}")

    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        options = ", ".join(sorted(known))
        raise ValueError(
            f"Unknown setting(s) in [{cls.__name__}]: {', '.join(sorted(unknown))}. "
            f"Valid options: {options}"
        )

    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        annotation = known[name].type
        nested = globals().get(annotation) if isinstance(annotation, str) else annotation
        if is_dataclass(nested):
            kwargs[name] = _build(nested, value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from TOML, falling back to defaults when absent.

    An explicitly named file that does not exist is an error; the implicit
    ``fallguys.toml`` simply being missing is not.
    """
    explicit = path is not None
    target = Path(path) if explicit else Path(DEFAULT_CONFIG_NAME)

    if not target.exists():
        if explicit:
            raise FileNotFoundError(f"Config file not found: {target}")
        return Settings()

    with target.open("rb") as handle:
        data = tomllib.load(handle)
    return _build(Settings, data)


def resolve_capture_backend(name: str) -> int:
    """Translate a backend name into the OpenCV constant.

    Imports cv2 lazily so that merely loading settings does not require it.
    """
    import cv2

    backends = {
        "any": cv2.CAP_ANY,
        "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
        "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
        "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
        "avfoundation": getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY),
    }
    key = name.lower()
    if key not in backends:
        raise ValueError(
            f"Unknown capture backend '{name}'. Choose from: {', '.join(backends)}"
        )
    return backends[key]
