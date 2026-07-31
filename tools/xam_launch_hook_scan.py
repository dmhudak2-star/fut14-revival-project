#!/usr/bin/env python3
"""List XAM ordinals currently redirected into DashLaunch through JRPC2."""

from __future__ import annotations

import argparse
import re
import socket


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--maximum", type=lambda value: int(value, 0), default=0x900)
    args = parser.parse_args()

    module = "xam.xex"
    module_hex = module.encode("ascii").hex().upper()
    with socket.create_connection((args.host, 730), timeout=5) as sock:
        sock.settimeout(5)
        stream = sock.makefile("rwb", buffering=0)
        greeting = stream.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        hits = 0
        for ordinal in range(1, args.maximum + 1):
            command = (
                'consolefeatures ver=2 type=9 params="'
                f"A\\0\\A\\2\\2/{len(module)}\\{module_hex}\\1\\{ordinal}\\\""
            )
            stream.write(command.encode("ascii") + b"\r\n")
            response = stream.readline().decode("ascii", "replace").strip()
            if not response.startswith("200"):
                continue
            values = re.findall(r"(?:0x)?([0-9A-Fa-f]{8})", response)
            if not values:
                continue
            address = int(values[-1], 16)
            if 0x91F00000 <= address < 0x91F1D000:
                print(f"ordinal=0x{ordinal:03X} address=0x{address:08X}")
                hits += 1
        print(f"{hits} DashLaunch hook(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
