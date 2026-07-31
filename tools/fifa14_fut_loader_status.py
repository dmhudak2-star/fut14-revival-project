#!/usr/bin/env python3
"""Query FIFA 14's native FUT loader readiness through its normal poll API."""

from __future__ import annotations

import argparse
import socket

from fifa14_native_fut_auth_call import call, parse_u32
from fifa14_plain_send_hook import Xbdm, verify_module


FUT_ADAPTER_GETTER = 0x827C6370
FUT_LOADER_POLL = 0x827C63B0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    memory = Xbdm(args.host)
    try:
        verify_module(memory)
    finally:
        memory.close()

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(20)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        adapter = parse_u32(call(file, FUT_ADAPTER_GETTER))
        if not 0xA0000000 <= adapter < 0xE0000000:
            raise RuntimeError(f"Invalid FUT adapter pointer: 0x{adapter:08X}")
        readiness = parse_u32(call(file, FUT_LOADER_POLL, (adapter,)))

    memory = Xbdm(args.host)
    try:
        state = int.from_bytes(memory.read(adapter + 0x114, 4), "big")
    finally:
        memory.close()
    print(f"FUT adapter      = 0x{adapter:08X}")
    print(f"loader state     = {state}")
    print(f"loader available = {readiness & 0xFF}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
