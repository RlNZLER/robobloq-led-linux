import mss
import time
import asyncio
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from .device import RobobloqController, find_vendor_device

app = FastAPI(title="Robobloq LED Controller")

# Single controller instance so counter increments properly
_controller: RobobloqController | None = None
_current = {"r": 255, "g": 200, "b": 120}  # assume warm-white start
_fade_task: asyncio.Task | None = None
_lock = asyncio.Lock()
_effect_task: asyncio.Task | None = None
_effect_stop = asyncio.Event()
_sync_task: asyncio.Task | None = None
_sync_stop = asyncio.Event()

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

class EffectRequest(BaseModel):
    effect: str              # "pulse" or "rainbow"
    speed: int = 50          # 1..100
    r: int | None = None     # pulse uses selected color (after brightness)
    g: int | None = None
    b: int | None = None
    brightness: int | None = 100

class SyncStartRequest(BaseModel):
    monitor: int = 2
    fps: int = 60
    thickness: int = 80
    downscale: int = 4          # sample every Nth pixel (1=no downscale)
    alpha: float = 0.35         # smoothing
    change_threshold: int = 6   # don't spam USB for tiny changes


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

def cancel_effect():
    global _effect_task
    if _effect_task and not _effect_task.done():
        _effect_stop.set()
        _effect_task.cancel()
    _effect_task = None
    _effect_stop.clear()

def cancel_sync():
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_stop.set()
        _sync_task.cancel()
    _sync_task = None
    _sync_stop.clear()

def wheel(pos: int) -> tuple[int, int, int]:
    # Classic rainbow wheel (0..255)
    pos = pos % 256
    if pos < 85:
        return (pos * 3, 255 - pos * 3, 0)
    if pos < 170:
        pos -= 85
        return (255 - pos * 3, 0, pos * 3)
    pos -= 170
    return (0, pos * 3, 255 - pos * 3)

async def effect_pulse(base: dict, speed: int):
    """
    Pulse between OFF and base color.
    speed: 1..100 (higher = faster)
    """
    ctl = get_controller()
    speed = clamp(speed, 1, 100)
    # period in seconds (fastest ~0.4s, slowest ~3.0s)
    period = 3.0 - (speed - 1) * (2.6 / 99.0)
    steps = 50
    dt = period / steps

    async with _lock:
        while not _effect_stop.is_set():
            # up
            for i in range(steps + 1):
                if _effect_stop.is_set(): return
                t = i / steps
                r = round(base["r"] * t)
                g = round(base["g"] * t)
                b = round(base["b"] * t)
                ctl.set_color(r, g, b)
                _current["r"], _current["g"], _current["b"] = r, g, b
                await asyncio.sleep(dt)
            # down
            for i in range(steps, -1, -1):
                if _effect_stop.is_set(): return
                t = i / steps
                r = round(base["r"] * t)
                g = round(base["g"] * t)
                b = round(base["b"] * t)
                ctl.set_color(r, g, b)
                _current["r"], _current["g"], _current["b"] = r, g, b
                await asyncio.sleep(dt)

async def effect_rainbow(speed: int):
    """
    Rainbow cycle.
    speed: 1..100 (higher = faster)
    """
    ctl = get_controller()
    speed = clamp(speed, 1, 100)
    # delay per step (fastest ~0.01s, slowest ~0.10s)
    delay = 0.10 - (speed - 1) * (0.09 / 99.0)

    async with _lock:
        j = 0
        while not _effect_stop.is_set():
            r, g, b = wheel(j)
            ctl.set_color(r, g, b)
            _current["r"], _current["g"], _current["b"] = r, g, b
            j = (j + 1) % 256
            await asyncio.sleep(delay)

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

async def screen_sync_loop(cfg: SyncStartRequest):
    ctl = get_controller()

    fps = clamp(cfg.fps, 1, 120)
    thickness = clamp(cfg.thickness, 1, 400)
    down = clamp(cfg.downscale, 1, 16)
    alpha = float(cfg.alpha)
    if alpha < 0.0: alpha = 0.0
    if alpha > 1.0: alpha = 1.0
    change_thr = clamp(cfg.change_threshold, 0, 255*3)

    dt = 1.0 / fps

    # State for smoothing + change threshold
    smooth = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    last_rgb = (-1, -1, -1)

    def avg_edge_color(img: np.ndarray, thickness_px: int):
        h, w, _ = img.shape
        t = max(1, min(thickness_px, w // 4, h // 4))

        left = img[:, :t, :]
        top = img[:t, :, :]
        right = img[:, w - t :, :]

        l = left.mean(axis=(0, 1))
        tcol = top.mean(axis=(0, 1))
        r = right.mean(axis=(0, 1))
        return l, tcol, r

    def combine_edges(l, tcol, r):
        # Weighted towards top (movie-friendly)
        return 0.25*l + 0.50*tcol + 0.25*r

    # IMPORTANT: mss is best created once per task
    with mss.mss() as sct:
        if cfg.monitor < 1 or cfg.monitor >= len(sct.monitors):
            raise ValueError(f"Invalid monitor index {cfg.monitor}. Available: 1..{len(sct.monitors)-1}")

        mon = sct.monitors[cfg.monitor]

        # Keep sync exclusive with other LED writers
        async with _lock:
            while not _sync_stop.is_set():
                start = time.time()

                frame = np.array(sct.grab(mon))[:, :, :3][:, :, ::-1]  # RGB
                img = frame[::down, ::down, :]  # downscale for speed

                l, tcol, r = avg_edge_color(img, thickness_px=thickness)
                target = combine_edges(l, tcol, r)

                # smoothing
                smooth = (1.0 - alpha) * smooth + alpha * target.astype(np.float32)
                rgb = tuple(np.clip(smooth, 0, 255).astype(int))

                # reduce USB spam
                if sum(abs(a - b) for a, b in zip(rgb, last_rgb)) > change_thr:
                    ctl.set_color(*rgb)
                    _current["r"], _current["g"], _current["b"] = rgb
                    last_rgb = rgb

                elapsed = time.time() - start
                sleep_for = dt - elapsed
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                else:
                    # if we’re slower than target FPS, yield control
                    await asyncio.sleep(0)


HTML_PAGE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Robobloq LED</title>
  <style>
    :root{
      --bg:#0b0f17;
      --card:rgba(255,255,255,.06);
      --card2:rgba(255,255,255,.10);
      --border:rgba(255,255,255,.12);
      --text:rgba(255,255,255,.92);
      --muted:rgba(255,255,255,.60);
      --shadow:0 18px 60px rgba(0,0,0,.45);
      --r:18px;
      --accent:#6ae4ff;
      --bad:#ff5b6e;
    }
    @media (prefers-color-scheme: light) {
      :root{
        --bg:#f6f7fb;
        --card:rgba(0,0,0,.04);
        --card2:rgba(0,0,0,.06);
        --border:rgba(0,0,0,.10);
        --text:rgba(0,0,0,.88);
        --muted:rgba(0,0,0,.60);
        --shadow:0 18px 60px rgba(0,0,0,.12);
        --accent:#0ea5e9;
        --bad:#e11d48;
      }
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
      color:var(--text);
      background:
        radial-gradient(1200px 600px at 20% 0%, rgba(106,228,255,0.20), transparent 60%),
        radial-gradient(900px 500px at 80% 10%, rgba(255,204,102,0.16), transparent 62%),
        radial-gradient(900px 700px at 55% 100%, rgba(72,227,155,0.14), transparent 60%),
        var(--bg);
    }
    .wrap{max-width:760px;margin:26px auto;padding:0 16px 34px}
    .top{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:14px}
    .brand{display:flex;gap:12px;align-items:center}
    .logo{width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg, rgba(106,228,255,.9), rgba(255,204,102,.8));box-shadow:0 12px 30px rgba(106,228,255,.25)}
    h1{margin:0;font-size:18px}
    .sub{margin:2px 0 0;font-size:13px;color:var(--muted)}
    .pill{font-size:12px;color:var(--muted);border:1px solid var(--border);background:var(--card);padding:8px 10px;border-radius:999px;backdrop-filter:blur(10px)}
    .card{border:1px solid var(--border);background:var(--card);border-radius:var(--r);padding:16px;box-shadow:var(--shadow);backdrop-filter:blur(12px)}
    .tabs{display:flex;gap:8px;margin-bottom:12px}
    .tab{padding:10px 12px;border-radius:12px;border:1px solid var(--border);background:var(--card2);cursor:pointer;user-select:none}
    .tab.active{border-color:rgba(106,228,255,.35);background:linear-gradient(135deg, rgba(106,228,255,.18), rgba(106,228,255,.06))}
    .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
    .btn{border:1px solid var(--border);background:var(--card2);color:var(--text);padding:10px 12px;border-radius:12px;cursor:pointer}
    .btn.primary{border-color:rgba(106,228,255,.35);background:linear-gradient(135deg, rgba(106,228,255,.18), rgba(106,228,255,.06))}
    .btn.danger{border-color:rgba(255,91,110,.35);background:linear-gradient(135deg, rgba(255,91,110,.16), rgba(255,91,110,.06))}
    .picker{display:flex;align-items:center;gap:10px;padding:10px;border-radius:14px;border:1px solid var(--border);background:var(--card2)}
    input[type=color]{width:54px;height:44px;border:none;background:none;padding:0;cursor:pointer}
    .label{min-width:92px;font-size:13px;color:var(--muted)}
    .value{min-width:70px;font-size:13px}
    input[type=range]{width:280px;accent-color:var(--accent)}
    .presets{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
    .preset{display:flex;align-items:center;gap:8px;padding:9px 10px;border-radius:999px;border:1px solid var(--border);background:var(--card2);cursor:pointer;font-size:13px}
    .dot{width:12px;height:12px;border-radius:50%;border:1px solid rgba(255,255,255,.22);flex:none}
    .toast{margin-top:12px;padding:10px 12px;border-radius:14px;border:1px solid var(--border);background:rgba(0,0,0,.18);color:var(--muted);min-height:44px;display:flex;align-items:center;justify-content:space-between;gap:10px;backdrop-filter:blur(10px)}
    @media (prefers-color-scheme: light){ .toast{background:rgba(255,255,255,.55)} }
    code{background:rgba(255,255,255,.10);padding:2px 6px;border-radius:8px}
    @media (prefers-color-scheme: light){ code{background:rgba(0,0,0,.08)} }
    .hint{margin-top:10px;font-size:12px;color:rgba(255,255,255,.45);line-height:1.35}
    @media (prefers-color-scheme: light){ .hint{color:rgba(0,0,0,.45)} }
    .hidden{display:none}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo"></div>
        <div>
          <h1>Robobloq LED</h1>
          <div class="sub">Linux web controller (1a86:fe07)</div>
        </div>
      </div>
      <div class="pill" id="conn">Ready</div>
    </div>

    <div class="card">
      <div class="tabs">
        <div class="tab active" id="tab-colors" onclick="showTab('colors')">Colors</div>
        <div class="tab" id="tab-effects" onclick="showTab('effects')">Effects</div>
        <div class="tab" id="tab-sync" onclick="showTab('sync')">Sync</div>
      </div>

      <!-- Colors tab -->
      <div id="panel-colors">
        <div class="row">
          <div class="picker">
            <input id="picker" type="color" value="#ffc878" oninput="syncLabels()" />
            <div>
              <div class="label">Selected</div>
              <div class="value" id="hexLabel">#FFC878</div>
            </div>
          </div>
          <button class="btn primary" onclick="applyInstant()">Apply</button>
          <button class="btn" onclick="applyFade()">Fade</button>
          <button class="btn danger" onclick="turnOff()">Off</button>
          <button class="btn" onclick="stopAll()">Stop</button>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Brightness</div>
          <input id="bright" type="range" min="0" max="100" value="100" oninput="syncLabels()">
          <div class="value" id="brightVal">100%</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Fade time</div>
          <input id="duration" type="range" min="0" max="3000" value="800" oninput="syncLabels()">
          <div class="value" id="durVal">800ms</div>
        </div>

        <div style="height:12px"></div>

        <div class="presets">
          <div class="preset" onclick="preset('#ffc878','Warm')"><span class="dot" style="background:#ffc878"></span>Warm</div>
          <div class="preset" onclick="preset('#d8ecff','Cool')"><span class="dot" style="background:#d8ecff"></span>Cool</div>
          <div class="preset" onclick="preset('#ffffff','White')"><span class="dot" style="background:#ffffff"></span>White</div>
          <div class="preset" onclick="preset('#ff3b30','Red')"><span class="dot" style="background:#ff3b30"></span>Red</div>
          <div class="preset" onclick="preset('#34c759','Green')"><span class="dot" style="background:#34c759"></span>Green</div>
          <div class="preset" onclick="preset('#0a84ff','Blue')"><span class="dot" style="background:#0a84ff"></span>Blue</div>
          <div class="preset" onclick="preset('#b400ff','Purple')"><span class="dot" style="background:#b400ff"></span>Purple</div>
        </div>
      </div>

      <!-- Effects tab -->
      <div id="panel-effects" class="hidden">
        <div class="row">
          <div class="label">Effect</div>
          <div class="preset" onclick="selectEffect('pulse')" id="eff-pulse"><span class="dot" style="background:#ffc878"></span>Pulse</div>
          <div class="preset" onclick="selectEffect('rainbow')" id="eff-rainbow"><span class="dot" style="background:#0a84ff"></span>Rainbow</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Speed</div>
          <input id="speed" type="range" min="1" max="100" value="50" oninput="syncLabels()">
          <div class="value" id="speedVal">50</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <button class="btn primary" onclick="startEffect()">Start</button>
          <button class="btn" onclick="stopAll()">Stop</button>
          <button class="btn danger" onclick="turnOff()">Off</button>
          <div class="hint">Pulse uses the selected color (from Colors tab) + brightness.</div>
        </div>
      </div>

      <!-- Sync tab -->
      <div id="panel-sync" class="hidden">
        <div class="row">
          <div class="label">Monitor</div>
          <input id="syncMonitor" type="number" min="1" value="2" style="width:90px">
          <div class="label">FPS</div>
          <input id="syncFps" type="range" min="5" max="90" value="60" oninput="syncLabels()">
          <div class="value" id="syncFpsVal">60</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Thickness</div>
          <input id="syncThickness" type="range" min="10" max="250" value="80" oninput="syncLabels()">
          <div class="value" id="syncThicknessVal">80px</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Downscale</div>
          <input id="syncDown" type="range" min="1" max="10" value="4" oninput="syncLabels()">
          <div class="value" id="syncDownVal">4x</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Smoothing</div>
          <input id="syncAlpha" type="range" min="0" max="100" value="35" oninput="syncLabels()">
          <div class="value" id="syncAlphaVal">0.35</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Change threshold</div>
          <input id="syncThr" type="range" min="0" max="60" value="6" oninput="syncLabels()">
          <div class="value" id="syncThrVal">6</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <button class="btn primary" onclick="startSync()">Start Sync</button>
          <button class="btn" onclick="stopSync()">Stop Sync</button>
          <button class="btn" onclick="stopAll()">Stop All</button>
          <button class="btn danger" onclick="turnOff()">Off</button>
        </div>

        <div class="hint">
          Use <code>Monitor</code> = the MSS index that matches your external display (you found it was 2).<br>
          Downscale 4x is a good default. Lower downscale = better quality but more CPU.
        </div>
      </div>

      <div class="toast">
        <div><strong>Status:</strong> <span id="status">Ready.</span></div>
        <div><span id="mini">Brightness 100% • Fade 800ms • Speed 50</span></div>
      </div>

      <div class="hint">
        Phone access: run with <code>--host 0.0.0.0</code> and open <code>http://&lt;PC_IP&gt;:8000</code>.<br>
        Tip: Use <code>Stop</code> if an effect is running and you want manual control.
      </div>
    </div>
  </div>
  

<script>
let selectedEffect = "pulse";

function setConn(text){ document.getElementById("conn").textContent = text; }
function setStatus(text){ document.getElementById("status").textContent = text; }

function showTab(name){
  document.getElementById("panel-colors").classList.toggle("hidden", name !== "colors");
  document.getElementById("panel-effects").classList.toggle("hidden", name !== "effects");
  document.getElementById("panel-sync").classList.toggle("hidden", name !== "sync");

  document.getElementById("tab-colors").classList.toggle("active", name === "colors");
  document.getElementById("tab-effects").classList.toggle("active", name === "effects");
  document.getElementById("tab-sync").classList.toggle("active", name === "sync");
}

function hexToRgb(hex) {
  const v = hex.replace('#','');
  return { r: parseInt(v.substring(0,2), 16), g: parseInt(v.substring(2,4), 16), b: parseInt(v.substring(4,6), 16) };
}

async function postJson(url, body) {
  setConn("Working…");
  const res = await fetch(url, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  setConn(res.ok ? "Ready" : "Error");
  return {ok: res.ok, data};
}

function syncLabels(){
  const hex = document.getElementById("picker").value.toUpperCase();
  const b = parseInt(document.getElementById("bright").value, 10);
  const d = parseInt(document.getElementById("duration").value, 10);
  const s = parseInt(document.getElementById("speed").value, 10);

  document.getElementById("hexLabel").textContent = hex;
  document.getElementById("brightVal").textContent = `${b}%`;
  document.getElementById("durVal").textContent = `${d}ms`;
  document.getElementById("speedVal").textContent = `${s}`;
  document.getElementById("mini").textContent = `Brightness ${b}% • Fade ${d}ms • Speed ${s}`;
  
  const sfps = parseInt(document.getElementById("syncFps").value, 10);
  const sth = parseInt(document.getElementById("syncThickness").value, 10);
  const sdown = parseInt(document.getElementById("syncDown").value, 10);
  const salpha = parseInt(document.getElementById("syncAlpha").value, 10);
  const sthr = parseInt(document.getElementById("syncThr").value, 10);

  document.getElementById("syncFpsVal").textContent = `${sfps}`;
  document.getElementById("syncThicknessVal").textContent = `${sth}px`;
  document.getElementById("syncDownVal").textContent = `${sdown}x`;
  document.getElementById("syncAlphaVal").textContent = `${(salpha/100).toFixed(2)}`;
  document.getElementById("syncThrVal").textContent = `${sthr}`;
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

async function stopAll(){
  // stops fade + stops effects
  await postJson("/api/stop", {});
  await postJson("/api/effect/stop", {});
  setStatus("Stopped.");
}

async function startSync(){
  const monitor = parseInt(document.getElementById("syncMonitor").value, 10);
  const fps = parseInt(document.getElementById("syncFps").value, 10);
  const thickness = parseInt(document.getElementById("syncThickness").value, 10);
  const downscale = parseInt(document.getElementById("syncDown").value, 10);
  const alpha = parseInt(document.getElementById("syncAlpha").value, 10) / 100.0;
  const change_threshold = parseInt(document.getElementById("syncThr").value, 10);

  const payload = { monitor, fps, thickness, downscale, alpha, change_threshold };

  const {ok, data} = await postJson("/api/sync/start", payload);
  setStatus(ok ? `Sync running (Monitor #${monitor} • ${fps} FPS)` : `Error: ${data.detail || "unknown"}`);
}

async function stopSync(){
  const {ok, data} = await postJson("/api/sync/stop", {});
  setStatus(ok ? "Sync stopped." : `Error: ${data.detail || "unknown"}`);
}

function preset(hex, name){
  document.getElementById("picker").value = hex;
  syncLabels();
  const d = parseInt(document.getElementById("duration").value, 10);
  setStatus(`Preset: ${name}`);
  return (d === 0) ? applyInstant() : applyFade();
}

function selectEffect(name){
  selectedEffect = name;
  document.getElementById("eff-pulse").style.borderColor = (name === "pulse") ? "rgba(106,228,255,.35)" : "";
  document.getElementById("eff-rainbow").style.borderColor = (name === "rainbow") ? "rgba(106,228,255,.35)" : "";
  setStatus(`Effect selected: ${name}`);
}

async function startEffect(){
  const s = parseInt(document.getElementById("speed").value, 10);
  const b = parseInt(document.getElementById("bright").value, 10);
  const hex = document.getElementById("picker").value;
  const rgb = hexToRgb(hex);

  let payload = { effect: selectedEffect, speed: s };

  // Pulse uses chosen color + brightness
  if (selectedEffect === "pulse"){
    payload = { ...payload, ...rgb, brightness: b };
  }

  const {ok, data} = await postJson("/api/effect/start", payload);
  setStatus(ok ? `Effect running: ${selectedEffect} (speed ${s})` : `Error: ${data.detail || "unknown"}`);
}

syncLabels();
selectEffect("pulse");
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
    cancel_sync()
    cancel_effect()

    brightness = clamp(c.brightness or 100, 0, 100)
    r, g, b = apply_brightness(clamp(c.r,0,255), clamp(c.g,0,255), clamp(c.b,0,255), brightness)

    ctl.set_color(r, g, b)
    _current["r"], _current["g"], _current["b"] = r, g, b
    return JSONResponse({"ok": True, "r": r, "g": g, "b": b, "brightness": brightness})

@app.post("/api/fade")
async def fade_api(req: FadeRequest):
    cancel_fade()
    cancel_sync()
    cancel_effect()

    brightness = clamp(req.brightness or 100, 0, 100)
    r, g, b = apply_brightness(clamp(req.r,0,255), clamp(req.g,0,255), clamp(req.b,0,255), brightness)
    target = {"r": r, "g": g, "b": b}

    global _fade_task
    _fade_task = asyncio.create_task(fade_to(target, req.duration_ms, req.steps))
    return JSONResponse({"ok": True, "target": target, "brightness": brightness, "duration_ms": req.duration_ms})

@app.post("/api/effect/start")
async def effect_start(req: EffectRequest):
    cancel_fade()
    cancel_sync()
    cancel_effect()

    eff = (req.effect or "").lower().strip()
    speed = clamp(req.speed, 1, 100)

    global _effect_task
    if eff == "pulse":
        # Use chosen color (with brightness)
        if req.r is None or req.g is None or req.b is None:
            raise HTTPException(status_code=400, detail="pulse requires r,g,b")
        brightness = clamp(req.brightness or 100, 0, 100)
        r, g, b = apply_brightness(clamp(req.r,0,255), clamp(req.g,0,255), clamp(req.b,0,255), brightness)
        base = {"r": r, "g": g, "b": b}
        _effect_task = asyncio.create_task(effect_pulse(base, speed))
        return JSONResponse({"ok": True, "effect": "pulse", "base": base, "speed": speed})

    elif eff == "rainbow":
        _effect_task = asyncio.create_task(effect_rainbow(speed))
        return JSONResponse({"ok": True, "effect": "rainbow", "speed": speed})

    raise HTTPException(status_code=400, detail="Unknown effect. Use 'pulse' or 'rainbow'.")

@app.post("/api/sync/start")
async def sync_start(req: SyncStartRequest):
    # Stop other modes
    cancel_fade()
    cancel_effect()
    cancel_sync()

    global _sync_task
    try:
        _sync_task = asyncio.create_task(screen_sync_loop(req))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse({
        "ok": True,
        "mode": "sync",
        "config": req.model_dump(),
    })

@app.post("/api/sync/stop")
def sync_stop():
    cancel_sync()
    return JSONResponse({"ok": True, "mode": "manual"})

@app.get("/api/status")
def status():
    mode = "manual"
    if _sync_task and not _sync_task.done():
        mode = "sync"
    elif _effect_task and not _effect_task.done():
        mode = "effect"
    elif _fade_task and not _fade_task.done():
        mode = "fade"
    return JSONResponse({"ok": True, "mode": mode, "current": _current})

@app.post("/api/effect/stop")
def effect_stop():
    cancel_sync()
    cancel_effect()
    return JSONResponse({"ok": True})

@app.post("/api/off")
def off_api():
    ctl = get_controller()
    cancel_fade()
    cancel_sync()
    cancel_effect()
    ctl.set_color(0, 0, 0)
    _current["r"], _current["g"], _current["b"] = 0, 0, 0
    return JSONResponse({"ok": True})

@app.post("/api/stop")
def stop_api():
    cancel_fade()
    cancel_sync()
    cancel_effect()
    return JSONResponse({"ok": True})