"""PrusaSlicer profiles and projects for the E3D ToolChanger.

PrusaSlicer configs (``.ini`` presets, and ``Metadata/Slic3r_PE.config`` inside
a ``.3mf``) are ``key = value`` lines; the 3MF copy prefixes each with ``; ``.
G-code values keep their newlines escaped as ``\\n``.
"""
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

PRUSA = r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe"
PROJECT_CONFIG = "Metadata/Slic3r_PE.config"

# Either one segfaults prusa-slicer-console 2.9 on load (the GUI is fine).
CLI_CRASH_KEYS = ("bed_temperature_extruder", "wipe_tower_extruder")


def toolchanger_gcode(tool):
    """Start/end G-code that drops off the camera, prints with ``tool``, parks it."""
    start = "\n".join([
        "T-1 ; drop off whatever is on the carriage (camera T0)",
        "M190 S[first_layer_bed_temperature] ; bed",
        f"G10 P{tool} S[first_layer_temperature] R[first_layer_temperature] ; preheat T{tool} in its dock",
        "G28 ; home all axes, no tool attached",
        "G29 S1 ; load the stored height map",
        f"T{tool} ; pick up the print tool",
        f"M116 P{tool} ; wait for T{tool}",
        "G1 Z5 F5000 ; lift nozzle",
    ])
    end = "\n".join([
        "G91",
        "G1 Z5 F600 ; lift clear of the part",
        "G90",
        f"G10 P{tool} S0 R0 ; T{tool} heater off before the park",
        "T-1 ; park the print tool (tfree moves the bed to SafeZ only if it is closer: see agent.park_check)",
        "M140 S0 ; bed off",
        "M84 ; disable motors",
    ])
    return {
        "start_gcode": start.replace("\n", "\\n") + "\\n",
        "end_gcode": end.replace("\n", "\\n") + "\\n",
        "autoemit_temperature_commands": "0",
    }


def patch(text, overrides=None, drop=(), prefix=""):
    """Set/replace ``overrides`` and remove ``drop`` keys in a config text."""
    overrides = dict(overrides or {})
    out = []
    for line in text.splitlines():
        m = re.match(re.escape(prefix) + r"(\w+) = ", line)
        key = m.group(1) if m else None
        if key in drop:
            continue
        if key in overrides:
            line = f"{prefix}{key} = {overrides.pop(key)}"
        out.append(line)
    out += [f"{prefix}{k} = {v}" for k, v in overrides.items()]
    return "\n".join(out) + "\n"


def patch_ini(src, dst, overrides=None, drop=()):
    with open(src, encoding="utf-8") as f:
        text = patch(f.read(), overrides, drop)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def patch_project(src, dst, overrides=None, drop=()):
    """Copy a .3mf, rewriting its embedded slicer config."""
    tmp = dst + ".tmp"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == PROJECT_CONFIG:
                data = patch(data.decode("utf-8"), overrides, drop, prefix="; ").encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, dst)


def project_config(path):
    with zipfile.ZipFile(path) as z:
        text = z.read(PROJECT_CONFIG).decode("utf-8")
    return dict(re.findall(r"^; (\w+) = (.*)$", text, re.M))


def slice_model(model, gcode_out, loads=(), overrides=None):
    """Slice an STL or 3MF with prusa-slicer-console; returns the G-code path."""
    cmd = [PRUSA]
    for ini in loads:
        cmd += ["--load", ini]
    for k, v in (overrides or {}).items():
        cmd += [f"--{k.replace('_', '-')}", str(v)]
    cmd += ["--export-gcode", "-o", gcode_out, model]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(gcode_out):
        raise RuntimeError(f"PrusaSlicer failed ({r.returncode}): {r.stdout[-500:]} {r.stderr[-500:]}")
    return gcode_out
