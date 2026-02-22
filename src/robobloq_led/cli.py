import argparse
from .device import RobobloqController, find_vendor_device

def main():
    p = argparse.ArgumentParser(prog="robobloq-led")
    p.add_argument("--dev", default=None, help="Path to hidraw device (auto-detect if omitted)")
    p.add_argument("--r", type=int, required=True)
    p.add_argument("--g", type=int, required=True)
    p.add_argument("--b", type=int, required=True)
    args = p.parse_args()

    dev = args.dev or find_vendor_device()
    ctl = RobobloqController(dev=dev)
    ctl.set_color(args.r, args.g, args.b)
    print(f"OK: set ({args.r},{args.g},{args.b}) on {dev}")

if __name__ == "__main__":
    main()
