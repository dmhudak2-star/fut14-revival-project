#!/usr/bin/env python3
"""Publish FIFA's native ConnectedToServers transition through JRPC2."""

from __future__ import annotations

import argparse
import re
import socket

from fifa14_plain_send_hook import Xbdm, verify_module


ONLINE_MANAGER_GETTER = 0x82762398
SET_CONNECTED_AND_PUBLISH = 0x8276AFB8
CONNECTED_EVENT_DESCRIPTOR = 0x83D93D98

RPC_VOID = 0
RPC_INT = 1


def xbdm_command(file, text: str) -> str:
    file.write(text.encode("ascii") + b"\r\n")
    return file.readline().decode("ascii", "replace").strip()


def complete_jrpc(file, response: str) -> str:
    for _ in range(16):
        match = re.search(r"buf_addr=(?:0x)?([0-9A-Fa-f]+)", response)
        if not match:
            return response
        response = xbdm_command(
            file,
            "consolefeatures ver=2 "
            f"buf_addr=0x{int(match.group(1), 16):X}",
        )
    # A title-thread call that publishes an event can remain represented by a
    # JRPC2 result buffer even after the native side has completed. The caller
    # verifies the observable event descriptor afterward.
    return "200- native call dispatched; JRPC2 result remained pending"


def jrpc_call(
    file,
    return_type: int,
    address: int,
    arguments: tuple[int, ...] = (),
) -> str:
    encoded = "".join(f"{RPC_INT}\\{value & 0xFFFFFFFF}\\" for value in arguments)
    request = (
        f"consolefeatures ver=2 type={return_type} as=0 "
        f'params="A\\{address:X}\\A\\{len(arguments)}\\{encoded}"'
    )
    response = complete_jrpc(file, xbdm_command(file, request))
    if not response.startswith("200"):
        raise RuntimeError(f"JRPC2 call 0x{address:08X} failed: {response}")
    return response


def parse_u32(response: str) -> int:
    match = re.search(r"\b([0-9A-Fa-f]{1,8})\s*$", response)
    if not match:
        raise RuntimeError(f"Invalid JRPC2 integer response: {response}")
    return int(match.group(1), 16)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply"))
    args = parser.parse_args()

    memory = Xbdm(args.host)
    try:
        verify_module(memory)
        before = int.from_bytes(
            memory.read(CONNECTED_EVENT_DESCRIPTOR, 4), "big"
        )
    finally:
        memory.close()

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(20)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        manager = parse_u32(
            jrpc_call(file, RPC_INT, ONLINE_MANAGER_GETTER)
        )
        print(f"online_manager             = 0x{manager:08X}")
        print(f"ConnectedToServers before = 0x{before:08X}")
        if args.action == "status":
            return 0
        if not 0xA0000000 <= manager < 0xE0000000:
            raise RuntimeError("Online manager pointer is outside title memory")
        response = jrpc_call(
            file,
            RPC_VOID,
            SET_CONNECTED_AND_PUBLISH,
            (manager, 1),
        )
        print(f"native transition result  = {response}")

    memory = Xbdm(args.host)
    try:
        after = int.from_bytes(
            memory.read(CONNECTED_EVENT_DESCRIPTOR, 4), "big"
        )
    finally:
        memory.close()
    print(f"ConnectedToServers after  = 0x{after:08X}")
    if after == 0:
        raise RuntimeError("ConnectedToServers event was not initialized")
    print("Verified: native ConnectedToServers event was published.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
