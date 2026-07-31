#!/usr/bin/env python3
"""Temporarily redirect the verified FIFA 14 EA endpoint to the local Mac."""

from __future__ import annotations

import argparse
import re
import socket


ENDPOINT = 0x304E50B8
EA_IP = bytes((159, 153, 52, 75))
MAC_IP = bytes((192, 168, 1, 35))
PREFIX = bytes.fromhex("00100010")
PORT_AND_FLAGS = bytes.fromhex("0C020000")


class Xbdm:
    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 730), timeout=5)
        self.file = self.sock.makefile("rwb", buffering=0)
        banner = self.file.readline().decode("ascii", "replace").strip()
        if not banner.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM banner: {banner}")

    def close(self) -> None:
        self.file.close()
        self.sock.close()

    def command(self, command: str) -> str:
        self.file.write(command.encode("ascii") + b"\r\n")
        return self.file.readline().decode("ascii", "replace").strip()

    def read(self, address: int, length: int) -> bytes:
        status = self.command(
            f"getmem addr=0x{address:08X} length=0x{length:X}"
        )
        if not status.startswith("202"):
            raise RuntimeError(f"getmem failed: {status}")
        lines: list[str] = []
        while True:
            line = self.file.readline().decode("ascii", "replace").strip()
            if line == ".":
                break
            lines.append(line)
        encoded = "".join(lines)
        if not re.fullmatch(r"[0-9A-Fa-f]+", encoded):
            raise RuntimeError("Invalid XBDM memory response")
        return bytes.fromhex(encoded)

    def write(self, address: int, data: bytes) -> None:
        status = self.command(
            f"setmem addr=0x{address:08X} data={data.hex().upper()}"
        )
        if not status.startswith("200"):
            raise RuntimeError(f"setmem failed: {status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        current = client.read(ENDPOINT, 12)
        if current[:4] != PREFIX or current[8:12] != PORT_AND_FLAGS:
            raise RuntimeError(
                f"Endpoint signature mismatch at 0x{ENDPOINT:08X}: "
                f"{current.hex().upper()}"
            )

        current_ip = current[4:8]
        if current_ip == EA_IP:
            state = "EA"
        elif current_ip == MAC_IP:
            state = "Mac"
        else:
            state = ".".join(str(octet) for octet in current_ip)
        print(f"0x{ENDPOINT:08X}: {state}, port 3074")

        if args.action == "status":
            return 0

        expected = EA_IP if args.action == "apply" else MAC_IP
        replacement = MAC_IP if args.action == "apply" else EA_IP
        if current_ip != expected:
            raise RuntimeError(
                f"Refusing {args.action}: expected {expected.hex().upper()}, "
                f"found {current_ip.hex().upper()}"
            )
        client.write(ENDPOINT + 4, replacement)
        verified = client.read(ENDPOINT, 12)
        if verified[4:8] != replacement:
            raise RuntimeError("Patch verification failed")
        print(
            f"Verified: {'.'.join(str(octet) for octet in replacement)}:3074"
        )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
