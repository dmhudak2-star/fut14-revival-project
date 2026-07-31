#!/usr/bin/env python3
"""Invoke FIFA 14's registered EnterFUT2 front-end action through JRPC2."""

from __future__ import annotations

import argparse
import re
import socket

from fifa14_plain_send_hook import Xbdm


ACTION_OBJECT_GLOBAL = 0x83DA4648
ACTION_VTABLE = 0x8206A698
ENTERFUT2_WRAPPER = 0x82DA6850
ENTERFUT2_HANDLER = 0x828350C8

RPC_VOID = 0


def command(file, text: str) -> str:
    file.write(text.encode("ascii") + b"\r\n")
    return file.readline().decode("ascii", "replace").strip()


def jrpc_call(file, address: int) -> str:
    request = (
        f"consolefeatures ver=2 type={RPC_VOID} as=0 "
        f'params="A\\{address:X}\\A\\0\\"'
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
    return "200- EnterFUT2 dispatched; JRPC2 result remained pending"


def verify_live_object(host: str) -> tuple[int, int, int]:
    client = Xbdm(host)
    try:
        action_object = int.from_bytes(
            client.read(ACTION_OBJECT_GLOBAL, 4), "big"
        )
        if not action_object:
            raise RuntimeError("EnterFUT2 action object is null")
        vtable = int.from_bytes(client.read(action_object, 4), "big")
        handler = int.from_bytes(client.read(vtable + 0x14, 4), "big")
        return action_object, vtable, handler
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    action_object, vtable, handler = verify_live_object(args.host)
    print(f"EnterFUT2 object  = 0x{action_object:08X}")
    print(f"vtable            = 0x{vtable:08X}")
    print(f"vtable+0x14       = 0x{handler:08X}")
    if vtable != ACTION_VTABLE or handler != ENTERFUT2_HANDLER:
        raise RuntimeError("Unexpected live EnterFUT2 binding")

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(20)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        response = jrpc_call(file, ENTERFUT2_WRAPPER)
        print(f"EnterFUT2 wrapper = {response}")

    print("Verified: the registered EnterFUT2 action was dispatched.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
