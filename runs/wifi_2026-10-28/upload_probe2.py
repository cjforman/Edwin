"""Instrumented rr_upload: where does the byte stream stall, and what does the Duet say afterwards?"""
import socket, sys, time, zlib, urllib.parse, os
sys.path.insert(0, r"D:\OneDrive\work\Org\Forman\3DParts\printAgent")
import duet

HOST = "192.168.1.14"

def probe(d, size, chunk=1460, sndbuf=8192, keepalive=False):
    data = (b"; probe line %08d\n" % 0) * 0
    line = b"G1 X%06.2f Y%06.2f E0.1234 ; filler\n"
    buf = bytearray()
    i = 0
    while len(buf) < size:
        buf += line % (i % 200, (i * 7) % 200); i += 1
    data = bytes(buf[:size])
    remote = f"0:/gcodes/_probe_{size}.gcode"
    crc = format(zlib.crc32(data) & 0xFFFFFFFF, "08x")
    q = urllib.parse.urlencode({"name": remote, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "crc32": crc}, safe=":/")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
    s.settimeout(30)
    s.connect((HOST, 80))
    conn = "keep-alive" if keepalive else "close"
    hdr = (f"POST /rr_upload?{q} HTTP/1.1\r\nHost: {HOST}\r\nContent-Type: application/octet-stream\r\n"
           f"Content-Length: {len(data)}\r\nX-Session-Key: {d.session_key}\r\nConnection: {conn}\r\n\r\n")
    t0 = time.time()
    s.sendall(hdr.encode())
    sent = 0; marks = []
    try:
        for off in range(0, len(data), chunk):
            t1 = time.time()
            s.sendall(data[off:off + chunk])
            sent = off + len(data[off:off + chunk])
            dt = time.time() - t1
            if dt > 0.2:
                marks.append((sent, round(dt, 2)))
        s.shutdown(socket.SHUT_WR) if not keepalive else None
        resp = b""
        while True:
            b = s.recv(4096)
            if not b: break
            resp += b
            if b"\r\n\r\n" in resp and keepalive: break
        status = resp.split(b"\r\n", 1)[0].decode(errors="replace")
        body = resp.split(b"\r\n\r\n", 1)[-1][:120].decode(errors="replace")
        result = f"OK status={status!r} body={body!r}"
    except Exception as e:
        result = f"FAILED after {sent}/{len(data)} B: {e!r}"
    dt = time.time() - t0
    s.close()
    print(f"size={size:6d} chunk={chunk} sndbuf={sndbuf} conn={conn}: {result}  {dt:.1f}s  slow sends(bytes,sec)={marks[:8]}")
    time.sleep(0.5)
    try:
        info = d._request("/rr_fileinfo", {"name": remote})
        print(f"   on card: size={info.get('size') if isinstance(info, dict) else info} expected={size}")
    except Exception as e:
        print(f"   fileinfo failed: {e!r}")

def m122(d):
    d.gcode("M122", wait_reply=3.0)
    time.sleep(1.5)
    r = d._request("/rr_reply")
    txt = r if isinstance(r, str) else str(r)
    keep = [ln for ln in txt.splitlines() if any(k in ln for k in ("HTTP", "Socket", "socket", "WiFi", "Reconnections", "Free mem", "Responder", "sessions", "errors", "Errors", "=== Network", "RSSI"))]
    print("M122 (network lines):"); print("\n".join("   " + k for k in keep[:30]))

if __name__ == "__main__":
    d = duet.Duet(HOST, timeout=20); d.connect()
    try:
        for size, chunk, sndbuf, ka in [(4000, 1460, 8192, False), (16000, 1460, 8192, False), (59000, 1460, 8192, False), (59000, 1460, 8192, True), (59000, 512, 2048, False)]:
            probe(d, size, chunk, sndbuf, ka)
        m122(d)
    finally:
        try: d.disconnect()
        except Exception: pass
