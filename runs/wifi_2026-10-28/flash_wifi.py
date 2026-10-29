"""Flash the Duet 2 WiFi module firmware over USB and verify. Logs every line."""
import sys, time, serial
from serial.tools import list_ports

def find_port():
    for p in list_ports.comports():
        if (p.vid, p.pid) in {(0x1D50, 0x60EC), (0x1D50, 0x60ED)} or "duet" in (p.description or "").lower():
            return p.device
    return "COM6"

def collect(ser, seconds, stop=None):
    buf, t0, last = b"", time.time(), time.time()
    while time.time() - t0 < seconds:
        b = ser.read(4096)
        if b:
            buf += b; last = time.time()
        elif stop and stop(buf) and time.time() - last > 1.0:
            break
    return buf.decode("utf-8", "replace")

def send(ser, cmd, seconds, stop=None):
    print(f"\n>>> {cmd}", flush=True)
    ser.reset_input_buffer()
    ser.write((cmd + "\n").encode())
    t0 = time.time()
    out = collect(ser, seconds, stop)
    print(out.rstrip(), flush=True)
    print(f"    [{time.time()-t0:.1f}s]", flush=True)
    return out

port = find_port()
print("port:", port)
ser = serial.Serial(port, 115200, timeout=0.2, write_timeout=10)
time.sleep(0.5); ser.reset_input_buffer()
ok = lambda b: b.rstrip().endswith(b"ok")
send(ser, "M29", 2, ok)
r = send(ser, "M115", 4, ok)
if "Duet 2 WiFi" not in r:
    print("NOT A DUET 2 WIFI, stopping"); sys.exit(2)
stage = sys.argv[1] if len(sys.argv) > 1 else "all"
if stage in ("flash", "all"):
    send(ser, 'M997 S1 P"0:/firmware/DuetWiFiServer.bin"', 150,
         lambda b: (b"ok" in b.rstrip()[-4:]) or b"Error" in b or b"rror" in b)
    time.sleep(5)
if stage in ("verify", "all"):
    r = send(ser, "M122", 10, ok)
    i = r.find("=== WiFi ===")
    print("\n--- WiFi section ---\n" + (r[i:] if i >= 0 else "(no WiFi section)"))
    send(ser, "M587", 6, ok)
    send(ser, "M552", 4, ok)
ser.close()
