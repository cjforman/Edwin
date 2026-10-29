#!/bin/bash
cd "/d/OneDrive/work/Org/Forman/3DParts/printAgent" || exit 1
E=/c/Users/User/.conda/envs/printagent; export PATH="$E:$E/Library/bin:$E/Scripts:$PATH"
LOG=runs/ball_joint_$(date +%Y%m%d_%H%M%S).log
echo "log: $LOG"
{
echo "== $(date) guard: axis limits vs part extents"
python - <<'PY' || exit 2
import sys, json, duet, toolchanger
text = open(r"..\ball_joint.T2.gcode", encoding="utf-8").read()
problems, s = toolchanger.check(text, 2)
if problems:
    print("CHECK FAILED:", problems); sys.exit(2)
with duet.Duet("192.168.1.14") as d:
    axes = {a["letter"]: a for a in d.model("move.axes", "d99vn")}
    print("limits:", {k: (v["min"], v["max"]) for k, v in axes.items()})
    ok = (axes["X"]["min"] <= s["x"][0] and s["x"][1] <= axes["X"]["max"] and
          axes["Y"]["min"] <= s["y"][0] and s["y"][1] <= axes["Y"]["max"] and s["z"][1] <= axes["Z"]["max"])
    print("part:", json.dumps(s), "fits:", ok)
    if not ok:
        sys.exit(2)
PY
echo "== $(date) starting agent"
python agent.py 192.168.1.14 print ../ball_joint.T2.gcode --tool 2 --yes --poll 5
echo "== $(date) agent exited with $?"
} 2>&1 | tee -a "$LOG"
