"""Read-only frame grabs from the SymbolicTM Jetson camera server.

grab() sends only ping / capture / get_frame / frame_received. update()
rewrites ALL the Jetson's camera parameters and reopens both cameras
(SymbolicTM's CamClient sends one on connect too), so it is used only to
set the print view from SymbolicTM's own settings and to put them back.
light() drives the NeoPixel read ring ("light" command); the ring stays as
last commanded until told off, so whoever lights it turns it off again.
Default camera is 1: camera 0 belongs to SymbolicTM.

Caveat: the server PUSHes frames round-robin to every connected PULL
socket, so don't run this while a SymbolicTM session is also connected.
A client killed without close() counts as connected until the Jetson
notices the dead TCP peer (about twenty minutes on 2026-10-29), and every
other frame goes to it meanwhile; grab() then times out, so retry once.
"""
import json
import time

import cv2
import numpy as np
import zmq

JETSON_IP = "192.168.56.102"
CMD_PORT = 5556
IMG_PORT = 5555


class JetsonCam:
    def __init__(self, ip=JETSON_IP, cam=1, timeout_s=15.0):
        self.cam = int(cam)
        self.timeout_s = timeout_s
        self.ctx = zmq.Context.instance()
        self.ip = ip
        self._open_cmd()
        self.pull = self.ctx.socket(zmq.PULL)
        self.pull.setsockopt(zmq.LINGER, 0)
        self.pull.connect(f"tcp://{ip}:{IMG_PORT}")

    def _open_cmd(self):
        self.cmd = self.ctx.socket(zmq.REQ)
        self.cmd.setsockopt(zmq.LINGER, 0)
        self.cmd.connect(f"tcp://{self.ip}:{CMD_PORT}")

    def _send(self, payload, timeout_ms=10000):
        self.cmd.send_string(json.dumps(payload))
        if self.cmd.poll(timeout_ms) == 0:
            # a REQ socket is stuck after a lost reply: rebuild it
            self.cmd.close(0)
            self._open_cmd()
            raise TimeoutError(f"no reply to {payload.get('cmd')}")
        return json.loads(self.cmd.recv_string())

    def grab(self, led=None, off_after=True):
        """Return (frame_name, image) from this camera; image is an HxWxC uint8 array.

        With `led` (a ring block such as {"lit": 12, "level": 1.0, "rgb":
        [255, 255, 255]}) the capture request carries it: the server lights
        the ring, settles 0.6 s, flushes the stale frames and then captures,
        and the ring is switched off again after the frame (off_after), so
        it is lit for about two seconds per shot.

        The server sends either [name, png] or [name, meta, raw] where meta
        gives dtype and shape of the raw pixel buffer.
        """
        req_id = int(time.time() * 1000)
        self._send({"cmd": "ping", "id": req_id}, 3000)
        capture = {"cmd": "capture", "cam": self.cam, "id": req_id}
        if led:
            capture["led"] = led
        ack = self._send(capture)
        if not ack.get("ok"):
            raise RuntimeError(f"capture failed: {ack}")
        name = ack["frame_name"]
        self._send({"cmd": "get_frame", "frame_name": name, "id": req_id})
        deadline = time.time() + self.timeout_s
        img = None
        while time.time() < deadline and img is None:
            if self.pull.poll(1000):
                parts = self.pull.recv_multipart()
                if json.loads(parts[0].decode("utf-8")) != name:
                    continue
                if len(parts) == 3:
                    meta = json.loads(parts[1].decode("utf-8"))
                    img = np.frombuffer(parts[2], dtype=meta["dtype"]).reshape(meta["shape"])
                else:
                    img = cv2.imdecode(np.frombuffer(parts[1], np.uint8), cv2.IMREAD_COLOR)
        self._send({"cmd": "frame_received", "frame_name": name, "id": req_id})
        if led and off_after:
            self.light({"off": True})
        if img is None:
            raise TimeoutError(f"frame {name} never arrived")
        return name, img

    def light(self, led):
        """Set the read ring: {"lit": 12, "level": 1.0, "rgb": [255, 255, 255]} or {"off": True}.

        The Jetson writes to the ring's box over serial and reads nothing
        back, so "ok" means the command was sent, not that the ring lit
        (its power switch is separate). A repeat of the last command is
        skipped server-side, so send off then on after a power cycle.
        """
        ack = self._send({"cmd": "light", "led": led, "id": int(time.time() * 1000)}, 10000)
        if not ack.get("ok"):
            raise RuntimeError(f"light refused: {ack.get('led_error') or ack}")
        return ack.get("led_state")

    def update(self, params):
        """Replace ALL camserver parameters (it reopens both cameras).

        Only for changing camera 1's crop, starting from SymbolicTM's own
        settings (imagingParams_symbolictm.json); restore() puts those back.
        """
        ack = self._send({"cmd": "update", "params": params, "id": int(time.time() * 1000)}, 15000)
        if not ack.get("ok"):
            raise RuntimeError(f"update refused: {ack}")
        time.sleep(3.0)  # cameras reopen after the ack
        return ack

    def close(self):
        self.cmd.close(0)
        self.pull.close(0)


if __name__ == "__main__":  # python jetson_cam.py light off | light on
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "light" and sys.argv[2] in ("on", "off"):
        _c = JetsonCam(cam=1, timeout_s=10)
        try:
            if sys.argv[2] == "off":
                print(_c.light({"off": True}))
            else:
                _c.light({"off": True})
                print(_c.light({"lit": 12, "level": 1.0, "rgb": [255, 255, 255]}))
        finally:
            _c.close()
    else:
        print("usage: python jetson_cam.py light on|off")
