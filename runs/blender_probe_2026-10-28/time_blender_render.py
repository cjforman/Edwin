"""Timing probe: how long does BlenderReality take to render the ball joint's toolpath?
Not part of printAgent; informs the plan only."""
import os, sys, time, json
sys.path.insert(0, r"D:\Source\BlenderReality")
from client import BlenderReality
from protocol.gcode import parse_gcode_file

GCODE = r"D:\OneDrive\work\Org\Forman\3DParts\ball_joint.T2.gcode"
OUT = os.path.dirname(os.path.abspath(__file__))
os.environ["BLENDER_REALITY_EXE"] = r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"

t = time.time()
paths = parse_gcode_file(GCODE)
print(f"parsed {len(paths)} polylines, {sum(len(p) for p in paths)} points in {time.time()-t:.1f}s", flush=True)
xs = [pt[0] for p in paths for pt in p]; ys = [pt[1] for p in paths for pt in p]
cx, cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
zs = sorted({round(p[0][2], 3) for p in paths})
print(f"{len(zs)} layer z values, z range {zs[0]}..{zs[-1]}, xy centre {cx:.1f},{cy:.1f}", flush=True)

def subset(every):
    keep = set(zs[::every]) | {zs[0]}
    return [p for p in paths if round(p[0][2], 3) in keep]

for label, every, res, samples, timeout in [("every10", 10, (640, 360), 8, 600),
                                            ("every3", 3, (640, 360), 8, 900),
                                            ("full", 1, (640, 360), 8, 1800)]:
    sub = subset(every)
    br = BlenderReality(work_dir=os.path.join(OUT, label), samples=samples, denoise=False, timeout=timeout)
    br.reset_scene(ground_size=400.0)
    br.deposit("part", paths=sub, origin=(-cx, -cy, 0.0), bead_radius=0.25)
    shot = br.snapshot(resolution=res)
    t = time.time()
    try:
        r = br.run(label)
        dt = time.time() - t
        png = r.image_path(shot)
        print(f"{label}: {len(sub)} polylines -> {png} in {dt:.0f}s ({os.path.getsize(png)} B)", flush=True)
    except Exception as e:
        print(f"{label}: FAILED after {time.time()-t:.0f}s: {str(e)[:300]}", flush=True)
