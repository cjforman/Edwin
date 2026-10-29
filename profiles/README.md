# E3D ToolChanger slicing presets

Copies of the PrusaSlicer presets the agent loads when it slices a bare STL
(or a `.scad` it has rendered), so the agent is self-contained. A `.3mf`
project carries its own settings and does not use these.

| File | Copied from (`%APPDATA%\PrusaSlicer\`) | Preset saved | Changes in the copy |
|---|---|---|---|
| `printer.ini` | `printer\E3D (PLA T2).ini` | 2026-04-13 00:56 | none here; at slice time the agent writes `<job>/<name>.T<tool>.printer.ini` with `start_gcode`, `end_gcode` and `autoemit_temperature_commands` replaced by `prusa.toolchanger_gcode(tool)` |
| `print.ini` | `print\E3D.ini` | 2026-04-13 00:35 | `bed_temperature_extruder` and `wipe_tower_extruder` removed: either one makes `prusa-slicer-console` 2.9.2 crash on load (access violation; the GUI is fine) |
| `filament.ini` | `filament\E3D (PLA T2).ini` | 2026-04-13 00:57 | `filament_density = 1.24` (was 0) so the G-code carries grams |

Copied 2026-10-28. What they set: 0.4 mm nozzle, 300 x 200 x 300 mm bed,
0.2 mm layers (0.35 first), 3 perimeters, 20% stars infill, supports on,
one skirt, 5 mm brim, PLA at 220 C on a 60 C bed, absolute E, no arcs.

If you change a preset in PrusaSlicer, copy it again and re-apply the
changes above.
