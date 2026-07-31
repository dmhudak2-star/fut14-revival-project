#!/usr/bin/env python3
"""Read-only JRPC2 export probe for Xbox 360 compression capabilities.

This does not call any resolved function and does not write console memory.
It only asks JRPC2 to resolve documented module ordinals.
"""

from __future__ import annotations

import argparse
import re
import socket


EXPORTS = (
    ("xboxkrnl.exe", 0x0B6, "LDICreateDecompression"),
    ("xboxkrnl.exe", 0x0B7, "LDIDecompress"),
    ("xboxkrnl.exe", 0x0B8, "LDIDestroyDecompression"),
    ("xboxkrnl.exe", 0x2E2, "LDIResetDecompression"),
    ("xam.xex", 0x1EA, "XamAlloc"),
    ("xam.xex", 0x1EC, "XamFree"),
)


class Xbdm:
    def __init__(self, host: str, timeout: float = 4.0) -> None:
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


def resolve(client: Xbdm, module: str, ordinal: int) -> tuple[str, int | None]:
    module_hex = module.encode("ascii").hex().upper()
    command = (
        'consolefeatures ver=2 type=9 params="'
        f"A\\0\\A\\2\\2/{len(module)}\\{module_hex}\\1\\{ordinal}\\\""
    )
    response = client.command(command)
    if not response.startswith("200"):
        return response, None
    matches = re.findall(r"(?:0x)?([0-9A-Fa-f]{8})", response)
    return response, int(matches[-1], 16) if matches else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()
    client = Xbdm(args.host)
    try:
        for module, ordinal, name in EXPORTS:
            response, address = resolve(client, module, ordinal)
            rendered = f"0x{address:08X}" if address else f"unresolved ({response})"
            print(f"{module}!{name} ordinal=0x{ordinal:X}: {rendered}")
    finally:
        client.close()

    print()
    print("No XMemCompress-family export is present in the known retail")
    print("xboxkrnl/xam export maps; XMem compression is supplied by xcompress.lib.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
