"""Make PrusaSlicer G-code safe for the E3D ToolChanger (RepRapFirmware).

The E3D printer profile is a single-extruder profile with ``T2`` written into
its start G-code. PrusaSlicer still thinks it is driving extruder 0, so it
heats tool 0 (``G10 S220 P0``), waits on every heater (``M116``), and at the
end turns off "the current tool" and homes X with the tool still attached.
``prepare`` rewrites those lines for one real tool and adds the tool-changer
moves:

    T-1               drop off whatever is on the carriage (the camera tool)
    ...bed heat, G28, G29 S1 as sliced...
    G10 P<t> S R      heat the print tool (active and standby)
    T<t>              pick it up (RRF runs tfree/tpre/tpost)
    M116 P<t>         wait on that tool only
    ...print...
    lift Z 5          clear of the part
    G10 P<t> S0 R0    heater off
    T-1               park the print tool (tfree<t>.g: its move to SafeZ must
                      be guarded for parts taller than SafeZ, agent.park_check)

``check`` then proves the result is what we think it is before it goes near
the printer.
"""
import re

BED_X = (0.0, 300.0)
BED_Y = (0.0, 200.0)
MAX_Z = 300.0
MIN_EXTRUDE_TEMP = 170.0

_HEAT_P0 = re.compile(r"^G10 S(\d+(?:\.\d+)?) P0\b.*$")
_WORD = re.compile(r"([A-Z])(-?\d*\.?\d+)")


def prepare(text, tool):
    """Return the G-code rewritten to print with tool ``tool``."""
    out = []
    started = False
    temp = None
    for line in text.splitlines():
        code = line.split(";", 1)[0].strip()
        if not started and code:
            out.append("T-1 ; drop off the tool on the carriage (camera) before anything moves")
            started = True
        m = _HEAT_P0.match(code)
        if m:
            temp = m.group(1)
            out.append(f"G10 P{tool} S{temp} R{temp} ; heat print tool T{tool}")
        elif code == "M116":
            out.append(f"M116 P{tool} ; wait for T{tool}")
        elif code == "M104 S0":
            out.append("G91\nG1 Z5 F600 ; lift clear of the part\nG90")
            out.append(f"G10 P{tool} S0 R0 ; T{tool} heater off before the park")
            out.append("T-1 ; park the print tool")
        elif code == "G28 X0":
            out.append(f"; {line} -- removed: parking replaces it")
        else:
            out.append(line)
    if temp is None:
        raise ValueError("no 'G10 S<temp> P0' found: not the E3D profile's G-code?")
    return "\n".join(out) + "\n"


def _words(code):
    return {k: float(v) for k, v in _WORD.findall(code)}


def check(text, tool):
    """Return a list of problems (empty means it passed) and a summary dict."""
    problems = []
    first = None
    current = None          # tool on the carriage, None = unknown
    heated = {}             # tool -> active temperature
    x = y = z = None
    absolute = True
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    extruding_moves = 0
    last_tool_cmd = None
    print_temp = None
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        if first is None:
            first = code
        cmd = code.split()[0]
        w = _words(code[len(cmd):])
        if re.fullmatch(r"T-?\d+", cmd):
            current = int(cmd[1:])
            last_tool_cmd = current
            if current >= 0 and current != tool:
                problems.append(f"line {n}: selects T{current}, expected only T{tool}")
        elif cmd == "G10" and "P" in w and "S" in w:
            heated[int(w["P"])] = w["S"]
            if int(w["P"]) != tool and w["S"] > 0:
                problems.append(f"line {n}: heats T{int(w['P'])}, which this job never uses")
        elif cmd in ("M104", "M109") and "T" not in w:
            problems.append(f"line {n}: {cmd} without T applies to whichever tool is current")
        elif cmd in ("G2", "G3", "G02", "G03"):
            problems.append(f"line {n}: arc move, not expected from this profile")
        elif cmd == "G90":
            absolute = True
        elif cmd == "G91":
            absolute = False
        elif cmd in ("G0", "G1", "G00", "G01"):
            if absolute:
                x, y, z = w.get("X", x), w.get("Y", y), w.get("Z", z)
            if cmd in ("G1", "G01") and "E" in w and ("X" in w or "Y" in w):
                extruding_moves += 1
                print_temp = heated.get(tool) if print_temp is None else print_temp
                if current != tool:
                    problems.append(f"line {n}: extrudes with T{current} on the carriage")
                    break
                if heated.get(tool, 0) < MIN_EXTRUDE_TEMP:
                    problems.append(f"line {n}: extrudes before T{tool} is set above {MIN_EXTRUDE_TEMP:g}C")
                    break
                for i, v in enumerate((x, y, z)):
                    if v is not None:
                        lo[i], hi[i] = min(lo[i], v), max(hi[i], v)
    if first != "T-1":
        problems.append(f"first command is '{first}', expected 'T-1' (drop off the camera)")
    if last_tool_cmd != -1:
        problems.append("job does not end by parking the tool (T-1)")
    if extruding_moves == 0:
        problems.append("no extruding moves")
    else:
        if lo[0] < BED_X[0] or hi[0] > BED_X[1] or lo[1] < BED_Y[0] or hi[1] > BED_Y[1]:
            problems.append(f"print area X {lo[0]:.1f}..{hi[0]:.1f} Y {lo[1]:.1f}..{hi[1]:.1f} leaves the bed")
        if hi[2] > MAX_Z:
            problems.append(f"print height {hi[2]:.1f} exceeds {MAX_Z:g}")
    summary = {
        "tool": tool,
        "extruding_moves": extruding_moves,
        "x": (lo[0], hi[0]), "y": (lo[1], hi[1]), "z": (lo[2], hi[2]),
        "tool_temp": print_temp,
    }
    return problems, summary
