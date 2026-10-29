"""Live aiming preview of a Jetson camera, shown in a window on this PC.

Frames come through the running camserver with read-only captures (see
jetson_cam.py), so the Jetson is never touched: no camserver stop/restart,
no parameter update, no LED. That limits it to ~1-2 fps and to the crop
camserver already applies (camera 1: 1500 x 500).

Keys: q / Esc quit, s save the current frame next to this script, r reset the best sharpness.
"""
import argparse
import json
import os
import time

import cv2
import numpy as np

import jetson_cam


def stretch(img):
    """Auto-levels: map the 1st..99th percentile to 0..255 so dim scenes are visible."""
    lo, hi = np.percentile(img, (1, 99))
    return np.clip((img.astype(np.float32) - lo) * (255.0 / max(hi - lo, 1.0)), 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=1)
    ap.add_argument("--raw", action="store_true", help="show frames without auto-levels")
    ap.add_argument("--minutes", type=float, default=20.0, help="stop after this long")
    ap.add_argument("--full", action="store_true",
                    help="set camera 1 to its full frame first (camserver update; ~4 s per frame)")
    ap.add_argument("--crop", help="set camera 1 crop x,y,w,h first (camserver update)")
    ap.add_argument("--focus", help="focus aid: score sharpness inside reticle x,y,w,h of the shown frame")
    ap.add_argument("--zoom", help="zoom window x,y,w,h shown enlarged (default: 3x the reticle)")
    a = ap.parse_args()

    cam = jetson_cam.JetsonCam(cam=a.cam, timeout_s=60)
    if a.full or a.crop:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "imagingParams_symbolictm.json")) as f:
            base = json.load(f)
        x, y, w, h = ((0, 0, base["image_width_cam1"], base["image_height_cam1"]) if a.full
                      else (int(v) for v in a.crop.split(",")))
        cam.update(dict(base, crop_xpos_cam1=x, crop_ypos_cam1=y, crop_width_cam1=w, crop_height_cam1=h))
    roi = [int(v) for v in a.focus.split(",")] if a.focus else None
    zoom = [int(v) for v in a.zoom.split(",")] if a.zoom else None
    best = 0.0
    title = f"Jetson camera {a.cam} preview (q quit, s save, r reset best)"
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(title, *((1000, 1000) if a.full else (1500, 500) if not a.crop else (1200, 1000)))
    stop_at = time.time() + a.minutes * 60
    t_last = time.time()
    try:
        while time.time() < stop_at:
            _, img = cam.grab()
            now = time.time()
            fps, t_last = 1.0 / max(now - t_last, 1e-3), now
            view = img.copy() if a.raw else stretch(img)
            h, w = view.shape[:2]
            cv2.line(view, (w // 2, 0), (w // 2, h), (0, 255, 0), 1)
            cv2.line(view, (0, h // 2), (w, h // 2), (0, 255, 0), 1)
            cv2.putText(view, f"cam{a.cam} {w}x{h} mean {img.mean():.0f} {fps:.1f} fps {time.strftime('%H:%M:%S')}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            if roi:
                # variance of the Laplacian inside the reticle: rises as edges get crisper
                rx, ry, rw, rh = roi
                patch = cv2.cvtColor(img[ry:ry + rh, rx:rx + rw], cv2.COLOR_BGR2GRAY)
                sharp = cv2.Laplacian(patch, cv2.CV_64F).var()
                best = max(best, sharp)
                zx, zy, zw, zh = zoom or (rx - rw, ry - rh, 3 * rw, 3 * rh)
                cv2.rectangle(view, (zx, zy), (zx + zw, zy + zh), (255, 255, 0), 2)
                cv2.rectangle(view, (rx, ry), (rx + rw, ry + rh), (0, 255, 255), 1)
                # zoom window, top right, with the reticle drawn where sharpness is measured
                k = max(1, min(6, (view.shape[1] // 2) // zw))
                inset = cv2.resize(stretch(img[zy:zy + zh, zx:zx + zw]) if not a.raw else img[zy:zy + zh, zx:zx + zw],
                                   None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)
                ox, oy = (rx - zx) * k, (ry - zy) * k
                cv2.rectangle(inset, (ox, oy), (ox + rw * k, oy + rh * k), (0, 255, 255), 2)
                ih, iw = inset.shape[:2]
                view[50:50 + ih, w - iw - 10:w - 10] = inset
                cv2.rectangle(view, (10, 50), (10 + int(600 * sharp / max(best, 1e-6)), 90), (0, 255, 255), -1)
                cv2.putText(view, f"sharpness {sharp:.0f}   best {best:.0f}   (r = reset best)", (10, 130),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3)
                print(f"{time.strftime('%H:%M:%S')} sharpness {sharp:.0f} best {best:.0f}", flush=True)
            cv2.imshow(title, view)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord("r"):
                best = 0.0
                print("best reset", flush=True)
            if key == ord("s"):
                path = os.path.join(os.path.dirname(__file__), f"cam{a.cam}_{time.strftime('%H%M%S')}.jpg")
                cv2.imwrite(path, img)
                print("saved", path, flush=True)
    finally:
        cam.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
