"""Per-layer timelapse of a running print: Duet layer counter -> Jetson frame.

Polls the Duet's object model; whenever job.layer changes it grabs a frame
from Jetson camera 1 (read-only capture) and saves it with the layer, time
and temperatures stamped on. When the printer leaves "processing" it takes
a final frame, writes an MP4 and restores SymbolicTM's camserver settings
(imagingParams_symbolictm.json), since camera 1's crop was changed for this.

  python timelapse.py 192.168.1.14 OUT_DIR [--fps 12]
"""
import argparse
import glob
import json
import os
import time

import cv2

import duet
import jetson_cam

HERE = os.path.dirname(os.path.abspath(__file__))


def stamp(img, text):
    cv2.rectangle(img, (0, 0), (img.shape[1], 44), (0, 0, 0), -1)
    cv2.putText(img, text, (12, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return img


def make_video(frames_dir, out, fps):
    paths = sorted(glob.glob(os.path.join(frames_dir, "layer_*.jpg")))
    if not paths:
        return None
    h, w = cv2.imread(paths[0]).shape[:2]
    vw = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for p in paths:
        vw.write(cv2.imread(p))
    for _ in range(fps * 2):  # hold the last frame for 2 s
        vw.write(cv2.imread(paths[-1]))
    vw.release()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("out")
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--crop", default="1250,1450,1600,1200", help="camera 1 crop x,y,w,h on the 3424x3424 frame")
    a = ap.parse_args()
    frames = os.path.join(a.out, "frames")
    os.makedirs(frames, exist_ok=True)
    with open(os.path.join(HERE, "imagingParams_symbolictm.json")) as f:
        base = json.load(f)
    x, y, w, h = (int(v) for v in a.crop.split(","))

    d = duet.Duet(a.host)
    d.connect()
    cam = jetson_cam.JetsonCam(cam=1, timeout_s=30)
    cam.update(dict(base, crop_xpos_cam1=x, crop_ypos_cam1=y, crop_width_cam1=w, crop_height_cam1=h))
    last_layer = None
    failures = 0
    try:
        while True:
            try:
                job = d.model("job", "d99vn")
                status = d.model("state.status", "")
                heaters = d.model("heat.heaters", "d99vn")
                failures = 0
            except Exception as e:  # dropped Wi-Fi request: retry, and reconnect after a run of them
                failures += 1
                print(f"duet poll failed ({failures}):", e, flush=True)
                if failures % 5 == 0:
                    try:
                        try:  # free the old session first: the Duet answers 503 once its sessions run out
                            d.disconnect()
                        except Exception:
                            pass
                        d.connect()
                        print("reconnected to duet", flush=True)
                    except Exception as e2:
                        print("reconnect failed:", e2, flush=True)
                time.sleep(a.poll)
                continue
            layer = job.get("layer")
            done = status != "processing" and last_layer is not None
            if done:
                layer = last_layer  # job.layer goes null once the print ends
            if layer is not None and (layer != last_layer or done):
                temps = " ".join(f"{h['current']:.0f}C" for h in heaters if h and h.get("active"))
                try:
                    name, img = cam.grab()
                    tag = "final" if done else f"{layer:03d}"
                    text = f"layer {layer}  {time.strftime('%H:%M:%S')}  {temps}  {status}"
                    cv2.imwrite(os.path.join(frames, f"layer_{tag}.jpg"), stamp(img.copy(), text))
                    print(text, flush=True)
                except Exception as e:
                    print(f"layer {layer}: capture failed: {e}", flush=True)
                last_layer = layer
            if done:
                break
            time.sleep(a.poll)
    finally:
        video = make_video(frames, os.path.join(a.out, "timelapse.mp4"), a.fps)
        print("video:", video, flush=True)
        print("restore SymbolicTM camserver settings:", cam.update(base), flush=True)
        cam.close()
        try:
            d.disconnect()
        except Exception as e:
            print("duet disconnect failed:", e, flush=True)


if __name__ == "__main__":
    main()
