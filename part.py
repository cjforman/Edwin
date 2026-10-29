"""part.py: Edwin's OpenSCAD side, the Blender build picture, and the job
folder convention.

Parts live apart from this code, in the parts folder (EDWIN_PARTS_DIR, by
default D:/OneDrive/work/Org/Forman/3DParts, backed up by OneDrive and
never in git).  Every job has its own folder there, named after the part:

  <parts>/<name>/<name>.scad              the source (written by a session)
  <parts>/<name>/<name>.stl               part.py render
  <parts>/<name>/<name>.png, <name>_below.png   part.py preview (OpenSCAD, above and below)
  <parts>/<name>/<name>.T<tool>.gcode     part.py slice / agent.py --slice (ToolChanger-ready)
  <parts>/<name>/<name>.blender.png, <name>.blender_layer1.png   part.py render-blender
  <parts>/<name>/<name>.estimate.json     part.py estimate
  <parts>/<name>/timelapse/, agent_<stamp>.log   agent.py print

A command's target is either a path or a bare part name: ``preview cube30_m3``
means ``<parts>/cube30_m3/cube30_m3.scad``.  A path is mapped to its folder by
its stem (``ball_joint.T2.gcode`` and ``../ball_joint.3mf`` both go to
``<parts>/ball_joint/``); a source already inside its own folder stays there.
``--job-dir DIR`` overrides.

  python part.py render NAME [-D var=value ...]
  python part.py preview NAME [-D var=value ...] [--size WxH]
  python part.py slice NAME [--tool 2] [--center X,Y]
  python part.py fit NAME [--host HOST]
  python part.py estimate NAME
  python part.py render-blender NAME [--every N] [--size WxH] [--samples S]
  python part.py info NAME.stl

Run with the printagent conda env from D:/Source/Edwin.  No printer is
involved except ``fit --host``, which reads the axis limits (one small request).
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

import prusa
import toolchanger

HERE = os.path.dirname(os.path.abspath(__file__))
PARTS_DIR = os.environ.get("EDWIN_PARTS_DIR", "D:/OneDrive/work/Org/Forman/3DParts")

OPENSCAD = r"C:\Program Files\OpenSCAD\openscad.com"
BLENDER_REALITY_DIR = os.environ.get("BLENDER_REALITY_DIR", r"D:\Source\BlenderReality")
BLENDER_43 = r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"

# OpenSCAD gimbal cameras: rotation about X then Z, distance set by --viewall
CAMERA_ABOVE = "0,0,0,55,0,25,0"
CAMERA_BELOW = "0,0,0,125,0,25,0"

MIN_FOOTPRINT_MM = 10.0
BEAD_RADIUS = 0.25          # half the E3D profile's 0.45 mm extrusion width, rounded up


class PartError(RuntimeError):
    pass


# ---- job folders ---------------------------------------------------------------

def stem(path):
    """``ball_joint.T2.gcode`` -> ``ball_joint``; ``x.blender.png`` -> ``x``."""
    name = os.path.basename(path)
    while True:
        n = re.sub(r"\.(scad|stl|3mf|gcode|png|json|ini|log)$", "", name, flags=re.I)
        n = re.sub(r"(\.T\d+|\.blender(_layer1)?|\.estimate|\.printer|_below)$", "", n)
        if n == name:
            return name
        name = n


def job_dir(path, job=None, root=None):
    """The folder a job's files live in; created if needed."""
    if job:
        os.makedirs(job, exist_ok=True)
        return os.path.abspath(job)
    name = stem(path)
    parent = os.path.dirname(os.path.abspath(path))
    if os.path.basename(parent) == name:
        return parent
    d = os.path.join(root or PARTS_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d


def job_path(path, suffix, job=None, root=None):
    return os.path.join(job_dir(path, job, root), stem(path) + suffix)


def resolve(target, exts, tool=2, root=None):
    """A path as given, or a bare part name looked up as <parts>/<name>/<name><ext>
    for each extension in ``exts`` (``T2`` in an extension follows ``tool``)."""
    if os.path.exists(target):
        return target
    base = os.path.basename(target)
    if target != base or "." in base:
        raise PartError(f"no such file {target}")
    tried = []
    for ext in exts:
        cand = os.path.join(root or PARTS_DIR, target, target + ext.replace("T2", f"T{tool}"))
        if os.path.exists(cand):
            return cand
        tried.append(cand)
    raise PartError(f"no part {target!r}: looked for " + ", ".join(tried))


# what each command's target may be when given as a bare name
TARGET_EXTS = {
    "render": (".scad",), "preview": (".scad",), "slice": (".scad", ".stl", ".3mf"),
    "fit": (".stl",), "info": (".stl",), "estimate": (".T2.gcode",), "render-blender": (".T2.gcode",),
}


# ---- OpenSCAD -------------------------------------------------------------------

def _run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _defines(defines):
    out = []
    for d in defines or ():
        if "=" not in d:
            raise PartError(f"-D needs var=value, got {d!r}")
        out += ["-D", d]
    return out


def openscad(scad, out, extra=(), defines=(), timeout=600):
    if not os.path.exists(OPENSCAD):
        raise PartError(f"OpenSCAD not found at {OPENSCAD}")
    if not os.path.exists(scad):
        raise PartError(f"no such file {scad}")
    cmd = [OPENSCAD, "-o", out] + list(extra) + _defines(defines) + [scad]
    code, log = _run(cmd, timeout)
    errors = [ln for ln in log.splitlines() if ln.startswith("ERROR")]
    if code != 0 or errors or not os.path.exists(out):
        raise PartError(f"openscad failed ({code}) on {scad}:\n" + "\n".join(errors or log.splitlines()[-10:]))
    warnings = [ln for ln in log.splitlines() if ln.startswith("WARNING")]
    return warnings


def info(stl):
    """PrusaSlicer ``--info`` as a dict: sizes, extents, manifold, facets, volume."""
    code, log = _run([prusa.PRUSA, "--info", stl], timeout=300)
    d = {}
    for ln in log.splitlines():
        m = re.match(r"^(\w+)\s*=\s*(.+?)\s*$", ln)
        if m:
            v = m.group(2)
            try:
                v = float(v)
            except ValueError:
                pass
            d[m.group(1)] = v
    if code != 0 or "size_x" not in d:
        raise PartError(f"prusa-slicer --info failed ({code}) on {stl}: {log[-400:]}")
    return d


def render(scad, defines=(), job=None, root=None):
    """OpenSCAD -> STL in the job folder; fails on an error, an empty or a
    non-manifold result."""
    out = job_path(scad, ".stl", job, root)
    warnings = openscad(scad, out, [], defines)
    if os.path.getsize(out) < 100:
        raise PartError(f"{out} is empty: the model produced no geometry")
    d = info(out)
    if d.get("manifold") != "yes":
        raise PartError(f"{out} is not manifold (manifold = {d.get('manifold')!r}); fix the model before slicing")
    if not d.get("volume") or d["volume"] <= 0:
        raise PartError(f"{out} has no volume")
    return out, d, warnings


def preview(scad, defines=(), job=None, root=None, size=(800, 600)):
    """Two PNGs: from above and from below (overhangs)."""
    outs = []
    for suffix, camera in ((".png", CAMERA_ABOVE), ("_below.png", CAMERA_BELOW)):
        out = job_path(scad, suffix, job, root)
        # --preview (OpenCSG) draws in under a second; --render (CGAL) took two
        # minutes on TinyAllenKeyHolder.scad and shows the same picture
        openscad(scad, out, ["--preview", f"--imgsize={size[0]},{size[1]}", "--autocenter", "--viewall",
                             f"--camera={camera}"], defines)
        outs.append(out)
    return outs


# ---- fit and estimate -----------------------------------------------------------

def fit(stl, host=None, mesh_info=None):
    """Bounding box against the bed; returns (problems, warnings, details)."""
    d = mesh_info or info(stl)
    problems, warnings = [], []
    bed_x = toolchanger.BED_X[1] - toolchanger.BED_X[0]
    bed_y = toolchanger.BED_Y[1] - toolchanger.BED_Y[0]
    sx, sy, sz = d["size_x"], d["size_y"], d["size_z"]
    if sx > bed_x or sy > bed_y:
        problems.append(f"footprint {sx:.1f} x {sy:.1f} mm does not fit the {bed_x:g} x {bed_y:g} mm bed")
    if sz > toolchanger.MAX_Z:
        problems.append(f"height {sz:.1f} mm exceeds {toolchanger.MAX_Z:g} mm")
    if d.get("manifold") != "yes":
        problems.append(f"mesh is not manifold (manifold = {d.get('manifold')!r})")
    if min(sx, sy) < MIN_FOOTPRINT_MM:
        warnings.append(f"footprint only {min(sx, sy):.1f} mm in one direction: adhesion risk, consider a brim or lying it down")
    if sz > 2.5 * min(sx, sy):
        warnings.append(f"tall and thin ({sz:.1f} mm high on {min(sx, sy):.1f} mm): may wobble")
    if d.get("number_of_parts", 1) > 1:
        warnings.append(f"{int(d['number_of_parts'])} separate parts in one mesh")
    details = {"size": [sx, sy, sz], "volume_cm3": round(d.get("volume", 0) / 1000, 2),
               "facets": d.get("number_of_facets"), "bed": [bed_x, bed_y, toolchanger.MAX_Z]}
    if host:
        import duet
        with duet.Duet(host) as dd:
            axes = {a["letter"]: (a["min"], a["max"]) for a in dd.model("move.axes", "d99vn")}
        details["printer_axes"] = axes
    return problems, warnings, details


_TIME = re.compile(r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?")


def parse_time(text):
    m = _TIME.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def estimate(gcode, job=None, root=None, write=True):
    """Time and filament from the lines PrusaSlicer writes into the G-code."""
    with open(gcode, encoding="utf-8") as f:
        text = f.read()
    est = {"file": os.path.abspath(gcode), "bytes": len(text.encode("utf-8")),
           "layers": text.count(";LAYER_CHANGE")}
    for ln in text.splitlines():
        m = re.match(r"^; (estimated printing time \(normal mode\)|filament used \[mm\]|filament used \[cm3\]"
                     r"|filament used \[g\]|total filament used \[g\]|filament cost) = (.+)$", ln)
        if not m:
            continue
        key, val = m.groups()
        if key.startswith("estimated"):
            est["time_text"] = val.strip()
            est["time_s"] = parse_time(val)
        else:
            k = {"filament used [mm]": "filament_mm", "filament used [cm3]": "filament_cm3",
                 "filament used [g]": "filament_g", "total filament used [g]": "filament_g_total",
                 "filament cost": "filament_cost"}[key]
            try:
                est[k] = float(val.split(",")[0])
            except ValueError:
                pass
    m = re.search(r"\.T(\d+)\.gcode$", gcode)
    tool = int(m.group(1)) if m else 2
    problems, summary = toolchanger.check(text, tool)
    est["tool"] = tool
    est["extents"] = {k: summary[k] for k in ("x", "y", "z")}
    est["extruding_moves"] = summary["extruding_moves"]
    est["tool_temp"] = summary["tool_temp"]
    est["problems"] = problems
    if est.get("time_s"):
        est["time_hm"] = f"{est['time_s'] // 3600}:{est['time_s'] // 60 % 60:02d}"
    if write:
        out = job_path(gcode, ".estimate.json", job, root)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(est, f, indent=1)
        est["written"] = out
    return est


def slice_model(model, tool=2, center=None, inis=(), job=None):
    """Render (.scad) and slice (.stl/.3mf) into the job folder without a
    printer; see agent.slice_model."""
    import agent  # agent imports part; importing it here avoids the cycle at load time
    return agent.slice_model(model, list(inis), tool, center, job=job)


def describe(est):
    """One line for a human: '1:51, 12.2 g, 140 layers, 85 x 153 x 25 mm'."""
    x, y, z = est["extents"]["x"], est["extents"]["y"], est["extents"]["z"]
    parts = [est.get("time_hm") or est.get("time_text") or "time ?"]
    if est.get("filament_g_total") or est.get("filament_g"):
        parts.append(f"{est.get('filament_g_total') or est.get('filament_g'):.1f} g")
    elif est.get("filament_cm3"):
        parts.append(f"{est['filament_cm3']:.1f} cm3")
    parts.append(f"{est['layers']} layers")
    parts.append(f"{x[1] - x[0]:.0f} x {y[1] - y[0]:.0f} x {z[1]:.0f} mm on the bed (skirt and brim included) "
                 f"at X {x[0]:.0f}..{x[1]:.0f} Y {y[0]:.0f}..{y[1]:.0f}")
    return ", ".join(parts)


# ---- Blender build picture -------------------------------------------------------

def _blender_reality():
    """Import BlenderReality's bpy-free client and its G-code parser."""
    client_py = os.path.join(BLENDER_REALITY_DIR, "client.py")
    if not os.path.exists(client_py):
        raise PartError(f"BlenderReality not found at {BLENDER_REALITY_DIR} (set BLENDER_REALITY_DIR)")
    if BLENDER_REALITY_DIR not in sys.path:
        sys.path.insert(0, BLENDER_REALITY_DIR)
    spec = importlib.util.spec_from_file_location("blender_reality_client", client_py)
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    from protocol import gcode as gcode_mod
    return client, gcode_mod


def blender_exe():
    exe = os.environ.get("BLENDER_REALITY_EXE") or os.environ.get("BLENDER_EXE")
    if exe and os.path.exists(exe):
        return exe
    if os.path.exists(BLENDER_43):
        return BLENDER_43
    return None  # let the client search


def layers_of(paths):
    """Sorted layer heights (z of each polyline's first point, 3 d.p.)."""
    return sorted({round(p[0][2], 3) for p in paths})


def subsample(paths, every=1):
    """Keep the first layer and every ``every``-th layer above it."""
    if every <= 1:
        return list(paths)
    zs = layers_of(paths)
    keep = set(zs[::every]) | {zs[0]}
    return [p for p in paths if round(p[0][2], 3) in keep]


def first_layer(paths):
    z0 = layers_of(paths)[0]
    return [p for p in paths if round(p[0][2], 3) == z0]


def recentre(paths):
    """Translate XY so the toolpath is centred on the origin (the worker's
    snapshot frames deposits through a stale transform when ``origin`` is
    used, so the points are moved here instead).  Returns (paths, extent)."""
    xs = [pt[0] for p in paths for pt in p]
    ys = [pt[1] for p in paths for pt in p]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    ex, ey = max(xs) - min(xs), max(ys) - min(ys)
    return [[[x - cx, y - cy, z] for x, y, z in p] for p in paths], (ex, ey)


def render_blender(gcode, every=1, size=(800, 450), samples=8, job=None, root=None, log=print):
    """Two pictures of the toolpath: the whole build from a wide angle, and the
    first layer from above.  Returns a dict with the paths and timings."""
    client, gcode_mod = _blender_reality()
    exe = blender_exe()
    t0 = time.time()
    paths = gcode_mod.parse_gcode_file(gcode)
    if not paths:
        raise PartError(f"no extruding moves in {gcode}")
    paths, extent = recentre(paths)
    n_layers = len(layers_of(paths))
    build = subsample(paths, every)
    layer1 = first_layer(paths)
    work = os.path.join(job_dir(gcode, job, root), "blender")
    os.makedirs(work, exist_ok=True)
    log(f"blender: {len(paths)} beads, {n_layers} layers, extent {extent[0]:.0f} x {extent[1]:.0f} mm, "
        f"parsed in {time.time() - t0:.1f} s; rendering {len(build)} beads (every {every}) with {exe or 'blender on PATH'}")
    ground = max(400.0, 3 * max(extent))
    result = {"beads": len(paths), "layers": n_layers, "every": every, "extent_mm": [round(e, 1) for e in extent]}
    jobs = [
        ("build", ".blender.png", build, lambda br: br.snapshot(resolution=size)),
        ("layer1", ".blender_layer1.png", layer1,
         lambda br: br.capture(position=(0, 0, 1.6 * max(extent) + 20), look_at=(0, 0, 0), lens=50.0,
                               fstop=64.0, resolution=(min(size), min(size)))),
    ]
    for label, suffix, beads, shoot in jobs:
        br = client.BlenderReality(blender_exe=exe, work_dir=work, samples=samples, denoise=False, timeout=1800)
        br.reset_scene(ground_size=ground)
        br.deposit("part", paths=beads, bead_radius=BEAD_RADIUS, color=(0.85, 0.55, 0.2))
        shot = shoot(br)
        t = time.time()
        try:
            r = br.run(label)
        except client.BlenderRealityError as e:
            raise PartError(f"BlenderReality {label} failed: {e}") from e
        out = job_path(gcode, suffix, job, root)
        shutil.copyfile(r.image_path(shot), out)
        result[label] = out
        result[f"{label}_s"] = round(time.time() - t, 1)
        log(f"blender: {label} -> {out} in {result[f'{label}_s']} s")
        for f in glob.glob(os.path.join(work, f"{label}_*.json")):
            os.remove(f)  # the job files hold every point; only the worker log is kept
        shutil.rmtree(r.output_dir, ignore_errors=True)
    result["total_s"] = round(time.time() - t0, 1)
    return result


# ---- CLI ------------------------------------------------------------------------

def _size(text):
    w, h = text.lower().split("x")
    return int(w), int(h)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("render", "preview", "slice", "fit", "estimate", "render-blender", "info"))
    ap.add_argument("target")
    ap.add_argument("-D", action="append", default=[], metavar="var=value", help="OpenSCAD variable override")
    ap.add_argument("--job-dir", help="put outputs here instead of 3DParts/<name>/")
    ap.add_argument("--host", help="fit: also read the printer's axis limits")
    ap.add_argument("--tool", type=int, default=2, help="slice: the print tool")
    ap.add_argument("--center", metavar="X,Y", help="slice: centre the part at this bed position (mm)")
    ap.add_argument("--ini", action="append", default=[], help="slice: PrusaSlicer profiles instead of profiles/")
    ap.add_argument("--size", type=_size, help="image size WxH")
    ap.add_argument("--every", type=int, default=1, help="render-blender: keep every Nth layer")
    ap.add_argument("--samples", type=int, default=8, help="render-blender: Cycles samples")
    a = ap.parse_args(argv)
    try:
        a.target = resolve(a.target, TARGET_EXTS[a.action], a.tool)
        if a.action == "render":
            out, d, warnings = render(a.target, a.D, a.job_dir)
            for w in warnings:
                print(w)
            print(json.dumps({"stl": out, "size": [d["size_x"], d["size_y"], d["size_z"]],
                              "volume_cm3": round(d["volume"] / 1000, 2), "facets": d["number_of_facets"]}, indent=1))
        elif a.action == "preview":
            for out in preview(a.target, a.D, a.job_dir, size=a.size or (800, 600)):
                print(out)
        elif a.action == "slice":
            out = slice_model(a.target, a.tool, a.center, a.ini, a.job_dir)
            est = estimate(out, a.job_dir)
            print(out)
            print(describe(est))
            for p in est["problems"]:
                print("PROBLEM:", p)
            return 1 if est["problems"] else 0
        elif a.action == "info":
            print(json.dumps(info(a.target), indent=1))
        elif a.action == "fit":
            problems, warnings, details = fit(a.target, a.host)
            print(json.dumps(details, indent=1))
            for w in warnings:
                print("WARNING:", w)
            for p in problems:
                print("PROBLEM:", p)
            return 1 if problems else 0
        elif a.action == "estimate":
            est = estimate(a.target, a.job_dir)
            print(json.dumps(est, indent=1))
            print(describe(est))
            for p in est["problems"]:
                print("PROBLEM:", p)
            return 1 if est["problems"] else 0
        elif a.action == "render-blender":
            r = render_blender(a.target, a.every, a.size or (800, 450), a.samples, a.job_dir)
            print(json.dumps(r, indent=1))
    except PartError as e:
        print("ERROR:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
