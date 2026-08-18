"""Command line interface.

Three subcommands, matching the order you actually do things in:
``check-camera`` to find a working source, ``tune`` to adjust thresholds with
no game and no gamepad, then ``run``.
"""

from __future__ import annotations

import argparse
import sys

from .config import Settings, load_settings

DESCRIPTION = """\
Play Fall Guys with your body. A webcam tracks your pose and drives a virtual
Xbox controller: run in place to move, lean to steer, hop to jump, arms up and
wide to dive, hands on head for one second to pause.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fallguys-pose",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="TOML settings file (defaults to ./fallguys.toml if present)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check-camera",
        help="find which camera index and backend actually deliver frames",
    )
    check.add_argument("--max-index", type=int, default=4)
    check.add_argument(
        "--no-preview", action="store_true", help="skip the preview window"
    )

    tune = subparsers.add_parser(
        "tune",
        help="HUD only, no gamepad output — for adjusting thresholds safely",
    )
    _add_shared_arguments(tune)

    run = subparsers.add_parser("run", help="drive the virtual gamepad")
    _add_shared_arguments(run)
    run.add_argument(
        "--gamepad",
        choices=("vgamepad", "null"),
        help="output backend (default: vgamepad)",
    )
    run.add_argument(
        "--no-hud", action="store_true", help="run without the preview window"
    )

    subparsers.add_parser(
        "selftest-gamepad",
        help="hold the virtual left stick right for 5s to prove Windows sees it",
    )
    return parser


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source", help="camera index, or an MJPEG URL for a phone camera"
    )
    parser.add_argument(
        "--backend", choices=("dshow", "msmf", "any", "v4l2", "avfoundation")
    )
    parser.add_argument(
        "--model-complexity",
        type=int,
        choices=(0, 1, 2),
        help="0 is faster and lower latency; use it if you drop below 25fps",
    )


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """Let flags win over the config file."""
    source = getattr(args, "source", None)
    if source is not None:
        settings.camera.source = int(source) if source.isdigit() else source
    if getattr(args, "backend", None):
        settings.camera.backend = args.backend
    if getattr(args, "model_complexity", None) is not None:
        settings.pose.model_complexity = args.model_complexity
    if getattr(args, "gamepad", None):
        settings.runtime.gamepad = args.gamepad
    if getattr(args, "no_hud", False):
        settings.runtime.show_hud = False
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "selftest-gamepad":
        from .gamepad import GamepadUnavailable, selftest

        try:
            selftest()
        except GamepadUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.command == "check-camera":
        from .app import check_camera

        return check_camera(args.max_index, preview=not args.no_preview)

    try:
        settings = load_settings(args.config)
    except (FileNotFoundError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    settings = _apply_overrides(settings, args)

    if args.command == "tune":
        # Tuning must never move the game, so the gamepad choice is forced
        # rather than merely defaulted.
        settings.runtime.gamepad = "null"
        settings.runtime.show_hud = True
        print("Tuning mode — HUD only, no gamepad output. Press Q to quit.\n")

    from .app import run_session
    from .camera import CameraError
    from .gamepad import GamepadUnavailable

    try:
        summary = run_session(settings)
    except (CameraError, GamepadUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if settings.runtime.print_summary:
        print(summary.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
