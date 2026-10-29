"""Read-only frame grabs from the SymbolicTM Jetson camera server.

Only ping / capture / get_frame / frame_received are sent. Never "update"
(SymbolicTM's CamClient sends one on connect, which rewrites the Jetson's
camera parameters and reopens both cameras) and never an "led" block, so
the server's settings and its LED ring are left exactly as SymbolicTM set
them. Default camera is 1: camera 0 belongs to SymbolicTM.

Caveat: the server PUSHes frames round-robin to every connected PULL
socket, so don't run this while a SymbolicTM session is also connected.
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

    def grab(self):
        """Return (frame_name, image) from this camera; image is an HxWxC uint8 array.

        The server sends either [name, png] or [name, meta, raw] where meta
        gives dtype and shape of the raw pixel buffer.
        """
        req_id = int(time.time() * 1000)
        self._send({"cmd": "ping", "id": req_id}, 3000)
        ack = self._send({"cmd": "capture", "cam": self.cam, "id": req_id})
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
        if img is None:
            raise TimeoutError(f"frame {name} never arrived")
        return name, img

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
