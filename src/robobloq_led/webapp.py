from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import asyncio

from .device import RobobloqController, find_vendor_device

app = FastAPI(title="Robobloq LED Controller")

# Single controller instance so counter increments properly
_controller: RobobloqController | None = None
_current = {"r": 255, "g": 200, "b": 120}  # assume warm-white start
_fade_task: asyncio.Task | None = None
_lock = asyncio.Lock()

def get_controller() -> RobobloqController:
    global _controller
    if _controller is None:
        dev = find_vendor_device()
        _controller = RobobloqController(dev=dev)
    return _controller

class Color(BaseModel):
    r: int
    g: int
    b: int
    brightness: int | None = 100  # 0..100

class FadeRequest(BaseModel):
    r: int
    g: int
    b: int
    brightness: int | None = 100  # 0..100
    duration_ms: int = 800        # fade time
    steps: int = 40               # number of steps

def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v

def apply_brightness(r: int, g: int, b: int, brightness: int) -> tuple[int,int,int]:
    brightness = clamp(brightness, 0, 100)
    scale = brightness / 100.0
    return (int(r * scale), int(g * scale), int(b * scale))

def cancel_fade():
    global _fade_task
    if _fade_task and not _fade_task.done():
        _fade_task.cancel()
    _fade_task = None

async def fade_to(target: dict, duration_ms: int, steps: int):
    ctl = get_controller()
    duration_ms = clamp(duration_ms, 0, 60_000)
    steps = clamp(steps, 1, 300)

    async with _lock:
        start = dict(_current)

        for i in range(1, steps + 1):
            t = i / steps
            r = round(start["r"] + (target["r"] - start["r"]) * t)
            g = round(start["g"] + (target["g"] - start["g"]) * t)
            b = round(start["b"] + (target["b"] - start["b"]) * t)

            ctl.set_color(r, g, b)
            _current["r"], _current["g"], _current["b"] = r, g, b

            await asyncio.sleep(duration_ms / steps / 1000.0)

HTML_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Robobloq LED</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 640px; margin: 28px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 16px; padding: 16px; }
    .row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    button { padding: 10px 14px; border-radius: 10px; border: 1px solid #ccc; background: #f7f7f7; cursor: pointer; }
    button:active { transform: scale(0.99); }
    input[type="color"] { width: 72px; height: 46px; border: none; background: none; padding: 0; }
    input[type="range"] { width: 220px; }
    .status { margin-top: 10px; color: #333; min-height: 20px; }
    .preview {
      margin-top: 14px;
      height: 120px;
      border-radius: 16px;
      border: 1px solid #e6e6e6;
      box-shadow: 0 6px 18px rgba(0,0,0,0.06);
      transition: background 120ms linear;
    }
    .small { color:#666; font-size:14px; }
    code { background:#f2f2f2; padding:2px 6px; border-radius:6px; }
  </style>
</head>
<body>
  <h2>Robobloq LED Controller</h2>

  <div class="card">
    <div class="row" style="margin-top:12px;">
      <label>Presets</label>
      <button onclick="preset('#ffc878')">Warm</button>
      <button onclick="preset('#d8ecff')">Cool</button>
      <button onclick="preset('#ffffff')">White</button>
      <button onclick="preset('#ff0000')">Red</button>
      <button onclick="preset('#00ff00')">Green</button>
      <button onclick="preset('#0000ff')">Blue</button>
      <button onclick="preset('#b400ff')">Purple</button>
      <button onclick="preset('#000000')">Off</button>
    </div>
    <div class="row">
      <label>Color</label>
      <input id="picker" type="color" value="#ffc878" oninput="updatePreview()" />
      <button onclick="applyInstant()">Apply</button>
      <button onclick="applyFade()">Fade</button>
      <button onclick="turnOff()">Off</button>
      <button onclick="stopFade()">Stop</button>
    </div>

    <div class="row" style="margin-top:12px;">
      <label>Brightness</label>
      <input id="bright" type="range" min="0" max="100" value="100" oninput="updatePreview()">
      <span id="brightVal">100%</span>
    </div>

    <div class="row" style="margin-top:12px;">
      <label>Fade</label>
      <input id="duration" type="range" min="0" max="3000" value="800">
      <span id="durVal">800ms</span>
      <span class="small">(0 = instant)</span>
    </div>

    <div class="preview" id="preview"></div>
    <div class="status" id="status">Ready.</div>

    <p class="small" style="margin-top:14px;">
      Phone access: run with <code>--host 0.0.0.0</code> and open <code>http://&lt;PC_IP&gt;:8000</code>.
    </p>
  </div>

<script>
function hexToRgb(hex) {
  const v = hex.replace('#','');
  return {
    r: parseInt(v.substring(0,2), 16),
    g: parseInt(v.substring(2,4), 16),
    b: parseInt(v.substring(4,6), 16),
  };
}

function applyBrightness(rgb, brightness) {
  const s = brightness / 100.0;
  return {
    r: Math.round(rgb.r * s),
    g: Math.round(rgb.g * s),
    b: Math.round(rgb.b * s),
  };
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  const data = await res.json().catch(() => ({}));
  return {ok: res.ok, data};
}

function updatePreview() {
  const hex = document.getElementById("picker").value;
  const b = parseInt(document.getElementById("bright").value, 10);
  document.getElementById("brightVal").textContent = `${b}%`;

  const d = parseInt(document.getElementById("duration").value, 10);
  document.getElementById("durVal").textContent = `${d}ms`;

  // preview shows brightness-scaled color (what the LEDs will receive)
  const rgb = applyBrightness(hexToRgb(hex), b);
  const previewHex = "#" + [rgb.r,rgb.g,rgb.b].map(x => x.toString(16).padStart(2,"0")).join("");
  document.getElementById("preview").style.background = previewHex;
}

function preset(hex) {
  document.getElementById("picker").value = hex;
  updatePreview();
  // Use fade if duration > 0, else instant
  const d = parseInt(document.getElementById("duration").value, 10);
  if (hex === "#000000") return turnOff();
  return (d === 0) ? applyInstant() : applyFade();
}

async function applyInstant() {
  const hex = document.getElementById("picker").value;
  const b = parseInt(document.getElementById("bright").value, 10);
  const rgb = hexToRgb(hex);
  const {ok, data} = await postJson("/api/color", {...rgb, brightness: b});
  document.getElementById("status").textContent =
    ok ? `Applied ${hex} @ ${b}%` : `Error: ${data.detail || "unknown"}`;
}

async function applyFade() {
  const hex = document.getElementById("picker").value;
  const b = parseInt(document.getElementById("bright").value, 10);
  const d = parseInt(document.getElementById("duration").value, 10);
  const rgb = hexToRgb(hex);

  if (d === 0) return applyInstant();

  const {ok, data} = await postJson("/api/fade", {...rgb, brightness: b, duration_ms: d, steps: 40});
  document.getElementById("status").textContent =
    ok ? `Fading to ${hex} @ ${b}% (${d}ms)` : `Error: ${data.detail || "unknown"}`;
}

async function turnOff() {
  const {ok, data} = await postJson("/api/off", {});
  document.getElementById("status").textContent =
    ok ? "Turned off." : `Error: ${data.detail || "unknown"}`;
}

async function stopFade() {
  const {ok, data} = await postJson("/api/stop", {});
  document.getElementById("status").textContent =
    ok ? "Stopped." : `Error: ${data.detail || "unknown"}`;
}

updatePreview();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE

@app.post("/api/color")
def set_color_api(c: Color):
    ctl = get_controller()
    cancel_fade()

    brightness = clamp(c.brightness or 100, 0, 100)
    r, g, b = apply_brightness(clamp(c.r,0,255), clamp(c.g,0,255), clamp(c.b,0,255), brightness)

    ctl.set_color(r, g, b)
    _current["r"], _current["g"], _current["b"] = r, g, b
    return JSONResponse({"ok": True, "r": r, "g": g, "b": b, "brightness": brightness})

@app.post("/api/fade")
async def fade_api(req: FadeRequest):
    cancel_fade()

    brightness = clamp(req.brightness or 100, 0, 100)
    r, g, b = apply_brightness(clamp(req.r,0,255), clamp(req.g,0,255), clamp(req.b,0,255), brightness)
    target = {"r": r, "g": g, "b": b}

    global _fade_task
    _fade_task = asyncio.create_task(fade_to(target, req.duration_ms, req.steps))
    return JSONResponse({"ok": True, "target": target, "brightness": brightness, "duration_ms": req.duration_ms})

@app.post("/api/off")
def off_api():
    ctl = get_controller()
    cancel_fade()
    ctl.set_color(0, 0, 0)
    _current["r"], _current["g"], _current["b"] = 0, 0, 0
    return JSONResponse({"ok": True})

@app.post("/api/stop")
def stop_api():
    cancel_fade()
    return JSONResponse({"ok": True})