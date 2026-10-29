# Plan: from a description to a printed part

(Edwin's plan. Edwin's code is `D:/Source/Edwin`; parts live in the parts
folder `D:/OneDrive/work/Org/Forman/3DParts`, see README.md. Paths below
that say `3DParts/<name>/` mean the parts folder; `printAgent/` paths now
mean this repo's root.)

Written 2026-10-28 by the SymbolicTM session; revised the same evening by
the printAgent session against the agent's real state, with the Wi-Fi
upload phase and the Blender render phase added, and then **phases 0 to 4
built and run** that night on the owner's "go as far as the first watched
print". Each phase below ends with its gate and what the gate showed.
Phase 5, the watched print, is the next review gate. The code was moved to
`D:\Source\Edwin` and committed on 2026-10-29; the parts folder stays out of git.

## The job folder rule (owner, 2026-10-28)

Everything a job produces lives in `3DParts/<name>/`: the `.scad` a
session wrote, `<name>.stl`, the OpenSCAD previews `<name>.png` and
`<name>_below.png`, `<name>.T<tool>.gcode` with the patched printer
profile (or 3MF) it was sliced with, `<name>.blender.png` and
`<name>.blender_layer1.png`, `<name>.estimate.json`, `blender/` (worker
logs), `timelapse/` and `agent_<stamp>.log`. `part.job_dir` maps any file
to its folder by stem (`ball_joint.T2.gcode`, `../ball_joint.3mf` and
`a.T2.printer.ini` all mean `3DParts/ball_joint/`); a source already in
its own folder stays there; `--job-dir` overrides. The old top-level
`ball_joint.*` and `.scad` files are left where they were.

## Where the agent is (checked 2026-10-28)

What `agent.py` has done on the printer:

- The ball joint, 2026-10-27 23:27 (`runs/ball_joint_20261027_232658.log`):
  `ball_joint.3mf` sliced through `--slice`, checked, uploaded over USB
  (1.41 MB in 11 s, size verified), started with `M32`, and watched for
  2 min 45 s until the printer was switched off while the bed was still
  heating. No layer was printed. That run was reported as "completed"; the
  `off`/`halted` handling was corrected at 23:31 and has only run against
  the fake Duet since.
- 2026-10-28 23:26 (phase 0b gate): `ball_joint.T2.gcode` (2.26 MB)
  uploaded over Wi-Fi with `rr_upload` in 15.9 s on the first attempt,
  size verified, then deleted. Commands sent: `rr_connect`, `rr_model`,
  `rr_upload`, `rr_fileinfo`, `rr_delete`, `rr_disconnect`. No motion, no
  anomalies.
- Never run alongside a print: `timelapse.py`.
- Offline: 25 tests pass in 28 s in the `printagent` env.

The link: fixed on 2026-10-28 (WiFi module 1.23 to 2.2.0, antenna
untaped; 1% loss at every frame size). `README.md` has the numbers.

Facts the plan relies on, verified:

- OpenSCAD 2021.01, `C:\Program Files\OpenSCAD\openscad.com`. Its
  `--render` (CGAL) took two minutes on `TinyAllenKeyHolder.scad`
  (`$fn = 1000`); `--preview` (OpenCSG) draws the same picture in under a
  second, so `part.py preview` uses that.
- PrusaSlicer 2.9.2 console with `--info` and `--export-stl`. The console
  crashes (access violation) when a loaded preset contains
  `bed_temperature_extruder` or `wipe_tower_extruder`; the copies in
  `profiles/` have them removed (`prusa.CLI_CRASH_KEYS`, already known
  from the 3MF path).
- Blender 4.3.2 at `C:\Program Files\Blender Foundation\Blender 4.3\blender.exe`;
  BlenderReality at `D:\Source\BlenderReality`, its client stdlib only.
- Axis limits read from the Duet on the 27th: X -35 to 328.5, Y -49 to
  243, Z 0 to 300. The slicer's bed is 0..300 x 0..200 (the printer
  preset), and that is what `fit` and `toolchanger.check` test against;
  tool 2's reach with its offset is not known more precisely.
- The E3D presets: absolute E, no arcs, 0.45 mm extrusion width, 0.2 mm
  layers (0.35 first), 220 C / 60 C, supports on, one skirt, 5 mm brim.
  `filament_density` was 0, so grams were missing; the copy says 1.24.
- Tool 0 is the camera; tool 2 is the default print tool. The Jetson
  camera server is at 192.168.56.102 (zmq 5556 / 5555), camera 1 only,
  never while a SymbolicTM session is connected.
- `3DParts` is not a git repository, nor is `printAgent`. "Commit" first
  needs `git init` somewhere: `printAgent/` alone, or `3DParts/` with the
  OpenSCAD sources and the job folders (`.gcode` and `.3mf` files are
  1.5 to 2.3 MB each and want a `.gitignore`). Your call, at the first
  commit.

## The shape of it

```
description ──(Claude writes OpenSCAD)──> <name>/<name>.scad
<name>.scad ──(openscad.com)──> <name>.stl + <name>.png, <name>_below.png     part.py render / preview
<name>.stl ──(PrusaSlicer, profiles/)──> <name>.T2.gcode                     part.py slice (or agent --slice)
<name>.T2.gcode ──(BlenderReality, headless)──> <name>.blender.png, _layer1   part.py render-blender
<name>.T2.gcode ──(part.py estimate)──> time, grams, layers, extents
<name>.T2.gcode ──(agent.py print --yes --timelapse)──> the print, watched, timelapsed
```

Three decisions, taken and now built:

1. **Claude writes the OpenSCAD inside a Claude Code session**, not a
   script calling the API. The step that matters is you looking at the
   pictures before plastic moves. An unattended API loop is the last
   phase, optional.
2. **OpenSCAD, not a mesh generator.** The scad file is the deliverable;
   the STL is derived.
3. **The Blender build picture is its own phase** (phase 2), so each gate
   stays small enough to review in one sitting.

## Phase 0: slice a bare STL with the E3D settings (small) — DONE

- `profiles/printer.ini`, `print.ini`, `filament.ini` copied from the
  PrusaSlicer presets of 2026-04-13, with the crash keys removed from the
  print preset and `filament_density = 1.24`; `profiles/README.md` says
  which is which.
- `agent.slice_model`: a `.scad` is rendered first; an STL is sliced with
  the three presets unless `--ini` names others, the printer preset
  patched per job with `prusa.toolchanger_gcode(tool)` into
  `<job>/<name>.T<tool>.printer.ini`; a `.3mf` keeps its own settings,
  patched as before. Output `<job>/<name>.T<tool>.gcode`. `part.py slice`
  does the same without a printer.
- Gate, run: `ball_joint.3mf` exported to `ball_joint/ball_joint.stl`
  (65.6 x 130.7 x 25.0 mm, manifold, 9.30 cm3) and sliced with the presets
  in 2.3 s. Same Y and Z extents and tool as the 3MF run of the 27th
  (Y 23.5..176.5, Z 0.35..24.95, T2); X differs (107..192 vs 195..280)
  because PrusaSlicer centres a bare STL on the bed; 140 layers instead of
  83 and 1:51 instead of 0:59 because the presets say 0.2 mm layers, 20%
  infill and supports where the 3MF carried 0.3 mm, 40% and none; 12.15 g
  now reported.

## Phase 0b: Wi-Fi upload by `rr_upload`, chunked path removed (small) — DONE

- `Duet.upload_wifi`: one `POST /rr_upload?name=&time=&crc32=` over
  `http.client` with `X-Session-Key`, `Connection: keep-alive` and an
  explicit `Content-Length`, the body streamed in 64 kB pieces so progress
  is reported; timeout `size / 10 kB/s + 30 s`; up to three attempts, the
  session re-established and a partial file deleted before a retry;
  success is `err 0` and the `rr_fileinfo` size (or a full download
  compare). The bytes go as they are, comments included.
- `upload_wifi_chunked`, `_m28_write`, `chunk_bytes`, the parts handling
  in `delete_upload` and their tests are gone. The fake Duet has `do_POST`
  with a mid-body drop; tests cover success with the headers, a retry
  after a drop, giving up when every attempt drops, a size mismatch, and
  the no-cable fallback.
- Gate, run on the printer (idle, no motion): see "Where the agent is",
  2.26 MB in 15.9 s, first attempt.

## Phase 1: `part.py`, the OpenSCAD side (medium) — DONE

- `part.py render NAME.scad [-D var=value ...]`: `openscad.com -o`, fails
  on an OpenSCAD error, an empty result, or `manifold != yes` / no volume
  from PrusaSlicer `--info`.
- `part.py preview NAME.scad`: above (55 deg) and below (125 deg), OpenCSG.
- `part.py fit NAME.stl [--host HOST]`: extents against the 300 x 200 x 300
  bed, manifoldness, a footprint under 10 mm, tall-and-thin, several
  bodies; `--host` adds the Duet's axis limits to the report.
- `part.py estimate NAME.T2.gcode`: time, mm, cm3, g, layers and the
  `toolchanger.check` extents, written to `<name>.estimate.json`, with a
  one-line summary ("1:46, 13.3 g, 149 layers, 52 x 52 x 30 mm on the bed
  (skirt and brim included) at X 124..176 Y 74..126").
- `agent.py print NAME.scad --slice` renders, slices and prints in one
  command; `--timelapse` with no argument records into `<job>/timelapse/`;
  the agent logs to `<job>/agent_<stamp>.log`.
- Gate, run: `part.py preview ../TinyAllenKeyHolder.scad` gave both
  pictures in `3DParts/TinyAllenKeyHolder/`; `render` gave a manifold
  22 x 22 x 30 mm STL; `fit` passed. Tests pass offline.

## Phase 2: the Blender build picture (small, measured) — DONE

- `part.py render-blender NAME.T2.gcode [--every N] [--size WxH] [--samples S]`
  writes `<name>.blender.png` (wide snapshot of the whole build) and
  `<name>.blender_layer1.png` (the first layer from 1.6 x the extent above).
  It imports BlenderReality's `client.py` by path (`BLENDER_REALITY_DIR`,
  default `D:\Source\BlenderReality`), parses with its `protocol/gcode.py`,
  recentres the points itself (the worker's `snapshot` frames through a
  stale transform when `deposit(origin=...)` is used; that one-line fix,
  `view_layer.update()` before framing, belongs in the BlenderReality repo
  as its own commit), `deposit(paths=..., bead_radius=0.25)`, 8 samples,
  no denoising, Blender 4.3 unless `BLENDER_REALITY_EXE` says otherwise.
  Worker logs stay in `<job>/blender/`; the job JSONs (every point) and
  the worker's output copies are removed.
- Measured: the ball joint (1864 beads, 83 layers, 85 x 153 mm) 25.3 s for
  the build and 7.1 s for the first layer, 32.9 s in all; the cube
  (1508 beads, 149 layers) 18.5 + 5.0 s. Blender peaks at about 1 GB. No
  subsampling is needed; `--every N` is there for a much bigger part. The
  STL-import fallback the handover offered is not needed.
- Gate, run: `part.py render-blender ../ball_joint.T2.gcode` gave both
  pictures in `3DParts/ball_joint/`; the integration test renders a
  three-layer square in about 10 s and skips without Blender.

## Phase 3: the describing step (small, mostly words) — DONE

- `3DParts/.claude/skills/part/SKILL.md`: the job folder rule, the scad
  conventions (parameters with units, `include <../StandardSizes.scad>`,
  `$fn`, printability, cutters through both faces), the loop (preview,
  show, ask, revise; render, fit, slice, render-blender, estimate; show
  both pictures and the estimate; print only on an explicit yes, always
  with `--timelapse`), and the rules (report every command sent, never
  `--yes` unasked, do not commit).
- `3DParts/CLAUDE.md` points at the skill and printAgent.
- Gate: "/part a 30 mm cube with an M3 hole through it" is what phase 4
  ran by hand. The skill has not yet been exercised by a fresh session
  typing `/part`; that is the first thing to try next.

## Phase 4: dry run end to end (small) — DONE

`cube30_m3/cube30_m3.scad` written to the conventions (parameters with
units, `M3HoleDiameter` from `StandardSizes.scad`, `$fn = 64`, flat on
the bed, cutter through both faces), then preview, render (30 x 30 x 30 mm,
26.77 cm3, manifold), fit (passes), slice (149 layers), render-blender
(23.8 s) and estimate: 1:46, 13.3 g, 52 x 52 mm on the bed with skirt and
brim. Nine files in `3DParts/cube30_m3/`, nothing uploaded. The pictures
show the hole, the brim and the skirt as they would print.

## Phase 5: the first watched print (printer time) — NEXT

The agent's own untested half: `M32` start, the watch loop to completion,
the corrected `off`/`halted` outcome reporting, and the Jetson timelapse
to an MP4 (Jetson on, SymbolicTM's camera server running, no SymbolicTM
session connected). The cube (1:46, 13.3 g) or the ball joint (1:51 with
the presets). Someone is in the room for this one, and the printer stays
on.

```bash
python agent.py 192.168.1.14 print ../cube30_m3/cube30_m3.T2.gcode --tool 2 --yes --timelapse
```

Gate: a finished part on the bed and an MP4 in `3DParts/<name>/timelapse/`.

## Phase 6, optional: unattended

`describe.py "..."` calling the Claude API with the same loop, sending
you the pictures and the estimate and waiting for a yes before printing.
Only worth building if phases 3 to 5 show you want parts made without a
session open.

## Costs and risks

- Phases 0 to 4 needed no printer except phase 0b's one upload (file
  write only). Phase 5 needs you present.
- OpenSCAD can produce non-manifold output from a careless `difference()`;
  `render` refuses it before the slicer guesses.
- The Blender picture is a picture of the toolpath, not a simulation: it
  will not show a part sagging or an overhang failing, only that the
  overhang is there. The OpenSCAD view from below is the other half.
- The extents `estimate` reports include the skirt and brim, which is the
  footprint that matters for placing two parts on the bed; the part itself
  is smaller.
- Every command sent to the printer or the Jetson is listed in the phase
  report, and reboots or anomalies are flagged at once.
