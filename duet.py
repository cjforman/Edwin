"""Duet (RepRapFirmware 3, standalone HTTP API) client for the E3D ToolChanger,
built for a lossy Wi-Fi link.

The link, measured (README.md and HANDOVER_2026-10-28.md have the tables):

  * 2026-10-27: frames from the PC to the Duet were lost 25% of the time at
    64 bytes and 91% at 1200 bytes and above (tape over the antenna, a
    charger beside the board, WiFi module firmware 1.23), while frames from
    the Duet to the PC arrived.  Any request bigger than a few hundred bytes
    stalled until the Duet's 8 s session timeout reset the connection.
    Everything here was designed around that.
  * 2026-10-28: tape off and the module flashed to DuetWiFiServer 2.2.0:
    -62 dBm, 1% loss at every frame size.  rr_upload now works: 13 of 13
    uploads of 4 kB to 1.4 MB succeeded (about 40 kB/s) when sent with
    "Connection: keep-alive" (with "Connection: close" the Duet writes the
    file but returns no status line) and retried on the rare stall that
    outlives the 8 s session timeout; verified by rr_fileinfo size.  Duet
    Web Control does the same with up to two retries.

What the client does:

  * every request it sends is small, and is retried with backoff;
  * the web session (which expires after 8 s idle) is re-established
    transparently on 401/403/503, resets and timeouts;
  * bulk uploads go over USB serial (duet_usb.py: M28 stream, ~150 kB/s,
    lossless) when the cable is present: the primary path, four times
    faster than Wi-Fi;
  * without the cable, upload_wifi sends one rr_upload POST with keep-alive,
    up to three attempts (the chunked M28/M98 path of the 27th is gone);
  * verification uses rr_fileinfo (size) or rr_download (full compare).

Protocol for a print: the whole file goes to 0:/gcodes/ and the printer prints
it from its own SD card (M32), so a network hiccup can't starve the motion
planner the way line-by-line streaming would.

Commands deliberately never sent over USB (they preceded a firmware
crash/reboot on 2026-10-27 22:05): M38, M587, M122 and M30 (files are deleted
with rr_delete instead).  M122 over Wi-Fi (rr_gcode then rr_reply) is fine.

CLI (see agent.py for the full pipeline):
  python duet.py prepare IN.gcode OUT.gcode --tool 2   (offline, rewrites + checks)
  python duet.py HOST status
  python duet.py HOST upload FILE.gcode [--port auto|COM6|none] [--verify size|full]
  python duet.py HOST start NAME.gcode --yes
  python duet.py HOST send FILE.gcode --yes         (check, upload, verify, start)
  python duet.py HOST delete NAME.gcode
"""
import argparse
import http.client
import json
import os
import random
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

import toolchanger


class DuetError(RuntimeError):
    pass


class DuetUnreachable(DuetError):
    """Gave up after repeated request failures (link down)."""


def m28_lines(text):
    """What M28 keeps of a file: RRF drops comments, trailing whitespace and blank lines."""
    out = []
    for ln in text.splitlines():
        ln = ln.split(";", 1)[0].rstrip()
        if ln:
            out.append(ln)
    return out


def m28_bytes(lines):
    return ("\n".join(lines) + "\n").encode("utf-8")


def forbidden_in_m28(lines):
    """M28/M29/M30 inside a file written through M28 would end the write early
    and let the rest of the file EXECUTE; such files are refused outright."""
    return [ln for ln in lines if ln.split()[0] in ("M28", "M29", "M30")]


RETRYABLE_HTTP = (401, 403, 503)
NETWORK_ERRORS = (urllib.error.URLError, socket.timeout, ConnectionError, http.client.HTTPException, OSError)


class Duet:
    def __init__(self, host, password="", timeout=5.0, retries=10, max_backoff=8.0, log=None):
        self.host = host
        self.base = f"http://{host}"
        self.password = password
        self.timeout = timeout
        self.retries = retries
        self.max_backoff = max_backoff
        self.log = log or (lambda msg: None)
        self.session_key = None
        self.stats = {"requests": 0, "retries": 0, "reconnects": 0}

    # ---- transport -------------------------------------------------------

    def _raw(self, path, params=None, data=None, timeout=None):
        url = self.base + path
        if params:
            url += ("&" if "?" in path else "?") + urllib.parse.urlencode(params, safe=":/")
        req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
        if self.session_key is not None:
            req.add_header("X-Session-Key", str(self.session_key))
        if data is not None:
            req.add_header("Content-Type", "application/octet-stream")
        self.stats["requests"] += 1
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            body = r.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            return body

    def _request(self, path, params=None, data=None, timeout=None, idempotent=True, retries=None):
        """One logical request, retried across lost frames and expired sessions.

        idempotent=False marks a request that must not be repeated blindly
        (rr_gcode carrying M28 content, M32).  It is still retried when the
        failure proves the Duet never acted on it (401/403/503, connection
        refused); an ambiguous failure (timeout, reset) raises at once.
        """
        attempts = (self.retries if retries is None else retries) + 1
        last = None
        for n in range(attempts):
            try:
                return self._raw(path, params, data, timeout)
            except urllib.error.HTTPError as e:
                last = e
                if e.code not in RETRYABLE_HTTP:
                    raise
                safe = True
                if e.code in (401, 403) and path not in ("/rr_connect", "/rr_disconnect"):
                    self._reconnect()
            except NETWORK_ERRORS as e:
                last = e
                reason = getattr(e, "reason", e)
                safe = idempotent or isinstance(reason, ConnectionRefusedError)
            if not safe:
                raise DuetError(f"{path[:40]} failed and is not safe to repeat: {last!r}")
            if n == attempts - 1:
                break
            self.stats["retries"] += 1
            delay = min(self.max_backoff, 0.3 * (2 ** n)) * (0.7 + 0.6 * random.random())
            self.log(f"retry {n + 1}/{attempts - 1} {path[:40]} in {delay:.1f}s: {str(last)[:80]}")
            time.sleep(delay)
        raise DuetUnreachable(f"{path[:40]}: gave up after {attempts} attempts: {last!r}")

    # ---- session ---------------------------------------------------------

    def connect(self):
        try:
            r = self._request("/rr_connect", {"password": self.password,
                                              "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise DuetError("no /rr_connect: this looks like a Duet 3 + SBC (/machine API), not supported")
            raise
        if not isinstance(r, dict) or r.get("err", 0) != 0:
            raise DuetError(f"rr_connect refused: {r} (err 1 = wrong password, 2 = no free session)")
        self.session_key = r.get("sessionKey")
        return r

    def _reconnect(self):
        self.stats["reconnects"] += 1
        self.log("session lost: reconnecting")
        try:
            self._raw("/rr_disconnect")
        except Exception:
            pass
        self.session_key = None
        time.sleep(0.2)
        self.connect()

    def disconnect(self):
        try:
            self._request("/rr_disconnect", retries=2)
        except Exception:
            pass
        finally:
            self.session_key = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()

    # ---- queries (all small, all idempotent) ------------------------------

    def model(self, key, flags="d99fn"):
        r = self._request("/rr_model", {"key": key, "flags": flags})
        if not isinstance(r, dict) or "result" not in r:
            raise DuetError(f"rr_model {key}: unexpected reply {str(r)[:200]!r}")
        return r["result"]

    def status_word(self):
        return self.model("state.status", "")

    def status(self):
        state = self.model("state")
        axes = self.model("move.axes", "d99vn")
        tools = self.model("tools", "d99vn")
        job = self.model("job", "d99vn")
        return {
            "status": state.get("status"),
            "current_tool": state.get("currentTool"),
            "homed": {a["letter"]: a["homed"] for a in axes},
            "tools": [{"number": t["number"], "name": t.get("name", ""),
                       "heaters": t.get("heaters", []),
                       "active": t.get("active"), "standby": t.get("standby")} for t in tools],
            "job_file": (job.get("file") or {}).get("fileName"),
        }

    def job_progress(self):
        """Small, poll-friendly view of a running job."""
        job = self.model("job", "d99vn")
        status = self.status_word()
        f = job.get("file") or {}
        size = f.get("size") or 0
        pos = job.get("filePosition") or 0
        return {
            "status": status,
            "file": f.get("fileName"),
            "layer": job.get("layer"),
            "layers": f.get("numLayers"),
            "fraction": (pos / size) if size else None,
            "duration": job.get("duration"),
            "time_left": (job.get("timesLeft") or {}).get("file"),
            "last_file": job.get("lastFileName"),
            "last_aborted": job.get("lastFileAborted"),
            "last_cancelled": job.get("lastFileCancelled"),
        }

    def fileinfo(self, remote):
        r = self._request("/rr_fileinfo", {"name": remote})
        return r if isinstance(r, dict) else {"err": 1, "raw": r}

    def filesize(self, remote):
        info = self.fileinfo(remote)
        return info.get("size") if info.get("err", 0) == 0 else None

    def download(self, remote, timeout=600):
        """Whole file; the Duet -> PC direction is the reliable one."""
        url = self.base + "/rr_download?name=" + urllib.parse.quote(remote, safe=":/.-_")
        last = None
        for n in range(4):
            try:
                req = urllib.request.Request(url)
                if self.session_key is not None:
                    req.add_header("X-Session-Key", str(self.session_key))
                self.stats["requests"] += 1
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (401, 403):
                    self._reconnect()
                elif e.code != 503:
                    raise
            except NETWORK_ERRORS as e:
                last = e
            self.stats["retries"] += 1
            time.sleep(1.0 * (n + 1))
        raise DuetUnreachable(f"download {remote}: {last!r}")

    _download = download  # old name, used by probes

    def delete(self, remote):
        r = self._request("/rr_delete", {"name": remote})
        return isinstance(r, dict) and r.get("err") == 0

    def filelist(self, directory):
        r = self._request("/rr_filelist", {"dir": directory})
        return r.get("files", []) if isinstance(r, dict) else []

    # ---- G-code ----------------------------------------------------------

    def _gcode_path(self, code):
        # escape only what must be: this WiFi server answers "bad escape" to %3A / %2F
        return "/rr_gcode?gcode=" + urllib.parse.quote(code, safe=":/.-_*=,")

    def gcode(self, code, wait_reply=2.0):
        """Send one command (never repeated on an ambiguous failure) and return the reply text."""
        self.log(f"-> {code}")
        self._request(self._gcode_path(code), idempotent=False)
        time.sleep(wait_reply)
        return self._request("/rr_reply")

    def is_idle(self):
        return self.status_word() == "idle"

    def wait_idle(self, timeout=60, poll=1.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.is_idle():
                return True
            time.sleep(poll)
        return False

    # ---- uploads ---------------------------------------------------------

    def upload(self, path, remote_name=None, port="auto", verify="size", progress=None):
        """Upload a G-code file to 0:/gcodes/: over USB if a Duet is on a
        serial port (or `port` names one), else one rr_upload POST over Wi-Fi.
        `port='none'` forces Wi-Fi.  Returns a dict describing what landed."""
        if port != "none":
            try:
                import duet_usb
                usb_port = duet_usb.find_port() if port in (None, "auto") else port
            except ImportError:
                usb_port = None
            if usb_port:
                return self.upload_usb(path, remote_name, usb_port, verify=verify, progress=progress)
            if port not in (None, "auto"):
                raise DuetError(f"USB port {port} not found")
            self.log("no Duet on USB: uploading over Wi-Fi with rr_upload")
        return self.upload_wifi(path, remote_name, verify=verify, progress=progress)

    def _prepare_text(self, path):
        with open(path, encoding="utf-8") as f:
            lines = m28_lines(f.read())
        bad = forbidden_in_m28(lines)
        if bad:
            raise DuetError(f"file contains {bad[0].split()[0]}; refusing to write it through M28")
        long = [ln for ln in lines if len(ln) > 200]
        if long:
            raise DuetError(f"{len(long)} lines longer than 200 chars (first: {long[0][:60]}...)")
        return lines

    def _verify(self, remote, expected, verify):
        size = self.filesize(remote)
        if size != len(expected):
            raise DuetError(f"{remote}: size on printer {size} != expected {len(expected)}")
        if verify == "full":
            got = self.download(remote)
            if got != expected:
                i = next((i for i, (a, b) in enumerate(zip(got, expected)) if a != b), min(len(got), len(expected)))
                raise DuetError(f"{remote}: content differs at byte {i}")
        return size

    def upload_usb(self, path, remote_name, port, verify="size", progress=None):
        import duet_usb
        name = remote_name or os.path.basename(path)
        remote = f"0:/gcodes/{name}"
        lines = self._prepare_text(path)
        data = m28_bytes(lines)
        self.log(f"usb upload: {len(lines)} lines, {len(data)} B -> {remote} via {port}")
        t0 = time.time()
        with duet_usb.DuetUSB(port, log=self.log) as usb:
            usb.write_file(remote, data, progress=progress)
        t_write = time.time() - t0
        self._verify(remote, data, verify)
        return {"remote": remote, "transport": f"usb:{port}", "bytes": len(data), "lines": len(lines),
                "seconds": round(t_write, 2), "verified": verify,
                "crc32": format(zlib.crc32(data) & 0xFFFFFFFF, "08x")}

    # Wi-Fi: one rr_upload POST, keep-alive, retried.  Measured 2026-10-28:
    # 13 of 13 uploads of 4 kB to 1.4 MB succeeded at about 40 kB/s.  A burst
    # of lost frames stalls the send (TCP back-off); a stall longer than the
    # Duet's 8 s session timeout drops the connection, and a retry covers it.

    def upload_wifi(self, path, remote_name=None, verify="size", progress=None, attempts=3, packet=65536):
        """POST the file's bytes as they are to 0:/gcodes/<name> with rr_upload.

        `Connection: keep-alive` is required: with `close` the Duet writes the
        file but returns no status line.  Each attempt is a fresh connection;
        before a retry the session is re-established and a partial file
        removed.  Success is err 0 in the reply and the rr_fileinfo size (or
        a full download compare with verify='full').
        """
        name = remote_name or os.path.basename(path)
        remote = f"0:/gcodes/{name}"
        with open(path, "rb") as f:
            data = f.read()
        crc = format(zlib.crc32(data) & 0xFFFFFFFF, "08x")
        timeout = max(30.0, len(data) / 10000 + 30.0)
        self.log(f"wifi upload: {len(data)} B -> {remote} (crc32 {crc}, up to {attempts} attempts)")
        t0 = time.time()
        last = None
        for attempt in range(1, attempts + 1):
            if self.session_key is None:
                self.connect()
            query = urllib.parse.urlencode({"name": remote, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                            "crc32": crc}, safe=":/")
            sent = [0]

            def body():
                for off in range(0, len(data), packet):
                    chunk = data[off:off + packet]
                    yield chunk
                    sent[0] += len(chunk)
                    if progress:
                        progress(sent[0], len(data), time.time() - t0)

            conn = http.client.HTTPConnection(self.host, timeout=timeout)
            try:
                self.stats["requests"] += 1
                conn.request("POST", "/rr_upload?" + query, body=body(), headers={
                    "X-Session-Key": str(self.session_key), "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)), "Connection": "keep-alive"})
                r = conn.getresponse()
                reply = r.read().decode("utf-8", "replace")
                if r.status in RETRYABLE_HTTP:
                    raise DuetError(f"rr_upload answered {r.status}")
                if r.status != 200:
                    raise DuetError(f"rr_upload answered {r.status}: {reply[:120]}")
                try:
                    j = json.loads(reply) if reply.strip() else {}
                except ValueError:
                    j = {}
                if isinstance(j, dict) and j.get("err", 0) != 0:
                    raise DuetError(f"rr_upload err {j.get('err')} (1 = write failed or crc mismatch)")
                size = self._verify(remote, data, verify)
                return {"remote": remote, "transport": "wifi", "bytes": len(data), "attempts": attempt,
                        "seconds": round(time.time() - t0, 1), "verified": verify, "crc32": crc, "size": size}
            except (DuetError,) + NETWORK_ERRORS as e:
                last = e
                self.log(f"wifi upload attempt {attempt}/{attempts} failed after {sent[0]} B: {str(e)[:100]}")
            finally:
                conn.close()
            self.stats["retries"] += 1
            if attempt == attempts:
                break
            self._reconnect()
            if self.filesize(remote) is not None:
                self.delete(remote)  # a partial file would otherwise confuse the next size check
            time.sleep(1.0 * attempt)
        raise DuetUnreachable(f"wifi upload of {name}: gave up after {attempts} attempts: {last!r}")

    def delete_upload(self, name):
        """Remove a job file from 0:/gcodes/.  Returns the number of files removed."""
        return 1 if self.delete(f"0:/gcodes/{name}") else 0

    # ---- printing --------------------------------------------------------

    def start(self, name):
        st = self.status()
        if st["status"] != "idle":
            raise DuetError(f"printer is '{st['status']}', not idle: not starting")
        if not all(st["homed"].get(a) for a in "XYZ"):
            raise DuetError(f"axes not homed: {st['homed']}")
        reply = self.gcode(f'M32 "0:/gcodes/{name}"')
        for _ in range(10):
            if self.status_word() in ("processing", "busy", "starting"):
                return reply
            time.sleep(1)
        raise DuetError(f"M32 sent but the printer did not start processing (reply {str(reply)[:120]!r})")


def preflight(st, tool):
    """Problems that should stop an upload/start, from a status() dict."""
    problems = []
    if st["status"] != "idle":
        problems.append(f"printer status is '{st['status']}', not idle")
    if not all(st["homed"].get(a) for a in "XYZ"):
        problems.append(f"not homed: {st['homed']} (T-1 needs homed axes to reach the dock)")
    if tool not in [t["number"] for t in st["tools"]]:
        problems.append(f"T{tool} is not defined on the printer")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", help="printer hostname/IP, or 'prepare' for the offline rewrite")
    ap.add_argument("action", nargs="?")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--tool", type=int, default=2)
    ap.add_argument("--password", default=os.environ.get("DUET_PASSWORD", ""))
    ap.add_argument("--port", default="auto", help="USB serial port for uploads: auto, COMn, or none (Wi-Fi)")
    ap.add_argument("--verify", choices=("size", "full"), default="size")
    ap.add_argument("--yes", action="store_true", help="really start the print")
    a = ap.parse_args(argv)

    if a.host == "prepare":
        src, dst = a.action, a.args[0]
        with open(src, encoding="utf-8") as f:
            text = toolchanger.prepare(f.read(), a.tool)
        problems, summary = toolchanger.check(text, a.tool)
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(json.dumps(summary, indent=1))
        for p in problems:
            print("PROBLEM:", p)
        return 1 if problems else 0

    def log(m):
        print("  " + m, flush=True)

    def progress(i, n, s):
        print(f"  upload {100 * i // max(n, 1)}% ({i}/{n}, {s:.0f} s)", flush=True)

    with Duet(a.host, a.password, log=log) as d:
        st = d.status()
        if a.action == "status":
            print(json.dumps(st, indent=1))
            for p in preflight(st, a.tool):
                print("PREFLIGHT:", p)
        elif a.action in ("upload", "send"):
            path = a.args[0]
            with open(path, encoding="utf-8") as f:
                problems, summary = toolchanger.check(f.read(), a.tool)
            if a.action == "send":
                problems += preflight(st, a.tool)
                if not a.yes:
                    problems.append("refusing to send without --yes")
            if problems:
                for p in problems:
                    print("PROBLEM:", p)
                return 1
            print("checked:", json.dumps(summary), flush=True)
            up = d.upload(path, port=a.port, verify=a.verify, progress=progress)
            print("uploaded:", json.dumps(up), flush=True)
            if a.action == "send":
                problems = preflight(d.status(), a.tool)
                if problems:
                    for p in problems:
                        print("PROBLEM:", p)
                    return 1
                print("starting:", d.start(os.path.basename(path)), flush=True)
                print("status:", d.status_word(), flush=True)
        elif a.action == "start":
            if not a.yes:
                print("refusing to start without --yes")
                return 1
            problems = preflight(st, a.tool)
            if problems:
                for p in problems:
                    print("PROBLEM:", p)
                return 1
            print(d.start(a.args[0]))
        elif a.action == "delete":
            print("removed", d.delete_upload(a.args[0]), "files")
        else:
            ap.error(f"unknown action {a.action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
