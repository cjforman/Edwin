import time, serial
def collect(ser, seconds, stop=None):
    buf, t0, last = b"", time.time(), time.time()
    while time.time() - t0 < seconds:
        b = ser.read(4096)
        if b: buf += b; last = time.time()
        elif stop and stop(buf) and time.time() - last > 1.0: break
    return buf.decode("utf-8", "replace")
def send(ser, cmd, seconds, stop=None):
    print(f"\n>>> {cmd}", flush=True); ser.reset_input_buffer(); ser.write((cmd + "\n").encode())
    t0 = time.time(); out = collect(ser, seconds, stop); print(out.rstrip(), flush=True); print(f"    [{time.time()-t0:.1f}s]", flush=True); return out
ok = lambda b: b.rstrip().endswith(b"ok")
