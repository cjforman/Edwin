"""USB serial transport to the Duet: the lossless, fast path for file uploads.

RRF's USB channel answers every command with "ok" (after any output), except
while a file is being written with M28: then incoming lines go to the SD
card silently and the only acknowledgement is "Done saving file." after M29.
USB flow control paces the stream (146 kB/s measured on 2026-10-27, byte-exact
on read-back); the caller verifies the result over Wi-Fi afterwards.

Safety:
  * nothing is streamed until the Duet has replied "Writing to file";
  * the caller refuses files containing M28/M29/M30 (duet.forbidden_in_m28),
    since M29 would end the write early and the rest would then EXECUTE;
  * on open, M29 is sent first to close a file a previous crashed run may
    have left open (otherwise harmless), then M115 must identify a Duet;
  * nothing else is ever sent over USB by this module.
"""
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None

DUET_VID_PID = {(0x1D50, 0x60EC), (0x1D50, 0x60ED)}  # Duet 2 WiFi/Ethernet, Duet 2 Maestro


def find_port():
    """COM port of a Duet, or None: by USB VID/PID, else a lone 'USB Serial Device'."""
    if list_ports is None:
        return None
    ports = list(list_ports.comports())
    for p in ports:
        if (p.vid, p.pid) in DUET_VID_PID or "duet" in (p.description or "").lower():
            return p.device
    generic = [p for p in ports if "usb serial" in (p.description or "").lower()]
    return generic[0].device if len(generic) == 1 else None


class DuetUSB:
    def __init__(self, port, baud=115200, log=None, timeout=0.2, ser=None):
        self.port = port
        self.log = log or (lambda m: None)
        self._writing = None
        if ser is not None:
            self.ser = ser  # injected (tests)
        else:
            if serial is None:
                raise RuntimeError("pyserial is not installed (pip install pyserial)")
            self.ser = serial.Serial(port, baud, timeout=timeout, write_timeout=120)
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        self.firmware = self._handshake()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- low level -------------------------------------------------------

    def _collect(self, seconds):
        buf, t0 = b"", time.time()
        while time.time() - t0 < seconds:
            buf += self.ser.read(4096)
        return buf.decode("utf-8", "replace")

    def _wait(self, needles, timeout):
        """Read until one of `needles` (bytes) appears; return (needle, text)."""
        buf, t0 = b"", time.time()
        while time.time() - t0 < timeout:
            buf += self.ser.read(4096)
            for n in needles:
                if n in buf:
                    return n, buf.decode("utf-8", "replace")
        raise TimeoutError(f"usb {self.port}: none of {needles!r} within {timeout}s (got {buf[-120:]!r})")

    def _handshake(self):
        self.ser.write(b"M29\n")  # close a file left open by an earlier crash; "ok" otherwise
        self._collect(1.0)
        self.ser.write(b"M115\n")
        _, reply = self._wait([b"ok"], 5)
        if "RepRapFirmware" not in reply:
            self.close()
            raise RuntimeError(f"{self.port} did not answer M115 like a Duet: {reply[:120]!r}")
        first = next((ln for ln in reply.splitlines() if "FIRMWARE" in ln), reply.splitlines()[0])
        self.log(f"usb {self.port}: {first[:90]}")
        return first

    # ---- public ----------------------------------------------------------

    def command(self, line, timeout=10):
        """Send one command and return its reply text (up to and excluding 'ok')."""
        self.ser.reset_input_buffer()
        self.ser.write((line.strip() + "\n").encode())
        _, reply = self._wait([b"ok"], timeout)
        return reply.rsplit("ok", 1)[0].strip()

    def write_file(self, remote, data, progress=None, piece=4096):
        """Write `data` (bytes, newline-terminated lines) to `remote` with M28/M29.

        Returns the seconds taken.  Raises before streaming if M28 is not
        confirmed; raises after M29 if the Duet does not report the save.
        """
        if not data.endswith(b"\n"):
            data += b"\n"
        self.ser.reset_input_buffer()
        self.ser.write(f'M28 "{remote}"\n'.encode())
        hit, reply = self._wait([b"Writing to file", b"Can't open", b"rror"], 5)
        if hit != b"Writing to file":
            raise RuntimeError(f"M28 {remote} refused: {reply.strip()[:160]!r}; nothing written")
        self._writing = remote
        t0 = time.time()
        sent = 0
        for i in range(0, len(data), piece):
            chunk = data[i:i + piece]
            self.ser.write(chunk)
            sent += len(chunk)
            if progress and (i // piece) % 32 == 0:
                progress(sent, len(data), time.time() - t0)
        self.ser.flush()
        self.ser.write(b"M29\n")
        self._wait([b"Done saving file"], max(30.0, len(data) / 20000))
        self._writing = None
        dt = time.time() - t0
        if progress:
            progress(len(data), len(data), dt)
        self.log(f"usb: wrote {len(data)} B to {remote} in {dt:.2f} s")
        return dt

    def close(self):
        try:
            if self._writing:
                self.ser.write(b"M29\n")
                self._collect(1.0)
                self._writing = None
        finally:
            self.ser.close()
