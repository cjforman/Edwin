"""Probe: rr_upload with a small socket send buffer and paced writes."""
import socket, sys, time, zlib, json, urllib.parse
import duet

def paced_upload(host, key, remote, data, chunk=1024, sndbuf=4096, gap=0.0, log=print):
    crc = format(zlib.crc32(data) & 0xFFFFFFFF, "08x")
    q = urllib.parse.urlencode({"name": remote, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "crc32": crc}, safe=":/")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(30)
    s.connect((host, 80))
    hdr = (f"POST /rr_upload?{q} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/octet-stream\r\n"
           f"Content-Length: {len(data)}\r\nX-Session-Key: {key}\r\nConnection: close\r\n\r\n")
    s.sendall(hdr.encode())
    t0, last = time.time(), 0
    for i in range(0, len(data), chunk):
        s.sendall(data[i:i + chunk])
        if gap: time.sleep(gap)
        if time.time() - last > 2:
            last = time.time(); log(f"  sent {i + chunk} / {len(data)}  {(i + chunk) / (time.time() - t0) / 1024:.0f} kB/s")
    resp = b""
    while True:
        b = s.recv(4096)
        if not b: break
        resp += b
    s.close()
    return resp.decode(errors="replace"), time.time() - t0, crc

if __name__ == "__main__":
    path, remote = sys.argv[1], sys.argv[2]
    chunk, sndbuf, gap = int(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5])
    data = open(path, "rb").read()
    d = duet.Duet("192.168.1.14", timeout=20); d.connect()
    try:
        resp, dt, crc = paced_upload("192.168.1.14", d.session_key, remote, data, chunk, sndbuf, gap)
        print("response:", resp.split("\r\n\r\n", 1)[-1][:200], f"{dt:.1f}s", f"{len(data) / dt / 1024:.0f} kB/s")
        info = d._request("/rr_fileinfo", {"name": remote})
        print("on card:", info.get("size") if isinstance(info, dict) else info, "expected", len(data), "crc", crc)
    except Exception as e:
        print("FAILED:", repr(e))
    finally:
        try: d.disconnect()
        except Exception: pass
