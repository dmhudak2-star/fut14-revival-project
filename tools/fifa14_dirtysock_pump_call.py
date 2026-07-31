#!/usr/bin/env python3
"""Invoke FIFA 14's no-argument DirtySock idle pump once through JRPC2."""

from __future__ import annotations

import argparse
import re
import socket


PUMP = 0x82D69A00


def command(file, text: str) -> str:
    file.write(text.encode("ascii") + b"\r\n")
    return file.readline().decode("ascii", "replace").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(12)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")

        # JRPC2: void return, direct address, title thread, zero arguments.
        request = (
            'consolefeatures ver=2 type=0 as=0 '
            f'params="A\\{PUMP:X}\\A\\0\\"'
        )
        response = command(file, request)
        for _ in range(12):
            match = re.search(r"buf_addr=(?:0x)?([0-9A-Fa-f]+)", response)
            if not match:
                break
            response = command(
                file,
                "consolefeatures ver=2 "
                f"buf_addr=0x{int(match.group(1), 16):X}",
            )
        print(f"DirtySock pump response: {response}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
