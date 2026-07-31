#!/usr/bin/env python3
"""Initialize the loaded FIFA 14 CardsDLL root and expose its auth object."""

from __future__ import annotations

import argparse
import re
import socket
import time

from fifa14_plain_send_hook import Xbdm


ROOT_GLOBAL = 0x897C3608
ROOT_VTABLE = 0x89708AE0
ROOT_INITIALIZE = 0x89748A38
AUTH_OFFSET = 0x3A08
AUTH_VTABLE = 0x89707078
AUTH_REQUEST = 0x897381E8

RPC_VOID = 0
RPC_INT = 1


def command(file, text: str) -> str:
    file.write(text.encode("ascii") + b"\r\n")
    return file.readline().decode("ascii", "replace").strip()


def jrpc_call(
    file,
    return_type: int,
    address: int,
    arguments: tuple[int, ...],
) -> str:
    encoded = "".join(f"{RPC_INT}\\{value & 0xFFFFFFFF}\\" for value in arguments)
    request = (
        f"consolefeatures ver=2 type={return_type} as=0 "
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
    return "200- native call dispatched; JRPC2 result remained pending"


def read_state(host: str) -> tuple[int, int, int, int]:
    client = Xbdm(host)
    try:
        module = next(
            (
                line
                for line in client.multiline("modules")
                if 'name="powdllzf.xex.dll"' in line.lower()
            ),
            None,
        )
        if module is None:
            raise RuntimeError("powdllzf.xex.dll is not loaded")
        root = int.from_bytes(client.read(ROOT_GLOBAL, 4), "big")
        root_vtable = (
            int.from_bytes(client.read(root, 4), "big") if root else 0
        )
        auth = (
            int.from_bytes(client.read(root + AUTH_OFFSET, 4), "big")
            if root
            else 0
        )
        auth_vtable = (
            int.from_bytes(client.read(auth, 4), "big") if auth else 0
        )
        return root, root_vtable, auth, auth_vtable
    finally:
        client.close()


def show(state: tuple[int, int, int, int]) -> None:
    root, root_vtable, auth, auth_vtable = state
    print(f"CardsDLL root       = 0x{root:08X}")
    print(f"root vtable         = 0x{root_vtable:08X}")
    print(f"root+0x3A08 auth    = 0x{auth:08X}")
    print(f"auth vtable         = 0x{auth_vtable:08X}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "init", "auth"))
    args = parser.parse_args()

    state = read_state(args.host)
    show(state)
    root, root_vtable, auth, auth_vtable = state
    if root_vtable != ROOT_VTABLE:
        raise RuntimeError("Unexpected CardsDLL root object")
    if args.action == "status":
        return 0

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(20)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")

        if args.action == "init":
            if auth:
                print("CardsDLL auth object is already initialized.")
                return 0
            response = jrpc_call(
                file, RPC_VOID, ROOT_INITIALIZE, (root,)
            )
            print(f"CardsDLL initialize = {response}")
        else:
            if not auth or auth_vtable != AUTH_VTABLE:
                raise RuntimeError("CardsDLL auth object is not initialized")
            response = jrpc_call(
                file, RPC_INT, AUTH_REQUEST, (auth,)
            )
            print(f"pow/auth request    = {response}")

    for _ in range(20):
        time.sleep(0.1)
        state = read_state(args.host)
        if args.action == "auth" or state[2]:
            break
    show(state)
    if args.action == "init" and state[3] != AUTH_VTABLE:
        raise RuntimeError("CardsDLL auth object was not created")
    print(
        "Verified: CardsDLL authentication object is initialized."
        if args.action == "init"
        else "Verified: native pow/auth method was invoked."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
