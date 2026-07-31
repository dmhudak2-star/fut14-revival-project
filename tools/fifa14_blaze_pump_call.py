#!/usr/bin/env python3
"""Invoke FIFA 14's top-level Blaze receive/dispatch pump once via JRPC2."""

from __future__ import annotations

import argparse
import re
import socket
import time


BLAZE_PUMP = 0x83AC83F0


def send(file, text: str) -> str:
    file.write(text.encode("ascii") + b"\r\n")
    return file.readline().decode("ascii", "replace").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    args = parser.parse_args()

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(8)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")

        response = send(
            file,
            'consolefeatures ver=2 type=0 as=0 '
            f'params="A\\{BLAZE_PUMP:X}\\A\\0\\"',
        )
        deadline = time.monotonic() + args.poll_seconds
        polls = 0
        while time.monotonic() < deadline:
            match = re.search(r"buf_addr=(?:0x)?([0-9A-Fa-f]+)", response)
            if not match:
                break
            time.sleep(0.025)
            response = send(
                file,
                "consolefeatures ver=2 "
                f"buf_addr=0x{int(match.group(1), 16):X}",
            )
            polls += 1
        print(f"Blaze pump response: {response} (polls={polls})")
        if "buf_addr=" in response:
            raise TimeoutError("Blaze pump did not return within the poll window")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
