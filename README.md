# motion-game-controller

Turning a webcam into a game controller, one game at a time. Each subdirectory
is a self-contained project with its own tuning, tests and README.

| Project | What it does |
|---|---|
| [fall-guys](fall-guys) | Body pose to a virtual Xbox pad. Run in place to move, lean to steer, hop to jump. |

The shared idea across projects: track pose with MediaPipe, normalize every
measurement against the body itself so nothing depends on distance from the
camera, and emit a **virtual gamepad** rather than synthetic keypresses — most
games ignore the latter, and an analog stick is what makes steering playable.

## License

MIT — see [LICENSE](LICENSE).
