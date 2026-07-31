#!/usr/bin/env python3
"""Temporarily redirect FIFA 14 Blaze redirector hostnames to the Mac.

This patches only the mapped default.xex .rdata strings.  Restarting FIFA
restores the original data automatically.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import socket


DEFAULT_LOCAL_IP = "192.0.2.35"
HOSTS = (
    (0x8210B238, b"gosredirector.ea.com\0"),
    (0x8210B250, b"gosredirector.scert.ea.com\0"),
    (0x8210B26C, b"gosredirector.stest.ea.com\0"),
    (0x8210B288, b"gosredirector.online.ea.com\0"),
)


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

    def command(self, command: str) -> str:
        self.file.write(command.encode("ascii") + b"\r\n")
        return self.file.readline().decode("ascii", "replace").strip()

    def multiline(self, command: str) -> list[str]:
        first = self.command(command)
        if not first.startswith("202"):
            raise RuntimeError(f"{command} failed: {first}")
        lines: list[str] = []
        while True:
            line = self.file.readline().decode("ascii", "replace").strip()
            if line == ".":
                return lines
            if not line:
                raise RuntimeError(f"Unexpected EOF during {command}")
            lines.append(line)

    def read(self, address: int, length: int) -> bytes:
        text = "".join(
            self.multiline(f"getmem addr=0x{address:08X} length=0x{length:X}")
        )
        if not re.fullmatch(r"[0-9A-Fa-f]+", text) or len(text) != length * 2:
            raise RuntimeError(f"Invalid memory response at 0x{address:08X}")
        return bytes.fromhex(text)

    def write(self, address: int, data: bytes) -> None:
        response = self.command(
            f"setmem addr=0x{address:08X} data={data.hex().upper()}"
        )
        if not response.startswith("200"):
            raise RuntimeError(f"setmem failed at 0x{address:08X}: {response}")


def padded_target(size: int, local_ip: str) -> bytes:
    target = local_ip.encode("ascii") + b"\0"
    if len(target) > size:
        raise RuntimeError("Target address does not fit redirector string slot")
    return target + b"\0" * (size - len(target))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    parser.add_argument("--local-ip", default=DEFAULT_LOCAL_IP)
    args = parser.parse_args()
    local_ip = str(ipaddress.IPv4Address(args.local_ip))

    client = Xbdm(args.host)
    try:
        for address, original in HOSTS:
            current = client.read(address, len(original))
            patched = padded_target(len(original), local_ip)
            if args.action == "status":
                if current == original:
                    state = "original"
                elif current == patched:
                    state = "redirected"
                else:
                    state = f"unexpected:{current!r}"
                print(f"0x{address:08X}: {state}")
                continue

            expected = original if args.action == "apply" else patched
            replacement = patched if args.action == "apply" else original
            if current != expected:
                raise RuntimeError(
                    f"Unexpected bytes at 0x{address:08X}: {current!r}"
                )
            client.write(address, replacement)
            if client.read(address, len(replacement)) != replacement:
                raise RuntimeError(f"Verification failed at 0x{address:08X}")
            print(f"0x{address:08X}: {args.action} verified")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
