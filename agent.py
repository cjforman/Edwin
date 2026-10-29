"""Edwin, the print agent for the E3D ToolChanger: (render) -> (slice) ->
prepare -> check -> upload -> verify -> start -> watch, with everything a job
produces kept in its own folder in the parts folder, <parts>/<name>/ (see
part.py; EDWIN_PARTS_DIR, default D:/OneDrive/work/Org/Forman/3DParts).
A target may be a path or a bare part name: ``print cube30_m3 --yes`` prints
<parts>/cube30_m3/cube30_m3.T2.gcode.

  python agent.py HOST status
  python agent.py HOST link                              measure the Wi-Fi link with small requests
  python agent.py HOST upload FILE.gcode [--port auto|COMn|none] [--verify size|full]
  python agent.py HOST print FILE.gcode --tool 2 --yes [--port ...] [--timelapse [DIR]]
  python agent.py HOST print MODEL.stl|.3mf|.scad --slice [--center X,Y] [--ini PROFILE.ini ...] --tool 2 --yes
  python agent.py HOST watch [NAME] [--timelapse [DIR]]  follow a print already running
  python agent.py HOST delete NAME.gcode                 remove a job file from the printer

Slicing: a .scad is rendered to STL first (part.py); an STL is sliced with
the E3D presets in profiles/ (printer, print, filament; --ini replaces them)
with the ToolChanger start and end G-code for --tool patched into the printer
profile; a .3mf keeps its embedded settings, patched the same way.

Where things run:
  * uploads go over USB when the Duet is on a COM port (fast, lossless), else
    over Wi-Fi with one rr_upload POST, retried;
  * everything else (status, start, progress) is small requests over Wi-Fi,
    retried and reconnected as needed; the print itself runs from the SD card,
    so losing the link never stops it.
Every command sent to the printer is printed as it goes, and logged to
<job>/agent_<stamp>.log.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time

import duet
import part
import prusa
import toolchanger

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES = os.path.join(HERE, "profiles")
ACTIVE = ("processing", "paused", "pausing", "resuming", "starting", "cancelling", "simulating", "changingTool")

_log_file = None


def log(msg):
    line = time.strftime("%H:%M:%S") + " " + msg
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def open_log(job):
    global _log_file
    path = os.path.join(job, time.strftime("agent_%Y%m%d_%H%M%S.log"))
    _log_file = open(path, "a", encoding="utf-8")
    return path


def fmt_secs(s):
    if s is None:
        return "?"
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


# ---- offline steps -----------------------------------------------------------

def default_profiles(tool, job, name):
    """The three E3D presets, the printer one patched with the ToolChanger
    start/end G-code for ``tool`` (written next to the job's other files)."""
    printer = os.path.join(job, f"{name}.T{tool}.printer.ini")
    prusa.patch_ini(os.path.join(PROFILES, "printer.ini"), printer, overrides=prusa.toolchanger_gcode(tool))
    return [printer, os.path.join(PROFILES, "print.ini"), os.path.join(PROFILES, "filament.ini")]


def slice_model(model, inis, tool, center=None, overrides=None, job=None):
    """Render (.scad) and slice (.stl/.3mf) for ``tool`` into the job folder.
    A 3MF project is first copied to <name>.T<tool>.3mf with the ToolChanger
    start/end G-code written into its embedded config (and the keys that
    crash the console slicer removed); an STL is sliced with the profiles/
    presets unless ``inis`` names others.  ``center`` = "X,Y" places the part
    there on the bed."""
    job = part.job_dir(model, job)
    name = part.stem(model)
    if model.lower().endswith(".scad"):
        stl, d, warnings = part.render(model, job=job)
        for w in warnings:
            log("openscad: " + w)
        log(f"rendered {model} -> {stl} ({d['size_x']:.1f} x {d['size_y']:.1f} x {d['size_z']:.1f} mm, "
            f"{d['volume'] / 1000:.1f} cm3, manifold)")
        model = stl
    loads = list(inis or [])
    if model.lower().endswith(".3mf"):
        patched = os.path.join(job, f"{name}.T{tool}.3mf")
        prusa.patch_project(model, patched, overrides=prusa.toolchanger_gcode(tool), drop=prusa.CLI_CRASH_KEYS)
        log(f"patched {model} -> {patched} (T{tool} start/end G-code)")
        model = patched
    elif not loads:
        loads = default_profiles(tool, job, name)
        log("profiles: " + ", ".join(loads))
    out = os.path.join(job, f"{name}.T{tool}.gcode")
    opts = dict(overrides or {})
    if center:
        opts["center"] = center
    log(f"slicing {model} -> {out}" + (f" centred at {center}" if center else ""))
    return prusa.slice_model(model, out, loads=loads, overrides=opts)


def prepared_gcode(path, tool, job=None):
    """Return (gcode_path, summary) of a file that passes toolchanger.check for
    ``tool``, rewriting the E3D profile's single-extruder output into the job
    folder if needed."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    problems, summary = toolchanger.check(text, tool)
    if not problems:
        return path, summary
    try:
        text = toolchanger.prepare(text, tool)
    except ValueError as e:
        raise SystemExit(f"{path} fails the checks and cannot be prepared ({e}):\n  " + "\n  ".join(problems))
    problems, summary = toolchanger.check(text, tool)
    if problems:
        raise SystemExit(f"{path} still fails after prepare:\n  " + "\n  ".join(problems))
    out = os.path.join(part.job_dir(path, job), f"{part.stem(path)}.T{tool}.gcode")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    log(f"prepared {out} for T{tool}")
    return out, summary


# ---- online steps ------------------------------------------------------------

def link_test(d, n=40):
    """Loss and latency of small requests (no retries), the way the agent uses the link."""
    times, failures = [], 0
    for _ in range(n):
        t = time.time()
        try:
            d._raw("/rr_model", {"key": "state.status"})
            times.append(time.time() - t)
        except Exception:
            failures += 1
            try:
                d.connect()
            except Exception:
                pass
    r = {"requests": n, "failed": failures}
    if times:
        times.sort()
        r.update(median_ms=round(1000 * statistics.median(times)), p90_ms=round(1000 * times[int(0.9 * (len(times) - 1))]),
                 max_ms=round(1000 * times[-1]))
    return r


def watch(d, poll=3.0, until_started=30.0, report_every=60.0, sleep=time.sleep):
    """Follow the job until it ends.  Returns the final status word.

    A Wi-Fi outage is logged and waited out; the print runs from the SD card.
    """
    t0 = time.time()
    last_key, t_last, started, bad = None, 0.0, False, 0
    while True:
        try:
            p = d.job_progress()
            bad = 0
        except duet.DuetError as e:
            bad += 1
            log(f"link: {str(e)[:100]} ({bad} in a row); the print continues from the SD card")
            sleep(min(30.0, poll * bad))
            continue
        st = p["status"]
        if st in ACTIVE:
            started = True
        key = (st, p["layer"], int(100 * (p["fraction"] or 0)))
        if key != last_key or time.time() - t_last > report_every:
            log(f"{st:<11} layer {p['layer']}/{p['layers']}  {100 * (p['fraction'] or 0):5.1f}%  "
                f"elapsed {fmt_secs(p['duration'])}  left {fmt_secs(p['time_left'])}")
            last_key, t_last = key, time.time()
        if started and st not in ACTIVE:
            if st in ("off", "halted"):
                log(f"PRINTER {st.upper()}: the firmware stopped the job (heater fault, emergency stop or power); "
                    "check the printer's message log")
            return st
        if not started and st in ("idle", "off", "halted") and time.time() - t0 > until_started:
            log("no job running")
            return st
        sleep(poll)


def start_timelapse(host, out_dir):
    cmd = [sys.executable, os.path.join(HERE, "timelapse.py"), host, out_dir]
    log("timelapse: " + " ".join(cmd))
    return subprocess.Popen(cmd)


def finish_timelapse(proc, timeout=300):
    if proc is None:
        return
    try:
        proc.wait(timeout=timeout)
        log(f"timelapse finished (exit {proc.returncode})")
    except subprocess.TimeoutExpired:
        log("timelapse still running; leaving it to finish on its own")


def print_job(d, path, tool, port, verify, start, timelapse_dir=None, poll=3.0, job=None):
    path, summary = prepared_gcode(path, tool, job)
    log("checked: " + json.dumps(summary))
    st = d.status()
    problems = duet.preflight(st, tool)
    if problems:
        raise SystemExit("preflight:\n  " + "\n  ".join(problems))
    name = os.path.basename(path)

    def progress(i, n, s):
        log(f"upload {100 * i // max(n, 1):3d}% ({i}/{n}, {s:.0f} s)")

    up = d.upload(path, port=port, verify=verify, progress=progress)
    log("uploaded: " + json.dumps(up))
    if not start:
        log("not starting (no --yes); the file is on the printer as " + up["remote"])
        return 0
    problems = duet.preflight(d.status(), tool)
    if problems:
        raise SystemExit("preflight before start:\n  " + "\n  ".join(problems))
    reply = d.start(name)
    log(f"started {name}: {str(reply).strip()[:120]!r}")
    proc = start_timelapse(d.host, timelapse_dir) if timelapse_dir else None
    try:
        final = watch(d, poll=poll)
    finally:
        finish_timelapse(proc)
    p = d.job_progress()
    if final in ("off", "halted"):
        outcome = f"stopped by the firmware (printer {final}: heater fault, emergency stop or power)"
    elif p.get("last_cancelled"):
        outcome = "cancelled"
    elif p.get("last_aborted"):
        outcome = "aborted"
    else:
        outcome = "completed"
    heaters = d.model("heat.heaters", "d99vn")
    faults = [i for i, h in enumerate(heaters) if h and h.get("state") == "fault"]
    if faults:
        log(f"heater fault on heater(s) {faults}")
    log(f"job {p.get('last_file') or name} {outcome}; printer {final}; link stats {d.stats}")
    return 0 if outcome == "completed" else 1


def timelapse_dir(option, job):
    """--timelapse with no DIR means <job>/timelapse."""
    if option is None:
        return None
    if option == "auto":
        if job is None:
            job = os.path.join(HERE, "runs")
        return os.path.join(job, "timelapse")
    return option


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("action", choices=("status", "link", "upload", "print", "watch", "delete"))
    ap.add_argument("target", nargs="?", help="G-code file, model (with --slice), or job name")
    ap.add_argument("--tool", type=int, default=2)
    ap.add_argument("--password", default=os.environ.get("DUET_PASSWORD", ""))
    ap.add_argument("--port", default="auto", help="USB port for uploads: auto, COMn, or none (Wi-Fi rr_upload)")
    ap.add_argument("--verify", choices=("size", "full"), default="size",
                    help="after upload: size check (default) or full download compare")
    ap.add_argument("--slice", action="store_true", help="target is a .scad/.stl/.3mf: render and slice it first")
    ap.add_argument("--ini", action="append", default=[], help="PrusaSlicer profile(s) to --load instead of profiles/")
    ap.add_argument("--center", metavar="X,Y", help="when slicing, centre the part at this bed position (mm)")
    ap.add_argument("--job-dir", help="the job folder (default 3DParts/<name>/)")
    ap.add_argument("--timelapse", nargs="?", const="auto", metavar="DIR",
                    help="run timelapse.py (Jetson camera 1) during the print, into DIR (default <job>/timelapse)")
    ap.add_argument("--poll", type=float, default=3.0)
    ap.add_argument("--yes", action="store_true", help="really start the print")
    a = ap.parse_args(argv)

    if a.target and a.action in ("upload", "print"):
        exts = (".scad", ".stl", ".3mf") if a.slice else (f".T{a.tool}.gcode", ".gcode")
        try:
            a.target = part.resolve(a.target, exts, a.tool)
        except part.PartError as e:
            ap.error(str(e))
    if a.target and a.action == "delete" and "." not in a.target:
        a.target = f"{a.target}.T{a.tool}.gcode"
    job = None
    if a.action in ("upload", "print", "watch") and (a.target or a.job_dir):
        job = part.job_dir(a.target or "job", a.job_dir)
        log(f"job folder {job}, log {open_log(job)}")

    with duet.Duet(a.host, a.password, log=lambda m: log("  " + m)) as d:
        if a.action == "status":
            st = d.status()
            print(json.dumps(st, indent=1))
            for p in duet.preflight(st, a.tool):
                print("PREFLIGHT:", p)
            return 0
        if a.action == "link":
            print(json.dumps(link_test(d), indent=1))
            return 0
        if a.action == "watch":
            proc = start_timelapse(a.host, timelapse_dir(a.timelapse, job)) if a.timelapse else None
            try:
                final = watch(d, poll=a.poll)
            finally:
                finish_timelapse(proc)
            log(f"printer {final}; link stats {d.stats}")
            return 0
        if a.action == "delete":
            log(f"removed {d.delete_upload(a.target)} files")
            return 0
        if not a.target:
            ap.error(f"{a.action} needs a file")
        path = slice_model(a.target, a.ini, a.tool, a.center, job=job) if a.slice else a.target
        if a.action == "upload":
            path, summary = prepared_gcode(path, a.tool, job)
            log("checked: " + json.dumps(summary))
            up = d.upload(path, port=a.port, verify=a.verify,
                          progress=lambda i, n, s: log(f"upload {100 * i // max(n, 1):3d}% ({i}/{n}, {s:.0f} s)"))
            log("uploaded: " + json.dumps(up))
            return 0
        return print_job(d, path, a.tool, a.port, a.verify, a.yes, timelapse_dir(a.timelapse, job), a.poll, job)


if __name__ == "__main__":
    sys.exit(main())
