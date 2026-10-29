"""Probe 2: translate the points myself (worker's snapshot reads a stale matrix_world when
`origin` is used), full toolpath, plus a top-down capture of the first layer only."""
import os, sys, time
sys.path.insert(0, r"D:\Source\BlenderReality")
from client import BlenderReality
from protocol.gcode import parse_gcode_file
GCODE = r"D:\OneDrive\work\Org\Forman\3DParts\ball_joint.T2.gcode"
OUT = os.path.dirname(os.path.abspath(__file__))
os.environ["BLENDER_REALITY_EXE"] = r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"
paths = parse_gcode_file(GCODE)
xs = [pt[0] for p in paths for pt in p]; ys = [pt[1] for p in paths for pt in p]
cx, cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
paths = [[[x - cx, y - cy, z] for x, y, z in p] for p in paths]
z0 = min(p[0][2] for p in paths)
first = [p for p in paths if abs(p[0][2] - z0) < 1e-6]
for label, sub, res, samples in [("full2", paths, (800, 450), 8), ("layer1", first, (600, 600), 8)]:
    br = BlenderReality(work_dir=os.path.join(OUT, label), samples=samples, denoise=False, timeout=900)
    br.reset_scene(ground_size=400.0)
    br.deposit("part", paths=sub, bead_radius=0.25)
    if label == "layer1":
        shot = br.capture(position=(0, 0, 250), look_at=(0, 0, 0), lens=50.0, resolution=res, fstop=64)
    else:
        shot = br.snapshot(resolution=res)
    t = time.time()
    r = br.run(label)
    print(f"{label}: {len(sub)} polylines -> {r.image_path(shot)} in {time.time()-t:.0f}s", flush=True)
