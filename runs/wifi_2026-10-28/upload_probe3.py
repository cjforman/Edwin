import sys, time
sys.path.insert(0, ".")
from upload_probe2 import probe, duet, HOST
d = duet.Duet(HOST, timeout=30); d.connect()
try:
    for rep in range(3):
        probe(d, 300000, 1460, 8192, True)   # burst: default-ish
        probe(d, 300000, 512, 2048, True)    # paced
    probe(d, 1400000, 512, 2048, True)       # ball_joint-sized, paced
    probe(d, 1400000, 1024, 4096, True)
    # M122 via rr_gcode + rr_reply, raw
    d._request("/rr_gcode", {"gcode": "M122"}); time.sleep(2.0)
    r = d._request("/rr_reply"); txt = r if isinstance(r, str) else str(r)
    for ln in txt.splitlines():
        if any(k in ln for k in ("=== Network", "HTTP", "Socket", "WiFi", "Reconnect", "Free mem", "Responder", "session", "RSSI", "Clock", "Bytes", "rror")):
            print("   " + ln.strip())
    for name in ("_probe_4000", "_probe_16000", "_probe_59000", "_probe_300000", "_probe_1400000"):
        try: d._request("/rr_delete", {"name": f"0:/gcodes/{name}.gcode"})
        except Exception as e: print("delete", name, e)
    print("deleted probe files")
finally:
    try: d.disconnect()
    except Exception: pass
