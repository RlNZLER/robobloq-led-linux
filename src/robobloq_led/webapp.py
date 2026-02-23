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

HTML_PAGE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Robobloq LED</title>
  <style>
    :root{
      --bg: #0b0f17;
      --card: rgba(255,255,255,0.06);
      --card2: rgba(255,255,255,0.08);
      --border: rgba(255,255,255,0.10);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.60);
      --muted2: rgba(255,255,255,0.45);
      --shadow: 0 18px 60px rgba(0,0,0,0.45);
      --radius: 18px;
      --radius2: 14px;
      --pad: 16px;
      --accent: #6ae4ff;
      --good: #48e39b;
      --warn: #ffcc66;
      --bad: #ff5b6e;
    }

    @media (prefers-color-scheme: light) {
      :root{
        --bg: #f6f7fb;
        --card: rgba(0,0,0,0.04);
        --card2: rgba(0,0,0,0.06);
        --border: rgba(0,0,0,0.10);
        --text: rgba(0,0,0,0.88);
        --muted: rgba(0,0,0,0.60);
        --muted2: rgba(0,0,0,0.45);
        --shadow: 0 18px 60px rgba(0,0,0,0.12);
        --accent: #0ea5e9;
        --good: #16a34a;
        --warn: #f59e0b;
        --bad: #e11d48;
      }
    }

    * { box-sizing: border-box; }
    body{
      margin:0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Apple Color Emoji","Segoe UI Emoji";
      background:
        radial-gradient(1200px 600px at 20% 0%, rgba(106,228,255,0.20), transparent 60%),
        radial-gradient(900px 500px at 80% 10%, rgba(255,204,102,0.16), transparent 62%),
        radial-gradient(900px 700px at 55% 100%, rgba(72,227,155,0.14), transparent 60%),
        var(--bg);
      color: var(--text);
    }

    .wrap{
      max-width: 980px;
      margin: 28px auto;
      padding: 0 16px 40px;
    }

    .topbar{
      display:flex;
      gap: 14px;
      align-items:center;
      justify-content: space-between;
      margin-bottom: 16px;
    }

    .brand{
      display:flex;
      gap: 12px;
      align-items:center;
    }

    .logo{
      width: 42px;
      height: 42px;
      border-radius: 14px;
      background: linear-gradient(135deg, rgba(106,228,255,0.9), rgba(255,204,102,0.8));
      box-shadow: 0 12px 30px rgba(106,228,255,0.25);
    }

    h1{
      font-size: 18px;
      margin: 0;
      letter-spacing: 0.2px;
    }
    .subtitle{
      margin: 2px 0 0;
      font-size: 13px;
      color: var(--muted);
    }

    .pill{
      font-size: 12px;
      color: var(--muted);
      border: 1px solid var(--border);
      background: var(--card);
      padding: 8px 10px;
      border-radius: 999px;
      backdrop-filter: blur(10px);
      display:flex;
      gap:8px;
      align-items:center;
    }

    .grid{
      display:grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 14px;
    }

    @media (max-width: 900px){
      .grid{ grid-template-columns: 1fr; }
    }

    .card{
      border: 1px solid var(--border);
      background: var(--card);
      border-radius: var(--radius);
      padding: 16px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }

    .card h2{
      margin:0 0 10px 0;
      font-size: 14px;
      color: var(--muted);
      font-weight: 600;
      letter-spacing: 0.3px;
      text-transform: uppercase;
    }

    .row{
      display:flex;
      gap: 10px;
      align-items:center;
      flex-wrap: wrap;
    }

    .spacer{ height: 12px; }

    .btn{
      border: 1px solid var(--border);
      background: var(--card2);
      color: var(--text);
      padding: 10px 12px;
      border-radius: 12px;
      cursor:pointer;
      transition: transform 120ms ease, background 120ms ease, border 120ms ease;
      user-select:none;
    }
    .btn:hover{ transform: translateY(-1px); }
    .btn:active{ transform: translateY(0px) scale(0.99); }

    .btn.primary{
      border-color: rgba(106,228,255,0.35);
      background: linear-gradient(135deg, rgba(106,228,255,0.18), rgba(106,228,255,0.06));
    }
    .btn.danger{
      border-color: rgba(255,91,110,0.35);
      background: linear-gradient(135deg, rgba(255,91,110,0.16), rgba(255,91,110,0.06));
    }
    .btn.ghost{
      background: transparent;
    }

    .picker{
      display:flex;
      align-items:center;
      gap: 10px;
      padding: 10px;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: var(--card2);
    }
    input[type="color"]{
      width: 54px;
      height: 44px;
      border: none;
      padding:0;
      background:none;
      cursor:pointer;
    }
    .kv{
      display:flex;
      flex-direction: column;
      gap: 2px;
    }
    .kv .k{ font-size: 12px; color: var(--muted); }
    .kv .v{ font-size: 13px; color: var(--text); }

    .slider{
      width: 260px;
      accent-color: var(--accent);
    }
    .label{
      font-size: 13px;
      color: var(--muted);
      min-width: 88px;
    }
    .value{
      font-size: 13px;
      color: var(--text);
      min-width: 64px;
    }

    .preview{
      height: 240px;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      background: #111;
      position: relative;
      overflow:hidden;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04);
      transition: background 120ms linear;
    }

    .glow{
      position:absolute;
      inset:-80px;
      background: radial-gradient(circle at 50% 40%, rgba(255,255,255,0.22), transparent 55%);
      mix-blend-mode: overlay;
      pointer-events:none;
    }

    .preview-meta{
      position:absolute;
      left: 14px;
      bottom: 14px;
      display:flex;
      gap: 10px;
      align-items: center;
    }

    .chip{
      border: 1px solid var(--border);
      background: rgba(0,0,0,0.18);
      padding: 7px 10px;
      border-radius: 999px;
      font-size: 12px;
      color: var(--muted);
      backdrop-filter: blur(10px);
    }

    @media (prefers-color-scheme: light){
      .chip{ background: rgba(255,255,255,0.55); }
    }

    .presets{
      display:flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    .preset{
      display:flex;
      align-items:center;
      gap: 8px;
      padding: 9px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--card2);
      cursor:pointer;
      transition: transform 120ms ease, background 120ms ease, border 120ms ease;
      font-size: 13px;
    }
    .preset:hover{ transform: translateY(-1px); }
    .preset:active{ transform: translateY(0px) scale(0.99); }
    .dot{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      border: 1px solid rgba(255,255,255,0.22);
      box-shadow: 0 0 0 3px rgba(255,255,255,0.04) inset;
      flex: none;
    }

    .toast{
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,0.18);
      color: var(--muted);
      display:flex;
      justify-content: space-between;
      align-items:center;
      gap: 10px;
      backdrop-filter: blur(10px);
      min-height: 44px;
    }
    @media (prefers-color-scheme: light){
      .toast{ background: rgba(255,255,255,0.55); }
    }
    .toast strong{ color: var(--text); font-weight: 600; }
    .hint{ font-size: 12px; color: var(--muted2); margin-top: 10px; line-height: 1.35; }
    code{ background: rgba(255,255,255,0.10); padding: 2px 6px; border-radius: 8px; }
    @media (prefers-color-scheme: light){
      code{ background: rgba(0,0,0,0.08); }
    }
  </style>
</head>

<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand">
        <div class="logo"></div>
        <div>
          <h1>Robobloq LED</h1>
          <div class="subtitle">Web controller for ROBOBLOQ USB HID (1a86:fe07)</div>
        </div>
      </div>
      <div class="pill" id="pill">
        <span>●</span>
        <span id="conn">Ready</span>
      </div>
    </div>

    <div class="grid">
      <!-- Left: Controls -->
      <div class="card">
        <h2>Controls</h2>

        <div class="row">
          <div class="picker">
            <input id="picker" type="color" value="#ffc878" oninput="updatePreview()" />
            <div class="kv">
              <div class="k">Selected</div>
              <div class="v" id="hexLabel">#FFC878</div>
            </div>
          </div>

          <button class="btn primary" onclick="applyInstant()">Apply</button>
          <button class="btn" onclick="applyFade()">Fade</button>
          <button class="btn danger" onclick="turnOff()">Off</button>
          <button class="btn ghost" onclick="stopFade()">Stop</button>
        </div>

        <div class="spacer"></div>

        <div class="row">
          <div class="label">Brightness</div>
          <input class="slider" id="bright" type="range" min="0" max="100" value="100" oninput="updatePreview()">
          <div class="value" id="brightVal">100%</div>
        </div>

        <div class="spacer"></div>

        <div class="row">
          <div class="label">Fade time</div>
          <input class="slider" id="duration" type="range" min="0" max="3000" value="800" oninput="updatePreview()">
          <div class="value" id="durVal">800ms</div>
        </div>

        <div class="spacer"></div>

        <h2>Presets</h2>
        <div class="presets">
          <div class="preset" onclick="preset('#ffc878','Warm')"><span class="dot" style="background:#ffc878"></span>Warm</div>
          <div class="preset" onclick="preset('#d8ecff','Cool')"><span class="dot" style="background:#d8ecff"></span>Cool</div>
          <div class="preset" onclick="preset('#ffffff','White')"><span class="dot" style="background:#ffffff"></span>White</div>
          <div class="preset" onclick="preset('#ff3b30','Red')"><span class="dot" style="background:#ff3b30"></span>Red</div>
          <div class="preset" onclick="preset('#34c759','Green')"><span class="dot" style="background:#34c759"></span>Green</div>
          <div class="preset" onclick="preset('#0a84ff','Blue')"><span class="dot" style="background:#0a84ff"></span>Blue</div>
          <div class="preset" onclick="preset('#b400ff','Purple')"><span class="dot" style="background:#b400ff"></span>Purple</div>
          <div class="preset" onclick="preset('#000000','Off')"><span class="dot" style="background:#000000"></span>Off</div>
        </div>

        <div class="toast" id="toast">
          <div><strong>Status:</strong> <span id="status">Ready.</span></div>
          <div class="chip" id="chip">RGB: 255,200,120</div>
        </div>

        <div class="hint">
          Phone access: run with <code>--host 0.0.0.0</code> and open <code>http://&lt;PC_IP&gt;:8000</code>.<br>
          Tip: Set Fade to <code>0ms</code> for instant changes.
        </div>
      </div>

      <!-- Right: Preview -->
      <div class="card">
        <h2>Live preview</h2>
        <div class="preview" id="preview">
          <div class="glow" id="glow"></div>
          <div class="preview-meta">
            <div class="chip" id="metaHex">#FFC878</div>
            <div class="chip" id="metaB">Brightness 100%</div>
            <div class="chip" id="metaF">Fade 800ms</div>
          </div>
        </div>
      </div>
    </div>
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

function rgbToHex(r,g,b){
  const h = (x) => x.toString(16).padStart(2,"0");
  return "#" + h(r) + h(g) + h(b);
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
  setConn("Working…");
  const res = await fetch(url, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  const data = await res.json().catch(() => ({}));
  setConn(res.ok ? "Ready" : "Error");
  return {ok: res.ok, data};
}

function setConn(text){
  const el = document.getElementById("conn");
  el.textContent = text;
}

function setStatus(text){
  document.getElementById("status").textContent = text;
}

function updatePreview() {
  const hex = document.getElementById("picker").value.toUpperCase();
  const b = parseInt(document.getElementById("bright").value, 10);
  const d = parseInt(document.getElementById("duration").value, 10);

  document.getElementById("hexLabel").textContent = hex;
  document.getElementById("brightVal").textContent = `${b}%`;
  document.getElementById("durVal").textContent = `${d}ms`;

  // preview shows brightness-scaled color (what the LEDs will receive)
  const rgb = applyBrightness(hexToRgb(hex), b);
  const previewHex = rgbToHex(rgb.r, rgb.g, rgb.b).toUpperCase();

  const prev = document.getElementById("preview");
  prev.style.background = previewHex;

  // glow tint
  const glow = document.getElementById("glow");
  glow.style.background = `radial-gradient(circle at 50% 40%, ${previewHex}66, transparent 55%)`;

  document.getElementById("chip").textContent = `RGB: ${rgb.r},${rgb.g},${rgb.b}`;
  document.getElementById("metaHex").textContent = previewHex;
  document.getElementById("metaB").textContent = `Brightness ${b}%`;
  document.getElementById("metaF").textContent = `Fade ${d}ms`;
}

async function applyInstant() {
  const hex = document.getElementById("picker").value;
  const b = parseInt(document.getElementById("bright").value, 10);
  const rgb = hexToRgb(hex);

  const {ok, data} = await postJson("/api/color", {...rgb, brightness: b});
  setStatus(ok ? `Applied ${hex.toUpperCase()} @ ${b}%` : `Error: ${data.detail || "unknown"}`);
}

async function applyFade() {
  const hex = document.getElementById("picker").value;
  const b = parseInt(document.getElementById("bright").value, 10);
  const d = parseInt(document.getElementById("duration").value, 10);
  const rgb = hexToRgb(hex);

  if (d === 0) return applyInstant();

  const {ok, data} = await postJson("/api/fade", {...rgb, brightness: b, duration_ms: d, steps: 40});
  setStatus(ok ? `Fading to ${hex.toUpperCase()} @ ${b}% (${d}ms)` : `Error: ${data.detail || "unknown"}`);
}

async function turnOff() {
  const {ok, data} = await postJson("/api/off", {});
  setStatus(ok ? "Turned off." : `Error: ${data.detail || "unknown"}`);
}

async function stopFade() {
  const {ok, data} = await postJson("/api/stop", {});
  setStatus(ok ? "Stopped." : `Error: ${data.detail || "unknown"}`);
}

function preset(hex, name){
  document.getElementById("picker").value = hex;
  updatePreview();
  const d = parseInt(document.getElementById("duration").value, 10);
  if (hex.toLowerCase() === "#000000") return turnOff();
  setStatus(`Preset: ${name}`);
  return (d === 0) ? applyInstant() : applyFade();
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