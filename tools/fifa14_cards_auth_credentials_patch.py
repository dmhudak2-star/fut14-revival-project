#!/usr/bin/env python3
"""Supply local EASW credentials to the Xbox Cards Authentication builder.

Loopizzle's verified PC route substitutes local values only when the retail
Authentication JSON builder receives null EASW-Session/EASW-Token pointers.
The Xbox 360 Cards module has the same two gates.  This reversible patch
replaces only each null-result check pair with a pointer to local-only strings;
it does not submit Authentication, publish a frontend event, or navigate.
"""

from __future__ import annotations

import argparse
import re

from fifa14_plain_send_hook import Xbdm, addi, addis, insn


MODULE_NAME = "powdllzf.xex.dll"
MODULE_BASE = 0x89700000

SESSION_SITE = 0x8974D2C8
TOKEN_SITE = 0x8974D31C

# or. r30,r3,r3 ; beq <builder failure>
SESSION_ORIGINAL = bytes.fromhex("7C7E1B79 418200EC")
TOKEN_ORIGINAL = bytes.fromhex("7C7E1B79 41820098")

DATA = 0x897BF200
SESSION_ADDRESS = DATA
TOKEN_ADDRESS = DATA + 0x40
SESSION_VALUE = b"LOCAL-FIFA14-EASW-SESSION\0"
TOKEN_VALUE = b"LOCAL-FIFA14-EASW-TOKEN\0"
DATA_SIZE = 0x80


def pointer_load(register: int, address: int) -> bytes:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return insn(addis(register, 0, high)) + insn(addi(register, register, low))


SESSION_PATCHED = pointer_load(30, SESSION_ADDRESS)
TOKEN_PATCHED = pointer_load(30, TOKEN_ADDRESS)


def verify_module(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if re.search(r'name="powdllzf\.xex\.dll"', line, re.IGNORECASE)
        ),
        None,
    )
    if module is None or f"base=0x{MODULE_BASE:08x}" not in module.lower():
        raise RuntimeError(f"Unexpected or missing {MODULE_NAME}: {module}")


def state(current: bytes, original: bytes, patched: bytes) -> str:
    if current == original:
        return "original"
    if current == patched:
        return "patched"
    return f"unexpected:{current.hex().upper()}"


def data_image() -> bytes:
    result = bytearray(DATA_SIZE)
    result[: len(SESSION_VALUE)] = SESSION_VALUE
    result[0x40 : 0x40 + len(TOKEN_VALUE)] = TOKEN_VALUE
    return bytes(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        session_current = client.read(SESSION_SITE, len(SESSION_ORIGINAL))
        token_current = client.read(TOKEN_SITE, len(TOKEN_ORIGINAL))
        session_state = state(session_current, SESSION_ORIGINAL, SESSION_PATCHED)
        token_state = state(token_current, TOKEN_ORIGINAL, TOKEN_PATCHED)
        print(f"EASW-Session gate: {session_state}")
        print(f"EASW-Token gate:   {token_state}")

        if args.action == "status":
            if session_state == token_state == "patched":
                raw = client.read(DATA, DATA_SIZE)
                print(f"session = {raw[:0x40].split(bytes([0]), 1)[0].decode('ascii')}")
                print(f"token   = {raw[0x40:].split(bytes([0]), 1)[0].decode('ascii')}")
            return 0

        allowed = {"original", "patched"}
        if session_state not in allowed or token_state not in allowed:
            raise RuntimeError("Refusing unexpected Authentication gate bytes")

        if args.action == "restore":
            if session_state == "patched":
                client.write(SESSION_SITE, SESSION_ORIGINAL)
            if token_state == "patched":
                client.write(TOKEN_SITE, TOKEN_ORIGINAL)
            if client.read(SESSION_SITE, 8) != SESSION_ORIGINAL:
                raise RuntimeError("EASW-Session gate restore failed")
            if client.read(TOKEN_SITE, 8) != TOKEN_ORIGINAL:
                raise RuntimeError("EASW-Token gate restore failed")
            print("Verified: retail Authentication credential gates restored.")
            return 0

        existing_data = client.read(DATA, DATA_SIZE)
        image = data_image()
        if existing_data not in (bytes(DATA_SIZE), image):
            raise RuntimeError("Authentication credential data cave is not free")
        client.write(DATA, image)
        if session_state == "original":
            client.write(SESSION_SITE, SESSION_PATCHED)
        if token_state == "original":
            client.write(TOKEN_SITE, TOKEN_PATCHED)
        if client.read(SESSION_SITE, 8) != SESSION_PATCHED:
            raise RuntimeError("EASW-Session gate patch verification failed")
        if client.read(TOKEN_SITE, 8) != TOKEN_PATCHED:
            raise RuntimeError("EASW-Token gate patch verification failed")
        if client.read(DATA, DATA_SIZE) != image:
            raise RuntimeError("Authentication credential data verification failed")
        print(
            "Verified: local EASW session/token supplied to the native "
            "Cards Authentication builder."
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
