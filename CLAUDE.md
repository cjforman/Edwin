# Edwin

Edwin (after Edwin Abbott, author of Flatland) is the print agent for the
E3D ToolChanger: a description becomes OpenSCAD, an STL, ToolChanger G-code,
two Blender pictures, an estimate, and, on an explicit yes, a watched and
timelapsed print.

- **Code and instructions live here**, `D:\Source\Edwin`, in git
  (github.com/cjforman/Edwin). **Parts live in the parts folder**,
  `EDWIN_PARTS_DIR` (default `D:\OneDrive\work\Org\Forman\3DParts`, backed
  up by OneDrive, never in git), one folder per job. Nothing a job
  produces is written here; `.gitignore` refuses `.gcode`, `.stl`, `.3mf`.
- To design or print a part, use the `/part` skill
  (`.claude/skills/part/SKILL.md`): the loop, the conventions, the rules.
- `README.md` says how it all works in practice; `documents/` holds the
  plan (what each phase delivered) and the 2026-10-28 handover.
- Run Python with the `printagent` conda env, PATH prepended inline (see
  the user CLAUDE.md). Run `python -m pytest tests -q` before a commit.
- Never start a print without an explicit yes in the conversation, never
  without `--timelapse`, and report every command sent to the printer or
  the Jetson. Commit only when asked; never commit a part, a credential,
  or the home network's name.
- BlenderReality, next door at `D:\Source\BlenderReality`, renders the
  build pictures; Edwin imports its bpy-free client and changes nothing
  there.
