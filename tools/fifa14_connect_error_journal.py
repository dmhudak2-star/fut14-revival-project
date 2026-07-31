#!/usr/bin/env python3
"""Journal WSAGetLastError and DirtySock's mapped connect result."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    stw,
    verify_module,
)


LAST_ERROR_SITE = 0x82D69E68
LAST_ERROR_ORIGINAL = bytes.fromhex("7C641B78")  # mr r4,r3
LAST_ERROR_STUB = 0x83C8E800

MAPPED_SITE = 0x82D69E74
MAPPED_ORIGINAL = bytes.fromhex("397F0030")     # addi r11,r31,0x30
MAPPED_STUB = 0x83C8E840

LOG = 0x83C8E780


def build_last_error_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1880),       # -> 0x83C8E780
        stw(3, 12, 0x00),            # WSAGetLastError
        0x7C641B78,                   # displaced mr r4,r3
        branch(LAST_ERROR_STUB + 16, LAST_ERROR_SITE + 4, link=False),
    ]
    return b"".join(insn(word) for word in words)


def build_mapped_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1880),
        stw(3, 12, 0x04),            # DirtySock-mapped result
        stw(31, 12, 0x08),           # owning socket object
        0x397F0030,                   # displaced addi r11,r31,0x30
        branch(MAPPED_STUB + 20, MAPPED_SITE + 4, link=False),
    ]
    return b"".join(insn(word) for word in words)


LAST_ERROR_STUB_BYTES = build_last_error_stub()
MAPPED_STUB_BYTES = build_mapped_stub()
LAST_ERROR_PATCH = insn(
    branch(LAST_ERROR_SITE, LAST_ERROR_STUB, link=False)
)
MAPPED_PATCH = insn(branch(MAPPED_SITE, MAPPED_STUB, link=False))


def signed(value: int) -> int:
    return value if value < 0x80000000 else value - 0x100000000


def site_state(client: Xbdm, site: int, original: bytes, patch: bytes) -> str:
    value = client.read(site, 4)
    if value == original:
        return "original"
    if value == patch:
        return "hooked"
    return f"unexpected:{value.hex().upper()}"


def print_log(client: Xbdm) -> None:
    data = client.read(LOG, 0x0C)
    last_error = int.from_bytes(data[0:4], "big")
    mapped = int.from_bytes(data[4:8], "big")
    owner = int.from_bytes(data[8:12], "big")
    print(
        f"WSAGetLastError={signed(last_error)} (0x{last_error:08X}); "
        f"mapped={signed(mapped)} (0x{mapped:08X}); "
        f"owner=0x{owner:08X}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states = (
            site_state(
                client,
                LAST_ERROR_SITE,
                LAST_ERROR_ORIGINAL,
                LAST_ERROR_PATCH,
            ),
            site_state(client, MAPPED_SITE, MAPPED_ORIGINAL, MAPPED_PATCH),
        )
        print(f"Connect error sites: last={states[0]}, mapped={states[1]}")
        if args.action == "status":
            if states == ("hooked", "hooked"):
                print_log(client)
            return 0

        if args.action == "apply":
            if states == ("hooked", "hooked"):
                print_log(client)
                return 0
            if states != ("original", "original"):
                raise RuntimeError("Refusing to overwrite unexpected code")
            for address, stub in (
                (LAST_ERROR_STUB, LAST_ERROR_STUB_BYTES),
                (MAPPED_STUB, MAPPED_STUB_BYTES),
            ):
                cave = client.read(address, len(stub))
                if cave not in (bytes(len(stub)), stub):
                    raise RuntimeError(
                        f"Code cave 0x{address:08X} is not free"
                    )
            client.write(LOG, bytes(0x0C))
            client.write(LAST_ERROR_STUB, LAST_ERROR_STUB_BYTES)
            client.write(MAPPED_STUB, MAPPED_STUB_BYTES)
            client.write(LAST_ERROR_SITE, LAST_ERROR_PATCH)
            client.write(MAPPED_SITE, MAPPED_PATCH)
            verified = (
                site_state(
                    client,
                    LAST_ERROR_SITE,
                    LAST_ERROR_ORIGINAL,
                    LAST_ERROR_PATCH,
                ),
                site_state(
                    client,
                    MAPPED_SITE,
                    MAPPED_ORIGINAL,
                    MAPPED_PATCH,
                ),
            )
            if verified != ("hooked", "hooked"):
                client.write(LAST_ERROR_SITE, LAST_ERROR_ORIGINAL)
                client.write(MAPPED_SITE, MAPPED_ORIGINAL)
                raise RuntimeError("Connect error journal verification failed")
            print("Verified: connect error journal active.")
            return 0

        if states == ("original", "original"):
            print("Already restored.")
            return 0
        if any(value not in ("original", "hooked") for value in states):
            raise RuntimeError("Refusing to restore unexpected code")
        if states[0] == "hooked":
            client.write(LAST_ERROR_SITE, LAST_ERROR_ORIGINAL)
        if states[1] == "hooked":
            client.write(MAPPED_SITE, MAPPED_ORIGINAL)
        print("Verified: connect error sites restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
