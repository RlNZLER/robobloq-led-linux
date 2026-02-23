# robobloq-led-linux

Control a ROBOBLOQ / QinHeng USB HID ambient LED strip (VID:PID **1a86:fe07**) on Linux (Ubuntu).  
Includes a simple **web UI** (color picker) and a small **CLI**.

> This project was built by reverse-engineering the device protocol via USB capture and implementing native Linux HID control.

---

## Features

- Set solid colors (RGB)
- Works without the vendor Windows app
- Web UI (FastAPI + simple HTML color picker)
- Auto-detects the correct vendor HID interface (`06 00 ff` report descriptor)
- Udev rule support (run without `sudo`)

---

## Supported device

This project targets devices that show up as:

- `lsusb` -> `ID 1a86:fe07 QinHeng Electronics USBHID`
- HID vendor interface report descriptor begins with: `06 00 ff`

---

## Setup

### 1) Udev rule (recommended)

Create a udev rule so you can access the LED device without `sudo`:

```bash
sudo tee /etc/udev/rules.d/99-robobloq-led.rules >/dev/null <<'EOF'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="fe07", MODE="0666"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
# Unplug/replug the LED strip
```

Verify permissions:

```bash
ls -l /dev/hidraw*
```

You should see the matching device nodes as `crw-rw-rw- ...`.

---

## Installation (virtual environment)

```bash
cd robobloq-led-linux
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# install package in editable mode (recommended for development)
pip install -e .
```

---

## CLI usage

Set a color:

```bash
robobloq-led --r 255 --g 0 --b 0     # red
robobloq-led --r 0 --g 255 --b 0     # green
robobloq-led --r 0 --g 0 --b 255     # blue
robobloq-led --r 0 --g 0 --b 0       # off
robobloq-led --r 255 --g 200 --b 120 # warm white
```

If auto-detect ever fails (after unplug/replug), you can pass the device path explicitly:

```bash
robobloq-led --dev /dev/hidraw1 --r 255 --g 255 --b 255
```

---

## Web UI

### Install web dependencies

```bash
pip install -r requirements.txt
```

### Run locally

```bash
uvicorn robobloq_led.webapp:app --reload
```

Open:

- http://127.0.0.1:8000

### Run on LAN (open from phone)

```bash
uvicorn robobloq_led.webapp:app --host 0.0.0.0 --port 8000 --reload
```

Find your PC IP:

```bash
hostname -I
```

Then open on your phone (same Wi-Fi):

- http://<YOUR_IP>:8000

---

## How it works (high level)

- The LED strip exposes two HID interfaces:
  - a keyboard-like HID interface (ignore)
  - a vendor-defined HID interface (descriptor starts with `06 00 ff`)
- We send 64-byte reports to the vendor interface.
- The device expects a checksum byte. This implementation recomputes the checksum on every color set.

---

## Troubleshooting

### Permission denied: /dev/hidrawX
- Apply the udev rule above and unplug/replug the device.

### Colors work once, then stop / some colors ignored
- This is usually a checksum/counter issue. The current implementation updates checksum and increments the counter.

### Device path changes after replug
- Normal. The project auto-detects the correct vendor interface by reading the report descriptor.
- If needed, specify `--dev /dev/hidrawX`.

### Confirm device is detected
```bash
lsusb | grep -i 1a86
```

---

## Screen Sync (Ambilight-style)

Supports:
- External monitor capture (X11)
- Real-time ambient color sync
- Adjustable FPS and edge thickness

Example:
```
python -m robobloq_led.screen_sync --monitor 2 --fps 40
```

## Roadmap

- [ ] Presets (movie / gaming / focus)
- [ ] Brightness slider (software scaling)
- [ ] Smooth fade transitions
- [ ] Effects (rainbow, pulse)
- [ ] Screen-sync mode (Ambilight-style)
- [ ] Package releases + GitHub Actions CI

---

## License

MIT