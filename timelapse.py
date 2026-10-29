"""Per-layer timelapse of a running print: one Jetson frame per layer.

Polls the Duet's object model and grabs a frame from Jetson camera 1
(read-only capture) at every layer change, stamped with the layer or Z,
the time and the temperatures. When the printer leaves "processing" it
takes a final frame, writes an MP4 and restores SymbolicTM's camserver
settings (imagingParams_symbolictm.json), since camera 1's crop was
changed for this.

Layer changes come from RRF's ``job.layer`` when the Duet reports one,
which it does only for a file that kept its slicer comments (a Wi-Fi
upload). After a USB upload ``M28`` has stripped the comments, RRF counts
no layers and ``job.layer`` stays null, so a change of Z while printing
counts as a layer instead (the E3D profiles have no z-hop; with z-hop you
get two frames per layer). The first poll takes a frame too.

A missed frame (the Jetson pushes frames round-robin to every connected
puller, including a client that died without closing, for some twenty
minutes) is retried once.

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


def layer_changed(layer, last_layer, z, last_z, min_dz=0.02):
    """True when a new frame is due: RRF's layer counter moved, or, with no
    counter, Z moved (either way; the first observation counts)."""
    if layer is not None:
        return layer != last_layer
    if z is None:
        return False
    return last_z is None or abs(z - last_z) > min_dz


def frame_name(seq, layer, z, done=False):
    """layer_0012_L012.jpg or layer_0012_z02.55.jpg, sorted by sequence; the last one is layer_9999_final.jpg."""
    if done:
        return "layer_9999_final.jpg"
    where = f"L{layer:03d}" if layer is not None else f"z{z:06.2f}"
    return f"layer_{seq:04d}_{where}.jpg"


def z_of(axes):
    return next((a.get("userPosition") for a in axes if a and a.get("letter") == "Z"), None)


def grab_with_retry(cam, attempts=2):
    err = None
    for _ in range(attempts):
        try:
            return cam.grab()
        except Exception as e:
            err = e
    raise err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("out")
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--crop", default="1250,1450,1600,1200", help="camera 1 crop x,y,w,h on the 3424x3424 frame")
    ap.add_argument("--idle-end", type=float, default=120.0,
                    help="seconds out of 'processing' after which to stop even if no layer was seen")
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
    last_layer, last_z, seq, failures, idle_since = None, None, 0, 0, None
    try:
        while True:
            try:
                job = d.model("job", "d99vn")
                status = d.model("state.status", "")
                heaters = d.model("heat.heaters", "d99vn")
                z = z_of(d.model("move.axes", "d99vn"))
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
            processing = status == "processing"
            idle_since = None if processing else (idle_since or time.time())
            done = not processing and (seq > 0 or time.time() - idle_since > a.idle_end)
            if done:
                layer, z = last_layer, (z if z is not None else last_z)
            if done or (processing and layer_changed(layer, last_layer, z, last_z)):
                temps = " ".join(f"{hh['current']:.0f}C" for hh in heaters if hh and hh.get("active"))
                where = f"layer {layer}" if layer is not None else f"z {z}"
                try:
                    name, img = grab_with_retry(cam)
                    seq += 1
                    text = f"{where}  {time.strftime('%H:%M:%S')}  {temps}  {status}"
                    cv2.imwrite(os.path.join(frames, frame_name(seq, layer, z, done)), stamp(img.copy(), text))
                    print(text, flush=True)
                except Exception as e:
                    print(f"{where}: capture failed twice: {e}", flush=True)
                last_layer, last_z = layer, z
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
