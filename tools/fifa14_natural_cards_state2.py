#!/usr/bin/env python3
"""Publish the native network-listener state-2 transition once.

This follows FIFA's own observer fan-out.  The registered powdll_FEThread
observer receives r6=2 and invokes the CardsDLL +C lifecycle naturally.
"""

from __future__ import annotations

import argparse
import socket
import time

from fifa14_force_connected_event import (
    RPC_VOID,
    jrpc_call,
)
from fifa14_plain_send_hook import Xbdm, verify_module


STATE2_TRANSITION = 0x8251A6E0
LISTENER_VTABLE = 0x8202D0F8
POWDLL_FE_THREAD = 0x82D63528

ROOT_GLOBAL = 0x897C3608
ROOT_VTABLE = 0x89708AE0
ROOT_INITIALIZE = 0x89748A38
AUTH_VTABLE = 0x89707078
HOST_GLOBALS = (0x897C33A0, 0x897C3370, 0x897C339C, 0x897C33CC)


def u32(client: Xbdm, address: int) -> int:
    return int.from_bytes(client.read(address, 4), "big")


def find_listener(client: Xbdm) -> int:
    # The B0C subscriber owns three signal vectors.  The network listener is
    # the unique receiver whose vtable+4 is FIFA's state-1 transition.
    state1 = 0x8251A560
    matches: list[int] = []
    owner = 0xBD2DC740
    for offset in (0x6F8, 0x778, 0x7F8):
        begin = u32(client, owner + offset + 4)
        end = u32(client, owner + offset + 8)
        if end < begin or (end - begin) % 4 or end - begin > 0x400:
            continue
        for slot in range(begin, end, 4):
            receiver = u32(client, slot)
            if not receiver:
                continue
            try:
                vtable = u32(client, receiver)
                callback = u32(client, vtable + 4)
            except Exception:
                continue
            if callback == state1 and receiver not in matches:
                matches.append(receiver)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one network listener, found {len(matches)}"
        )
    return matches[0]


def validate_cards(client: Xbdm, listener: int) -> tuple[int, int]:
    vtable = u32(client, listener)
    state2 = u32(client, vtable + 0x0C)
    if vtable != LISTENER_VTABLE or state2 != STATE2_TRANSITION:
        raise RuntimeError(
            f"Unexpected listener binding: vtable=0x{vtable:08X} "
            f"state2=0x{state2:08X}"
        )

    observer_begin = u32(client, listener - 0x48)
    observer_count = u32(client, listener - 0x44)
    if not 0 < observer_count <= 0x100:
        raise RuntimeError(f"Unexpected observer count: {observer_count}")
    callbacks: list[int] = []
    for index in range(observer_count):
        observer = u32(client, observer_begin + index * 4)
        if not observer:
            continue
        callbacks.append(u32(client, u32(client, observer)))
    if callbacks.count(POWDLL_FE_THREAD) != 1:
        raise RuntimeError(
            "The powdll_FEThread observer is absent or duplicated"
        )

    root = u32(client, ROOT_GLOBAL)
    if not root or u32(client, root) != ROOT_VTABLE:
        raise RuntimeError("Unexpected or missing CardsDLL root")
    if u32(client, ROOT_VTABLE + 0x0C) != ROOT_INITIALIZE:
        raise RuntimeError("Unexpected CardsDLL +C lifecycle target")
    for offset in (0x80, 0x3A08, 0x3A0C, 0x3A10, 0x3A14):
        value = u32(client, root + offset)
        if value:
            raise RuntimeError(
                f"Cards +C lifecycle is not pristine: "
                f"+0x{offset:X}=0x{value:08X}"
            )
    for offset in (0x3A44, 0x3A48, 0x3AF8, 0x3A4C):
        if not u32(client, root + offset):
            raise RuntimeError(
                f"Cards +8 lifecycle prerequisite +0x{offset:X} is null"
            )
    for address in HOST_GLOBALS:
        if not u32(client, address):
            raise RuntimeError(
                f"Cards host global 0x{address:08X} is null"
            )
    return root, observer_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        pow_module = next(
            (
                line
                for line in client.multiline("modules")
                if 'name="powdllzf.xex.dll"' in line.lower()
            ),
            None,
        )
        if pow_module is None or "base=0x89700000" not in pow_module.lower():
            raise RuntimeError(f"Unexpected or missing powdllzf: {pow_module}")
        listener = find_listener(client)
        root, observer_count = validate_cards(client, listener)
        state_before = u32(client, listener + 0x974)
        print(f"network listener = 0x{listener:08X}")
        print(f"observer count   = {observer_count}")
        print(f"state before     = {state_before}")
        print(f"Cards root       = 0x{root:08X}")
        if args.action == "status":
            return 0
        if state_before not in (0, 1):
            raise RuntimeError(
                f"Refusing state2 transition from state {state_before}"
            )
    finally:
        client.close()

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(25)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        response = jrpc_call(
            file,
            RPC_VOID,
            STATE2_TRANSITION,
            (listener,),
        )
        print(f"native state2 call = {response}")

    deadline = time.monotonic() + 8.0
    last = (0, 0, 0)
    while time.monotonic() < deadline:
        client = Xbdm(args.host)
        try:
            state = u32(client, listener + 0x974)
            auth = u32(client, root + 0x3A08)
            auth_vtable = u32(client, auth) if auth else 0
            last = (state, auth, auth_vtable)
        finally:
            client.close()
        if state == 2 and auth and auth_vtable == AUTH_VTABLE:
            break
        time.sleep(0.1)

    state, auth, auth_vtable = last
    print(f"state after      = {state}")
    print(f"auth object      = 0x{auth:08X}")
    print(f"auth vtable      = 0x{auth_vtable:08X}")
    if state != 2:
        raise RuntimeError("Native listener did not reach state 2")
    if not auth or auth_vtable != AUTH_VTABLE:
        raise RuntimeError("State2 completed but Cards auth was not created")
    print("Verified: natural state2 fan-out created Cards authentication.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
