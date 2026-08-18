# Sweatstick

Turning a webcam into a game controller, so that playing is the workout.

The pitch is narrow on purpose: not "control games with gestures", but **make
the effort real**. Forward motion is gated on you actually running in place,
and it's a rate rather than a switch — jog harder and you move faster. A Fall
Guys race round is about two minutes of near-constant forward input, which
means you end up doing two-minute intervals whether you planned to or not.

| Project | What it does |
|---|---|
| [fall-guys](fall-guys) | Body pose to a virtual Xbox pad. Run in place to move, lean to steer, hop to jump. |

## How it works

```
webcam ──▶ MediaPipe Pose ──▶ intents ──▶ virtual gamepad ──▶ game
             (~30fps CPU)      (pure)       (ViGEmBus)
```

Three ideas carry across every project here:

**Emit a virtual gamepad, not synthetic keypresses.** Most games ignore
`pyautogui`-style input entirely, and even where keys land you get binary
left/right instead of the analog stick that makes steering playable.

**Normalize every measurement against the body itself.** Distances are divided
by torso length, so a lean of 0.25 means the same thing at 2m from the camera
as at 4m — and there's no calibration step to forget to run.

**Keep the mapping layer pure.** The code that turns landmarks into controller
intents imports no camera and no gamepad library, so it can be tested with
synthetic skeletons on any machine, with no hardware and no game running.

## Requirements

Windows, for the virtual gamepad driver. See each project's README for setup.

## License

MIT — see [LICENSE](LICENSE).
