#!/usr/bin/env python3
"""Trace the return value of FIFA 14's native EnterFUT call."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    lwz,
    stw,
    verify_module,
)


SITE = 0x82835248
ORIGINAL = bytes.fromhex("2C030000")  # cmpwi r3,0
STUB = 0x83C8D900
ALT_SITE = 0x828352FC
ALT_ORIGINAL = bytes.fromhex("817F0000")  # lwz r11,0(r31)
ALT_STUB = 0x83C8D940
JOURNAL = 0x83C8D980
JOURNAL_SIZE = 0x20


def build_stub(
    stub_address: int,
    site: int,
    original: bytes,
    count_offset: int,
    result_offset: int,
) -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x2680),      # JOURNAL
        lwz(11, 12, count_offset),
        addi(11, 11, 1),
        stw(11, 12, count_offset),
        stw(3, 12, result_offset),   # native EnterFUT result
        int.from_bytes(original, "big"),
        0,
    ]
    words[-1] = branch(
        stub_address + (len(words) - 1) * 4, site + 4, False
    )
    return b"".join(insn(word) for word in words)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    stub = build_stub(STUB, SITE, ORIGINAL, 0x00, 0x04)
    alt_stub = build_stub(
        ALT_STUB, ALT_SITE, ALT_ORIGINAL, 0x08, 0x0C
    )
    patch = insn(branch(SITE, STUB, False))
    alt_patch = insn(branch(ALT_SITE, ALT_STUB, False))
    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = client.read(SITE, 4)
        alt_current = client.read(ALT_SITE, 4)
        state = (
            "original"
            if current == ORIGINAL
            else "patched"
            if current == patch
            else f"unexpected:{current.hex().upper()}"
        )
        alt_state = (
            "original"
            if alt_current == ALT_ORIGINAL
            else "patched"
            if alt_current == alt_patch
            else f"unexpected:{alt_current.hex().upper()}"
        )
        print(
            f"Native EnterFUT return trace: checked={state}, "
            f"menu-enabled={alt_state}"
        )
        if args.action in ("status", "read"):
            raw = client.read(JOURNAL, JOURNAL_SIZE)
            result = int.from_bytes(raw[4:8], "big")
            signed = result if result < 0x80000000 else result - 0x100000000
            alt_result = int.from_bytes(raw[0x0C:0x10], "big")
            alt_signed = (
                alt_result
                if alt_result < 0x80000000
                else alt_result - 0x100000000
            )
            print(
                f"checked_count     = "
                f"{int.from_bytes(raw[0:4], 'big')}"
            )
            print(f"checked_result    = 0x{result:08X} ({signed})")
            print(
                f"menu_count        = "
                f"{int.from_bytes(raw[8:12], 'big')}"
            )
            print(
                f"menu_result       = "
                f"0x{alt_result:08X} ({alt_signed})"
            )
            return 0
        if args.action == "apply":
            if state not in ("original", "patched") or alt_state not in (
                "original",
                "patched",
            ):
                raise RuntimeError("Unexpected native EnterFUT return site")
            cave = client.read(STUB, len(stub))
            alt_cave = client.read(ALT_STUB, len(alt_stub))
            if cave not in (bytes(len(stub)), stub) or alt_cave not in (
                bytes(len(alt_stub)),
                alt_stub,
            ):
                raise RuntimeError("Native EnterFUT trace cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(ALT_STUB, alt_stub)
            client.write(SITE, patch)
            client.write(ALT_SITE, alt_patch)
            if (
                client.read(SITE, 4) != patch
                or client.read(ALT_SITE, 4) != alt_patch
            ):
                raise RuntimeError("Native EnterFUT trace verification failed")
            print("Verified: both native EnterFUT return traces armed.")
            return 0
        if state == "patched":
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected native EnterFUT return site")
        if alt_state == "patched":
            client.write(ALT_SITE, ALT_ORIGINAL)
        elif alt_state != "original":
            raise RuntimeError("Unexpected menu-enabled EnterFUT return site")
        print("Verified: original native EnterFUT return sites restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
