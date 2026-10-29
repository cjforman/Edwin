import sys, time, serial
sys.path.insert(0, r"C:\Users\User\AppData\Local\Temp\claude\D--Source-SymbolicTM--claude-worktrees-3d-printer-wifi-diagnostics-c655d7\9f8ed073-7d02-4e4f-968b-0f9ef9ab2b09\scratchpad")
from flash_wifi_lib import send, ok
ser = serial.Serial("COM6", 115200, timeout=0.2, write_timeout=10); time.sleep(0.5); ser.reset_input_buffer()
send(ser, "M552", 3, ok)
send(ser, "M552 S0", 15, ok)      # idle the module
time.sleep(5)
send(ser, "M552", 3, ok)
send(ser, "M552 S1", 40, lambda b: b"WiFi module is connected" in b or b"rror" in b)   # start it, wait for the connection report
time.sleep(5)
send(ser, "M552", 3, ok)
r = send(ser, "M122", 10, ok)
i = r.find("=== WiFi ===")
print("\n--- WiFi section ---\n" + (r[i:] if i >= 0 else "(no WiFi section)"))
send(ser, "M587", 8, ok)
ser.close()
