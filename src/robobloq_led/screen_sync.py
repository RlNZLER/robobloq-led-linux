import time
import argparse
import numpy as np
import mss

from .device import RobobloqController, find_vendor_device

def avg_edge_color(img: np.ndarray, thickness: int = 80):
    """
    img: HxWx3 RGB uint8
    Returns avg colors for left/top/right edges.
    """
    h, w, _ = img.shape
    t = max(1, min(thickness, w // 4, h // 4))

    left = img[:, :t, :]
    top = img[:t, :, :]
    right = img[:, w - t :, :]

    l = left.mean(axis=(0, 1))
    tcol = top.mean(axis=(0, 1))
    r = right.mean(axis=(0, 1))

    return tuple(l.astype(int)), tuple(tcol.astype(int)), tuple(r.astype(int))

def combine_edges(left_rgb, top_rgb, right_rgb):
    """
    Combine edges into one solid color (device currently runs whole-strip color).
    Weighted towards top for movies.
    """
    lr = np.array(left_rgb, dtype=np.float32)
    tr = np.array(top_rgb, dtype=np.float32)
    rr = np.array(right_rgb, dtype=np.float32)
    out = (0.25 * lr + 0.50 * tr + 0.25 * rr)
    return tuple(out.astype(int))

def run_sync(monitor_index: int = 2, fps: int = 25, thickness: int = 80):
    dev = find_vendor_device()
    ctl = RobobloqController(dev=dev)

    dt = 1.0 / max(1, fps)

    with mss.mss() as sct:
        if monitor_index < 1 or monitor_index >= len(sct.monitors):
            raise SystemExit(f"Invalid monitor index {monitor_index}. Available: 1..{len(sct.monitors)-1}")

        mon = sct.monitors[monitor_index]
        print(f"Sync running @ {fps} FPS | thickness={thickness}px | monitor_index={monitor_index}")
        print("Monitor rect:", mon)
        print("Ctrl+C to stop.\n")

        while True:
            start = time.time()

            frame = np.array(sct.grab(mon))      # BGRA
            img = frame[:, :, :3][:, :, ::-1]    # -> RGB

            l, tcol, r = avg_edge_color(img, thickness=thickness)
            R, G, B = combine_edges(l, tcol, r)

            ctl.set_color(int(R), int(G), int(B))

            elapsed = time.time() - start
            if elapsed < dt:
                time.sleep(dt - elapsed)

def main():
    p = argparse.ArgumentParser(description="Screen sync for Robobloq LED (solid color mode).")
    p.add_argument("--monitor", type=int, default=2, help="MSS monitor index (1..N). 0 is all screens (avoid).")
    p.add_argument("--fps", type=int, default=25, help="Frames per second.")
    p.add_argument("--thickness", type=int, default=80, help="Edge sample thickness in pixels.")
    args = p.parse_args()
    run_sync(monitor_index=args.monitor, fps=args.fps, thickness=args.thickness)

if __name__ == "__main__":
    main()