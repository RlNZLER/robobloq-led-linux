import os
import glob
import fcntl
from dataclasses import dataclass

# Your working BLUE capture (base packet). We modify counter+RGB+checksum.
BASE_HEX = (
    "5242100e86010000ff4142000000feb9"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
)

IOC_WRITE = 1
def _IOC(dir_, type_, nr, size):
    return (dir_ << 30) | (ord(type_) << 8) | (nr << 0) | (size << 16)

def HIDIOCSFEATURE(size):
    # Set FEATURE report via hidraw
    return _IOC(IOC_WRITE, "H", 0x06, size)

def _is_vendor_descriptor(hidraw_path: str) -> bool:
    # Vendor interface starts with: 06 00 ff
    sysname = os.path.basename(hidraw_path)
    desc_path = f"/sys/class/hidraw/{sysname}/device/report_descriptor"
    try:
        with open(desc_path, "rb") as f:
            return f.read(3) == bytes([0x06, 0x00, 0xFF])
    except FileNotFoundError:
        return False

def find_vendor_device() -> str:
    """Find the correct /dev/hidrawX node for the ROBOBLOQ vendor interface."""
    for dev in sorted(glob.glob("/dev/hidraw*")):
        if _is_vendor_descriptor(dev):
            return dev
    raise RuntimeError("Vendor HID interface not found. Unplug/replug the LED and try again.")

@dataclass
class RobobloqController:
    dev: str
    counter: int = 0x0E  # start from known working value

    def _build_report(self, r: int, g: int, b: int) -> bytes:
        pkt = bytearray(bytes.fromhex(BASE_HEX))
        if len(pkt) != 64:
            raise ValueError("BASE_HEX must be 64 bytes")

        pkt[3] = self.counter & 0xFF
        pkt[6] = int(r) & 0xFF
        pkt[7] = int(g) & 0xFF
        pkt[8] = int(b) & 0xFF

        # checksum byte = sum(bytes[0..14]) & 0xFF
        pkt[15] = sum(pkt[0:15]) & 0xFF

        return bytes(pkt)

    def _send_feature(self, report64: bytes) -> None:
        buf = bytearray(report64)
        fd = os.open(self.dev, os.O_RDWR)
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE(64), buf, True)
        finally:
            os.close(fd)

    def _send_raw(self, report64: bytes) -> None:
        with open(self.dev, "wb", buffering=0) as f:
            f.write(report64)

    def set_color(self, r: int, g: int, b: int) -> None:
        report = self._build_report(r, g, b)
        # raw write works on your machine; feature is a safe fallback
        try:
            self._send_raw(report)
        except OSError:
            self._send_feature(report)
        self.counter = (self.counter + 1) & 0xFF

def set_color(r: int, g: int, b: int, dev: str | None = None) -> None:
    """Convenience function: set a solid color."""
    if dev is None:
        dev = find_vendor_device()
    RobobloqController(dev=dev).set_color(r, g, b)
