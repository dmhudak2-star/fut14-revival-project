#!/usr/bin/env python3
"""Passively watch FIFA 14's cached EA endpoint through XBDM."""

from __future__ import annotations

import argparse
import re
import socket
import time


ADDRESS = 0x304E40B8
LENGTH = 0x20


class Xbdm:
    def __init__(self, host: str, timeout: float = 5.0) -> None:
        self.sock = socket.create_connection((host, 730), timeout)
        self.file = self.sock.makefile("rwb", buffering=0)
        greeting = self.file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")

    def close(self) -> None:
        self.file.close()
        self.sock.close()

    def read(self, address: int, length: int) -> bytes:
        command = f"getmem addr=0x{address:08X} length=0x{length:X}\r\n"
        self.file.write(command.encode("ascii"))
        first = self.file.readline().decode("ascii", "replace").strip()
        if not first.startswith("202"):
            raise RuntimeError(f"getmem failed: {first}")
        lines: list[str] = []
        while True:
            line = self.file.readline().decode("ascii", "replace").strip()
            if line == ".":
                break
            if not line:
                raise RuntimeError("Unexpected EOF from XBDM")
            lines.append(line)
        text = "".join(lines)
        if not re.fullmatch(r"[0-9A-Fa-f]+", text) or len(text) != length * 2:
            raise RuntimeError("Invalid XBDM memory response")
        return bytes.fromhex(text)


def describe(data: bytes) -> str:
    ip = ".".join(str(octet) for octet in data[4:8])
    return f"{data.hex().upper()}  ip@+4={ip}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--interval", type=float, default=0.10)
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        previous = client.read(ADDRESS, LENGTH)
        print(f"Initial: {describe(previous)}", flush=True)
        print("WATCH_READY", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            current = client.read(ADDRESS, LENGTH)
            if current != previous:
                elapsed = args.seconds - max(0.0, deadline - time.monotonic())
                print(f"T+{elapsed:06.2f}s: {describe(current)}", flush=True)
                previous = current
            time.sleep(args.interval)
        print(f"Final: {describe(previous)}", flush=True)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
