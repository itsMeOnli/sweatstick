"""Play Fall Guys with your body: webcam pose tracking to a virtual Xbox pad."""

from __future__ import annotations

__version__ = "0.1.0"

from .config import Settings, load_settings
from .intents import Intent, IntentEngine, Point, Pose

__all__ = [
    "__version__",
    "Settings",
    "load_settings",
    "Intent",
    "IntentEngine",
    "Pose",
    "Point",
]
