# fallguys-pose

Play Fall Guys with your body. A webcam tracks your pose and drives a **virtual Xbox controller**, so the game sees a normal gamepad — you run in place to move, lean to steer, and hop to jump.

A Fall Guys race round is roughly two minutes of near-constant forward input, which means you end up doing two-minute cardio intervals whether you planned to or not.

```
webcam ──▶ MediaPipe Pose ──▶ intents ──▶ vgamepad ──▶ Fall Guys
             (~30fps CPU)      (pure)      (ViGEmBus)
```

Going through a virtual gamepad rather than synthetic keypresses is the point. Most games ignore `pyautogui`-style input entirely, and even where keys land you get binary left/right instead of an analog stick that lets you steer.

## Controls

| Movement | How it's detected | Output |
|---|---|---|
| Lean torso left/right | Shoulder midpoint offset from hip midpoint | Left stick X |
| Run in place | Alternating knee lift, measured as cadence | Left stick Y |
| Hop | Hip midpoint rises above its rolling baseline | **A** (jump) |
| Both arms up and wide | Wrists above shoulders, hands wider than shoulders | **X** (dive) |
| Both hands on head, 1s | Wrists above nose, hands close together | Pause toggle |
| `Q` in the preview window | — | Quit |

**Everything is divided by your torso length**, so nothing depends on how far you stand from the camera — a lean of 0.25 means the same thing at 2m as at 4m. That's why there's no calibration step to forget.

**Forward motion is a rate, not a switch.** The script watches your knees alternate, measures the interval between steps, and converts that cadence into stick magnitude: below 1.2 steps/sec you don't move at all, ramping to full speed at 3.5. Jog harder, move faster.

**Jump uses a slow-moving average of your hip height** as its reference, so it adapts as you drift closer to the camera or settle into a crouch. **Dive requires arms specifically *wide*** so it can't be confused with the hands-on-head pause gesture.

Pause is a gesture rather than a keypress because gamepad input follows window focus. Once Fall Guys is focused, the preview window can't receive a keystroke — a gesture is the only way to stop the stick mid-match without alt-tabbing.

## Requirements

- **Windows.** `vgamepad` drives the ViGEmBus kernel driver; there's no macOS or Linux equivalent. Don't use WSL — it has no direct webcam access and can't reach a Windows kernel driver.
- **Python 3.11 or 3.12.** MediaPipe 0.10.14 has no wheels for 3.13+.
- A webcam that can see your **head and knees at the same time** — roughly 2.5–3m back, at chest height.

## Install

Use Command Prompt (`Win+R` → `cmd`) rather than PowerShell, which blocks venv activation scripts by default.

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[gamepad]"
```

During the `vgamepad` install a ViGEmBus driver installer pops up — accept it. If you miss the prompt, `pip install --force-reinstall vgamepad` triggers it again. Reboot if it asks.

## Setup, in order

**1. Confirm Windows sees the virtual pad**, before involving the camera at all:

```bash
fallguys-pose selftest-gamepad
```

Open <https://hardwaretester.com/gamepad> and watch the left stick X axis peg to 1.0 for five seconds. (Better than `joy.cpl`, which is deprecated on Windows 11 and only shows a tiny crosshair.) If nothing moves, ViGEmBus didn't install — fix that before anything else.

Unplug any real controller while testing, or Fall Guys may bind to the wrong one.

**2. Find a working camera:**

```bash
fallguys-pose check-camera
```

This tries every index against DirectShow, MSMF and the default backend, and reports which combinations actually deliver a frame — opening a camera is not the same as it working. Copy the printed `[camera]` block into `fallguys.toml`.

Camera placement is the whole ballgame:

- 2.5–3m back, camera at chest height
- **Head and knees both in frame.** If knees are cropped, forward motion silently never triggers
- Light in front of you, not behind — backlighting wrecks landmark confidence
- Clear a 1.5m radius. You'll be hopping while looking at a screen

**3. Tune with no game running:**

```bash
fallguys-pose tune
```

HUD only, no gamepad output. Each meter is drawn with a white tick at its configured threshold, so tuning is visual — you watch whether a bar crosses its tick.

| Check | Expect | If not |
|---|---|---|
| Stand still | `cadence` 0.00, `lean` near zero | Drifting lean means you're standing crooked or the camera is tilted |
| Lean over | Stick dot hits the edge at a comfortable lean | Lower `lean_full` if it's too stiff |
| Run in place | `cadence` climbs past its tick, stick pushes forward | Lower `step_threshold` to 0.06 — but check your knees are in frame first |
| **Run 30s straight** | The JUMP label never flashes | Raise `jump_threshold` to 0.22, then 0.25 |
| Then hop deliberately | JUMP flashes | You raised `jump_threshold` too far |

That last tension is the entire tuning job: high enough to ignore the bob of running in place, low enough to catch a real hop.

Also worth knowing: if fps looks fine but the skeleton visibly lags you, that's stream buffering rather than pose speed — switch a phone camera from Wi-Fi to USB. And set `model_complexity = 0` if you drop below 25fps. The accuracy loss is small; the latency win is not.

**4. Play:**

```bash
fallguys-pose run
```

Set Fall Guys to windowed borderless so the preview can live on a second monitor. The game auto-detects the pad and swaps its button prompts to Xbox glyphs — that's your confirmation it bound.

**Test in the lobby, not a match.** The pre-game hub lets you run, jump and dive with no stakes and no timer. Confirm all four gestures there before you queue.

On exit it prints duration, steps, jumps and dives.

## Configuration

Copy `fallguys.toml.example` to `fallguys.toml` and edit. Every option is documented there. A misspelt key raises an error rather than being silently ignored.

The one you'll touch first is `jump_threshold`. Your hips bob while running in place; if that bob crosses the threshold you'll jump constantly.

## Using a phone as the camera

A laptop webcam sits at desk height with a narrow field of view, which is bad for full-body at 3m. A phone on a tripod sees you far better.

- **iPhone:** iVCam, Camo, or Iriun register a virtual camera driver, so the phone shows up as another camera index. **Connect over USB, not Wi-Fi** — wireless adds 100–300ms on top of the ~100ms pose latency you already have.
- **Android:** the IP Webcam app streams MJPEG over HTTP with no driver at all. Set `source = "http://192.168.1.5:8080/video"` — URL sources are handled natively.

Use the rear camera: better sensor, wider field of view.

If it works for about ten minutes and then goes black at 1fps, that's **thermal throttling**, not the bridge app — feel the phone. Dropping capture to 640×480 (the default here, for exactly this reason) buys a much longer run, since MediaPipe downsamples internally anyway. The capture layer detects black frames, zeroes the gamepad, and reopens the stream after 3 seconds rather than leaving your bean sprinting.

A cheap USB webcam deletes this entire problem class — no app, no driver, no thermals.

## Known limitations

**Pose detection adds ~80–120ms** on top of your own reaction time. You will be noticeably worse at the game. Races stay fun; precision platforming rounds will make you want to throw the camera. For workout purposes that's arguably correct — losing early just means another warm-up round.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

`intents.py` imports neither `cv2` nor `vgamepad`. That's deliberate: the whole mapping layer is tested with synthetic landmarks, so the tests run anywhere — no camera, no driver, no game, including on macOS.

| Module | Role |
|---|---|
| `intents.py` | Landmarks → `Intent`. Pure, no hardware |
| `tracking.py` | MediaPipe wrapper |
| `camera.py` | Capture, black-frame detection, reconnect |
| `gamepad.py` | vgamepad and null backends |
| `hud.py` | Preview overlay with threshold ticks |
| `app.py` | Run loop |
| `cli.py` | `check-camera`, `tune`, `run`, `selftest-gamepad` |
| `config.py` | Settings and TOML loading |

Two bugs the tests caught, both worth keeping regression coverage on:

- **Cadence was measured as events in a rolling window**, which overestimates the rate by roughly 1/window — a true 1.0 steps/sec read as 1.5, so you'd creep forward while barely moving. It's now measured from intervals between steps, with a timeout so it drops to zero when you stop instead of coasting.
- **Cooldown timestamps initialized to `0.0`** suppressed the first jump or dive whenever the clock started near zero. Invisible under `time.monotonic()`, wrong under test.

## Prior art

Nothing off-the-shelf covers this today. [Gamebody](https://github.com/everythingishacked/Gamebody) is a real, free full-body controller in Python/MediaPipe, but it emits keyboard keys — binary movement, no analog stick, no "run faster = move faster". XerController is the same idea as a product but is waitlist-only. Tiltility (Steam) is upper-body tilt only, no legs.

## License

MIT
