# Edwin: the print agent for the E3D ToolChanger

Named after Edwin Abbott, author of Flatland. Edwin takes a description,
a model or a G-code file to a finished print on the ToolChanger (Duet 2
WiFi, RepRapFirmware 3.5.4, DuetWiFiServer 2.2.0), with a per-layer
timelapse from the Jetson camera. A Claude Code session drives it through
the `/part` skill in `.claude/skills/part/`.

## How it works in practice

**Two places, two jobs.**

| Where | What lives there | Backed up by |
|---|---|---|
| `D:\Source\Edwin` (this repo) | Edwin's code, the slicing presets, the tests, the `/part` skill, the plan and the handover notes. Next door to `D:\Source\BlenderReality`, which renders the build pictures. | git, github.com/cjforman/Edwin |
| `D:\OneDrive\work\Org\Forman\3DParts` (the parts folder) | `StandardSizes.scad`, the older top-level `.scad` files, and one folder per job: `<name>/<name>.scad`, the STL, the previews, the G-code, the Blender pictures, the estimate, the timelapse and the log. | OneDrive. Never git: `.gitignore` here refuses `.gcode`, `.stl` and `.3mf`. |

Edwin finds the parts folder through `EDWIN_PARTS_DIR`; the default is the
OneDrive path above, so nothing needs setting on this machine. Every
command takes a bare part name and resolves it inside that folder:
`python part.py preview cube30_m3` means
`<parts>/cube30_m3/cube30_m3.scad`, `python agent.py 192.168.1.14 print cube30_m3 --yes`
means `<parts>/cube30_m3/cube30_m3.T2.gcode`. Paths work too, and
`--job-dir` sends a job's outputs somewhere else.

**Making a part, step by step.**

1. Open a Claude Code session in `D:\Source\Edwin` (a session in the parts
   folder works as well: its `CLAUDE.md` and a stub `/part` skill point
   here). Type `/part a 30 mm cube with an M3 hole through it`.
2. The session names the part (`cube30_m3`), creates
   `<parts>/cube30_m3/`, writes `cube30_m3.scad` there to the folder's
   conventions (parameters with units, `include <../StandardSizes.scad>`),
   runs `part.py preview` and shows you the two OpenSCAD pictures, from
   above and from below. You say what to change; it revises and shows
   again. It does not go on until you say the shape is right.
3. It runs `render` (STL, refused if not manifold), `fit` (against the
   300 x 200 x 300 mm bed), `slice` (the E3D presets in `profiles/`, tool
   2, PLA, 0.2 mm layers, supports on, ToolChanger start and end G-code
   patched in, checked), `render-blender` (the toolpath as it would print,
   and the first layer from above, about half a minute) and `estimate`.
   It shows you the OpenSCAD preview and the Blender build side by side,
   the first-layer picture, and one line: "1:46, 13.3 g, 149 layers,
   52 x 52 x 30 mm on the bed (skirt and brim included)". Then it stops.
4. You say yes, with the printer on and idle, the Jetson on,
   SymbolicTM's camera server running and no SymbolicTM session connected,
   and you in the room. The session runs
   `agent.py 192.168.1.14 print cube30_m3 --tool 2 --yes --timelapse`.
   Edwin homes the printer if it is not (refused with a tool on the
   carriage), pictures the bed with camera 1 and stops if the picture
   differs from the empty-bed reference (`--bed-clear` overrides when you
   have looked), refuses a part taller than the Duet's SafeZ unless the
   tool-park macro is the guarded one (see 2026-10-30 in the status log),
   uploads over USB if the cable is in (1.4 MB in
   11 s) or over Wi-Fi if not (2.3 MB in 16 s), starts the job from the SD
   card, watches it to the end, records a frame per layer from camera 1
   into `<parts>/cube30_m3/timelapse/` and writes an MP4, and logs every
   command it sent to `<parts>/cube30_m3/agent_<stamp>.log`.
5. The session reports what happened, listing every command sent to the
   printer and the Jetson, and flags a reboot, heater fault or anything
   odd at once. It never commits anything.

**Without a session**, the same commands work by hand from this folder in
the `printagent` conda env (from MSYS bash prepend its folders to PATH
inline, see the user CLAUDE.md; the env keeps its old name):

```bash
python part.py preview NAME            # NAME.png and NAME_below.png in the job folder
python part.py render NAME             # NAME.stl, refused if not manifold
python part.py fit NAME [--host 192.168.1.14]   # bed check; --host adds the Duet's axis limits
python part.py slice NAME --tool 2     # NAME.T2.gcode with the E3D presets, no printer
python part.py render-blender NAME     # NAME.blender.png, NAME.blender_layer1.png
python part.py estimate NAME           # time, grams, layers, extents; NAME.estimate.json
python agent.py 192.168.1.14 status
python agent.py 192.168.1.14 link
python agent.py 192.168.1.14 upload NAME [--port none] [--verify full]
python agent.py 192.168.1.14 print NAME --tool 2 --yes --timelapse
python agent.py 192.168.1.14 print path/to/model.3mf --slice --tool 2 --yes --timelapse
python agent.py 192.168.1.14 watch NAME --timelapse
python agent.py 192.168.1.14 delete NAME
python -m pytest tests -q
```

`--slice` takes a `.scad` (rendered first), an STL (sliced with
`profiles/`, or the `--ini` files you name) or a `.3mf` (its own settings),
always with the ToolChanger start and end G-code for `--tool` patched in.
`print` refuses to start without `--yes`. Before starting it homes the
printer when an axis is unhomed (`G28`; refused while a tool is on the
carriage, because homeall.g turns the coupler), checks that the printer
is idle and has the tool, and pictures the bed with Jetson camera 1 into
`<job>/bed_before.jpg`, scored against `calib/cam1_bed_empty.jpg` by
`bed.py` (take the reference with `python bed.py reference` with the bed
empty and the printer homed; `--bed-clear` skips the check; without a
reference or a camera the check is logged and the print goes on).
`python agent.py HOST home` homes on its own. A G-code file from the E3D
profile is rewritten as `<name>.T<tool>.gcode` (camera tool parked, print
tool heated and picked, parked again at the end) and checked for tool,
heater, bed-area and height problems. `--port none` forces the Wi-Fi path;
`--port COM6` names the cable. `--timelapse` with no argument records into
`<job>/timelapse/`.

**Changing Edwin.** Edit here, run `python -m pytest tests -q` (42 tests,
about 30 s; the OpenSCAD, PrusaSlicer and Blender ones skip where those
are missing), commit, push. The parts folder is never touched by git and
this repo never holds a part, a credential or the home network's name
(the Wi-Fi flash log has the SSID redacted).

## Files

| File | Role |
|---|---|
| `agent.py` | the pipeline: render, slice, prepare, check, upload, verify, start, watch, timelapse; everything into the job folder |
| `part.py` | the OpenSCAD side and the job folders: `render`, `preview`, `slice`, `fit`, `estimate`, `render-blender` (BlenderReality) |
| `duet.py` | Duet HTTP client: retries, reconnects, uploads (USB `M28` or Wi-Fi `rr_upload`), start |
| `duet_usb.py` | USB serial transport (`M28` stream) |
| `toolchanger.py` | rewrites PrusaSlicer output for one tool and checks it before it goes near the printer |
| `prusa.py` | PrusaSlicer console slicing and profile patching |
| `profiles/` | the E3D PrusaSlicer presets Edwin slices a bare STL with (see its README) |
| `timelapse.py`, `jetson_cam.py` | per-layer frames from Jetson camera 1 into an MP4; `python jetson_cam.py light off` if the ring is left on |
| `bed.py`, `calib/` | is the bed clear: a camera 1 picture scored against the empty-bed reference |
| `cam_preview.py`, `focus_map.py` | camera aiming and focus aids |
| `tests/` | offline tests against a fake Duet with fault injection; `test_part.py` also drives OpenSCAD, PrusaSlicer and Blender when installed |
| `firmware/` | DuetWiFiServer 2.2.0 (installed 2026-10-28) and the 1.23 release it replaced: Duet's binaries, no credentials |
| `runs/` | print logs, the launch script, the Wi-Fi flash and upload probes, and the Blender timing probe of 2026-10-28 |
| `documents/PLAN_part_front_end.md` | the plan: description to OpenSCAD to print, with the Blender render, and what each phase delivered |
| `documents/HANDOVER_2026-10-28.md` | what the SymbolicTM session did to the printer and measured |
| `.claude/skills/part/SKILL.md` | the `/part` skill: how a session designs, previews, slices and (on a yes) prints a part |
| `CLAUDE.md` | the rules for a session working in this repo |

## The link, measured

| When | RSSI | Loss PC to Duet, 64 B / 1200 B / 1400 B | Notes |
|---|---|---|---|
| 2026-10-27 evening | -70 dBm | 25% / 91% / - | tape over the antenna, charger beside the board, module firmware 1.23, 33 reconnections |
| 2026-10-28 morning | -70 dBm | 1% / 10% / 13% | tape off, still 1.23 |
| 2026-10-28 after the flash | -62 dBm | 1% / 1% / 1% | DuetWiFiServer 2.2.0, 802.11n, channel 8, 0 reconnections |

Loss was only ever in the PC-to-Duet direction (the ESP8266's receiver);
Duet-to-PC was always clean, and the board itself was always healthy. On
2026-10-28 the WiFi module was flashed from 1.23 (2019) to 2.2.0 over USB
(`M997 S1`, transcript in `runs/wifi_2026-10-28/flash_2026-10-28.log`; the
2.2.0 image and the 1.23 release are in `firmware/`). The Duet is 2.4 GHz
only; the PC's band never mattered. After the flash Edwin's 40 small
requests all succeed; their median latency rose from 23 ms to 81 ms
(tight spread, unexplained, harmless). Details in
`documents/HANDOVER_2026-10-28.md`.

### Uploads over HTTP: `rr_upload` works

Measured on 2026-10-28 with a raw-socket `POST /rr_upload?name=&time=&crc32=`
carrying `X-Session-Key` (`runs/wifi_2026-10-28/upload_probe2.py`,
`upload_probe3.py` and their logs):

* 13 of 13 uploads succeeded, 4 kB to 1.4 MB, verified by `rr_fileinfo`.
  1.4 MB took 31 to 37 s (about 40 kB/s); 300 kB took 6 to 28 s.
* `Connection: keep-alive` is required to get the `200 OK` back. With
  `Connection: close` the Duet writes the file but sends no status line.
* Why it failed before: each burst of lost frames stalls the send for
  0.3, 0.6, 1.2, 2.4 s (Windows TCP retransmission back-off). RRF closes
  an HTTP session idle for 8 s, so a few consecutive losses pushed the
  back-off past 8 s and the Duet dropped the connection with nothing
  written. That was the "fails at 8 s" of the 27th. With the better
  signal the stalls stay short; a retry covers the rare long one. Duet
  Web Control sends the same request with up to two automatic retries,
  which is why it "just works".
* Pacing the sends (small `SO_SNDBUF`, small writes) did not help
  consistently.

Consequences:

* USB `M28` stays the primary upload: lossless, about 150 kB/s.
* The cable-less fallback is `rr_upload` with keep-alive and up to three
  attempts, verified by `rr_fileinfo` size (`Duet.upload_wifi`). Through
  Edwin on the evening of the 28th: 2.26 MB in 16 s on the first attempt.
* The earlier note in `duet.py` blaming the command sandbox was wrong, and
  `paced_upload_probe.py` was the dead end that showed it.

## Design

| Job | Transport | Why |
|---|---|---|
| status, start, progress | Wi-Fi, small `rr_model`/`rr_gcode` requests, retried with backoff, session re-established on 401/403/503/resets | every request fits in one small frame |
| file upload | USB `M28` stream when the Duet is on a COM port | 1.4 MB in 11 s, lossless |
| file upload, no cable | Wi-Fi `rr_upload`, keep-alive, up to 3 attempts, `rr_fileinfo` size check | 2.26 MB in 16 s; a dropped connection is retried after the partial file is deleted |
| verification | `rr_fileinfo` size, or full `rr_download` compare (`--verify full`) | downloads are reliable |
| printing | from the SD card (`M32`) | the link can vanish mid-print without effect |

`M28` strips comments, trailing whitespace and blank lines, so a file
uploaded over USB is the stripped text on the card and verification
compares against that. `rr_upload` writes the bytes as sent, comments
included. The stripped file has no `;LAYER_CHANGE` markers and no
settings trailer, so for a USB-uploaded job RRF reports no layer number,
no layer count and no print-time estimate (`job.layer` stays null). The
watch loop and the timelapse therefore go by Z: the watch line shows
`z 18.35` instead of `layer 92/149`, and `timelapse.py` takes a frame at
every change of Z while printing. `--port none` forces the Wi-Fi upload
when you want the Duet's own layer and time figures.

### The chunked Wi-Fi path, removed

`upload_wifi_chunked` (200 B `M28` writes into 8 kB chunk files joined by
`M98` calls, about 1.4 kB/s) was written on the 27th when nothing bigger
than a few hundred bytes got through. It was removed on the 28th with its
tests; its pause caveat (RRF 3.5 defers a pause until the running macro
ends) went with it.

### Commands never sent over USB

`M38`, `M587`, `M122` and `M30` over USB preceded the firmware crash of
2026-10-27 22:05 and are avoided on that transport. Files are deleted with
`rr_delete`. `M122` over Wi-Fi (`rr_gcode` then `rr_reply`) is fine and
was used on 2026-10-28. On opening the USB port Edwin sends `M29` first,
to close a file a crashed run may have left open, then `M115` to confirm
it is a Duet.

## Status

Done on the printer:

* 2026-10-27, scratch files, deleted afterwards: USB uploads of 24 kB,
  85 kB (real PrusaSlicer output, full verify) and 215 kB, all
  byte-exact, 1.3 s for 85 kB.
* 2026-10-27 23:27, the ball joint (`runs/ball_joint_20261027_232658.log`):
  `ball_joint.3mf` sliced through `--slice`, checked, uploaded over USB
  (1.41 MB in 11 s, size verified), started with `M32`, and watched for
  2 min 45 s until the printer was switched off while the bed was still
  heating. No layer was printed. Edwin reported that run as "completed";
  the handling of a printer that goes `off` or `halted` was corrected the
  same night and has only run against the fake Duet since.
* 2026-10-28: WiFi module flashed to 2.2.0; `rr_upload` measured by the
  probes, then through Edwin that evening: `ball_joint.T2.gcode` (2.26 MB,
  sliced from the STL) uploaded with `--port none` in 16 s on the first
  attempt, size verified, and deleted again.

Done without a printer on 2026-10-28 (phases 0 to 4 of the plan): the
ball joint sliced from a bare STL with the presets; `TinyAllenKeyHolder.scad`
previewed, rendered and fitted; the ball joint rendered in Blender in
33 s; a described part (`cube30_m3`, a 30 mm cube with an M3 hole) taken
from OpenSCAD source to checked G-code, two Blender pictures and an
estimate (1:46, 13.3 g) in its own folder.

* 2026-10-29 09:10 to 10:59, the first watched print (phase 5 of the
  plan, `cube30_m3/agent_20261029_090953.log`): `cube30_m3` uploaded over
  USB (876 kB in 4.8 s, size verified), started with `M32`, printed to
  completion in 1:48:39 and reported "completed; printer idle" (4148 link
  requests, no retries, no reconnects); timelapse MP4 of 138 frames in
  `cube30_m3/timelapse/`. The print showed that a USB-uploaded file gives
  the Duet no layer count (see "M28 strips comments" above), so the watch
  line and the timelapse now go by Z, and that a missed camera frame needs
  one retry.

* 2026-10-29 16:25 to 2026-10-30 07:53, `recorder_set` (179 mm tall, 15.5
  hours, 901 timelapse frames with the ring flashed per shot) printed
  perfectly and was then crushed by its own end-of-print park. `T-1` runs
  `sys/tfree2.g`, whose first move is `G53 G1 Z{global.SafeZ}` with SafeZ
  65 mm: at the end of a 179 mm print that raised the bed 120 mm into the
  nozzle, still at 220 C because the heater-off came after the park, and
  the fast X/Y moves to the dock knocked the part off. The Duet reported
  idle and nothing in the log looked wrong; the frame after the last layer
  shows the bed risen out of view. Every tool change (`tfree0-3.g`,
  `tpre0-3.g`) and `homeall.g`/`homez.g` make the same move. Fix in two
  places: the eight tool-change macros wrap the move in
  `if move.axes[2].machinePosition < global.SafeZ` (originals and guarded
  copies with a diff in `3DParts/duet_sys/`), and Edwin's end G-code turns
  the heater off before `T-1` while `agent.park_check` reads SafeZ and
  `tfree<tool>.g` from the Duet and refuses a part taller than SafeZ minus
  5 mm unless the guard is there. Homing with a tall part left on the bed
  is still unguarded: Edwin's bed check runs after homing, so clear the
  bed before a power cycle.

Offline: `python -m pytest tests` (42 tests, about 50 s) covers resets,
expired sessions, Wi-Fi uploads with dropped connections, USB, the watch
loop, the job folders, fit and estimate, the timelapse's layer detection,
the park check, and, when the tools are installed, OpenSCAD rendering,
slicing with the presets and a Blender render.

Not yet done: the `/part` skill driven by a fresh session typing `/part`;
a brush wipe before the print (the Duet has `macros/wipe.g`, nothing calls
it). Lighting: the timelapse and the bed check use `cam1_print_view.json`,
which since 2026-10-29 flashes the Jetson's read ring (its power switch is
separate) for each shot, about two seconds a layer with the ring aimed at
the print, and exposes 100 ms at gain 2 for a dark room; under the flood
light use 15 ms, gain 1. `python jetson_cam.py light on` keeps it lit.
Found on 2026-10-29 evening: the ring must sit on the frame, not on the
bed (the bed drops as the print grows) and just outside the camera's
view, or it flares; lit from the front left it gives the bed about 40
of 255 and the parts about 70. The Jetson acknowledges ring commands
whether or not the ring has power, so check by eye once. A timelapse
restarted into the same folder continues the frame numbering and makes
one MP4 of the whole set; stopping one by killing it leaves a dead
puller on the Jetson that steals alternate frames for twenty minutes. The `printagent` env needs
`pyzmq`, `opencv-python-headless` and `numpy` for the timelapse.
