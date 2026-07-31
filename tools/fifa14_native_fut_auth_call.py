#!/usr/bin/env python3
"""Invoke FIFA 14's native FUT WebSession authentication through JRPC2."""

from __future__ import annotations

import argparse
import re
import socket

from fifa14_plain_send_hook import Xbdm, verify_module


FUT_ADAPTER_GETTER = 0x827C6370
FUT_AUTHENTICATION = 0x82782078
RPC_INT = 1


def command(file, text: str) -> str:
    file.write(text.encode("ascii") + b"\r\n")
    return file.readline().decode("ascii", "replace").strip()


def call(
    file,
    address: int,
    arguments: tuple[int, ...] = (),
) -> str:
    encoded = "".join(f"{RPC_INT}\\{value & 0xFFFFFFFF}\\" for value in arguments)
    request = (
        f"consolefeatures ver=2 type={RPC_INT} as=0 "
        f'params="A\\{address:X}\\A\\{len(arguments)}\\{encoded}"'
    )
    response = command(file, request)
    for _ in range(16):
        match = re.search(r"buf_addr=(?:0x)?([0-9A-Fa-f]+)", response)
        if not match:
            return response
        response = command(
            file,
            "consolefeatures ver=2 "
            f"buf_addr=0x{int(match.group(1), 16):X}",
        )
    return response


def parse_u32(response: str) -> int:
    match = re.search(r"\b([0-9A-Fa-f]{1,8})\s*$", response)
    if not match:
        raise RuntimeError(f"Invalid JRPC2 integer response: {response}")
    return int(match.group(1), 16)


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
            raise RuntimeError("FUT adapter pointer is outside title memory")

        memory = Xbdm(args.host)
        try:
            before = int.from_bytes(memory.read(adapter + 0x114, 4), "big")
        finally:
            memory.close()
        print(f"FUT adapter            = 0x{adapter:08X}")
        print(f"WebSession+0x114 before = 0x{before:08X}")

        response = call(file, FUT_AUTHENTICATION, (adapter,))
        result = parse_u32(response)
        print(f"Authentication result   = 0x{result:08X} ({result})")

    memory = Xbdm(args.host)
    try:
        after = int.from_bytes(memory.read(adapter + 0x114, 4), "big")
    finally:
        memory.close()
    print(f"WebSession+0x114 after  = 0x{after:08X}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
