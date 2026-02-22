cat > README.md <<'EOF'
# robobloq-led-linux

Control ROBOBLOQ / QinHeng USBHID ambient LED strips (VID:PID `1a86:fe07`) on Linux (Ubuntu).

## What works
- Solid color control via HID vendor interface.

## Requirements
- Linux with `/dev/hidraw*`
- Device enumerates as `1a86:fe07`
- Python 3.10+

## Setup (recommended)
Create a udev rule so you don't need `sudo`:

```bash
sudo tee /etc/udev/rules.d/99-robobloq-led.rules >/dev/null <<'EOF'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="fe07", MODE="0666"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
# Unplug/replug the LED