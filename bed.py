"""Is the build plate clear?  A Jetson camera 1 picture of the bed against a
reference picture taken when the bed was known to be empty.

The printer is homed first (the head parks at X150 Y-49, out of the bed
area), camera 1 is set to the print view (cam1_print_view.json: crop, exposure,
gain and frame rate, the same settings the timelapse uses, and the read
ring flashed on for the shot if the view says so), one frame is grabbed and SymbolicTM's camera
settings are restored. The frame and the reference are compared inside
the bed polygon (calib/cam1_bed_mask.json, in crop pixels): grey, shrunk,
blurred, each normalised to zero mean and unit spread so a change of
lighting cancels, and the fraction of bed pixels that differ by more than
one spread is the score. A change of light alone scores about zero; a
30 mm cube covers about 6% of the bed area in this view, so the threshold
is 0.03 until a real calibration (the Jetson was off when this was
written on 2026-10-29) says otherwise.

  python bed.py reference            take the empty-bed reference (bed clear, printer homed)
  python bed.py snapshot [OUT.jpg]   one picture of the bed
  python bed.py check [IMG.jpg]      score a picture (or a fresh one) against the reference
"""
import json
import os
import sys
import time

import cv2
import numpy as np

from timelapse import split_view

HERE = os.path.dirname(os.path.abspath(__file__))
CALIB = os.path.join(HERE, "calib")
REFERENCE = os.path.join(CALIB, "cam1_bed_empty.jpg")
MASK = os.path.join(CALIB, "cam1_bed_mask.json")
VIEW = os.path.join(HERE, "cam1_print_view.json")
BASE = os.path.join(HERE, "imagingParams_symbolictm.json")
THRESHOLD = 0.03   # fraction of bed pixels that differ; see the docstring
SIZE = (400, 300)  # comparison resolution


def snapshot(out, view_path=VIEW, base_path=BASE):
    """Grab one frame of the bed from camera 1 into `out` (jpg) and restore the camera settings."""
    import jetson_cam
    with open(base_path) as f:
        base = json.load(f)
    with open(view_path) as f:
        view = json.load(f)
    params, led = split_view(view)
    cam = jetson_cam.JetsonCam(cam=1, timeout_s=30)
    try:
        cam.update(dict(base, **params))
        err = None
        for _ in range(2):  # a dead puller elsewhere steals alternate frames
            try:
                _, img = cam.grab(led=led)  # the ring flashes on for the shot
                break
            except Exception as e:
                err, img = e, None
        if img is None:
            raise err
    finally:
        try:
            if led:
                cam.light({"off": True})
        except Exception:
            pass
        try:
            cam.update(base)
        finally:
            cam.close()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cv2.imwrite(out, img)
    return out


def load_mask(shape, path=MASK):
    """uint8 mask of the bed area in an image of `shape`; the lower 55% when there is no polygon file."""
    h, w = shape[:2]
    m = np.zeros((h, w), np.uint8)
    if os.path.exists(path):
        with open(path) as f:
            poly = np.array(json.load(f)["polygon"], np.int32)
        cv2.fillPoly(m, [poly], 255)
    else:
        m[int(0.45 * h):, :] = 255
    return m


def _prep(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.GaussianBlur(cv2.resize(g, SIZE), (7, 7), 0).astype(np.float32)
    return (g - g.mean()) / (g.std() + 1e-6)


def score(img, ref, mask=None):
    """Fraction of bed pixels where the normalised pictures differ by more than one spread."""
    diff = np.abs(_prep(img) - _prep(ref))
    if mask is None:
        mask = load_mask(img.shape, path="")
    m = cv2.resize(mask, SIZE, interpolation=cv2.INTER_NEAREST) > 0
    return float((diff[m] > 1.0).mean()) if m.any() else 0.0


def check(img_path, ref_path=REFERENCE, mask_path=MASK, threshold=THRESHOLD):
    """Score `img_path` against the reference. Raises FileNotFoundError without a reference."""
    if not os.path.exists(ref_path):
        raise FileNotFoundError(f"no empty-bed reference at {ref_path}; take one with 'python bed.py reference'")
    img, ref = cv2.imread(img_path), cv2.imread(ref_path)
    if img is None or ref is None:
        raise ValueError(f"cannot read {img_path if img is None else ref_path}")
    if img.shape != ref.shape:
        img = cv2.resize(img, (ref.shape[1], ref.shape[0]))
    s = score(img, ref, load_mask(ref.shape, mask_path))
    return {"score": round(s, 4), "threshold": threshold, "clear": s <= threshold,
            "picture": img_path, "reference": ref_path}


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if not a or a[0] not in ("reference", "snapshot", "check"):
        print(__doc__)
        return 2
    if a[0] == "reference":
        print("reference:", snapshot(REFERENCE))
        return 0
    if a[0] == "snapshot":
        out = a[1] if len(a) > 1 else f"bed_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        print("snapshot:", snapshot(out))
        return 0
    img = a[1] if len(a) > 1 else snapshot(f"bed_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
    r = check(img)
    print(json.dumps(r))
    return 0 if r["clear"] else 1


if __name__ == "__main__":
    sys.exit(main())
