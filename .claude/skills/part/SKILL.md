---
name: part
description: Make a 3D-printed part from a description on the E3D ToolChanger with Edwin. Writes OpenSCAD into the parts folder, previews it, slices it, renders the build in Blender, estimates time and filament, and prints only on an explicit yes. Use for "/part ...", "design a part", "print me a ...", or any request to model or print something.
---

# /part: from a description to a printed part

Edwin's code is `D:\Source\Edwin` (this repo). Parts live in the parts
folder, `EDWIN_PARTS_DIR` (default `D:\OneDrive\work\Org\Forman\3DParts`),
one folder per job, `<parts>/<name>/`. Nothing a job produces is written
anywhere else, and nothing in the parts folder goes into git.

## Before anything

Run every command from `D:\Source\Edwin` with the `printagent` conda env.
From bash, prepend its folders to PATH for each command (a bare `python`
exits 127 with no output otherwise):

```bash
cd /d/Source/Edwin && _ENV=/c/Users/User/.conda/envs/printagent && export PATH="$_ENV:$_ENV/Library/bin:$_ENV/Scripts:$PATH" && python part.py preview NAME
```

A bare `NAME` means `<parts>/NAME/NAME.scad` (or `.stl`, `.T2.gcode`,
whichever the command wants); a path works too.

## The loop

1. **Name the part** in snake_case, short, specific (`cube30_m3`,
   `psu_jack_bracket`). Create `<parts>/<name>/`.
2. **Write `<parts>/<name>/<name>.scad`.** Conventions of the parts folder:
   - a comment at the top saying what it is and which face goes on the bed;
   - parameters at the top, units in the names (`cube_mm`, `wall_mm`,
     `hole_d_mm`), nothing magic inside the geometry;
   - `include <../StandardSizes.scad>` for hole and nut sizes and
     tolerances (`M3HoleDiameter`, `M3NutDiameter`, `looseTol`, `firmTol`,
     ...); read that file before inventing a number;
   - `$fn = 64` for ordinary holes, more only for large round parts;
   - design for the printer: the part sits on z = 0 on its largest flat
     face, holes vertical where you can, overhangs under 45 degrees, walls
     at least 1.2 mm (three 0.45 mm perimeters), fillets only where they
     matter;
   - `difference()` cutters must poke through both faces (`-1` below, `+2`
     long), or the mesh is not manifold and `render` refuses it.
3. **Preview**: `python part.py preview NAME` writes `NAME.png` (from
   above) and `NAME_below.png` (from below, for overhangs) in the job
   folder. Look at both with the Read tool and **show them to the user**.
   Ask whether it is what they meant. Revise and preview again until they
   say so. Do not go further on your own.
4. **Render and fit**: `python part.py render NAME` writes the STL and
   fails on an OpenSCAD error or a non-manifold mesh;
   `python part.py fit NAME` checks it against the 300 x 200 x 300 mm bed
   and warns about thin footprints. Fix problems in the scad, not by hand.
5. **Slice**: `python part.py slice NAME` renders, slices with the E3D
   presets in `profiles/` for tool 2 (PLA, 0.2 mm layers, supports on) and
   writes `NAME.T2.gcode`, checked for the ToolChanger, with a one-line
   estimate. `--center X,Y` places it on the bed; the default is the bed
   centre.
6. **Render the build**: `python part.py render-blender NAME` writes
   `NAME.blender.png` (the toolpath as it would print, with layer lines
   and the skirt) and `NAME.blender_layer1.png` (the first layer, from
   above). About half a minute.
7. **Estimate**: `python part.py estimate NAME` prints time, grams, layers
   and extents (skirt and brim included) and writes `NAME.estimate.json`.
8. **Show and ask**: show the OpenSCAD preview and the Blender build side
   by side, the first-layer picture, and the estimate line. Ask: "Print it?
   About H:MM and N g." Then stop and wait.
9. **Print** only after an explicit yes in this conversation, with someone
   in the room and the printer on, idle and homed:

   ```bash
   python agent.py 192.168.1.14 print NAME --tool 2 --yes --timelapse
   ```

   `--timelapse` with no argument records Jetson camera 1 into
   `<parts>/NAME/timelapse/` (Jetson on, SymbolicTM's camera server
   running, no SymbolicTM session connected; camera 0 belongs to
   SymbolicTM). Edwin checks the printer is idle and homed and has T2,
   uploads over USB when the cable is in (else Wi-Fi), starts from the SD
   card, watches to the end and logs to `<parts>/NAME/agent_<stamp>.log`.

## Rules

- Never run `agent.py ... --yes` without having shown both pictures and the
  estimate in this conversation and received a yes for this exact file.
- Never skip `--timelapse` on a print.
- List every command sent to the printer or the Jetson in your report, and
  flag a reboot, a heater fault or anything odd at once.
- Tool 0 is the camera, tool 2 prints PLA at 220 C on a 60 C bed with a
  0.4 mm nozzle; the bed is 300 x 200 mm, 300 mm high. Nothing is sent to
  the printer by any `part.py` command except `fit --host`.
- Do not edit `StandardSizes.scad` for one part; add a parameter to the
  part instead.
- Do not commit; the owner commits. Parts are never committed anywhere.
