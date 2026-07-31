#!/usr/bin/env python3
"""Describe string-like pointers reachable from FIFA's active ION flow object."""

from __future__ import annotations

import argparse
import string

from fifa14_plain_send_hook import Xbdm, verify_module


FLOW_SERVICE_GLOBAL = 0x83D922B8


def u32(client: Xbdm, address: int) -> int:
    return int.from_bytes(client.read(address, 4), "big")


def title_pointer(value: int) -> bool:
    return 0x80000000 <= value < 0xE0000000


def readable(client: Xbdm, pointer: int, limit: int = 96) -> str | None:
    if not title_pointer(pointer):
        return None
    try:
        raw = client.read(pointer, limit).split(b"\0", 1)[0]
    except Exception:
        return None
    if not 2 <= len(raw) < limit:
        return None
    allowed = set(bytes(string.printable, "ascii"))
    if any(byte not in allowed or byte in b"\r\n\t\x0b\x0c" for byte in raw):
        return None
    return raw.decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--size", type=lambda value: int(value, 0), default=0x800)
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        service = u32(client, FLOW_SERVICE_GLOBAL)
        ui = u32(client, service + 0x78)
        flow = u32(client, ui + 0x18)
        raw = client.read(flow, args.size)
        print(f"service=0x{service:08X} ui=0x{ui:08X} flow=0x{flow:08X}")
        hits = 0
        for offset in range(0, len(raw) - 3, 4):
            pointer = int.from_bytes(raw[offset : offset + 4], "big")
            text = readable(client, pointer)
            if text is not None:
                print(f"flow+0x{offset:03X} -> 0x{pointer:08X}  {text!r}")
                hits += 1
        print(f"Readable direct pointers: {hits}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
