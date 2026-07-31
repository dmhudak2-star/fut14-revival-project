#!/usr/bin/env python3
"""Temporarily load FIFA 14's CardsDLLzf through xboxkrnl!XexLoadImage.

The probe runs only while FIFA 14's default.xex is mapped.  It preserves the
12-byte writable .XBMOVIE scratch slot used for XexLoadImage's output handle.
"""

from __future__ import annotations

import argparse
import re
import socket
import struct
import sys


XEX_LOAD_IMAGE_ORDINAL = 0x199
SCRATCH = 0x83E8B000
SCRATCH_SIZE = 12
EXPECTED_DEFAULT_BASE = 0x82000000
CARDS_PATH = r"D:\CardsDLLzf.xex.dll"


class Xbdm:
    def __init__(self, host: str, timeout: float = 8.0) -> None:
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

    def read_memory(self, address: int, length: int) -> bytes:
        lines = self.multiline(f"getmem addr=0x{address:08X} length=0x{length:X}")
        text = "".join(lines)
        if not re.fullmatch(r"[0-9A-Fa-f]+", text) or len(text) != length * 2:
            raise RuntimeError(f"Invalid getmem response at 0x{address:08X}")
        return bytes.fromhex(text)

    def write_memory(self, address: int, data: bytes) -> None:
        response = self.command(
            f"setmem addr=0x{address:08X} data={data.hex().upper()}"
        )
        if not response.startswith("200"):
            raise RuntimeError(f"setmem failed: {response}")


def module_map(client: Xbdm) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in client.multiline("modules"):
        match = re.search(r'name="([^"]+)".*?base=0x([0-9A-Fa-f]+)', line)
        if match:
            result[match.group(1).lower()] = line
    return result


def jrpc_load_cards(client: Xbdm) -> str:
    path_hex = CARDS_PATH.encode("ascii").hex().upper()
    # Title DLLs are loaded with module flags 0x9 by the normal XEX loader.
    # XexLoadImage(path, flags=0x9, minimum_version=0, module_handle_out).
    params = (
        f"A\\0\\A\\4\\"
        f"7/{len(CARDS_PATH)}\\{path_hex}\\"
        f"1\\9\\"
        f"1\\0\\"
        f"1\\{SCRATCH}\\"
    )
    command = (
        'consolefeatures ver=2 type=1 '
        f'module="xboxkrnl.exe" ord={XEX_LOAD_IMAGE_ORDINAL} '
        f'as=0 params="{params}"'
    )
    response = client.command(command)
    for _ in range(12):
        match = re.search(r"buf_addr=(?:0x)?([0-9A-Fa-f]+)", response)
        if not match:
            return response
        response = client.command(
            f"consolefeatures ver=2 buf_addr=0x{int(match.group(1), 16):X}"
        )
    return "200- pending (JRPC2 result loop remains active)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    client = Xbdm(args.host)
    saved = b""
    try:
        before = module_map(client)
        default = before.get("default.xex")
        if not default or f"base=0x{EXPECTED_DEFAULT_BASE:08x}" not in default.lower():
            raise RuntimeError("FIFA 14 default.xex is not currently loaded")
        if "cardsdllzf.xex.dll" in before:
            print("CardsDLLzf.xex.dll is already loaded.")
            return 0

        saved = client.read_memory(SCRATCH, SCRATCH_SIZE)
        client.write_memory(SCRATCH, b"\0" * SCRATCH_SIZE)
        response = jrpc_load_cards(client)
        print(f"XexLoadImage response: {response}")

        handle_data = client.read_memory(SCRATCH, 4)
        handle = struct.unpack(">I", handle_data)[0]
        print(f"Module handle output: 0x{handle:08X}")

        after = module_map(client)
        cards = after.get("cardsdllzf.xex.dll")
        if not cards:
            raise RuntimeError("CardsDLLzf did not appear in the module list")
        print(f"Loaded: {cards}")
        return 0
    finally:
        if saved:
            try:
                client.write_memory(SCRATCH, saved)
            except Exception as exc:
                print(f"Warning: scratch restore failed: {exc}", file=sys.stderr)
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
